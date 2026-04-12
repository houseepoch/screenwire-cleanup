"""
CC-First Deterministic Parser — graph/cc_parser.py
====================================================

Deterministic, no-LLM parser that reads CC output files and builds the
complete NarrativeGraph. Replaces Morpheus Agents 1–3.

Reads:
  - {project_dir}/creative_output/outline_skeleton.md
  - {project_dir}/creative_output/creative_output.md

Pipeline:
  Step 1: parse_skeleton()     → cast, locations, props, name_map
  Step 2: parse_creative_output() → scenes, frames, dialogue, cast_states
  Step 3: wire_edges()         → all GraphEdge types
  Step 4: validate()           → warnings / errors list

All parsing is pure string/regex — no LLM calls.
Expected wall time: <5 seconds for any project size.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .api import get_frame_cast_state_models
from .schema import (
    NarrativeGraph,
    ProjectNode,
    Provenance,
    CastNode,
    CastIdentity,
    CastStateVariant,
    CastFrameState,
    CastFrameRole,
    LocationNode,
    LocationDirections,
    LocationDirectionView,
    PropNode,
    SceneNode,
    StagingBeat,
    FrameNode,
    FrameBackground,
    DialogueNode,
    GraphEdge,
    EdgeType,
    NarrativeRole,
    TimeOfDay,
    canonical_edge_id,
)

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# REGEX PATTERNS — Section 2.3 of the spec
# ═══════════════════════════════════════════════════════════════════════════════

RE_CAST_TAG      = re.compile(r'^///CAST:\s*(.+)$', re.MULTILINE)
RE_LOCATION_TAG  = re.compile(r'^///LOCATION:\s*(.+)$', re.MULTILINE)
RE_LOCATION_DIR  = re.compile(r'^///LOCATION_DIR:\s*(.+)$', re.MULTILINE)
RE_PROP_TAG      = re.compile(r'^///PROP:\s*(.+)$', re.MULTILINE)
RE_SCENE_TAG     = re.compile(r'^///SCENE:\s*(.+)$', re.MULTILINE)
RE_DIALOGUE_TAG  = re.compile(r'^///DLG:\s*(.+)$', re.MULTILINE)

# Frame marker — lines starting with /// but NOT entity/meta tag keywords
RE_FRAME_MARKER  = re.compile(
    r'^///\s+(?!CAST:|LOCATION:|LOCATION_DIR:|PROP:|SCENE:|SCENE_STAGING:|DLG:|ADDITION_JUSTIFICATION:)(.+)$',
    re.MULTILINE,
)

RE_KV_PAIR   = re.compile(r'(\w+)=([^|]+)')
RE_ENV_FIELD = re.compile(r'(\w+)=([^|,]+)')


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_provenance(source_text: str) -> Provenance:
    """Build a parser-stamped Provenance node. source_prose_chunk is always non-empty."""
    return Provenance(
        source_prose_chunk=source_text.strip() or "(cc_parser)",
        generated_by="cc_parser",
        confidence=1.0,
        created_at=_now_iso(),
    )


def _slugify(name: str) -> str:
    """Convert a display name to a snake_case slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9\s_]', '', slug)
    slug = re.sub(r'\s+', '_', slug)
    return slug.strip('_')


def _parse_tag_fields(tag_line: str) -> dict[str, str]:
    """Parse pipe-separated key=value fields from a tag line.

    Splits on ' | ' first (canonical form), falls back to '|'.
    Returns dict of key → stripped value.
    """
    fields: dict[str, str] = {}
    segments = tag_line.split(' | ')
    if len(segments) == 1:
        segments = tag_line.split('|')
    for segment in segments:
        segment = segment.strip()
        m = RE_KV_PAIR.search(segment)
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def _parse_csv(value: str) -> list[str]:
    """Split a CSV string, strip whitespace, drop empty elements."""
    return [v.strip() for v in value.split(',') if v.strip()]


def _parse_hair(value: str) -> tuple[str, str, str]:
    """Parse hair field: 'length,style,color' → (hair_length, hair_style, hair_color)."""
    parts = [v.strip() for v in value.split(',')]
    return (
        parts[0] if len(parts) > 0 else "",
        parts[1] if len(parts) > 1 else "",
        parts[2] if len(parts) > 2 else "",
    )


def _resolve_cast_id(name: str, name_map: dict[str, str], warnings: list[str]) -> str:
    """Resolve a display name to a cast_id.

    Tries exact match → slug match → prefix match.
    Generates a fallback and warns if unresolvable.
    """
    normalized = name.lower().strip()
    if normalized in name_map:
        return name_map[normalized]
    slug = _slugify(name)
    if slug in name_map:
        return name_map[slug]
    # Partial match
    for key, val in name_map.items():
        if normalized in key or key in normalized:
            return val
    generated = f"cast_{slug}"
    warnings.append(f"WARN: name '{name}' not in name_map — generated id '{generated}'")
    return generated


def _resolve_narrative_role(role_str: str) -> NarrativeRole:
    mapping: dict[str, NarrativeRole] = {
        "protagonist": NarrativeRole.PROTAGONIST,
        "antagonist":  NarrativeRole.ANTAGONIST,
        "mentor":      NarrativeRole.MENTOR,
        "ally":        NarrativeRole.ALLY,
        "catalyst":    NarrativeRole.CATALYST,
        "supporting":  NarrativeRole.SUPPORTING,
        "background":  NarrativeRole.BACKGROUND,
    }
    return mapping.get(role_str.lower().strip(), NarrativeRole.SUPPORTING)


def _resolve_time_of_day(tod_str: str) -> Optional[TimeOfDay]:
    mapping: dict[str, TimeOfDay] = {
        "dawn":      TimeOfDay.DAWN,
        "morning":   TimeOfDay.MORNING,
        "midday":    TimeOfDay.MIDDAY,
        "afternoon": TimeOfDay.AFTERNOON,
        "dusk":      TimeOfDay.DUSK,
        "night":     TimeOfDay.NIGHT,
    }
    return mapping.get(tod_str.lower().strip())


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — SKELETON PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def extract_cast_tags(skeleton_text: str, warnings: list[str]) -> list[CastNode]:
    """Extract CastNode list from ///CAST tags in the skeleton."""
    nodes: list[CastNode] = []

    for m in RE_CAST_TAG.finditer(skeleton_text):
        raw = m.group(1)
        fields = _parse_tag_fields(raw)

        cast_id = fields.get('id', '').strip()
        name    = fields.get('name', '').strip()
        if not cast_id or not name:
            warnings.append(f"WARN: CAST tag missing id or name: {raw[:100]}")
            continue

        hair_length, hair_style, hair_color = _parse_hair(fields.get('hair', ''))
        age    = fields.get('age', '')
        gender = fields.get('gender', '')
        build  = fields.get('build', '')
        skin   = fields.get('skin', '')

        # Section 2.4: build physical_description string
        physical_description = (
            f"{age} {gender}, {build} build, {skin} skin, "
            f"{hair_color} {hair_length} {hair_style} hair"
        ).strip()

        identity = CastIdentity(
            age_descriptor=age or None,
            gender=gender or None,
            build=build or None,
            skin=skin or None,
            hair_length=hair_length or None,
            hair_style=hair_style or None,
            hair_color=hair_color or None,
            clothing=_parse_csv(fields.get('clothing', '')),
            clothing_style=fields.get('clothing_style') or None,
            clothing_fabric=fields.get('clothing_fabric') or None,
            footwear=fields.get('footwear') or None,
            accessories=_parse_csv(fields.get('accessories', '')),
            physical_description=physical_description,
            wardrobe_description=fields.get('wardrobe', ''),
        )

        # state_tags: "base" always implied; each listed tag gets a CastStateVariant
        state_variants: dict[str, CastStateVariant] = {
            "base": CastStateVariant(state_tag="base", derived_from="base"),
        }
        for tag in _parse_csv(fields.get('state_tags', '')):
            if tag and tag != 'base':
                state_variants[tag] = CastStateVariant(state_tag=tag, derived_from="base")

        node = CastNode(
            cast_id=cast_id,
            name=name,
            identity=identity,
            personality=", ".join(_parse_csv(fields.get('personality', ''))),
            role=_resolve_narrative_role(fields.get('role', 'supporting')),
            arc_summary=fields.get('arc', ''),
            state_variants=state_variants,
            provenance=_make_provenance(raw),
        )
        nodes.append(node)

    return nodes


def extract_location_tags(skeleton_text: str, warnings: list[str]) -> list[LocationNode]:
    """Extract LocationNode list from ///LOCATION + ///LOCATION_DIR tags."""
    loc_map: dict[str, LocationNode] = {}

    # First pass: base LocationNodes
    for m in RE_LOCATION_TAG.finditer(skeleton_text):
        raw = m.group(1)
        fields = _parse_tag_fields(raw)

        loc_id = fields.get('id', '').strip()
        name   = fields.get('name', '').strip()
        if not loc_id or not name:
            warnings.append(f"WARN: LOCATION tag missing id or name: {raw[:100]}")
            continue

        node = LocationNode(
            location_id=loc_id,
            name=name,
            location_type=fields.get('type', 'exterior') or 'exterior',
            atmosphere=fields.get('atmosphere', ''),
            material_palette=_parse_csv(fields.get('material_palette', '')),
            architecture_keywords=_parse_csv(fields.get('architecture', '')),
            flora=fields.get('flora') or None,
            description=fields.get('description', ''),
            provenance=_make_provenance(raw),
        )
        loc_map[loc_id] = node

    # Second pass: attach direction views from ///LOCATION_DIR tags
    for m in RE_LOCATION_DIR.finditer(skeleton_text):
        raw = m.group(1)
        fields = _parse_tag_fields(raw)

        loc_id    = fields.get('id', '').strip()
        direction = fields.get('direction', '').lower().strip()

        if loc_id not in loc_map:
            warnings.append(f"WARN: LOCATION_DIR references unknown location '{loc_id}'")
            continue

        valid_directions = ('north', 'south', 'east', 'west', 'exterior')
        if direction not in valid_directions:
            warnings.append(
                f"WARN: LOCATION_DIR has unknown direction '{direction}' for '{loc_id}'"
            )
            continue

        view = LocationDirectionView(
            description=fields.get('description', ''),
            key_features=_parse_csv(fields.get('features', '')),
            depth_description=fields.get('depth', ''),
        )
        setattr(loc_map[loc_id].directions, direction, view)

    return list(loc_map.values())


def extract_prop_tags(skeleton_text: str, warnings: list[str]) -> list[PropNode]:
    """Extract PropNode list from ///PROP tags."""
    nodes: list[PropNode] = []

    for m in RE_PROP_TAG.finditer(skeleton_text):
        raw = m.group(1)
        fields = _parse_tag_fields(raw)

        prop_id = fields.get('id', '').strip()
        name    = fields.get('name', '').strip()
        if not prop_id or not name:
            warnings.append(f"WARN: PROP tag missing id or name: {raw[:100]}")
            continue

        node = PropNode(
            prop_id=prop_id,
            name=name,
            description=fields.get('description', ''),
            narrative_significance=fields.get('significance', ''),
            associated_cast=_parse_csv(fields.get('associated_cast', '')),
            material_context=_parse_csv(fields.get('materials', '')),
            provenance=_make_provenance(raw),
        )
        nodes.append(node)

    return nodes


def build_name_to_id_map(
    cast_nodes: list[CastNode],
    location_nodes: list[LocationNode],
    prop_nodes: list[PropNode],
) -> dict[str, str]:
    """Build display-name → entity-id lookup for name resolution."""
    name_map: dict[str, str] = {}
    for n in cast_nodes:
        name_map[n.name.lower().strip()] = n.cast_id
        name_map[_slugify(n.name)] = n.cast_id
    for n in location_nodes:
        name_map[n.name.lower().strip()] = n.location_id
        name_map[_slugify(n.name)] = n.location_id
    for n in prop_nodes:
        name_map[n.name.lower().strip()] = n.prop_id
        name_map[_slugify(n.name)] = n.prop_id
    return name_map


def parse_skeleton(skeleton_text: str, warnings: list[str]) -> dict:
    """Step 1: Parse skeleton text → entities dict.

    Returns dict with keys: cast, locations, props, name_map.
    """
    cast_nodes     = extract_cast_tags(skeleton_text, warnings)
    location_nodes = extract_location_tags(skeleton_text, warnings)
    prop_nodes     = extract_prop_tags(skeleton_text, warnings)
    name_map       = build_name_to_id_map(cast_nodes, location_nodes, prop_nodes)

    return {
        'cast':      cast_nodes,
        'locations': location_nodes,
        'props':     prop_nodes,
        'name_map':  name_map,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — CREATIVE OUTPUT PARSING
# ═══════════════════════════════════════════════════════════════════════════════

# ── Scene extraction ─────────────────────────────────────────────────────────

def _extract_scene_map(text: str, warnings: list[str]) -> tuple[dict[str, SceneNode], list[str]]:
    """Extract SceneNode dict + ordered scene_id list from ///SCENE tags."""
    scenes: dict[str, SceneNode] = {}
    scene_order: list[str] = []

    for m in RE_SCENE_TAG.finditer(text):
        raw = m.group(1)
        fields = _parse_tag_fields(raw)

        scene_id = fields.get('id', '').strip()
        if not scene_id:
            warnings.append(f"WARN: SCENE tag missing id: {raw[:100]}")
            continue

        if scene_id in scenes:
            continue  # deduplicate; first occurrence wins

        num_m = re.search(r'scene_0*(\d+)', scene_id)
        scene_number = int(num_m.group(1)) if num_m else 0

        location_id = fields.get('location', '').strip()
        tod_str     = fields.get('time_of_day', '').strip()
        tod         = _resolve_time_of_day(tod_str)
        int_ext     = fields.get('int_ext', 'INT').upper()

        # Placeholder heading — updated post-parse with real location name
        scene_heading = f"{int_ext}. {location_id} — {tod_str.upper()}"

        # cast_states field → initial per-cast state defaults for the scene
        # Stored temporarily in provenance metadata; applied during frame extraction.
        node = SceneNode(
            scene_id=scene_id,
            scene_number=scene_number,
            title=fields.get('title', ''),
            location_id=location_id or None,
            time_of_day=tod,
            int_ext=int_ext,
            scene_heading=scene_heading,
            cast_present=_parse_csv(fields.get('cast', '')),
            props_present=_parse_csv(fields.get('props', '')),
            mood_keywords=_parse_csv(fields.get('mood', '')),
            pacing=fields.get('pacing') or None,
            provenance=_make_provenance(raw),
        )
        # Stash cast_states for frame extraction (not a schema field)
        node.__dict__['_cast_states_raw'] = fields.get('cast_states', '')

        scenes[scene_id] = node
        scene_order.append(scene_id)

    return scenes, scene_order


# ── Staging extraction ────────────────────────────────────────────────────────

def _extract_staging_blocks(text: str) -> list[str]:
    """Collect full ///SCENE_STAGING blocks including multiline | continuations.

    The CC may format staging on one long line or across several lines
    where each beat starts with '| beat: ...'. Both forms are collapsed
    into a single string per block.
    """
    blocks: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith('///SCENE_STAGING:'):
            first_content = stripped[len('///SCENE_STAGING:'):].strip()
            parts = [first_content]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt:
                    i += 1
                    continue
                if nxt.startswith('|') and not nxt.startswith('///'):
                    # Strip the leading '|' from continuation lines
                    parts.append(nxt[1:].strip())
                    i += 1
                else:
                    break
            blocks.append(' | '.join(parts))
        else:
            i += 1
    return blocks


def _parse_staging_block(
    block_content: str,
    warnings: list[str],
) -> tuple[str, dict[str, StagingBeat]]:
    """Parse a collapsed SCENE_STAGING block into (scene_id, staging_plan dict)."""
    id_match = re.search(r'id=(scene_\w+)', block_content)
    if not id_match:
        warnings.append(f"WARN: SCENE_STAGING missing id: {block_content[:100]}")
        return '', {}

    scene_id = id_match.group(1)
    staging_plan: dict[str, StagingBeat] = {}

    for beat_name in ('start', 'mid', 'end'):
        pattern = re.compile(
            rf'\b{beat_name}:\s*(.+?)(?=\b(?:start|mid|end):|$)',
            re.DOTALL | re.IGNORECASE,
        )
        bm = pattern.search(block_content)
        if not bm:
            continue

        beat_raw = bm.group(1).strip().strip('|').strip()
        beat = StagingBeat()

        # Each entry: cast_id=position,looking_at,facing
        for entry_m in re.finditer(r'(cast_\w+)=([^|]+)', beat_raw):
            c_id   = entry_m.group(1).strip()
            values = [v.strip() for v in entry_m.group(2).strip().split(',')]
            if len(values) >= 1:
                beat.cast_positions[c_id]  = values[0]
            if len(values) >= 2:
                beat.cast_looking_at[c_id] = values[1]
            if len(values) >= 3:
                beat.cast_facing[c_id]     = values[2]

        staging_plan[beat_name] = beat

    return scene_id, staging_plan


def _attach_staging_plans(
    skeleton_text: str,
    scenes: dict[str, SceneNode],
    warnings: list[str],
) -> None:
    """Parse all ///SCENE_STAGING blocks and attach them to their SceneNodes."""
    for block_content in _extract_staging_blocks(skeleton_text):
        scene_id, staging_plan = _parse_staging_block(block_content, warnings)
        if scene_id and scene_id in scenes:
            scenes[scene_id].staging_plan = staging_plan
        elif scene_id:
            warnings.append(f"WARN: SCENE_STAGING references unknown scene '{scene_id}'")


# ── Frame marker parsing ──────────────────────────────────────────────────────

def _parse_marker_fields(marker_content: str) -> dict[str, str]:
    """Parse frame marker content into a structured fields dict.

    Handles: cast:{names} | cam:{dir} | dlg | cast_states:{...}
    """
    fields: dict[str, str] = {}
    segments = [s.strip() for s in marker_content.split('|')]
    for seg in segments:
        if seg == 'dlg':
            fields['dlg'] = 'true'
        elif seg.startswith('cast:'):
            fields['cast'] = seg[5:].strip()
        elif seg.startswith('cam:'):
            fields['cam'] = seg[4:].strip()
        elif seg.startswith('cast_states:'):
            fields['cast_states'] = seg[12:].strip()
        elif ':' in seg:
            k, _, v = seg.partition(':')
            fields[k.strip()] = v.strip()
    return fields


def extract_frame_markers(
    creative_text: str,
    scenes: dict[str, SceneNode],
    name_map: dict[str, str],
    warnings: list[str],
) -> tuple[list[FrameNode], list[CastFrameState]]:
    """Walk creative_output.md line by line, extracting /// frame markers.

    Tracks current scene context (updated on ///SCENE lines).
    For each frame marker:
      1. Parse marker fields (cast, cam, dlg, cast_states).
      2. Capture paragraph text until next /// or EOF.
      3. Build FrameNode with incremented sequence_index.
      4. Build base CastFrameState per cast member.
      5. Link previous_frame_id / next_frame_id.

    Returns (list[FrameNode], list[CastFrameState]).
    """
    lines = creative_text.splitlines()
    frames: list[FrameNode]           = []
    all_cast_states: list[CastFrameState] = []

    frame_counter   = 0
    current_scene_id   = ''
    current_location_id = ''
    current_time_of_day: Optional[TimeOfDay] = None
    # cast_id → active_state_tag default for current scene
    scene_cast_defaults: dict[str, str] = {}

    re_scene_inline = re.compile(r'^///SCENE:\s*(.+)$')
    re_staging_inline = re.compile(r'^///SCENE_STAGING:')
    re_frame_inline = re.compile(
        r'^///\s+(?!CAST:|LOCATION:|LOCATION_DIR:|PROP:|SCENE:|SCENE_STAGING:|DLG:|ADDITION_JUSTIFICATION:)(.+)$'
    )

    i = 0
    while i < len(lines):
        line = lines[i]

        # ── Scene header ────────────────────────────────────────────────────
        sm = re_scene_inline.match(line)
        if sm:
            raw = sm.group(1)
            fields = _parse_tag_fields(raw)
            current_scene_id    = fields.get('id', current_scene_id)
            current_location_id = fields.get('location', current_location_id)
            tod_str = fields.get('time_of_day', '')
            if tod_str:
                current_time_of_day = _resolve_time_of_day(tod_str)

            # Reset scene cast defaults
            scene_cast_defaults = {}
            if current_scene_id in scenes:
                for cid in scenes[current_scene_id].cast_present:
                    scene_cast_defaults[cid] = 'base'

            # Apply cast_states overrides from this scene header
            cast_states_raw = fields.get('cast_states', '')
            if cast_states_raw:
                for pair in cast_states_raw.split(','):
                    pair = pair.strip()
                    if ':' in pair:
                        c_id, _, s_tag = pair.partition(':')
                        scene_cast_defaults[c_id.strip()] = s_tag.strip()

            i += 1
            continue

        # Skip SCENE_STAGING lines (handled separately via _attach_staging_plans)
        if re_staging_inline.match(line.strip()):
            # Consume continuation lines
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if nxt.startswith('|') and not nxt.startswith('///'):
                    i += 1
                elif nxt == '':
                    i += 1
                else:
                    break
            continue

        # ── Frame marker ────────────────────────────────────────────────────
        fm = re_frame_inline.match(line)
        if fm:
            marker_content = fm.group(1).strip()
            marker_fields  = _parse_marker_fields(marker_content)

            # Capture paragraph text until next /// line or EOF
            i += 1
            para_lines: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith('///'):
                    break
                para_lines.append(nxt)
                i += 1

            source_text = '\n'.join(para_lines).strip()

            # Frame identity
            frame_counter += 1
            frame_id = f"f_{frame_counter:03d}"

            cam         = marker_fields.get('cam', '').strip() or None
            is_dialogue = 'dlg' in marker_fields

            # Resolve cast members in this frame
            cast_names_raw = marker_fields.get('cast', '')
            cast_names     = _parse_csv(cast_names_raw) if cast_names_raw else []
            cast_ids       = [_resolve_cast_id(n, name_map, warnings) for n in cast_names]

            # Per-frame cast_states overrides
            frame_state_overrides: dict[str, str] = {}
            cs_raw = marker_fields.get('cast_states', '')
            if cs_raw:
                for pair in cs_raw.split(','):
                    pair = pair.strip()
                    if '=' in pair:
                        c_name, _, s_tag = pair.partition('=')
                        c_id = _resolve_cast_id(c_name.strip(), name_map, warnings)
                        frame_state_overrides[c_id] = s_tag.strip()

            # Build base CastFrameState per cast member
            frame_cast_states: list[CastFrameState] = []
            prov_text = (marker_content + " " + source_text[:150]).strip() or "(cc_parser)"
            for c_id in cast_ids:
                active_tag = (
                    frame_state_overrides.get(c_id)
                    or scene_cast_defaults.get(c_id)
                    or 'base'
                )
                # Frame role: SUBJECT when sole cast, BACKGROUND otherwise
                frame_role = (
                    CastFrameRole.SUBJECT if len(cast_ids) == 1
                    else CastFrameRole.BACKGROUND
                )
                cs = CastFrameState(
                    cast_id=c_id,
                    frame_id=frame_id,
                    frame_role=frame_role,
                    active_state_tag=active_tag,
                    provenance=_make_provenance(prov_text),
                )
                frame_cast_states.append(cs)
                all_cast_states.append(cs)

            # FrameNode
            frame_node = FrameNode(
                frame_id=frame_id,
                scene_id=current_scene_id,
                sequence_index=frame_counter - 1,
                source_text=source_text,
                narrative_beat=source_text,
                is_dialogue=is_dialogue,
                location_id=current_location_id or None,
                time_of_day=current_time_of_day,
                cast_states=frame_cast_states,
                background=FrameBackground(camera_facing=cam),
                provenance=_make_provenance(
                    source_text[:200] if source_text else marker_content or "(cc_parser)"
                ),
            )
            frames.append(frame_node)

            # Register frame with its scene
            if current_scene_id and current_scene_id in scenes:
                scene = scenes[current_scene_id]
                if frame_id not in scene.frame_ids:
                    scene.frame_ids.append(frame_id)
                scene.frame_count = len(scene.frame_ids)

            continue

        i += 1  # non-matching line

    # ── Wire previous_frame_id / next_frame_id + continuity_chain ────────────
    for idx, frame in enumerate(frames):
        if idx > 0:
            frame.previous_frame_id = frames[idx - 1].frame_id
        if idx < len(frames) - 1:
            frame.next_frame_id = frames[idx + 1].frame_id

        if idx > 0:
            prev = frames[idx - 1]
            frame.continuity_chain = (
                frame.scene_id == prev.scene_id
                and frame.location_id is not None
                and frame.location_id == prev.location_id
            )

    return frames, all_cast_states


# ── Dialogue extraction ───────────────────────────────────────────────────────

def _extract_by_src_lines(creative_lines: list[str], start_line: int, end_line: int) -> str:
    """Extract text from line range (1-indexed input → 0-indexed slice)."""
    s = max(0, start_line - 1)
    e = min(len(creative_lines), end_line)
    return '\n'.join(creative_lines[s:e])


def _validate_fuzzy_anchors(
    src_start: str,
    src_end: str,
    start_line: int,
    end_line: int,
    creative_lines: list[str],
) -> bool:
    """Fuzzy-match src_start and src_end within ±5 lines of the src_lines window."""
    search_start = max(0, start_line - 6)
    search_end   = min(len(creative_lines), end_line + 5)
    search_text  = '\n'.join(creative_lines[search_start:search_end])
    start_ok = (not src_start) or (src_start in search_text)
    end_ok   = (not src_end)   or (src_end   in search_text)
    return start_ok and end_ok


def extract_dialogue(
    skeleton_text: str,
    creative_text: str,
    frames: list[FrameNode],
    name_map: dict[str, str],
    warnings: list[str],
) -> list[DialogueNode]:
    """Extract DialogueNode list from ///DLG tags in skeleton.

    For each tag:
      1. Parse fields: speaker, cast_id, src_lines, src_start, src_end, perf, env.
      2. Extract verbatim text from creative_output.md at src_lines (primary anchor).
      3. Fuzzy-validate with src_start / src_end anchors (±5 lines).
      4. Assign temporal span = enclosing dlg frame (sequentially matched).
      5. Parse ENV tag fields.
    """
    creative_lines = creative_text.splitlines()

    # Sequential dlg frame iterator — DLG tags map to dlg frames in order
    dlg_frames = [f for f in frames if f.is_dialogue]
    dlg_iter   = iter(dlg_frames)
    current_dlg_frame: Optional[FrameNode] = next(dlg_iter, None)
    last_used_dlg_frame: Optional[FrameNode] = current_dlg_frame

    # frame_id → scene_id lookup
    frame_to_scene: dict[str, str] = {f.frame_id: f.scene_id for f in frames}

    dialogue_nodes: list[DialogueNode] = []
    dlg_counter = 0

    for m in RE_DIALOGUE_TAG.finditer(skeleton_text):
        raw    = m.group(1)
        fields = _parse_tag_fields(raw)

        speaker     = fields.get('speaker', '').strip()
        cast_id_raw = fields.get('cast_id', '').strip()
        src_lines   = fields.get('src_lines', '').strip()
        src_start   = fields.get('src_start', '').strip().strip('"').strip("'")
        src_end     = fields.get('src_end',   '').strip().strip('"').strip("'")
        perf        = fields.get('perf', '').strip()
        env_raw     = fields.get('env', '').strip()

        cast_id = cast_id_raw or _resolve_cast_id(speaker, name_map, warnings)

        # ── Extract raw_line from creative_output at src_lines ──────────────
        raw_line   = ''
        start_line = 0
        end_line   = 0

        if src_lines and '-' in src_lines:
            try:
                sl_parts   = src_lines.split('-')
                start_line = int(sl_parts[0].strip())
                end_line   = int(sl_parts[1].strip())
                raw_line   = _extract_by_src_lines(creative_lines, start_line, end_line)

                # Fuzzy anchor validation (warn but don't halt)
                if (src_start or src_end) and not _validate_fuzzy_anchors(
                    src_start, src_end, start_line, end_line, creative_lines
                ):
                    warnings.append(
                        f"WARN: DLG anchor mismatch for speaker='{speaker}' "
                        f"src_lines={src_lines} src_start='{src_start[:30]}'"
                    )
            except (ValueError, IndexError) as exc:
                warnings.append(f"WARN: cannot parse src_lines '{src_lines}' for '{speaker}': {exc}")

        if not raw_line.strip():
            warnings.append(
                f"WARN: DLG tag for '{speaker}' has no resolved raw_line (src_lines={src_lines})"
            )
            raw_line = f"[dialogue: {speaker}]"

        # ── ENV parsing ─────────────────────────────────────────────────────
        env_location: Optional[str] = None
        env_distance: Optional[str] = None
        env_intensity: Optional[str] = None
        env_medium: Optional[str]   = None
        env_atmosphere: list[str]   = []

        if env_raw:
            env_parts = _parse_csv(env_raw)
            if len(env_parts) > 0: env_location  = env_parts[0]
            if len(env_parts) > 1: env_distance  = env_parts[1]
            if len(env_parts) > 2: env_intensity  = env_parts[2]
            if len(env_parts) > 3: env_medium     = env_parts[3]
            if len(env_parts) > 4: env_atmosphere = env_parts[4:]

        # ── Temporal span assignment ─────────────────────────────────────────
        if current_dlg_frame is not None:
            assoc_frame = current_dlg_frame
        elif last_used_dlg_frame is not None:
            assoc_frame = last_used_dlg_frame
        elif frames:
            assoc_frame = frames[-1]
        else:
            assoc_frame = None

        assoc_frame_id = assoc_frame.frame_id if assoc_frame else 'f_001'
        assoc_scene_id = frame_to_scene.get(assoc_frame_id, '')

        dlg_counter += 1
        dialogue_id = f"dlg_{dlg_counter:03d}"

        node = DialogueNode(
            dialogue_id=dialogue_id,
            scene_id=assoc_scene_id,
            order=dlg_counter - 1,
            speaker=speaker,
            cast_id=cast_id,
            start_frame=assoc_frame_id,
            end_frame=assoc_frame_id,
            primary_visual_frame=assoc_frame_id,
            raw_line=raw_line.strip(),
            line=raw_line.strip(),
            performance_direction=perf,
            env_tags=env_raw,
            env_location=env_location,
            env_distance=env_distance,
            env_intensity=env_intensity,
            env_medium=env_medium,
            env_atmosphere=env_atmosphere,
            provenance=_make_provenance(raw),
        )
        dialogue_nodes.append(node)

        # Register dialogue_id on the frame and advance to next dlg frame
        if assoc_frame is not None:
            if dialogue_id not in assoc_frame.dialogue_ids:
                assoc_frame.dialogue_ids.append(dialogue_id)
            last_used_dlg_frame = assoc_frame
            # Advance only once per DLG tag so multiple tags can share a frame
            # if there's only one dlg frame left
            next_frame = next(dlg_iter, None)
            if next_frame is not None:
                current_dlg_frame = next_frame
            # else: current_dlg_frame stays as-is (remaining tags pile onto last frame)

    return dialogue_nodes


# ── Step 2 main entry ────────────────────────────────────────────────────────

def parse_creative_output(
    creative_text: str,
    skeleton_text: str,
    name_map: dict[str, str],
    warnings: list[str],
) -> dict:
    """Step 2: Parse creative output + skeleton → scenes, frames, dialogue, cast_states.

    Scene definitions live in the skeleton; the creative output drives frame
    extraction (scene headers there are for context tracking only).
    """
    # Scene definitions: skeleton is authoritative
    scenes, scene_order = _extract_scene_map(skeleton_text, warnings)

    # Merge any scene headers present only in creative output (edge case)
    co_scenes, co_order = _extract_scene_map(creative_text, warnings)
    for sid, snode in co_scenes.items():
        if sid not in scenes:
            scenes[sid] = snode
    for sid in co_order:
        if sid not in scene_order:
            scene_order.append(sid)

    # Staging plans (skeleton only)
    _attach_staging_plans(skeleton_text, scenes, warnings)

    # Frame extraction (creative output)
    frames, cast_states = extract_frame_markers(creative_text, scenes, name_map, warnings)

    # Dialogue extraction (DLG tags in skeleton, text in creative output)
    dialogue_nodes = extract_dialogue(skeleton_text, creative_text, frames, name_map, warnings)

    return {
        'scenes':       scenes,
        'scene_order':  scene_order,
        'frames':       frames,
        'cast_states':  cast_states,
        'dialogue':     dialogue_nodes,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — EDGE WIRING
# ═══════════════════════════════════════════════════════════════════════════════

def _make_edge(
    source_id: str,
    edge_type: EdgeType,
    target_id: str,
    evidence: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
) -> GraphEdge:
    """Build a GraphEdge with a canonical edge_id and parser provenance."""
    eid = canonical_edge_id(source_id, edge_type, target_id)
    return GraphEdge(
        edge_id=eid,
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        evidence=evidence or [],
        metadata=metadata or {},
        provenance=_make_provenance(f"{source_id} {edge_type.value} {target_id}"),
    )


def wire_edges(graph: NarrativeGraph, warnings: list[str]) -> list[GraphEdge]:
    """Step 3: Wire all edge types and return the full edge list.

    Edge types:
      FOLLOWS          — frame[N] → frame[N+1] (global sequence)
      BELONGS_TO_SCENE — frame → scene
      APPEARS_IN       — cast_id → frame_id (per CastFrameState)
      AT_LOCATION      — frame_id → location_id
      DIALOGUE_SPANS   — dialogue_id → frame_id (temporal span)
      SPOKEN_BY        — dialogue_id → cast_id
      USES_PROP        — frame_id → prop_id (scene-level props)
      CONTINUITY_CHAIN — frame[N] → frame[N+1] (same scene+location)
    """
    edges: list[GraphEdge] = []
    seen_edge_ids: set[str] = set()

    def add_edge(e: GraphEdge) -> None:
        if e.edge_id not in seen_edge_ids:
            edges.append(e)
            seen_edge_ids.add(e.edge_id)

    frames_ordered = [graph.frames[fid] for fid in graph.frame_order if fid in graph.frames]

    # FOLLOWS: frame[N] → frame[N+1]
    for idx in range(len(frames_ordered) - 1):
        add_edge(_make_edge(
            frames_ordered[idx].frame_id,
            EdgeType.FOLLOWS,
            frames_ordered[idx + 1].frame_id,
            evidence=["sequential_frame_order"],
        ))

    for frame in frames_ordered:
        fid = frame.frame_id

        # BELONGS_TO_SCENE
        if frame.scene_id:
            add_edge(_make_edge(fid, EdgeType.BELONGS_TO_SCENE, frame.scene_id))

        # AT_LOCATION
        if frame.location_id:
            add_edge(_make_edge(fid, EdgeType.AT_LOCATION, frame.location_id))

        # APPEARS_IN (cast_id → frame_id)
        for cs in get_frame_cast_state_models(graph, fid):
            add_edge(_make_edge(cs.cast_id, EdgeType.APPEARS_IN, fid))

        # USES_PROP (scene-level — all props in the scene appear in every frame of that scene)
        if frame.scene_id and frame.scene_id in graph.scenes:
            for prop_id in graph.scenes[frame.scene_id].props_present:
                add_edge(_make_edge(fid, EdgeType.USES_PROP, prop_id))

        # CONTINUITY_CHAIN (same scene + same location as previous frame)
        if frame.next_frame_id and frame.continuity_chain:
            add_edge(_make_edge(fid, EdgeType.CONTINUITY_CHAIN, frame.next_frame_id))

    # DIALOGUE_SPANS + SPOKEN_BY
    for dlg in graph.dialogue.values():
        # SPOKEN_BY
        add_edge(_make_edge(dlg.dialogue_id, EdgeType.SPOKEN_BY, dlg.cast_id))

        # DIALOGUE_SPANS — cover full start..end frame range
        if dlg.start_frame == dlg.end_frame:
            add_edge(_make_edge(dlg.dialogue_id, EdgeType.DIALOGUE_SPANS, dlg.start_frame))
        else:
            in_range = False
            for fid in graph.frame_order:
                if fid == dlg.start_frame:
                    in_range = True
                if in_range:
                    add_edge(_make_edge(dlg.dialogue_id, EdgeType.DIALOGUE_SPANS, fid))
                if fid == dlg.end_frame:
                    break

    return edges


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate(graph: NarrativeGraph, warnings: list[str]) -> list[str]:
    """Step 4: Validate graph integrity per Section 2.8.

    Appends all findings to warnings and returns just the new issues.
    """
    issues: list[str] = []

    cast_ids     = set(graph.cast.keys())
    location_ids = set(graph.locations.keys())
    prop_ids     = set(graph.props.keys())
    frame_ids    = set(graph.frames.keys())

    # Sequential frame order — contiguous with no duplicates
    if len(graph.frame_order) != len(set(graph.frame_order)):
        issues.append("ERROR: duplicate frame_ids in frame_order")

    for idx, fid in enumerate(graph.frame_order):
        expected = f"f_{idx + 1:03d}"
        if fid != expected:
            issues.append(
                f"ERROR: frame_order[{idx}] = '{fid}', expected '{expected}'"
            )

    for frame in graph.frames.values():
        fid = frame.frame_id

        # Empty source_text
        if not frame.source_text or not frame.source_text.strip():
            issues.append(f"ERROR: frame {fid} has empty source_text")

        # Missing camera direction
        if not frame.background.camera_facing:
            issues.append(f"ERROR: frame {fid} missing background.camera_facing")

        # Orphan cast
        for cs in get_frame_cast_state_models(graph, fid):
            if cs.cast_id not in cast_ids:
                issues.append(f"ERROR: frame {fid}: cast_id '{cs.cast_id}' not in graph.cast")

        # Orphan location
        if frame.location_id and frame.location_id not in location_ids:
            issues.append(
                f"ERROR: frame {fid}: location_id '{frame.location_id}' not in graph.locations"
            )

        # dlg frame without any dialogue_ids
        if frame.is_dialogue and not frame.dialogue_ids:
            issues.append(f"WARN: frame {fid} is_dialogue=True but has no dialogue_ids")

        # camera_facing direction should exist on the location
        cam = frame.background.camera_facing
        if cam and frame.location_id and frame.location_id in graph.locations:
            loc = graph.locations[frame.location_id]
            if cam in ('north', 'south', 'east', 'west', 'exterior'):
                view = getattr(loc.directions, cam, None)
                if view is None:
                    issues.append(
                        f"WARN: frame {fid}: camera_facing='{cam}' but "
                        f"location '{frame.location_id}' has no '{cam}' direction view"
                    )

    # Orphan props in scenes
    for scene in graph.scenes.values():
        for prop_id in scene.props_present:
            if prop_id not in prop_ids:
                issues.append(
                    f"WARN: scene {scene.scene_id}: prop_id '{prop_id}' not in graph.props"
                )

    # Dialogue integrity
    dlg_referenced_frames: set[str] = set()
    for dlg in graph.dialogue.values():
        dlg_referenced_frames.add(dlg.start_frame)

        if dlg.primary_visual_frame not in frame_ids:
            issues.append(
                f"WARN: dialogue {dlg.dialogue_id}: primary_visual_frame "
                f"'{dlg.primary_visual_frame}' not in graph.frames"
            )
        if dlg.cast_id not in cast_ids:
            issues.append(
                f"WARN: dialogue {dlg.dialogue_id}: cast_id '{dlg.cast_id}' not in graph.cast"
            )

    # Dialogue without dlg flag
    for fid in dlg_referenced_frames:
        if fid in graph.frames and not graph.frames[fid].is_dialogue:
            issues.append(
                f"WARN: frame {fid} referenced by dialogue but is_dialogue=False"
            )

    warnings.extend(issues)
    return issues


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════

def parse_cc_output(
    project_dir: Path,
    project_node: ProjectNode,
) -> NarrativeGraph:
    """Parse CC output files and build the complete NarrativeGraph.

    Reads:
      - {project_dir}/creative_output/outline_skeleton.md
      - {project_dir}/creative_output/creative_output.md

    Returns a fully populated NarrativeGraph with all entities, frames,
    dialogue, states, and edges. Ready for Haiku enrichment.

    No LLM calls. Expected wall time: <5 seconds.
    """
    warnings: list[str] = []

    project_dir = Path(project_dir)
    co_dir      = project_dir / "creative_output"
    skel_path   = co_dir / "outline_skeleton.md"
    prose_path  = co_dir / "creative_output.md"

    if not skel_path.exists():
        raise FileNotFoundError(f"Skeleton not found: {skel_path}")
    if not prose_path.exists():
        raise FileNotFoundError(f"Creative output not found: {prose_path}")

    skeleton_text = skel_path.read_text(encoding='utf-8')
    creative_text = prose_path.read_text(encoding='utf-8')

    # ── Step 1: Parse entities from skeleton ─────────────────────────────────
    skel_data      = parse_skeleton(skeleton_text, warnings)
    cast_nodes:     list[CastNode]     = skel_data['cast']
    location_nodes: list[LocationNode] = skel_data['locations']
    prop_nodes:     list[PropNode]     = skel_data['props']
    name_map:       dict[str, str]     = skel_data['name_map']

    # ── Step 2: Parse creative output ────────────────────────────────────────
    co_data = parse_creative_output(creative_text, skeleton_text, name_map, warnings)
    scenes:         dict[str, SceneNode]     = co_data['scenes']
    scene_order:    list[str]                = co_data['scene_order']
    frames:         list[FrameNode]          = co_data['frames']
    cast_states:    list[CastFrameState]     = co_data['cast_states']
    dialogue_nodes: list[DialogueNode]       = co_data['dialogue']

    # ── Refine scene headings now that we have real location names ────────────
    loc_name_map = {n.location_id: n.name for n in location_nodes}
    for scene in scenes.values():
        if scene.location_id and scene.location_id in loc_name_map:
            loc_name = loc_name_map[scene.location_id].upper()
            tod = scene.time_of_day.value.upper() if scene.time_of_day else ''
            scene.scene_heading = f"{scene.int_ext}. {loc_name} — {tod}"
        # Update location.scenes_used
        if scene.location_id:
            for loc in location_nodes:
                if loc.location_id == scene.location_id:
                    if scene.scene_id not in loc.scenes_used:
                        loc.scenes_used.append(scene.scene_id)

    # ── Ordered sequences ─────────────────────────────────────────────────────
    frame_order    = [f.frame_id for f in frames]
    dialogue_order = [d.dialogue_id for d in dialogue_nodes]

    # ── Cast frame states dict (keyed by {cast_id}@{frame_id}) ───────────────
    cast_frame_states_dict: dict[str, CastFrameState] = {
        f"{cs.cast_id}@{cs.frame_id}": cs
        for cs in cast_states
    }

    # ── Assemble graph ────────────────────────────────────────────────────────
    graph = NarrativeGraph(
        project=project_node,
        cast={n.cast_id: n for n in cast_nodes},
        locations={n.location_id: n for n in location_nodes},
        props={n.prop_id: n for n in prop_nodes},
        scenes=scenes,
        frames={f.frame_id: f for f in frames},
        dialogue={d.dialogue_id: d for d in dialogue_nodes},
        cast_frame_states=cast_frame_states_dict,
        frame_order=frame_order,
        scene_order=scene_order,
        dialogue_order=dialogue_order,
        build_log=list(warnings),
    )

    # Mark seeded domains
    graph.seeded_domains.update({
        'cast_identity':    bool(cast_nodes),
        'locations':        bool(location_nodes),
        'props':            bool(prop_nodes),
        'scenes':           bool(scenes),
        'frames':           bool(frames),
        'dialogue':         bool(dialogue_nodes),
        'cast_frame_states': bool(cast_states),
    })

    # ── Step 3: Wire edges ────────────────────────────────────────────────────
    graph.edges = wire_edges(graph, warnings)
    graph.seeded_domains['edges_relationships'] = True
    graph.seeded_domains['edges_continuity']    = True

    # ── Step 4: Validate ──────────────────────────────────────────────────────
    issues = validate(graph, warnings)

    # Final build_log with all warnings + issues
    graph.build_log = list(warnings)

    error_count = sum(1 for w in issues if w.startswith('ERROR'))
    warn_count  = sum(1 for w in issues if w.startswith('WARN'))

    log.info(
        "cc_parser: %d cast | %d locations | %d props | %d scenes | "
        "%d frames | %d dialogue | %d edges | %d errors | %d warnings",
        len(cast_nodes), len(location_nodes), len(prop_nodes),
        len(scenes), len(frames), len(dialogue_nodes),
        len(graph.edges), error_count, warn_count,
    )

    return graph

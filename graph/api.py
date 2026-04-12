"""
Graph API — Morpheus's Tool Belt
==================================

Python functions that Morpheus calls to manage the narrative graph.
These are the database operations: query, upsert, wire edges,
detect conflicts, trace errors, and surgically correct them.

Each function operates on the in-memory NarrativeGraph and the
GraphStore handles persistence. Morpheus calls these via Python
skills (bash tool calls to Python scripts that import this module).

Design principle: Morpheus is a database manager, not just a
text generator. These tools give it the precision to trace any
data point back to its source and fix errors without burning
down the graph.
"""

from __future__ import annotations

from typing import Any, Optional

from .schema import (
    NarrativeGraph,
    CastNode, CastFrameState,
    LocationNode, LocationFrameState,
    PropNode, PropFrameState,
    SceneNode, FrameNode, DialogueNode,
    StoryboardGrid, ShotMatchGroup,
    ShotPacket, ShotNeighborBeat, ShotAudioBeat, ShotAudioTurn, ShotIntent,
    GraphEdge, EdgeType, Provenance, canonical_edge_id,
)
from .store import GraphStore


# ═══════════════════════════════════════════════════════════════════════════════
# QUERY OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════


def query_graph(
    graph: NarrativeGraph,
    node_type: str,
    filters: Optional[dict[str, Any]] = None,
) -> list[dict]:
    """Query graph nodes by type with optional field filters.

    node_type: "cast", "location", "prop", "scene", "frame", "dialogue",
               "cast_frame_state", "prop_frame_state", "location_frame_state"
    filters: dict of field_name → value to match (exact match)

    Returns list of matching nodes as dicts.

    Examples:
        query_graph(g, "cast", {"name": "Mei"})
        query_graph(g, "cast_frame_state", {"cast_id": "cast_001_mei", "frame_id": "f_014"})
        query_graph(g, "frame", {"scene_id": "scene_02"})
    """
    registry_map = {
        "cast": graph.cast,
        "location": graph.locations,
        "prop": graph.props,
        "scene": graph.scenes,
        "frame": graph.frames,
        "dialogue": graph.dialogue,
        "storyboard_grid": graph.storyboard_grids,
        "cast_frame_state": graph.cast_frame_states,
        "prop_frame_state": graph.prop_frame_states,
        "location_frame_state": graph.location_frame_states,
    }

    registry = registry_map.get(node_type)
    if registry is None:
        raise ValueError(f"Unknown node_type: {node_type}. Valid: {list(registry_map.keys())}")

    results = []
    for key, node in registry.items():
        node_dict = node.model_dump() if hasattr(node, "model_dump") else node
        if filters:
            match = all(
                node_dict.get(field) == value
                for field, value in filters.items()
            )
            if not match:
                continue
        results.append(node_dict)

    return results


def get_frame_cast_state_models(
    graph: NarrativeGraph,
    frame_id: str,
) -> list[CastFrameState]:
    """Return cast states for a frame from the canonical flat registry."""
    if graph.frames.get(frame_id) is None:
        raise KeyError(f"Frame {frame_id} not found in graph")
    suffix = f"@{frame_id}"
    return [
        state for key, state in graph.cast_frame_states.items()
        if key.endswith(suffix)
    ]


def get_frame_prop_state_models(
    graph: NarrativeGraph,
    frame_id: str,
) -> list[PropFrameState]:
    """Return prop states for a frame from the canonical flat registry."""
    if graph.frames.get(frame_id) is None:
        raise KeyError(f"Frame {frame_id} not found in graph")
    suffix = f"@{frame_id}"
    return [
        state for key, state in graph.prop_frame_states.items()
        if key.endswith(suffix)
    ]


def get_frame_location_state_model(
    graph: NarrativeGraph,
    frame_id: str,
    location_id: Optional[str] = None,
) -> Optional[LocationFrameState]:
    """Return the location state for a frame from the canonical flat registry."""
    frame = graph.frames.get(frame_id)
    if frame is None:
        raise KeyError(f"Frame {frame_id} not found in graph")
    resolved_location_id = location_id or frame.location_id
    if not resolved_location_id:
        return None
    return graph.location_frame_states.get(f"{resolved_location_id}@{frame_id}")


def get_frame_context(graph: NarrativeGraph, frame_id: str) -> dict:
    """Get the complete context packet for a frame — everything a
    prompt builder or downstream agent needs in a single read.

    Returns a dict with:
        frame: the FrameNode
        scene: the parent SceneNode
        cast: list of CastNode dicts for characters in this frame
        cast_states: list of CastFrameState snapshots for this frame
        location: the LocationNode for this frame
        location_state: LocationFrameState if the location has mutated, else None
        props: list of PropNode dicts for props in this frame
        prop_states: list of PropFrameState snapshots for this frame
        dialogue: list of DialogueNode dicts audible during this frame
        adjacent_frames: {previous: FrameNode or None, next: FrameNode or None}
        world: WorldContext
        visual: VisualDirection
    """
    frame = graph.frames.get(frame_id)
    if frame is None:
        raise KeyError(f"Frame {frame_id} not found in graph")

    # Scene
    scene = graph.scenes.get(frame.scene_id)

    # Cast nodes + frame states
    # Prefer frame-level cast_states; fall back to graph-level registry if empty
    frame_cast_states = get_frame_cast_state_models(graph, frame_id)
    cast_ids_in_frame = [cs.cast_id for cs in frame_cast_states]
    cast_nodes = [graph.cast[cid].model_dump() for cid in cast_ids_in_frame if cid in graph.cast]
    cast_states = [cs.model_dump() for cs in frame_cast_states]

    # Location + state
    loc_node = graph.locations.get(frame.location_id) if frame.location_id else None
    loc_state = get_frame_location_state_model(graph, frame_id)

    # Props + states
    frame_prop_states = get_frame_prop_state_models(graph, frame_id)
    all_prop_ids = list({ps.prop_id for ps in frame_prop_states})
    prop_nodes = [graph.props[pid].model_dump() for pid in all_prop_ids if pid in graph.props]
    prop_states = [ps.model_dump() for ps in frame_prop_states]

    # Dialogue for this frame — each frame gets its unique chunk of any
    # multi-frame dialogue.  No words are repeated across frames.
    dialogue_by_id: dict[str, dict] = {}
    # 1. Direct dialogue_ids on the frame (Morpheus-assigned)
    for did in (frame.dialogue_ids or []):
        dnode = graph.dialogue.get(did)
        if dnode:
            dialogue_by_id[did] = dnode.model_dump()
    # 2. Scan all dialogue whose temporal span covers this frame
    for did, dnode in graph.dialogue.items():
        if did not in dialogue_by_id:
            if dnode.primary_visual_frame == frame_id or \
               _frame_in_span(graph, frame_id, dnode.start_frame, dnode.end_frame):
                dialogue_by_id[did] = dnode.model_dump()

    # Split multi-frame dialogue so each frame gets a unique word chunk
    for did, ddict in dialogue_by_id.items():
        start_f = ddict.get("start_frame", "")
        end_f = ddict.get("end_frame", "")
        if start_f and end_f and start_f != end_f:
            span_frames = _get_span_frame_ids(graph, start_f, end_f)
            if len(span_frames) > 1 and frame_id in span_frames:
                frame_idx = span_frames.index(frame_id)
                for text_key in ("raw_line", "line"):
                    full_text = ddict.get(text_key, "")
                    if full_text:
                        ddict[text_key] = _split_text_chunk(
                            full_text, frame_idx, len(span_frames)
                        )

    # Sort by dialogue order
    dialogue_nodes = sorted(
        dialogue_by_id.values(),
        key=lambda d: d.get("order", 0),
    )

    # Adjacent frames
    prev_frame = graph.frames.get(frame.previous_frame_id) if frame.previous_frame_id else None
    next_frame = graph.frames.get(frame.next_frame_id) if frame.next_frame_id else None

    return {
        "frame": frame.model_dump(),
        "scene": scene.model_dump() if scene else None,
        "cast": cast_nodes,
        "cast_states": cast_states,
        "location": loc_node.model_dump() if loc_node else None,
        "location_state": loc_state.model_dump() if loc_state else None,
        "props": prop_nodes,
        "prop_states": prop_states,
        "dialogue": dialogue_nodes,
        "adjacent_frames": {
            "previous": prev_frame.model_dump() if prev_frame else None,
            "next": next_frame.model_dump() if next_frame else None,
        },
        "world": graph.world.model_dump(),
        "visual": graph.visual.model_dump(),
    }


def build_shot_packet(graph: NarrativeGraph, frame_id: str) -> ShotPacket:
    """Build the canonical deterministic shot packet for a frame.

    The packet consolidates neighboring beats, continuity deltas, invariants,
    blocking, background, shot intent, and native-audio context into one
    structured object for downstream prompt assembly.
    """
    ctx = get_frame_context(graph, frame_id)
    frame = graph.frames.get(frame_id)
    if frame is None:
        raise KeyError(f"Frame {frame_id} not found in graph")

    prev_frame = graph.frames.get(frame.previous_frame_id) if frame.previous_frame_id else None
    next_frame = graph.frames.get(frame.next_frame_id) if frame.next_frame_id else None
    cast_states = get_frame_cast_state_models(graph, frame_id)
    visible_cast_states = [
        state for state in cast_states
        if getattr(getattr(state, "frame_role", None), "value", getattr(state, "frame_role", None)) != "referenced"
    ]
    prop_states = get_frame_prop_state_models(graph, frame_id)
    location_state = get_frame_location_state_model(graph, frame_id)

    visible_cast_ids = [state.cast_id for state in visible_cast_states]
    visible_prop_ids = [state.prop_id for state in prop_states]

    continuity_deltas = _build_continuity_deltas(graph, frame, prev_frame, visible_cast_states, prop_states, location_state)
    cast_invariants = _build_cast_invariants(graph, visible_cast_states)
    prop_invariants = _build_prop_invariants(graph, prop_states)
    location_invariants = _build_location_invariants(graph, frame, ctx, location_state)
    blocking = _build_blocking_lines(graph, ctx, visible_cast_states)
    background = _build_background_lines(frame, ctx)
    audio = _build_audio_beat(ctx)

    intent = ShotIntent(
        shot=frame.composition.shot,
        angle=frame.composition.angle,
        movement=frame.composition.movement,
        focus=frame.composition.focus,
        dramatic_purpose=frame.directing.dramatic_purpose,
        beat_turn=frame.directing.beat_turn,
        pov_owner=frame.directing.pov_owner,
        viewer_knowledge_delta=frame.directing.viewer_knowledge_delta,
        power_dynamic=frame.directing.power_dynamic,
        tension_source=frame.directing.tension_source,
        camera_motivation=frame.directing.camera_motivation,
        movement_motivation=frame.directing.movement_motivation,
        movement_path=frame.directing.movement_path,
        reaction_target=frame.directing.reaction_target,
    )

    return ShotPacket(
        frame_id=frame.frame_id,
        scene_id=frame.scene_id,
        sequence_index=frame.sequence_index,
        location_id=frame.location_id,
        subject_count=len(visible_cast_states),
        visible_cast_ids=visible_cast_ids,
        visible_prop_ids=visible_prop_ids,
        previous_beat=_neighbor_beat(prev_frame),
        current_beat=frame.narrative_beat or frame.action_summary or frame.source_text,
        video_optimized_prompt_block=frame.video_optimized_prompt_block,
        next_beat=_neighbor_beat(next_frame),
        continuity_deltas=continuity_deltas,
        cast_invariants=cast_invariants,
        prop_invariants=prop_invariants,
        location_invariants=location_invariants,
        blocking=blocking,
        background=background,
        shot_intent=intent,
        audio=audio,
    )


def _neighbor_beat(frame: Optional[FrameNode]) -> Optional[ShotNeighborBeat]:
    if frame is None:
        return None
    return ShotNeighborBeat(
        frame_id=frame.frame_id,
        narrative_beat=frame.narrative_beat,
        action_summary=frame.action_summary,
        scene_id=frame.scene_id,
        is_dialogue=frame.is_dialogue,
    )


def _build_continuity_deltas(
    graph: NarrativeGraph,
    frame: FrameNode,
    prev_frame: Optional[FrameNode],
    cast_states: list[CastFrameState],
    prop_states: list[PropFrameState],
    location_state: Optional[LocationFrameState],
) -> list[str]:
    if prev_frame is None:
        return ["Sequence opener."]

    deltas: list[str] = []

    prev_cast = {
        state.cast_id: state
        for state in get_frame_cast_state_models(graph, prev_frame.frame_id)
        if getattr(getattr(state, "frame_role", None), "value", getattr(state, "frame_role", None)) != "referenced"
    }
    current_cast = {state.cast_id: state for state in cast_states}

    prev_ids = set(prev_cast)
    current_ids = set(current_cast)
    added = sorted(current_ids - prev_ids)
    removed = sorted(prev_ids - current_ids)
    if added:
        deltas.append("Cast entering frame: " + ", ".join(added))
    if removed:
        deltas.append("Cast leaving frame: " + ", ".join(removed))

    for cast_id in sorted(prev_ids & current_ids):
        prev_state = prev_cast[cast_id]
        curr_state = current_cast[cast_id]
        changed: list[str] = []
        for field_name in (
            "action",
            "posture",
            "emotion",
            "screen_position",
            "facing_direction",
            "looking_at",
            "injury",
            "active_state_tag",
        ):
            prev_value = getattr(prev_state, field_name, None)
            curr_value = getattr(curr_state, field_name, None)
            if prev_value != curr_value and curr_value not in (None, "", []):
                changed.append(f"{field_name}={curr_value}")
        if prev_state.props_held != curr_state.props_held and curr_state.props_held:
            changed.append("props_held=" + ",".join(curr_state.props_held))
        if changed:
            deltas.append(f"{cast_id}: " + "; ".join(changed))

    prev_props = {
        state.prop_id: state
        for state in get_frame_prop_state_models(graph, prev_frame.frame_id)
    }
    current_props = {state.prop_id: state for state in prop_states}
    for prop_id in sorted(set(prev_props) | set(current_props)):
        prev_state = prev_props.get(prop_id)
        curr_state = current_props.get(prop_id)
        if prev_state is None and curr_state is not None:
            deltas.append(f"{prop_id}: enters frame {curr_state.condition}")
            continue
        if prev_state is not None and curr_state is None:
            deltas.append(f"{prop_id}: leaves frame")
            continue
        if prev_state and curr_state:
            prop_changes: list[str] = []
            if prev_state.condition != curr_state.condition:
                prop_changes.append(f"condition={curr_state.condition}")
            if prev_state.holder_cast_id != curr_state.holder_cast_id and curr_state.holder_cast_id:
                prop_changes.append(f"holder={curr_state.holder_cast_id}")
            if prev_state.visibility != curr_state.visibility:
                prop_changes.append(f"visibility={curr_state.visibility}")
            if prop_changes:
                deltas.append(f"{prop_id}: " + "; ".join(prop_changes))

    prev_location_state = get_frame_location_state_model(graph, prev_frame.frame_id)
    if prev_frame.location_id != frame.location_id and frame.location_id:
        deltas.append(f"Location shifts to {frame.location_id}")
    if location_state and (
        prev_location_state is None
        or prev_location_state.condition_modifiers != location_state.condition_modifiers
        or prev_location_state.atmosphere_override != location_state.atmosphere_override
        or prev_location_state.lighting_override != location_state.lighting_override
    ):
        loc_bits: list[str] = []
        if location_state.condition_modifiers:
            loc_bits.append("condition=" + ", ".join(location_state.condition_modifiers))
        if location_state.atmosphere_override:
            loc_bits.append(f"atmosphere={location_state.atmosphere_override}")
        if location_state.lighting_override:
            loc_bits.append(f"lighting={location_state.lighting_override}")
        if loc_bits:
            deltas.append("Location state: " + "; ".join(loc_bits))

    if not deltas:
        deltas.append("Maintain prior frame continuity with no major state changes.")
    return deltas


def _build_cast_invariants(graph: NarrativeGraph, cast_states: list[CastFrameState]) -> list[str]:
    invariants: list[str] = []
    for state in cast_states:
        cast = graph.cast.get(state.cast_id)
        if cast is None:
            continue
        appearance = cast.identity.physical_description or cast.identity.wardrobe_description
        bits = [cast.name]
        if appearance:
            bits.append(appearance)
        if state.clothing_current:
            bits.append("wearing " + ", ".join(state.clothing_current[:3]))
        elif cast.identity.wardrobe_description:
            bits.append("wearing " + cast.identity.wardrobe_description)
        if state.active_state_tag and state.active_state_tag != "base":
            bits.append(f"state {state.active_state_tag}")
        if state.injury:
            bits.append(state.injury)
        invariants.append(" | ".join(bit for bit in bits if bit))
    return invariants


def _build_prop_invariants(graph: NarrativeGraph, prop_states: list[PropFrameState]) -> list[str]:
    invariants: list[str] = []
    for state in prop_states:
        prop = graph.props.get(state.prop_id)
        name = prop.name if prop else state.prop_id
        bits = [name, f"condition {state.condition}", f"visibility {state.visibility}"]
        if state.holder_cast_id:
            bits.append(f"held by {state.holder_cast_id}")
        if state.spatial_position:
            bits.append(state.spatial_position)
        invariants.append(" | ".join(bit for bit in bits if bit))
    return invariants


def _build_location_invariants(
    graph: NarrativeGraph,
    frame: FrameNode,
    ctx: dict,
    location_state: Optional[LocationFrameState],
) -> list[str]:
    invariants: list[str] = []
    location = graph.locations.get(frame.location_id) if frame.location_id else None
    if location is not None:
        invariants.append(location.name)
        if location.description:
            invariants.append(location.description)
        if location.atmosphere:
            invariants.append(location.atmosphere)

    bg = frame.background
    if bg.camera_facing:
        invariants.append(f"camera_facing {bg.camera_facing}")
    visible_description = bg.visible_description
    if not visible_description and bg.camera_facing and ctx.get("location"):
        directions = ctx["location"].get("directions", {})
        if isinstance(directions, dict):
            direction_view = directions.get(bg.camera_facing)
            if isinstance(direction_view, dict):
                visible_description = direction_view.get("description", "")
            elif isinstance(direction_view, str):
                visible_description = direction_view
    if visible_description:
        invariants.append(visible_description)

    lighting = frame.environment.lighting
    if lighting.direction or lighting.quality or lighting.color_temp:
        lighting_bits = [
            getattr(lighting.direction, "value", lighting.direction) or "",
            getattr(lighting.quality, "value", lighting.quality) or "",
            lighting.color_temp or "",
            lighting.motivated_source or "",
        ]
        invariants.append("lighting " + ", ".join(bit for bit in lighting_bits if bit))

    atmosphere = frame.environment.atmosphere
    atmosphere_bits = [
        atmosphere.weather or "",
        atmosphere.particles or "",
        atmosphere.ambient_motion or "",
        atmosphere.temperature_feel or "",
    ]
    if any(atmosphere_bits):
        invariants.append("atmosphere " + ", ".join(bit for bit in atmosphere_bits if bit))

    if location_state:
        if location_state.condition_modifiers:
            invariants.append("location modifiers " + ", ".join(location_state.condition_modifiers))
        if location_state.atmosphere_override:
            invariants.append("location atmosphere " + location_state.atmosphere_override)
        if location_state.lighting_override:
            invariants.append("location lighting " + location_state.lighting_override)

    return invariants


def _build_blocking_lines(graph: NarrativeGraph, ctx: dict, cast_states: list[CastFrameState]) -> list[str]:
    blocking: list[str] = []
    cast_lookup = {cast.cast_id: cast.name for cast in graph.cast.values()}
    for state in cast_states:
        bits = [cast_lookup.get(state.cast_id, state.cast_id)]
        if state.screen_position:
            bits.append(f"at {state.screen_position}")
        elif state.spatial_position:
            bits.append(f"at {state.spatial_position}")
        if state.facing_direction:
            bits.append(f"facing {state.facing_direction}")
        if state.action:
            bits.append(state.action)
        elif state.posture:
            posture = getattr(state.posture, "value", state.posture)
            bits.append(str(posture))
        if state.looking_at:
            bits.append(f"looking at {state.looking_at}")
        elif state.eye_direction:
            bits.append(f"eyes {state.eye_direction}")
        if state.props_held:
            bits.append("holding " + ", ".join(state.props_held[:3]))
        blocking.append(" | ".join(bit for bit in bits if bit))
    return blocking


def _build_background_lines(frame: FrameNode, ctx: dict) -> list[str]:
    lines: list[str] = []
    bg = frame.background
    if bg.visible_description:
        lines.append(bg.visible_description)
    if bg.background_action:
        lines.append(bg.background_action)
    if bg.background_sound:
        lines.append(f"sound cue {bg.background_sound}")
    if bg.background_music:
        lines.append(f"music cue {bg.background_music}")
    for layer in bg.depth_layers[:3]:
        lines.append(layer)

    env = frame.environment
    if env.foreground_objects:
        lines.append("foreground " + ", ".join(env.foreground_objects[:3]))
    if env.midground_detail:
        lines.append("midground " + env.midground_detail)
    if env.background_depth:
        lines.append("background depth " + env.background_depth)
    return lines


def _build_audio_beat(ctx: dict) -> ShotAudioBeat:
    dialogue_nodes = ctx.get("dialogue", [])
    turns: list[ShotAudioTurn] = []
    ambient_layers: list[str] = []
    background = ctx.get("frame", {}).get("background", {}) or {}
    atmosphere = (ctx.get("frame", {}).get("environment", {}) or {}).get("atmosphere", {}) or {}

    for dnode in dialogue_nodes:
        turns.append(ShotAudioTurn(
            dialogue_id=dnode.get("dialogue_id", ""),
            cast_id=dnode.get("cast_id", ""),
            speaker=dnode.get("speaker", ""),
            line=dnode.get("raw_line", ""),
            performance_direction=dnode.get("performance_direction", ""),
            env_intensity=dnode.get("env_intensity"),
            env_distance=dnode.get("env_distance"),
            env_medium=dnode.get("env_medium"),
            env_atmosphere=dnode.get("env_atmosphere", []) or [],
        ))
        for tag in dnode.get("env_atmosphere", []) or []:
            if tag not in ambient_layers:
                ambient_layers.append(tag)

    for value in (
        atmosphere.get("weather"),
        atmosphere.get("ambient_motion"),
        background.get("background_sound"),
    ):
        if value and value not in ambient_layers:
            ambient_layers.append(value)

    return ShotAudioBeat(
        dialogue_present=bool(turns),
        turns=turns,
        ambient_layers=ambient_layers,
        background_music=background.get("background_music"),
    )


def _frame_in_span(
    graph: NarrativeGraph,
    frame_id: str,
    start_frame: str,
    end_frame: str,
) -> bool:
    """Check if a frame falls within a temporal span (inclusive)."""
    if frame_id == start_frame or frame_id == end_frame:
        return True
    try:
        order = graph.frame_order
        fi = order.index(frame_id)
        si = order.index(start_frame)
        ei = order.index(end_frame)
        return si <= fi <= ei
    except ValueError:
        return False


def _get_span_frame_ids(
    graph: NarrativeGraph,
    start_frame: str,
    end_frame: str,
) -> list[str]:
    """Return the ordered list of frame IDs from start_frame to end_frame inclusive."""
    if start_frame == end_frame:
        return [start_frame]
    try:
        order = graph.frame_order
        si = order.index(start_frame)
        ei = order.index(end_frame)
        return order[si:ei + 1]
    except ValueError:
        return [start_frame]


def _split_text_chunk(text: str, chunk_index: int, total_chunks: int) -> str:
    """Split text into total_chunks roughly equal word groups and return chunk_index.

    Splits on sentence boundaries when possible so each frame gets coherent
    phrases rather than mid-sentence cuts.
    """
    import re as _re

    # Try sentence-level split first
    sentences = [s.strip() for s in _re.split(r'(?<=[.!?…])\s+', text) if s.strip()]
    if len(sentences) >= total_chunks:
        # Distribute sentences across chunks
        per_chunk = len(sentences) / total_chunks
        start = round(chunk_index * per_chunk)
        end = round((chunk_index + 1) * per_chunk)
        return " ".join(sentences[start:end])

    # Fall back to word-level split
    words = text.split()
    if not words:
        return ""
    per_chunk = len(words) / total_chunks
    start = round(chunk_index * per_chunk)
    end = round((chunk_index + 1) * per_chunk)
    # Ensure at least one word per chunk
    if start >= len(words):
        return ""
    end = max(end, start + 1)
    return " ".join(words[start:end])


# ═══════════════════════════════════════════════════════════════════════════════
# UPSERT OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════


def upsert_node(
    graph: NarrativeGraph,
    node_type: str,
    data: dict,
    provenance: dict,
) -> str:
    """Insert or update a node in the graph.

    If a node with the same ID already exists, it is updated (fields merged).
    If not, a new node is created.

    Returns the node's ID.

    Raises ValueError if provenance is missing source_prose_chunk.
    """
    prov = Provenance.model_validate(provenance)
    if not prov.source_prose_chunk.strip():
        raise ValueError(
            "REJECTED: upsert_node requires provenance.source_prose_chunk. "
            "Every graph mutation must be traceable to source text."
        )

    data["provenance"] = prov.model_dump()

    type_to_model = {
        "cast": (CastNode, graph.cast, "cast_id"),
        "location": (LocationNode, graph.locations, "location_id"),
        "prop": (PropNode, graph.props, "prop_id"),
        "scene": (SceneNode, graph.scenes, "scene_id"),
        "frame": (FrameNode, graph.frames, "frame_id"),
        "dialogue": (DialogueNode, graph.dialogue, "dialogue_id"),
        "storyboard_grid": (StoryboardGrid, graph.storyboard_grids, "grid_id"),
    }

    if node_type not in type_to_model:
        raise ValueError(f"Unknown node_type for upsert: {node_type}")

    model_cls, registry, id_field = type_to_model[node_type]
    node_id = data.get(id_field)
    if not node_id:
        raise ValueError(f"Missing required ID field: {id_field}")

    if node_id in registry:
        # Update: merge new data into existing node
        existing = registry[node_id]
        existing_dict = existing.model_dump()
        existing_dict.update(data)
        registry[node_id] = model_cls.model_validate(existing_dict)
    else:
        # Insert new node
        registry[node_id] = model_cls.model_validate(data)

    # Track frame ordering
    if node_type == "frame" and node_id not in graph.frame_order:
        graph.frame_order.append(node_id)
    elif node_type == "scene" and node_id not in graph.scene_order:
        graph.scene_order.append(node_id)
    elif node_type == "dialogue" and node_id not in graph.dialogue_order:
        graph.dialogue_order.append(node_id)

    return node_id


def upsert_frame_state(
    graph: NarrativeGraph,
    state_type: str,
    data: dict,
    provenance: dict,
) -> str:
    """Insert or update a per-frame state snapshot.

    state_type: "cast_frame_state", "prop_frame_state", "location_frame_state"

    Key format: "{entity_id}@{frame_id}"
    """
    prov = Provenance.model_validate(provenance)
    if not prov.source_prose_chunk.strip():
        raise ValueError("REJECTED: frame state upsert requires provenance.source_prose_chunk.")

    data["provenance"] = prov.model_dump()

    if state_type == "cast_frame_state":
        model_cls = CastFrameState
        registry = graph.cast_frame_states
        key = f"{data['cast_id']}@{data['frame_id']}"
    elif state_type == "prop_frame_state":
        model_cls = PropFrameState
        registry = graph.prop_frame_states
        key = f"{data['prop_id']}@{data['frame_id']}"
    elif state_type == "location_frame_state":
        model_cls = LocationFrameState
        registry = graph.location_frame_states
        key = f"{data['location_id']}@{data['frame_id']}"
    else:
        raise ValueError(f"Unknown state_type: {state_type}")

    if key in registry:
        existing_dict = registry[key].model_dump()
        existing_dict.update(data)
        registry[key] = model_cls.model_validate(existing_dict)
    else:
        registry[key] = model_cls.model_validate(data)

    return key


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════


def create_edge(
    graph: NarrativeGraph,
    source_id: str,
    target_id: str,
    edge_type: str,
    provenance: dict,
    weight: float = 1.0,
    start_frame: Optional[str] = None,
    end_frame: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> GraphEdge:
    """Create a new edge in the graph.

    For temporal edges (possession, state changes), use start_frame/end_frame.
    """
    prov = Provenance.model_validate(provenance)
    if not prov.source_prose_chunk.strip():
        raise ValueError("REJECTED: edge creation requires provenance.source_prose_chunk.")

    edge_id = canonical_edge_id(source_id, edge_type, target_id)
    for existing in graph.edges:
        if existing.edge_id == edge_id:
            existing.weight = weight
            existing.start_frame = start_frame
            existing.end_frame = end_frame
            existing.metadata = metadata or {}
            existing.provenance = prov
            return existing

    edge = GraphEdge(
        edge_id=edge_id,
        source_id=source_id,
        target_id=target_id,
        edge_type=EdgeType(edge_type),
        weight=weight,
        start_frame=start_frame,
        end_frame=end_frame,
        metadata=metadata or {},
        provenance=prov,
    )
    graph.edges.append(edge)
    return edge


def close_temporal_edge(
    graph: NarrativeGraph,
    source_id: str,
    target_id: str,
    edge_type: str,
    end_frame: str,
) -> bool:
    """Close an open temporal edge by setting its end_frame.

    Used when a possession ends, a state resolves, etc.
    Returns True if an edge was found and closed, False otherwise.
    """
    for edge in graph.edges:
        if (
            edge.source_id == source_id
            and edge.target_id == target_id
            and edge.edge_type == edge_type
            and edge.end_frame is None
        ):
            edge.end_frame = end_frame
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# CONTINUITY & CONFLICT DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


class ContinuityConflict:
    """A detected continuity error in the graph."""

    def __init__(
        self,
        conflict_type: str,
        node_id: str,
        conflicting_node_id: Optional[str],
        reason: str,
        frame_id: str,
        severity: str = "warning",  # "warning" | "error" | "critical"
    ):
        self.conflict_type = conflict_type
        self.node_id = node_id
        self.conflicting_node_id = conflicting_node_id
        self.reason = reason
        self.frame_id = frame_id
        self.severity = severity

    def to_dict(self) -> dict:
        return {
            "conflict_type": self.conflict_type,
            "node_id": self.node_id,
            "conflicting_node_id": self.conflicting_node_id,
            "reason": self.reason,
            "frame_id": self.frame_id,
            "severity": self.severity,
        }


def check_continuity(graph: NarrativeGraph, frame_id: str) -> list[ContinuityConflict]:
    """Run continuity checks against a frame and its predecessors.

    Checks:
    1. Prop possession: if frame says cast holds prop, does prev frame
       have the prop held by same cast or transferred?
    2. Cast presence: if cast is SUBJECT in this frame, were they in
       the scene at all?
    3. Dialogue speaker: is the dialogue speaker present in the frame?
    4. Bidirectional link consistency: previous_frame_id / next_frame_id
       must be reciprocal (A.next=B ↔ B.prev=A).
    5. Continuity chain validation: if continuity_chain is True, same
       scene_id and location_id as previous frame.
    6. Sequence index ordering: sequence_index must be monotonically
       increasing along the chain.
    7. FOLLOWS edge existence: a FOLLOWS edge must exist between
       linked frames.
    """
    conflicts: list[ContinuityConflict] = []
    frame = graph.frames.get(frame_id)
    if not frame:
        return conflicts

    scene = graph.scenes.get(frame.scene_id)
    prev_frame = graph.frames.get(frame.previous_frame_id) if frame.previous_frame_id else None
    frame_cast_states = get_frame_cast_state_models(graph, frame_id)
    prev_cast_states = (
        get_frame_cast_state_models(graph, prev_frame.frame_id)
        if prev_frame else []
    )

    # 1. Prop possession continuity
    for cs in frame_cast_states:
        for prop_id in cs.props_held:
            if prev_frame:
                # Check if anyone else was holding this prop in prev frame
                prev_holder = None
                for prev_cs in prev_cast_states:
                    if prop_id in prev_cs.props_held:
                        prev_holder = prev_cs.cast_id
                        break
                if prev_holder and prev_holder != cs.cast_id:
                    # Prop changed hands — check if there's a TRANSFERRED state
                    prop_state_key = f"{prop_id}@{frame_id}"
                    ps = graph.prop_frame_states.get(prop_state_key)
                    if not ps or ps.frame_role != "transferred":
                        conflicts.append(ContinuityConflict(
                            conflict_type="prop_possession",
                            node_id=f"{cs.cast_id}@{frame_id}",
                            conflicting_node_id=f"{prev_holder}@{frame.previous_frame_id}",
                            reason=(
                                f"{cs.cast_id} holds {prop_id} in {frame_id} but "
                                f"{prev_holder} held it in {frame.previous_frame_id} "
                                f"with no transfer marked"
                            ),
                            frame_id=frame_id,
                            severity="error",
                        ))

    # 2. Cast scene presence
    if scene:
        for cs in frame_cast_states:
            if cs.frame_role in ("subject", "object", "background"):
                if cs.cast_id not in scene.cast_present:
                    conflicts.append(ContinuityConflict(
                        conflict_type="cast_presence",
                        node_id=cs.cast_id,
                        conflicting_node_id=scene.scene_id,
                        reason=(
                            f"{cs.cast_id} is {cs.frame_role} in {frame_id} but "
                            f"not listed in scene {scene.scene_id} cast_present"
                        ),
                        frame_id=frame_id,
                        severity="error",
                    ))

    # 3. Dialogue speaker presence
    for did in frame.dialogue_ids:
        dnode = graph.dialogue.get(did)
        if dnode:
            cast_ids_in_frame = {cs.cast_id for cs in frame_cast_states
                                 if cs.frame_role != "referenced"}
            if dnode.cast_id not in cast_ids_in_frame:
                # Not necessarily an error — could be a J-cut (audio before visual)
                # Only flag if this is the primary_visual_frame
                if dnode.primary_visual_frame == frame_id:
                    conflicts.append(ContinuityConflict(
                        conflict_type="dialogue_speaker_absent",
                        node_id=did,
                        conflicting_node_id=frame_id,
                        reason=(
                            f"Dialogue {did} speaker {dnode.cast_id} is the primary "
                            f"visual target but not physically present in {frame_id}"
                        ),
                        frame_id=frame_id,
                        severity="critical",
                    ))

    # 4. Bidirectional frame link consistency
    #    If this frame points backward via previous_frame_id, the previous
    #    frame's next_frame_id must point back here.  And vice-versa.
    if frame.previous_frame_id:
        if prev_frame is None:
            conflicts.append(ContinuityConflict(
                conflict_type="broken_link",
                node_id=frame_id,
                conflicting_node_id=frame.previous_frame_id,
                reason=(
                    f"{frame_id} has previous_frame_id={frame.previous_frame_id} "
                    f"but that frame does not exist in the graph"
                ),
                frame_id=frame_id,
                severity="error",
            ))
        elif prev_frame.next_frame_id != frame_id:
            conflicts.append(ContinuityConflict(
                conflict_type="bidirectional_link_mismatch",
                node_id=frame_id,
                conflicting_node_id=frame.previous_frame_id,
                reason=(
                    f"{frame_id}.previous_frame_id={frame.previous_frame_id} but "
                    f"{frame.previous_frame_id}.next_frame_id="
                    f"{prev_frame.next_frame_id} (expected {frame_id})"
                ),
                frame_id=frame_id,
                severity="warning",
            ))

    if frame.next_frame_id:
        next_frame = graph.frames.get(frame.next_frame_id)
        if next_frame is None:
            conflicts.append(ContinuityConflict(
                conflict_type="broken_link",
                node_id=frame_id,
                conflicting_node_id=frame.next_frame_id,
                reason=(
                    f"{frame_id} has next_frame_id={frame.next_frame_id} "
                    f"but that frame does not exist in the graph"
                ),
                frame_id=frame_id,
                severity="error",
            ))
        elif next_frame.previous_frame_id != frame_id:
            conflicts.append(ContinuityConflict(
                conflict_type="bidirectional_link_mismatch",
                node_id=frame_id,
                conflicting_node_id=frame.next_frame_id,
                reason=(
                    f"{frame_id}.next_frame_id={frame.next_frame_id} but "
                    f"{frame.next_frame_id}.previous_frame_id="
                    f"{next_frame.previous_frame_id} (expected {frame_id})"
                ),
                frame_id=frame_id,
                severity="warning",
            ))

    # 5. Continuity chain validation
    #    If continuity_chain is True, the frame must share the same scene_id
    #    and location_id as its predecessor.
    if frame.continuity_chain and prev_frame:
        if frame.scene_id != prev_frame.scene_id:
            conflicts.append(ContinuityConflict(
                conflict_type="continuity_chain_scene_mismatch",
                node_id=frame_id,
                conflicting_node_id=frame.previous_frame_id,
                reason=(
                    f"{frame_id} has continuity_chain=True but scene_id="
                    f"{frame.scene_id} differs from previous frame "
                    f"{frame.previous_frame_id} scene_id={prev_frame.scene_id}"
                ),
                frame_id=frame_id,
                severity="error",
            ))
        if frame.location_id != prev_frame.location_id:
            conflicts.append(ContinuityConflict(
                conflict_type="continuity_chain_location_mismatch",
                node_id=frame_id,
                conflicting_node_id=frame.previous_frame_id,
                reason=(
                    f"{frame_id} has continuity_chain=True but location_id="
                    f"{frame.location_id} differs from previous frame "
                    f"{frame.previous_frame_id} location_id={prev_frame.location_id}"
                ),
                frame_id=frame_id,
                severity="error",
            ))
    elif frame.continuity_chain and not frame.previous_frame_id:
        conflicts.append(ContinuityConflict(
            conflict_type="continuity_chain_no_predecessor",
            node_id=frame_id,
            conflicting_node_id=None,
            reason=(
                f"{frame_id} has continuity_chain=True but no "
                f"previous_frame_id is set"
            ),
            frame_id=frame_id,
            severity="warning",
        ))

    # 6. Sequence index ordering
    #    If this frame has a predecessor, its sequence_index must be strictly
    #    greater than the predecessor's.
    if prev_frame:
        if frame.sequence_index <= prev_frame.sequence_index:
            conflicts.append(ContinuityConflict(
                conflict_type="sequence_index_non_monotonic",
                node_id=frame_id,
                conflicting_node_id=frame.previous_frame_id,
                reason=(
                    f"{frame_id} sequence_index={frame.sequence_index} is not "
                    f"greater than predecessor {frame.previous_frame_id} "
                    f"sequence_index={prev_frame.sequence_index}"
                ),
                frame_id=frame_id,
                severity="error",
            ))

    # 7. FOLLOWS edge existence
    #    If this frame has a previous_frame_id, there should be a FOLLOWS
    #    edge from the previous frame to this one.
    if frame.previous_frame_id:
        has_follows_edge = any(
            e.source_id == frame.previous_frame_id
            and e.target_id == frame_id
            and e.edge_type == EdgeType.FOLLOWS
            for e in graph.edges
        )
        if not has_follows_edge:
            conflicts.append(ContinuityConflict(
                conflict_type="missing_follows_edge",
                node_id=frame_id,
                conflicting_node_id=frame.previous_frame_id,
                reason=(
                    f"No FOLLOWS edge from {frame.previous_frame_id} → "
                    f"{frame_id} despite previous_frame_id link"
                ),
                frame_id=frame_id,
                severity="warning",
            ))

    # 8. Dialogue-to-frame assignment consistency
    #    a) dialogue_ids on the frame must match temporal span resolution
    #    b) DIALOGUE_SPANS edges must exist for each dialogue → frame link
    #    c) SPOKEN_BY edges must exist for each dialogue → cast link
    for did in (frame.dialogue_ids or []):
        dnode = graph.dialogue.get(did)
        if not dnode:
            conflicts.append(ContinuityConflict(
                conflict_type="dialogue_id_dangling",
                node_id=frame_id,
                conflicting_node_id=did,
                reason=(
                    f"{frame_id} references dialogue {did} in dialogue_ids "
                    f"but that dialogue node does not exist in the graph"
                ),
                frame_id=frame_id,
                severity="error",
            ))
            continue

        # Check temporal span includes this frame
        if not _frame_in_span(graph, frame_id, dnode.start_frame, dnode.end_frame):
            conflicts.append(ContinuityConflict(
                conflict_type="dialogue_span_mismatch",
                node_id=did,
                conflicting_node_id=frame_id,
                reason=(
                    f"Dialogue {did} listed in {frame_id}.dialogue_ids but "
                    f"frame is outside temporal span "
                    f"({dnode.start_frame}→{dnode.end_frame})"
                ),
                frame_id=frame_id,
                severity="warning",
            ))

        # Check DIALOGUE_SPANS edge exists
        has_spans_edge = any(
            e.source_id == did
            and e.target_id == frame_id
            and e.edge_type == EdgeType.DIALOGUE_SPANS
            for e in graph.edges
        )
        if not has_spans_edge:
            conflicts.append(ContinuityConflict(
                conflict_type="missing_dialogue_spans_edge",
                node_id=did,
                conflicting_node_id=frame_id,
                reason=(
                    f"No DIALOGUE_SPANS edge from {did} → {frame_id} "
                    f"despite dialogue_ids assignment"
                ),
                frame_id=frame_id,
                severity="warning",
            ))

        # Check SPOKEN_BY edge exists
        has_spoken_edge = any(
            e.source_id == did
            and e.target_id == dnode.cast_id
            and e.edge_type == EdgeType.SPOKEN_BY
            for e in graph.edges
        )
        if not has_spoken_edge:
            conflicts.append(ContinuityConflict(
                conflict_type="missing_spoken_by_edge",
                node_id=did,
                conflicting_node_id=dnode.cast_id,
                reason=(
                    f"No SPOKEN_BY edge from {did} → {dnode.cast_id}"
                ),
                frame_id=frame_id,
                severity="warning",
            ))

    # 9. is_dialogue flag consistency
    #    If frame has dialogue_ids, is_dialogue should be True.
    #    If frame has no dialogue_ids but is within a dialogue span, warn.
    if frame.dialogue_ids and not frame.is_dialogue:
        conflicts.append(ContinuityConflict(
            conflict_type="is_dialogue_flag_mismatch",
            node_id=frame_id,
            conflicting_node_id=None,
            reason=(
                f"{frame_id} has dialogue_ids={frame.dialogue_ids} "
                f"but is_dialogue=False"
            ),
            frame_id=frame_id,
            severity="warning",
        ))
    # Check reverse: frame is within a dialogue span but has no dialogue_ids
    if not frame.dialogue_ids:
        for did, dnode in graph.dialogue.items():
            if _frame_in_span(graph, frame_id, dnode.start_frame, dnode.end_frame):
                conflicts.append(ContinuityConflict(
                    conflict_type="dialogue_span_not_linked",
                    node_id=did,
                    conflicting_node_id=frame_id,
                    reason=(
                        f"Dialogue {did} span ({dnode.start_frame}→"
                        f"{dnode.end_frame}) covers {frame_id} but "
                        f"frame has no dialogue_ids set"
                    ),
                    frame_id=frame_id,
                    severity="warning",
                ))
                break  # One warning is enough per frame

    # 10. Reaction frame validation
    #     If this frame is listed as a reaction_frame for a dialogue node,
    #     the dialogue speaker should NOT be the camera subject here.
    for did, dnode in graph.dialogue.items():
        if frame_id in (dnode.reaction_frame_ids or []):
            for cs in frame_cast_states:
                if cs.cast_id == dnode.cast_id and cs.frame_role == "subject":
                    conflicts.append(ContinuityConflict(
                        conflict_type="reaction_frame_shows_speaker",
                        node_id=did,
                        conflicting_node_id=frame_id,
                        reason=(
                            f"{frame_id} is a reaction_frame for {did} but "
                            f"the speaker {dnode.cast_id} is the subject — "
                            f"reaction frames should show the listener"
                        ),
                        frame_id=frame_id,
                        severity="warning",
                    ))

    return conflicts


def check_dialogue_ordering(graph: NarrativeGraph) -> list[ContinuityConflict]:
    """Validate dialogue ordering is monotonically consistent with frame order.

    Checks:
    1. dialogue_order list has monotonically increasing 'order' field
    2. start_frame of each dialogue is at or after the start_frame of the previous
    3. Every dialogue's primary_visual_frame, start_frame, end_frame exist in graph
    """
    conflicts: list[ContinuityConflict] = []

    prev_order = -1
    prev_start_idx = -1

    for did in graph.dialogue_order:
        dnode = graph.dialogue.get(did)
        if not dnode:
            conflicts.append(ContinuityConflict(
                conflict_type="dialogue_order_dangling",
                node_id=did,
                conflicting_node_id=None,
                reason=f"Dialogue {did} in dialogue_order but not in graph.dialogue",
                frame_id="",
                severity="error",
            ))
            continue

        # 1. Order field monotonic
        if dnode.order <= prev_order:
            conflicts.append(ContinuityConflict(
                conflict_type="dialogue_order_non_monotonic",
                node_id=did,
                conflicting_node_id=None,
                reason=(
                    f"Dialogue {did} order={dnode.order} is not greater "
                    f"than previous order={prev_order}"
                ),
                frame_id=dnode.primary_visual_frame,
                severity="error",
            ))
        prev_order = dnode.order

        # 2. Temporal ordering vs frame_order
        try:
            start_idx = graph.frame_order.index(dnode.start_frame)
            if start_idx < prev_start_idx:
                conflicts.append(ContinuityConflict(
                    conflict_type="dialogue_temporal_regression",
                    node_id=did,
                    conflicting_node_id=None,
                    reason=(
                        f"Dialogue {did} start_frame={dnode.start_frame} "
                        f"appears earlier in frame_order than previous dialogue's start"
                    ),
                    frame_id=dnode.start_frame,
                    severity="warning",
                ))
            prev_start_idx = start_idx
        except ValueError:
            conflicts.append(ContinuityConflict(
                conflict_type="dialogue_frame_missing",
                node_id=did,
                conflicting_node_id=dnode.start_frame,
                reason=f"Dialogue {did} start_frame={dnode.start_frame} not in frame_order",
                frame_id=dnode.start_frame,
                severity="error",
            ))

        # 3. All referenced frames exist
        for label, fid in [
            ("start_frame", dnode.start_frame),
            ("end_frame", dnode.end_frame),
            ("primary_visual_frame", dnode.primary_visual_frame),
        ]:
            if fid not in graph.frames:
                conflicts.append(ContinuityConflict(
                    conflict_type="dialogue_frame_missing",
                    node_id=did,
                    conflicting_node_id=fid,
                    reason=f"Dialogue {did} {label}={fid} does not exist in graph.frames",
                    frame_id=fid,
                    severity="error",
                ))

        # end_frame must be at or after start_frame
        try:
            si = graph.frame_order.index(dnode.start_frame)
            ei = graph.frame_order.index(dnode.end_frame)
            if ei < si:
                conflicts.append(ContinuityConflict(
                    conflict_type="dialogue_span_inverted",
                    node_id=did,
                    conflicting_node_id=None,
                    reason=(
                        f"Dialogue {did} end_frame={dnode.end_frame} "
                        f"comes before start_frame={dnode.start_frame}"
                    ),
                    frame_id=dnode.start_frame,
                    severity="error",
                ))
        except ValueError:
            pass  # Already caught above

    return conflicts


# ═══════════════════════════════════════════════════════════════════════════════
# PROVENANCE TRACING & SURGICAL CORRECTION
# ═══════════════════════════════════════════════════════════════════════════════


def trace_provenance(graph: NarrativeGraph, node_id: str) -> Optional[dict]:
    """Trace the provenance of any node or state snapshot.

    Returns the Provenance dict including source_prose_chunk,
    generated_by agent, confidence, and timestamps.
    """
    # Check all registries
    for registry_name, registry in [
        ("cast", graph.cast),
        ("location", graph.locations),
        ("prop", graph.props),
        ("scene", graph.scenes),
        ("frame", graph.frames),
        ("dialogue", graph.dialogue),
        ("storyboard_grid", graph.storyboard_grids),
        ("cast_frame_state", graph.cast_frame_states),
        ("prop_frame_state", graph.prop_frame_states),
        ("location_frame_state", graph.location_frame_states),
    ]:
        if node_id in registry:
            node = registry[node_id]
            prov = node.provenance if hasattr(node, "provenance") else None
            if prov:
                return {
                    "registry": registry_name,
                    "node_id": node_id,
                    "provenance": prov.model_dump(),
                }

    # Check edges
    for edge in graph.edges:
        if edge.edge_id == node_id:
            return {
                "registry": "edge",
                "node_id": node_id,
                "provenance": edge.provenance.model_dump(),
            }

    return None


def prune_and_revert(
    graph: NarrativeGraph,
    node_id: str,
    cascade: bool = True,
) -> dict:
    """Remove a node/edge and optionally cascade-remove dependents.

    If cascade=True, also removes:
    - Edges that reference this node as source or target
    - Frame states that reference this node
    - Dialogue nodes that reference removed frames

    Returns a summary of what was removed.
    """
    removed = {"nodes": [], "edges": [], "states": []}

    # Try removing from each registry
    for registry_name, registry, id_field in [
        ("cast", graph.cast, "cast_id"),
        ("location", graph.locations, "location_id"),
        ("prop", graph.props, "prop_id"),
        ("scene", graph.scenes, "scene_id"),
        ("frame", graph.frames, "frame_id"),
        ("dialogue", graph.dialogue, "dialogue_id"),
    ]:
        if node_id in registry:
            del registry[node_id]
            removed["nodes"].append(f"{registry_name}:{node_id}")

            if cascade:
                # Remove edges referencing this node
                before = len(graph.edges)
                graph.edges = [
                    e for e in graph.edges
                    if e.source_id != node_id and e.target_id != node_id
                ]
                removed["edges"].extend(
                    [f"edge (cascade from {node_id})"] * (before - len(graph.edges))
                )

                # Remove frame states referencing this node
                for state_registry_name, state_registry in [
                    ("cast_frame_state", graph.cast_frame_states),
                    ("prop_frame_state", graph.prop_frame_states),
                    ("location_frame_state", graph.location_frame_states),
                ]:
                    keys_to_remove = [
                        k for k in state_registry
                        if k.startswith(f"{node_id}@") or k.endswith(f"@{node_id}")
                    ]
                    for k in keys_to_remove:
                        del state_registry[k]
                        removed["states"].append(f"{state_registry_name}:{k}")

            # Remove from ordered lists
            if node_id in graph.frame_order:
                graph.frame_order.remove(node_id)
            if node_id in graph.scene_order:
                graph.scene_order.remove(node_id)
            if node_id in graph.dialogue_order:
                graph.dialogue_order.remove(node_id)

            break

    # Also try removing state snapshots directly
    for state_registry_name, state_registry in [
        ("cast_frame_state", graph.cast_frame_states),
        ("prop_frame_state", graph.prop_frame_states),
        ("location_frame_state", graph.location_frame_states),
    ]:
        if node_id in state_registry:
            del state_registry[node_id]
            removed["states"].append(f"{state_registry_name}:{node_id}")

    # Try removing edges by edge_id
    before = len(graph.edges)
    graph.edges = [e for e in graph.edges if e.edge_id != node_id]
    if len(graph.edges) < before:
        removed["edges"].append(f"edge:{node_id}")

    return removed


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT PROPAGATION — Copy-and-mutate for absolute state snapshots
# ═══════════════════════════════════════════════════════════════════════════════


def propagate_cast_state(
    graph: NarrativeGraph,
    cast_id: str,
    from_frame_id: str,
    to_frame_id: str,
    mutations: Optional[dict] = None,
    provenance: Optional[dict] = None,
) -> CastFrameState:
    """Copy a cast member's state from one frame to the next, apply mutations.

    This is the core of the absolute-snapshot pattern:
    1. Read the snapshot at from_frame_id
    2. Copy all fields
    3. Apply mutations (only the fields that changed)
    4. Save as the snapshot at to_frame_id
    5. Record which fields were mutated in delta_fields

    If no previous state exists (first frame), creates a fresh state
    with mutations applied.
    """
    prev_key = f"{cast_id}@{from_frame_id}"
    new_key = f"{cast_id}@{to_frame_id}"

    prev_state = graph.cast_frame_states.get(prev_key)
    if prev_state:
        new_data = prev_state.model_dump()
    else:
        new_data = CastFrameState(cast_id=cast_id, frame_id=to_frame_id).model_dump()

    # Update frame_id
    new_data["frame_id"] = to_frame_id

    # Apply mutations and track deltas
    delta_fields = []
    if mutations:
        for field, value in mutations.items():
            if field in ("cast_id", "frame_id", "delta_fields", "provenance"):
                continue
            if new_data.get(field) != value:
                delta_fields.append(field)
            new_data[field] = value
    new_data["delta_fields"] = delta_fields
    provenance = provenance or {}
    upsert_frame_state(graph, "cast_frame_state", new_data, provenance)
    return graph.cast_frame_states[new_key]


def propagate_prop_state(
    graph: NarrativeGraph,
    prop_id: str,
    from_frame_id: str,
    to_frame_id: str,
    mutations: Optional[dict] = None,
    provenance: Optional[dict] = None,
) -> PropFrameState:
    """Copy a prop's state from one frame to the next, apply mutations."""
    prev_key = f"{prop_id}@{from_frame_id}"
    new_key = f"{prop_id}@{to_frame_id}"

    prev_state = graph.prop_frame_states.get(prev_key)
    if prev_state:
        new_data = prev_state.model_dump()
    else:
        new_data = PropFrameState(prop_id=prop_id, frame_id=to_frame_id).model_dump()

    new_data["frame_id"] = to_frame_id
    delta_fields = []
    if mutations:
        for field, value in mutations.items():
            if field in ("prop_id", "frame_id", "delta_fields", "provenance"):
                continue
            if new_data.get(field) != value:
                delta_fields.append(field)
            new_data[field] = value
    new_data["delta_fields"] = delta_fields
    provenance = provenance or {}
    upsert_frame_state(graph, "prop_frame_state", new_data, provenance)
    return graph.prop_frame_states[new_key]


def propagate_location_state(
    graph: NarrativeGraph,
    location_id: str,
    from_frame_id: str,
    to_frame_id: str,
    mutations: Optional[dict] = None,
    provenance: Optional[dict] = None,
) -> LocationFrameState:
    """Copy a location's state from one frame to the next, apply mutations."""
    prev_key = f"{location_id}@{from_frame_id}"
    new_key = f"{location_id}@{to_frame_id}"

    prev_state = graph.location_frame_states.get(prev_key)
    if prev_state:
        new_data = prev_state.model_dump()
    else:
        new_data = LocationFrameState(
            location_id=location_id, frame_id=to_frame_id
        ).model_dump()

    new_data["frame_id"] = to_frame_id
    delta_fields = []
    if mutations:
        for field, value in mutations.items():
            if field in ("location_id", "frame_id", "delta_fields", "provenance"):
                continue
            if new_data.get(field) != value:
                delta_fields.append(field)
            new_data[field] = value
    new_data["delta_fields"] = delta_fields
    provenance = provenance or {}
    upsert_frame_state(graph, "location_frame_state", new_data, provenance)
    return graph.location_frame_states[new_key]


# ═══════════════════════════════════════════════════════════════════════════════
# STORYBOARD GRID BUILDING
# ═══════════════════════════════════════════════════════════════════════════════

MAX_GRID_SIZE = 6


def is_large_shift(graph: NarrativeGraph, prev_frame_id: str, curr_frame_id: str) -> bool:
    """Detect a major visual shift between two consecutive frames."""
    prev = graph.frames.get(prev_frame_id)
    curr = graph.frames.get(curr_frame_id)
    if not prev or not curr:
        return False

    if prev.scene_id != curr.scene_id:
        return True

    if prev.location_id != curr.location_id:
        return True

    if prev.time_of_day and curr.time_of_day and prev.time_of_day != curr.time_of_day:
        return True

    if _camera_signature(prev) != _camera_signature(curr):
        return True

    if _background_signature(prev) != _background_signature(curr):
        return True

    return False


def _grid_layout(n: int) -> tuple[int, int]:
    """Return (rows, cols) for a grid holding n frames.

    Guidance grids are intentionally small so they remain a continuity aid
    instead of becoming the final frame source.
    """
    if n <= 1:
        return 1, 1
    if n <= 2:
        return 1, 2
    if n <= 4:
        return 2, 2
    return 2, 3


def _visible_cast_signature(graph: NarrativeGraph, frame_id: str) -> tuple[str, ...]:
    return tuple(sorted(
        state.cast_id for state in get_frame_cast_state_models(graph, frame_id)
        if getattr(getattr(state, "frame_role", None), "value", getattr(state, "frame_role", None)) != "referenced"
    ))


def _prop_signature(graph: NarrativeGraph, frame_id: str) -> tuple[str, ...]:
    states = get_frame_prop_state_models(graph, frame_id)
    return tuple(sorted(
        f"{state.prop_id}:{state.condition}:{state.holder_cast_id or ''}:{state.visibility}"
        for state in states
    ))


def _dialogue_signature(frame: FrameNode) -> tuple[str, ...]:
    return tuple(frame.dialogue_ids or [])


def _camera_signature(frame: FrameNode) -> tuple[str, ...]:
    comp = frame.composition
    bg = frame.background
    return (
        frame.cinematic_tag.tag or "",
        comp.shot or "",
        comp.angle or "",
        comp.movement or "",
        comp.focus or "",
        bg.camera_facing or "",
    )


def _background_signature(frame: FrameNode) -> tuple[str, ...]:
    bg = frame.background
    env = frame.environment
    atmo = env.atmosphere
    return (
        bg.visible_description or "",
        bg.background_action or "",
        bg.background_sound or "",
        bg.background_music or "",
        env.background_depth or "",
        atmo.weather or "",
        atmo.ambient_motion or "",
        atmo.particles or "",
    )


def _storyboard_break_reason(
    graph: NarrativeGraph,
    prev_frame: FrameNode,
    curr_frame: FrameNode,
) -> Optional[str]:
    if prev_frame.scene_id != curr_frame.scene_id:
        return "scene_break"
    if _dialogue_signature(prev_frame) != _dialogue_signature(curr_frame):
        return "dialogue_turn_change"
    if _visible_cast_signature(graph, prev_frame.frame_id) != _visible_cast_signature(graph, curr_frame.frame_id):
        return "cast_set_change"
    if _prop_signature(graph, prev_frame.frame_id) != _prop_signature(graph, curr_frame.frame_id):
        return "prop_state_change"
    if _camera_signature(prev_frame) != _camera_signature(curr_frame):
        return "camera_shift"
    if _background_signature(prev_frame) != _background_signature(curr_frame):
        return "background_shift"
    if is_large_shift(graph, prev_frame.frame_id, curr_frame.frame_id):
        return "large_shift"
    return None


def build_storyboard_grids(graph: NarrativeGraph) -> list[StoryboardGrid]:
    """Partition frame_order into small sequential guidance grids.

    Grids are capped at 6 frames and break early when a new frame changes the
    dialogue beat, cast set, prop state, camera setup, or background state.
    Storyboards stay a continuity-planning layer, not a final render proxy.

    Clears existing grids and rebuilds from scratch.
    Returns the list of grids created.
    """
    graph.storyboard_grids.clear()
    grids: list[StoryboardGrid] = []

    if not graph.frame_order:
        return grids

    grid_idx = 0
    current_batch: list[str] = []

    def _flush_batch(reason: str) -> None:
        nonlocal grid_idx
        if not current_batch:
            return

        grid_id = f"grid_{grid_idx + 1:02d}"
        rows, cols = _grid_layout(len(current_batch))

        # Gather scene IDs, cast, props
        scene_ids: set[str] = set()
        all_cast: set[str] = set()
        all_props: set[str] = set()
        for fid in current_batch:
            frame = graph.frames.get(fid)
            if frame:
                scene_ids.add(frame.scene_id)
                for cs in get_frame_cast_state_models(graph, fid):
                    if cs.frame_role != "referenced":
                        all_cast.add(cs.cast_id)
                for ps in get_frame_prop_state_models(graph, fid):
                    all_props.add(ps.prop_id)

        cell_map = {i: fid for i, fid in enumerate(current_batch)}

        grid = StoryboardGrid(
            grid_id=grid_id,
            frame_ids=list(current_batch),
            frame_count=len(current_batch),
            rows=rows,
            cols=cols,
            scene_ids=sorted(scene_ids),
            break_reason=reason,
            cast_present=sorted(all_cast),
            props_present=sorted(all_props),
            cell_map=cell_map,
            provenance=Provenance(
                source_prose_chunk=(
                    f"Auto-built storyboard grid from sequential frames "
                    f"{current_batch[0]} through {current_batch[-1]} "
                    f"({reason})."
                ),
                generated_by="graph_build_grids",
                confidence=1.0,
            ),
        )
        graph.storyboard_grids[grid_id] = grid
        grids.append(grid)
        grid_idx += 1

    for fid in graph.frame_order:
        frame = graph.frames.get(fid)
        if not frame:
            continue

        break_reason = None
        if current_batch:
            prev_frame = graph.frames.get(current_batch[-1])
            if prev_frame:
                break_reason = _storyboard_break_reason(graph, prev_frame, frame)

        if len(current_batch) >= MAX_GRID_SIZE:
            _flush_batch("full")
            current_batch = []
        elif break_reason:
            _flush_batch(break_reason)
            current_batch = []

        current_batch.append(fid)

    _flush_batch("end")

    # Wire previous/next pointers
    grid_ids = [g.grid_id for g in grids]
    for i, g in enumerate(grids):
        if i > 0:
            g.previous_grid_id = grid_ids[i - 1]
        if i < len(grids) - 1:
            g.next_grid_id = grid_ids[i + 1]

    graph.seeded_domains["storyboard_grids"] = True
    return grids


def get_frame_cell_image(graph: NarrativeGraph, frame_id: str) -> str | None:
    """Resolve the grid cell image path for a given frame.

    Finds which grid the frame belongs to and returns the path to the
    split cell image. Checks for frame-ID-named files first ({frame_id}.png),
    then falls back to legacy indexed naming (frame_NNN.png).
    """
    for grid in graph.storyboard_grids.values():
        if frame_id in grid.frame_ids:
            if not grid.cell_image_dir:
                return None
            # Return frame-ID-named cell path (new naming convention)
            return f"{grid.cell_image_dir}/{frame_id}.png"
    return None


def match_shots_in_grid(graph: NarrativeGraph, grid: StoryboardGrid) -> list[ShotMatchGroup]:
    """Group frames in a grid by shared shot setup for visual consistency.

    Groups by (cinematic_tag.tag, visible_cast_set, composition.shot, composition.angle).
    Buckets with 2+ frames become ShotMatchGroups.
    """
    from collections import defaultdict

    buckets: dict[str, list[str]] = defaultdict(list)

    for fid in grid.frame_ids:
        frame = graph.frames.get(fid)
        if not frame:
            continue

        ct_tag = frame.cinematic_tag.tag or ""
        cast_states = get_frame_cast_state_models(graph, fid)
        visible_cast = tuple(sorted(
            cs.cast_id for cs in cast_states if cs.frame_role != "referenced"
        ))
        shot = frame.composition.shot if frame.composition else ""
        angle = frame.composition.angle if frame.composition else ""

        key = f"{ct_tag}_{shot}_{angle}_{','.join(visible_cast)}"
        buckets[key].append(fid)

    groups: list[ShotMatchGroup] = []
    group_idx = 0
    for basis, frame_ids in buckets.items():
        if len(frame_ids) >= 2:
            smg = ShotMatchGroup(
                group_id=f"smg_{grid.grid_id}_{group_idx:02d}",
                frame_ids=frame_ids,
                match_basis=basis,
                confidence=1.0,
            )
            groups.append(smg)
            group_idx += 1

    grid.shot_match_groups = groups
    grid.shot_matching_status = "matched"
    return groups

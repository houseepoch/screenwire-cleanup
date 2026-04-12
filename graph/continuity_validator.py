"""
ScreenWire — Continuity Validator
===================================

Deterministic graph integrity checks. No LLM calls.
Runs after Haiku enrichment to audit the complete NarrativeGraph.

Returns a list of issue dicts:
    {severity, frame_id, check_name, message}

Severity:
    ERROR — blocks pipeline
    WARN  — logged, pipeline continues

Checks:
    a. Spatial consistency across cuts (SPATIAL_JUMP)
    b. Cast state delta continuity (CLOTHING_STATE_JUMP, INJURY_UNEXPLAINED,
       PROP_PICKUP_MISSING, STATE_TAG_UNMOTIVATED)
    c. Dialogue coverage (DIALOGUE_FRAME_UNCOVERED, DIALOGUE_ORPHAN_VISUAL_FRAME,
       MISSING_SPOKEN_BY_EDGE, MISSING_DIALOGUE_SPANS_EDGE)
    d. Edge completeness (MISSING_BELONGS_TO_SCENE, MISSING_FOLLOWS_EDGE,
       MISSING_AT_LOCATION, MISSING_APPEARS_IN)
    e. Graph data completeness (INCOMPLETE_FRAME_DATA, INCOMPLETE_CAST_STATE)
    f. Staging plan compliance (STAGING_VIOLATION)

CLI:
    python3 graph/continuity_validator.py --project-dir ./projects/test [--strict] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# ─── Import resolution (package vs. direct execution) ─────────────────────────

if __package__:
    from .api import get_frame_cast_state_models
    from .reference_collector import pose_state_from_cast_state
    from .schema import (
        CastBible,
        NarrativeGraph,
        CastFrameState,
        CastFrameRole,
        EdgeType,
        FrameNode,
        GraphEdge,
        SceneNode,
        StagingBeat,
    )
else:
    # Direct CLI execution: add repo root to path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from graph.api import get_frame_cast_state_models
    from graph.reference_collector import pose_state_from_cast_state
    from graph.schema import (
        CastBible,
        NarrativeGraph,
        CastFrameState,
        CastFrameRole,
        EdgeType,
        FrameNode,
        GraphEdge,
        SceneNode,
        StagingBeat,
    )


# ─── Issue builder ─────────────────────────────────────────────────────────────

def _issue(severity: str, frame_id: str, check_name: str, message: str) -> dict:
    return {
        "severity": severity,
        "frame_id": frame_id,
        "check_name": check_name,
        "message": message,
    }


# ─── Edge index ────────────────────────────────────────────────────────────────

def _build_edge_index(graph: NarrativeGraph) -> dict[str, set[str]]:
    """Build bidirectional edge index for O(1) lookups.

    Forward key:  "{source_id}::{edge_type}" → set[target_id]
    Reverse key:  "{edge_type}::{target_id}" → set[source_id]
    """
    idx: dict[str, set[str]] = {}
    for edge in graph.edges:
        et = edge.edge_type.value if hasattr(edge.edge_type, "value") else str(edge.edge_type)
        fwd = f"{edge.source_id}::{et}"
        rev = f"{et}::{edge.target_id}"
        idx.setdefault(fwd, set()).add(edge.target_id)
        idx.setdefault(rev, set()).add(edge.source_id)
    return idx


def _fwd(idx: dict, source_id: str, edge_type: EdgeType) -> set[str]:
    """Forward lookup: which targets does source_id point to via edge_type?"""
    return idx.get(f"{source_id}::{edge_type.value}", set())


def _rev(idx: dict, edge_type: EdgeType, target_id: str) -> set[str]:
    """Reverse lookup: which sources point to target_id via edge_type?"""
    return idx.get(f"{edge_type.value}::{target_id}", set())


# ─── Graph traversal helpers ──────────────────────────────────────────────────

def _ordered_frames(graph: NarrativeGraph) -> list[FrameNode]:
    """Return FrameNodes in global sequence order."""
    if graph.frame_order:
        return [graph.frames[fid] for fid in graph.frame_order if fid in graph.frames]
    return sorted(graph.frames.values(), key=lambda f: f.sequence_index)


def _scene_frames_ordered(graph: NarrativeGraph, scene_id: str) -> list[FrameNode]:
    """Return FrameNodes for a scene in sequence order."""
    scene = graph.scenes.get(scene_id)
    if not scene:
        return []
    if graph.frame_order:
        scene_set = set(scene.frame_ids)
        return [graph.frames[fid] for fid in graph.frame_order
                if fid in scene_set and fid in graph.frames]
    return [graph.frames[fid] for fid in scene.frame_ids if fid in graph.frames]


def _get_cast_state(
    graph: NarrativeGraph, cast_id: str, frame_id: str
) -> Optional[CastFrameState]:
    """Retrieve CastFrameState for cast_id at frame_id from the canonical registry."""
    return graph.cast_frame_states.get(f"{cast_id}@{frame_id}")


def _cast_label(graph: NarrativeGraph, cast_id: str) -> str:
    node = graph.cast.get(cast_id)
    return node.name if node else cast_id


def _resolve_staging_beat(
    scene: SceneNode, frame_position_ratio: float
) -> Optional[StagingBeat]:
    """Return the StagingBeat (start/mid/end) for the given frame position.

    ratio 0.0–0.33 → start, 0.34–0.66 → mid, 0.67–1.0 → end.
    """
    if not scene.staging_plan:
        return None
    if frame_position_ratio < 0.34:
        return scene.staging_plan.get("start")
    elif frame_position_ratio < 0.67:
        return scene.staging_plan.get("mid")
    else:
        return scene.staging_plan.get("end")


# ─── Check a: Spatial consistency ─────────────────────────────────────────────

_MOVEMENT_POSTURES = {"walking", "running"}
_MOTION_DELTA_FIELDS = {"action", "spatial_position", "screen_position", "posture", "facing_direction"}
_MOTION_ACTION_WORDS = {"cross", "walk", "run", "move", "approach", "retreat", "step",
                        "lunge", "sprint", "stride", "dart", "rush"}


def _is_motivated_move(cs: CastFrameState) -> bool:
    """Return True if the cast state's action or posture implies purposeful movement."""
    if cs.posture and cs.posture.value in _MOVEMENT_POSTURES:
        return True
    action = (cs.action or "").lower()
    if any(word in action for word in _MOTION_ACTION_WORDS):
        return True
    if _MOTION_DELTA_FIELDS & set(cs.delta_fields):
        return True
    return False


def _check_spatial_consistency(graph: NarrativeGraph) -> list[dict]:
    """a. Flag SPATIAL_JUMP when screen_position changes without motivated action."""
    issues = []
    ordered = _ordered_frames(graph)

    for i in range(len(ordered) - 1):
        fn = ordered[i]
        fn1 = ordered[i + 1]

        if fn.scene_id != fn1.scene_id:
            continue  # scene cuts are exempt

        for cs_n in get_frame_cast_state_models(graph, fn.frame_id):
            if cs_n.frame_role == CastFrameRole.REFERENCED:
                continue
            cast_id = cs_n.cast_id
            cs_n1 = _get_cast_state(graph, cast_id, fn1.frame_id)
            if cs_n1 is None or cs_n1.frame_role == CastFrameRole.REFERENCED:
                continue

            pos_n = cs_n.screen_position
            pos_n1 = cs_n1.screen_position
            if not pos_n or not pos_n1 or pos_n == pos_n1:
                continue

            if not _is_motivated_move(cs_n1) and not _is_motivated_move(cs_n):
                issues.append(_issue(
                    "WARN",
                    fn1.frame_id,
                    "SPATIAL_JUMP",
                    f"{_cast_label(graph, cast_id)} jumps {pos_n} → {pos_n1} "
                    f"between {fn.frame_id} → {fn1.frame_id} without intervening action",
                ))

    return issues


# ─── Check b: Cast state delta continuity ─────────────────────────────────────

_DAMAGE_STATES = {"damaged", "torn_clothing", "bloodied", "wet"}
_DAMAGE_CAUSE_WORDS = {
    "hit", "stab", "wound", "bleed", "rain", "water", "tear", "rip",
    "splash", "soak", "burn", "attack", "fall", "crash", "fight", "strike",
    "shoot", "cut", "drench", "flood", "spill",
}
_INJURY_CAUSE_WORDS = {
    "hit", "stab", "wound", "bleed", "attack", "strike",
    "fall", "crash", "fight", "shoot", "cut", "burn", "slam", "throw",
}
_STATE_TAG_TRIGGERS: dict[str, set[str]] = {
    "wet": {"rain", "water", "soak", "splash", "swim", "river", "flood", "drench", "spill"},
    "sweating": {"sweat", "exert", "run", "fight", "heat", "struggle"},
    "wounded": {"wound", "bleed", "stab", "hit", "attack", "cut", "shot", "strike"},
    "bloodied": {"blood", "wound", "bleed", "stab", "cut", "kill"},
    "dirty": {"dirt", "mud", "crawl", "fall", "ground", "grapple", "roll"},
    "torn_clothing": {"tear", "rip", "fight", "fall", "grab", "struggle"},
    "exhausted": {"exhaust", "collapse", "struggle", "tire", "drag", "barely"},
    "disguised": {"disguise", "costume", "change", "wear", "put on", "dress"},
    "formal": {"formal", "dress", "suit", "ceremony", "gala"},
    "casual": {"casual", "change", "relax", "comfortable"},
}


def _action_text(cs: CastFrameState, frame: Optional[FrameNode] = None) -> str:
    parts = [cs.action or ""]
    if frame:
        parts.append(frame.narrative_beat or "")
        parts.append(frame.source_text or "")
    return " ".join(parts).lower()


def _check_cast_state_delta(graph: NarrativeGraph) -> list[dict]:
    """b. Clothing state, injury, prop pickup, and active_state_tag continuity."""
    issues = []
    ordered = _ordered_frames(graph)

    for i in range(len(ordered) - 1):
        fn = ordered[i]
        fn1 = ordered[i + 1]

        for cs_n1 in get_frame_cast_state_models(graph, fn1.frame_id):
            if cs_n1.frame_role == CastFrameRole.REFERENCED:
                continue
            cast_id = cs_n1.cast_id
            cs_n = _get_cast_state(graph, cast_id, fn.frame_id)
            label = _cast_label(graph, cast_id)

            if cs_n is None:
                continue  # first appearance — no prior state to compare

            # b1. clothing_state: base → damaged without cause
            prev_cloth = cs_n.clothing_state
            curr_cloth = cs_n1.clothing_state
            if prev_cloth == "base" and curr_cloth in _DAMAGE_STATES:
                combined = _action_text(cs_n, fn) + " " + _action_text(cs_n1, fn1)
                if not any(w in combined for w in _DAMAGE_CAUSE_WORDS):
                    issues.append(_issue(
                        "WARN",
                        fn1.frame_id,
                        "CLOTHING_STATE_JUMP",
                        f"{label} clothing state {prev_cloth} → {curr_cloth} "
                        f"({fn.frame_id} → {fn1.frame_id}) without apparent cause",
                    ))

            # b2. injury: appears without preceding action
            prev_injury = cs_n.injury
            curr_injury = cs_n1.injury
            if curr_injury and not prev_injury:
                combined = _action_text(cs_n, fn) + " " + _action_text(cs_n1, fn1)
                if not any(w in combined for w in _INJURY_CAUSE_WORDS):
                    issues.append(_issue(
                        "WARN",
                        fn1.frame_id,
                        "INJURY_UNEXPLAINED",
                        f"{label} has new injury '{curr_injury}' in {fn1.frame_id} "
                        f"without preceding action (from {fn.frame_id})",
                    ))

            # b3. props_held: new props must have been interacted with
            prev_held = set(cs_n.props_held)
            curr_held = set(cs_n1.props_held)
            new_props = curr_held - prev_held
            prior_interactions = set(cs_n.props_interacted) | set(cs_n1.props_interacted)
            for prop_id in new_props:
                if prop_id not in prior_interactions:
                    prop_node = graph.props.get(prop_id)
                    prop_label = prop_node.name if prop_node else prop_id
                    issues.append(_issue(
                        "WARN",
                        fn1.frame_id,
                        "PROP_PICKUP_MISSING",
                        f"{label} holds '{prop_label}' in {fn1.frame_id} "
                        f"with no prior pickup/interaction (from {fn.frame_id})",
                    ))

            # b4. active_state_tag: change must align with narrative beat
            prev_tag = cs_n.active_state_tag
            curr_tag = cs_n1.active_state_tag
            if prev_tag != curr_tag:
                triggers = _STATE_TAG_TRIGGERS.get(curr_tag, set())
                if triggers:
                    combined = _action_text(cs_n, fn) + " " + _action_text(cs_n1, fn1)
                    if not any(w in combined for w in triggers):
                        issues.append(_issue(
                            "WARN",
                            fn1.frame_id,
                            "STATE_TAG_UNMOTIVATED",
                            f"{label} active_state_tag {prev_tag} → {curr_tag} "
                            f"in {fn1.frame_id} without matching narrative beat",
                        ))

    return issues


# ─── Check c: Dialogue coverage ───────────────────────────────────────────────

def _check_dialogue_coverage(graph: NarrativeGraph, idx: dict) -> list[dict]:
    """c. Every dialogue frame has a DialogueNode; every DialogueNode has edges."""
    issues = []

    # c1. is_dialogue=True frames must have at least one DialogueNode referencing them
    for frame_id, frame in graph.frames.items():
        if not frame.is_dialogue:
            continue
        spans_sources = _rev(idx, EdgeType.DIALOGUE_SPANS, frame_id)
        if not spans_sources and not frame.dialogue_ids:
            issues.append(_issue(
                "ERROR",
                frame_id,
                "DIALOGUE_FRAME_UNCOVERED",
                f"Frame {frame_id} is_dialogue=True but no DialogueNode references it",
            ))

    # c2–c4. Per-DialogueNode checks
    for dlg_id, dlg in graph.dialogue.items():
        anchor = dlg.primary_visual_frame or dlg_id

        # c2. primary_visual_frame must exist
        if dlg.primary_visual_frame not in graph.frames:
            issues.append(_issue(
                "ERROR",
                dlg.primary_visual_frame or dlg_id,
                "DIALOGUE_ORPHAN_VISUAL_FRAME",
                f"DialogueNode {dlg_id} primary_visual_frame '{dlg.primary_visual_frame}' "
                f"not in graph.frames",
            ))

        # c3. SPOKEN_BY edge must exist
        spoken_targets = _fwd(idx, dlg_id, EdgeType.SPOKEN_BY)
        if not spoken_targets:
            issues.append(_issue(
                "ERROR",
                anchor,
                "MISSING_SPOKEN_BY_EDGE",
                f"DialogueNode {dlg_id} has no SPOKEN_BY edge (cast_id={dlg.cast_id})",
            ))

        # c4. DIALOGUE_SPANS edges must cover start_frame..end_frame range
        spans_targets = _fwd(idx, dlg_id, EdgeType.DIALOGUE_SPANS)
        start, end = dlg.start_frame, dlg.end_frame
        if graph.frame_order and start in graph.frame_order and end in graph.frame_order:
            s = graph.frame_order.index(start)
            e = graph.frame_order.index(end)
            expected = set(graph.frame_order[s : e + 1])
        else:
            expected = {start, end}
        missing_spans = expected - spans_targets
        if missing_spans:
            issues.append(_issue(
                "ERROR",
                start,
                "MISSING_DIALOGUE_SPANS_EDGE",
                f"DialogueNode {dlg_id} missing DIALOGUE_SPANS for: {sorted(missing_spans)}",
            ))

    return issues


# ─── Check d: Edge completeness ───────────────────────────────────────────────

def _check_edge_completeness(graph: NarrativeGraph, idx: dict) -> list[dict]:
    """d. Verify structural edges exist for every frame and visible cast."""
    issues = []

    first_frame = graph.frame_order[0] if graph.frame_order else None

    # Build set of frames that are FOLLOWS targets (have an incoming FOLLOWS)
    follows_targets: set[str] = set()
    for edge in graph.edges:
        if edge.edge_type == EdgeType.FOLLOWS:
            follows_targets.add(edge.target_id)

    for frame_id, frame in graph.frames.items():
        # BELONGS_TO_SCENE
        if not _fwd(idx, frame_id, EdgeType.BELONGS_TO_SCENE):
            issues.append(_issue(
                "ERROR",
                frame_id,
                "MISSING_BELONGS_TO_SCENE",
                f"Frame {frame_id} has no BELONGS_TO_SCENE edge",
            ))

        # FOLLOWS — every frame except the first must have an incoming FOLLOWS
        if frame_id != first_frame and frame_id not in follows_targets:
            issues.append(_issue(
                "ERROR",
                frame_id,
                "MISSING_FOLLOWS_EDGE",
                f"Frame {frame_id} has no incoming FOLLOWS edge",
            ))

        # AT_LOCATION
        if not _fwd(idx, frame_id, EdgeType.AT_LOCATION):
            issues.append(_issue(
                "ERROR",
                frame_id,
                "MISSING_AT_LOCATION",
                f"Frame {frame_id} has no AT_LOCATION edge",
            ))

        # APPEARS_IN for each physically-present cast member
        for cs in get_frame_cast_state_models(graph, frame_id):
            if cs.frame_role == CastFrameRole.REFERENCED:
                continue
            appears_in_frames = _fwd(idx, cs.cast_id, EdgeType.APPEARS_IN)
            if frame_id not in appears_in_frames:
                label = _cast_label(graph, cs.cast_id)
                issues.append(_issue(
                    "ERROR",
                    frame_id,
                    "MISSING_APPEARS_IN",
                    f"{label} ({cs.cast_id}) visible in {frame_id} "
                    f"but has no APPEARS_IN edge to this frame",
                ))

    return issues


# ─── Check e: Data completeness ──────────────────────────────────────────────

_REQUIRED_FRAME_FIELDS = [
    ("composition.shot",        lambda f: f.composition.shot),
    ("composition.angle",       lambda f: f.composition.angle),
    ("composition.movement",    lambda f: f.composition.movement),
    ("action_summary",          lambda f: f.action_summary),
    ("background.camera_facing", lambda f: f.background.camera_facing),
]

_REQUIRED_CAST_STATE_FIELDS = [
    ("screen_position",  lambda cs: cs.screen_position),
    ("looking_at",       lambda cs: cs.looking_at),
    ("emotion",          lambda cs: cs.emotion),
    ("posture",          lambda cs: cs.posture),
    ("facing_direction", lambda cs: cs.facing_direction),
]


def _check_data_completeness(graph: NarrativeGraph) -> list[dict]:
    """e. Verify all required fields are populated after Haiku enrichment."""
    issues = []

    for frame_id, frame in graph.frames.items():
        for field_name, getter in _REQUIRED_FRAME_FIELDS:
            if not getter(frame):
                issues.append(_issue(
                    "ERROR",
                    frame_id,
                    "INCOMPLETE_FRAME_DATA",
                    f"Frame {frame_id} missing required field: {field_name}",
                ))

        for cs in get_frame_cast_state_models(graph, frame_id):
            if cs.frame_role == CastFrameRole.REFERENCED:
                continue
            label = _cast_label(graph, cs.cast_id)
            for field_name, getter in _REQUIRED_CAST_STATE_FIELDS:
                if not getter(cs):
                    issues.append(_issue(
                        "ERROR",
                        frame_id,
                        "INCOMPLETE_CAST_STATE",
                        f"CastFrameState for {label} in {frame_id} "
                        f"missing required field: {field_name}",
                    ))

    return issues


# ─── Check f: Staging plan compliance ────────────────────────────────────────

def _check_staging_compliance(graph: NarrativeGraph) -> list[dict]:
    """f. Flag STAGING_VIOLATION when CastFrameState contradicts the staging anchor."""
    issues = []

    for scene_id, scene in graph.scenes.items():
        if not scene.staging_plan:
            continue

        scene_frames = _scene_frames_ordered(graph, scene_id)
        total = len(scene_frames)
        if total == 0:
            continue

        for pos_idx, frame in enumerate(scene_frames):
            ratio = pos_idx / total if total > 1 else 0.0
            beat = _resolve_staging_beat(scene, ratio)
            if beat is None:
                continue

            beat_name = "start" if ratio < 0.34 else ("mid" if ratio < 0.67 else "end")

            for cs in get_frame_cast_state_models(graph, frame.frame_id):
                if cs.frame_role == CastFrameRole.REFERENCED:
                    continue
                cast_id = cs.cast_id

                # Motivated movement — exempt from staging violation
                if _is_motivated_move(cs):
                    continue

                label = _cast_label(graph, cast_id)

                # screen_position vs staging anchor
                anchor_pos = beat.cast_positions.get(cast_id)
                if anchor_pos and cs.screen_position and cs.screen_position != anchor_pos:
                    issues.append(_issue(
                        "WARN",
                        frame.frame_id,
                        "STAGING_VIOLATION",
                        f"{label} at '{cs.screen_position}' but staging {beat_name} anchor "
                        f"expects '{anchor_pos}' in {frame.frame_id}",
                    ))

                # looking_at vs staging anchor
                anchor_looking = beat.cast_looking_at.get(cast_id)
                if anchor_looking and cs.looking_at and cs.looking_at != anchor_looking:
                    issues.append(_issue(
                        "WARN",
                        frame.frame_id,
                        "STAGING_VIOLATION",
                        f"{label} looking_at '{cs.looking_at}' but staging {beat_name} anchor "
                        f"expects '{anchor_looking}' in {frame.frame_id}",
                    ))

    return issues


def _check_cast_bible_alignment(
    graph: NarrativeGraph,
    cast_bible: Optional[CastBible],
) -> list[dict]:
    """g. Ensure the persisted cast bible matches the canonical graph states."""
    ordered = _ordered_frames(graph)
    if not ordered:
        return []

    if cast_bible is None:
        return [
            _issue(
                "WARN",
                ordered[0].frame_id,
                "CAST_BIBLE_MISSING",
                "No cast bible snapshot exists for the current graph state.",
            )
        ]

    issues: list[dict] = []
    for frame in ordered:
        for cast_state in get_frame_cast_state_models(graph, frame.frame_id):
            if cast_state.frame_role == CastFrameRole.REFERENCED:
                continue

            sheet = cast_bible.characters.get(cast_state.cast_id)
            label = _cast_label(graph, cast_state.cast_id)
            if sheet is None:
                issues.append(
                    _issue(
                        "WARN",
                        frame.frame_id,
                        "CAST_BIBLE_MISSING_CHARACTER",
                        f"{label} has no cast bible sheet for {frame.frame_id}.",
                    )
                )
                continue

            locked_pose = sheet.pose_for_frame(frame.frame_id)
            if locked_pose is None:
                issues.append(
                    _issue(
                        "WARN",
                        frame.frame_id,
                        "CAST_BIBLE_MISSING_POSE",
                        f"{label} has no pose snapshot for {frame.frame_id}.",
                    )
                )
                continue

            expected_pose = pose_state_from_cast_state(
                cast_state,
                frame_id=frame.frame_id,
                sequence_index=frame.sequence_index,
                frame_text=" ".join(
                    part
                    for part in (frame.narrative_beat, frame.source_text)
                    if part
                ),
            )
            if (
                locked_pose.pose != expected_pose.pose
                or locked_pose.modifiers != expected_pose.modifiers
            ):
                issues.append(
                    _issue(
                        "WARN",
                        frame.frame_id,
                        "CAST_BIBLE_POSE_MISMATCH",
                        f"{label} pose lock drifted: cast bible has "
                        f"'{locked_pose.pose}' but graph state resolves to "
                        f"'{expected_pose.pose}'.",
                    )
                )

    return issues


# ─── Auto-fix helpers ─────────────────────────────────────────────────────────

def _make_edge(source_id: str, edge_type: EdgeType, target_id: str) -> GraphEdge:
    """Create a minimal auto-generated GraphEdge."""
    return GraphEdge(
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        evidence=["auto_fixed_by_validator"],
    )


def _existing_edge(graph: NarrativeGraph, source_id: str, edge_type: EdgeType, target_id: str) -> bool:
    """Return True if this exact edge already exists on the graph."""
    for e in graph.edges:
        if e.source_id == source_id and e.edge_type == edge_type and e.target_id == target_id:
            return True
    return False


def _infer_scene_for_orphan(graph: NarrativeGraph, frame_id: str) -> Optional[str]:
    """Try to infer a scene_id for an orphan frame from adjacent frames in frame_order."""
    if not graph.frame_order or frame_id not in graph.frame_order:
        # Fall back: look for any scene that lists this frame
        for scene_id, scene in graph.scenes.items():
            if frame_id in scene.frame_ids:
                return scene_id
        return None

    idx = graph.frame_order.index(frame_id)
    # Check neighbours (prev, then next)
    for neighbour_idx in (idx - 1, idx + 1):
        if 0 <= neighbour_idx < len(graph.frame_order):
            neighbour_id = graph.frame_order[neighbour_idx]
            neighbour = graph.frames.get(neighbour_id)
            if neighbour and neighbour.scene_id:
                return neighbour.scene_id
    return None


def _apply_fixes(graph: NarrativeGraph, issues: list[dict]) -> None:
    """Mutate graph in-place to fix deterministic issues. Annotates each issue dict."""
    ordered = _ordered_frames(graph)
    frame_pos: dict[str, int] = {f.frame_id: i for i, f in enumerate(ordered)}
    first_frame_id = graph.frame_order[0] if graph.frame_order else None

    for issue in issues:
        check = issue["check_name"]
        frame_id = issue["frame_id"]

        # ── Deterministic edge fixes ───────────────────────────────────────

        if check == "MISSING_FOLLOWS_EDGE":
            if frame_id == first_frame_id:
                issue["auto_fixed"] = True  # first frame never needs FOLLOWS
                continue
            pos = frame_pos.get(frame_id)
            if pos is not None and pos > 0:
                prev_id = ordered[pos - 1].frame_id
                if not _existing_edge(graph, prev_id, EdgeType.FOLLOWS, frame_id):
                    graph.edges.append(_make_edge(prev_id, EdgeType.FOLLOWS, frame_id))
                issue["auto_fixed"] = True
            else:
                issue["needs_re_enrichment"] = True

        elif check == "MISSING_BELONGS_TO_SCENE":
            frame = graph.frames.get(frame_id)
            scene_id = (frame.scene_id if frame else None) or _infer_scene_for_orphan(graph, frame_id)
            if scene_id and scene_id in graph.scenes:
                if not _existing_edge(graph, frame_id, EdgeType.BELONGS_TO_SCENE, scene_id):
                    graph.edges.append(_make_edge(frame_id, EdgeType.BELONGS_TO_SCENE, scene_id))
                if frame and not frame.scene_id:
                    frame.scene_id = scene_id
                scene = graph.scenes[scene_id]
                if frame_id not in scene.frame_ids:
                    scene.frame_ids.append(frame_id)
                issue["auto_fixed"] = True
            else:
                issue["needs_re_enrichment"] = True

        elif check == "MISSING_AT_LOCATION":
            frame = graph.frames.get(frame_id)
            scene_id = frame.scene_id if frame else None
            location_id = None
            if scene_id and scene_id in graph.scenes:
                location_id = graph.scenes[scene_id].location_id
            if location_id and location_id in graph.locations:
                if not _existing_edge(graph, frame_id, EdgeType.AT_LOCATION, location_id):
                    graph.edges.append(_make_edge(frame_id, EdgeType.AT_LOCATION, location_id))
                issue["auto_fixed"] = True
            else:
                issue["needs_re_enrichment"] = True

        elif check == "MISSING_APPEARS_IN":
            # Extract cast_id from message: "{label} ({cast_id}) visible in ..."
            cast_id = None
            msg = issue.get("message", "")
            import re as _re
            m = _re.search(r"\((\S+)\) visible in", msg)
            if m:
                cast_id = m.group(1)
            if cast_id:
                if not _existing_edge(graph, cast_id, EdgeType.APPEARS_IN, frame_id):
                    graph.edges.append(_make_edge(cast_id, EdgeType.APPEARS_IN, frame_id))
                issue["auto_fixed"] = True
            else:
                issue["needs_re_enrichment"] = True

        elif check == "MISSING_SPOKEN_BY_EDGE":
            # Extract dialogue_id from message: "DialogueNode {dlg_id} has no SPOKEN_BY"
            msg = issue.get("message", "")
            import re as _re
            m = _re.search(r"DialogueNode (\S+) has no SPOKEN_BY.*cast_id=(\S+)\)", msg)
            if m:
                dlg_id, cast_id = m.group(1), m.group(2)
                dlg = graph.dialogue.get(dlg_id)
                if dlg and cast_id:
                    if not _existing_edge(graph, dlg_id, EdgeType.SPOKEN_BY, cast_id):
                        graph.edges.append(_make_edge(dlg_id, EdgeType.SPOKEN_BY, cast_id))
                    issue["auto_fixed"] = True
                    continue
            issue["needs_re_enrichment"] = True

        elif check == "MISSING_DIALOGUE_SPANS_EDGE":
            # Extract dlg_id and missing frame list from message
            msg = issue.get("message", "")
            import re as _re
            m_dlg = _re.search(r"DialogueNode (\S+) missing", msg)
            m_frames = _re.search(r"missing DIALOGUE_SPANS for: \[(.+)\]", msg)
            if m_dlg and m_frames:
                dlg_id = m_dlg.group(1)
                raw_ids = m_frames.group(1).replace("'", "").replace('"', "")
                missing_fids = [fid.strip() for fid in raw_ids.split(",")]
                for fid in missing_fids:
                    if fid and not _existing_edge(graph, dlg_id, EdgeType.DIALOGUE_SPANS, fid):
                        graph.edges.append(_make_edge(dlg_id, EdgeType.DIALOGUE_SPANS, fid))
                issue["auto_fixed"] = True
            else:
                issue["needs_re_enrichment"] = True

        # ── Haiku re-enrichment required ──────────────────────────────────

        elif check in ("INCOMPLETE_FRAME_DATA", "INCOMPLETE_CAST_STATE"):
            msg = issue.get("message", "")
            issue["needs_re_enrichment"] = True
            # Extract what's missing for correction context
            import re as _re
            m = _re.search(r"missing required field: (.+)$", msg)
            if m:
                issue["what"] = f"missing required field: {m.group(1)}"
            else:
                issue["what"] = msg

        elif check in ("SPATIAL_JUMP", "STAGING_VIOLATION"):
            issue["needs_re_enrichment"] = True
            issue["what"] = issue.get("message", check)


# ─── Public API ───────────────────────────────────────────────────────────────

def validate_continuity(
    graph: NarrativeGraph,
    fix: bool = False,
    project_dir: Optional[Path] = None,
) -> list[dict]:
    """Run all continuity checks on a fully-enriched NarrativeGraph.

    Args:
        graph:       Complete NarrativeGraph after Haiku enrichment.
        fix:         When True, attempt deterministic auto-fixes for missing
                     edges and orphan frames directly on the graph. Issues that
                     cannot be fixed deterministically are flagged with
                     needs_re_enrichment=True. The graph is saved after fixes.
        project_dir: Required when fix=True — path to the project directory
                     so the fixed graph can be persisted.

    Returns:
        List of issue dicts: {severity, frame_id, check_name, message}
        Auto-fixed issues also carry:  auto_fixed=True
        Un-fixable issues also carry:  needs_re_enrichment=True, what=<description>
        Ordered by check category, then frame sequence order.
    """
    idx = _build_edge_index(graph)
    issues: list[dict] = []
    cast_bible = None

    if project_dir is not None:
        if __package__:
            from .store import GraphStore
        else:
            from graph.store import GraphStore
        cast_bible = GraphStore(project_dir).load_latest_cast_bible(
            sequence_id=getattr(graph.project, "project_id", "") or "",
        )

    issues.extend(_check_spatial_consistency(graph))       # a
    issues.extend(_check_cast_state_delta(graph))           # b
    issues.extend(_check_dialogue_coverage(graph, idx))     # c
    issues.extend(_check_edge_completeness(graph, idx))     # d
    issues.extend(_check_data_completeness(graph))          # e
    issues.extend(_check_staging_compliance(graph))         # f
    if project_dir is not None:
        issues.extend(_check_cast_bible_alignment(graph, cast_bible))  # g

    if fix:
        if issues:
            _apply_fixes(graph, issues)
        if project_dir is not None:
            if __package__:
                from .store import GraphStore
                from .reference_collector import ReferenceImageCollector
            else:
                from graph.store import GraphStore
                from graph.reference_collector import ReferenceImageCollector
            store = GraphStore(project_dir)
            store.save(graph)
            ReferenceImageCollector(graph, Path(project_dir)).sync_cast_bible(
                store=store,
                sequence_id=getattr(graph.project, "project_id", "") or "",
            )

    return issues


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate narrative graph continuity and data integrity.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        metavar="DIR",
        help="Project directory containing graph/narrative_graph.json",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any WARN issues exist (default: only ERRORs cause non-zero exit)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output issues as a JSON array",
    )
    args = parser.parse_args()

    # Load graph
    if __package__:
        from .store import GraphStore
    else:
        from graph.store import GraphStore

    project_dir = Path(args.project_dir)
    store = GraphStore(project_dir)
    try:
        graph = store.load()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    issues = validate_continuity(graph)
    errors = [i for i in issues if i["severity"] == "ERROR"]
    warns  = [i for i in issues if i["severity"] == "WARN"]

    if args.json_output:
        print(json.dumps(issues, indent=2))
    else:
        if not issues:
            print("✓ No continuity issues found.")
        else:
            col_w = 36
            for issue in issues:
                sev   = issue["severity"]
                fid   = issue["frame_id"]
                check = issue["check_name"]
                msg   = issue["message"]
                tag   = "ERROR" if sev == "ERROR" else " WARN"
                print(f"[{tag}] {fid:>8} | {check:<{col_w}} | {msg}")

        print(f"\nSummary: {len(errors)} error(s), {len(warns)} warning(s) — {len(issues)} total")

    if errors:
        sys.exit(1)
    if args.strict and warns:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

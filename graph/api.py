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
    GraphEdge, EdgeType, Provenance,
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
    frame_cast_states = frame.cast_states
    if not frame_cast_states:
        # Hydrate from graph.cast_frame_states registry (keyed "cast_id@frame_id")
        frame_cast_states = [
            cfs for key, cfs in graph.cast_frame_states.items()
            if key.endswith(f"@{frame_id}")
        ]
    cast_ids_in_frame = [cs.cast_id for cs in frame_cast_states]
    cast_nodes = [graph.cast[cid].model_dump() for cid in cast_ids_in_frame if cid in graph.cast]
    cast_states = [cs.model_dump() for cs in frame_cast_states]

    # Location + state
    loc_node = graph.locations.get(frame.location_id) if frame.location_id else None
    loc_state_key = f"{frame.location_id}@{frame_id}" if frame.location_id else None
    loc_state = graph.location_frame_states.get(loc_state_key) if loc_state_key else None

    # Props + states
    prop_ids = list(frame.prop_states) if frame.prop_states else []
    # Also check prop_roles on frame for IDs
    if hasattr(frame, 'prop_states') and frame.prop_states:
        prop_ids_from_states = [ps.prop_id for ps in frame.prop_states]
    else:
        prop_ids_from_states = []
    all_prop_ids = list(set(prop_ids_from_states))
    prop_nodes = [graph.props[pid].model_dump() for pid in all_prop_ids if pid in graph.props]
    prop_states = [ps.model_dump() for ps in (frame.prop_states or [])]

    # Dialogue audible during this frame
    dialogue_nodes = []
    for did, dnode in graph.dialogue.items():
        # Check if this frame falls within the dialogue's temporal span
        if _frame_in_span(graph, frame_id, dnode.start_frame, dnode.end_frame):
            dialogue_nodes.append(dnode.model_dump())

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
        "world": graph.project.world.model_dump(),
        "visual": graph.project.visual.model_dump(),
    }


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

    edge = GraphEdge(
        edge_id=f"{source_id}__{edge_type}__{target_id}",
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

    # 1. Prop possession continuity
    for cs in frame.cast_states:
        for prop_id in cs.props_held:
            if prev_frame:
                # Check if anyone else was holding this prop in prev frame
                prev_holder = None
                for prev_cs in prev_frame.cast_states:
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
        for cs in frame.cast_states:
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
            cast_ids_in_frame = {cs.cast_id for cs in frame.cast_states
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

    new_state = CastFrameState.model_validate(new_data)
    graph.cast_frame_states[new_key] = new_state
    return new_state


def propagate_prop_state(
    graph: NarrativeGraph,
    prop_id: str,
    from_frame_id: str,
    to_frame_id: str,
    mutations: Optional[dict] = None,
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

    new_state = PropFrameState.model_validate(new_data)
    graph.prop_frame_states[new_key] = new_state
    return new_state


def propagate_location_state(
    graph: NarrativeGraph,
    location_id: str,
    from_frame_id: str,
    to_frame_id: str,
    mutations: Optional[dict] = None,
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

    new_state = LocationFrameState.model_validate(new_data)
    graph.location_frame_states[new_key] = new_state
    return new_state

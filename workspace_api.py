"""UI-facing workspace snapshot helpers for the local Morpheus app."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase_persistence import schedule_graph_persistence


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def read_text(path: Path, fallback: str = "") -> str:
    if not path.exists():
        return fallback
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return fallback


_GRAPH_COLLECTIONS = {
    "cast": "cast",
    "location": "locations",
    "locations": "locations",
    "prop": "props",
    "props": "props",
    "scene": "scenes",
    "scenes": "scenes",
    "frame": "frames",
    "frames": "frames",
    "dialogue": "dialogue",
    "cast_frame_state": "cast_frame_states",
    "cast_frame_states": "cast_frame_states",
    "prop_frame_state": "prop_frame_states",
    "prop_frame_states": "prop_frame_states",
    "location_frame_state": "location_frame_states",
    "location_frame_states": "location_frame_states",
}


def graph_path(project_dir: Path) -> Path:
    return project_dir / "graph" / "narrative_graph.json"


def load_graph(project_dir: Path) -> dict[str, Any]:
    return load_json(graph_path(project_dir), {})


def save_graph(project_dir: Path, graph: dict[str, Any]) -> None:
    path = graph_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")


def graph_collection_name(node_type: str) -> str:
    normalized = str(node_type or "").strip().lower()
    if normalized not in _GRAPH_COLLECTIONS:
        raise ValueError(f"Unsupported graph node type: {node_type}")
    return _GRAPH_COLLECTIONS[normalized]


def get_graph_node(project_dir: Path, node_type: str, node_id: str) -> dict[str, Any] | None:
    graph = load_graph(project_dir)
    collection_name = graph_collection_name(node_type)
    collection = graph.get(collection_name) or {}
    if not isinstance(collection, dict):
        return None
    node = collection.get(node_id)
    return node if isinstance(node, dict) else None


def _deep_merge_node(current: Any, updates: Any) -> Any:
    if isinstance(current, dict) and isinstance(updates, dict):
        merged = {**current}
        for key, value in updates.items():
            if value is None:
                merged[key] = None
            else:
                merged[key] = _deep_merge_node(merged.get(key), value)
        return merged
    return updates


def patch_graph_node(
    project_dir: Path,
    node_type: str,
    node_id: str,
    updates: dict[str, Any],
    *,
    modified_by: str = "ui",
) -> dict[str, Any]:
    if not isinstance(updates, dict):
        raise ValueError("Graph updates must be an object")

    collection_name = graph_collection_name(node_type)
    from graph.runtime_state import save_graph_projection
    from graph.store import GraphStore

    store = GraphStore(project_dir)
    graph = store.load()
    collection = getattr(graph, collection_name, None)
    if collection is None:
        raise ValueError(f"Graph collection '{collection_name}' is not editable")
    existing = collection.get(node_id)
    if existing is None:
        raise KeyError(node_id)

    updated = _deep_merge_node(existing.model_dump(), updates)
    updated_model = existing.__class__.model_validate(updated)
    provenance = getattr(updated_model, "provenance", None)
    if provenance is not None:
        provenance.last_modified_at = datetime.now(timezone.utc).isoformat()
        provenance.last_modified_by = modified_by
    collection[node_id] = updated_model
    save_graph_projection(graph, project_dir, store=store)
    schedule_graph_persistence(
        project_dir,
        operation="patch",
        node_type=node_type,
        node_id=node_id,
        actor=modified_by,
        payload={"updates": updates},
    )
    if collection_name in {"cast", "locations", "props"}:
        record_review_entity_change(project_dir, node_type, node_id, action="updated")
        mark_pipeline_invalidation(
            project_dir,
            4,
            "entity_graph_updated",
            source=modified_by,
            subject_type=node_type,
            subject_id=node_id,
            clear_approvals=("referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt"),
        )
    elif collection_name in {"scenes", "frames", "dialogue", "cast_frame_states", "prop_frame_states", "location_frame_states"}:
        mark_pipeline_invalidation(
            project_dir,
            4,
            "graph_downstream_updated",
            source=modified_by,
            subject_type=node_type,
            subject_id=node_id,
            clear_approvals=("timelineApprovedAt", "videoApprovedAt"),
        )
    return updated_model.model_dump()


def _slug_token(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "item"


def _entity_collection_and_id_field(node_type: str) -> tuple[str, str]:
    collection_name = graph_collection_name(node_type)
    mapping = {
        "cast": ("cast", "cast_id"),
        "locations": ("locations", "location_id"),
        "props": ("props", "prop_id"),
    }
    if collection_name not in mapping:
        raise ValueError(f"Unsupported entity node type: {node_type}")
    return mapping[collection_name]


def _next_entity_id(collection: Any, node_type: str, preferred_name: str) -> str:
    prefix_map = {
        "cast": "cast",
        "location": "loc",
        "prop": "prop",
    }
    normalized_type = str(node_type or "").strip().lower().rstrip("s")
    prefix = prefix_map.get(normalized_type, normalized_type or "entity")
    base = f"{prefix}_{_slug_token(preferred_name)}"
    candidate = base
    counter = 2
    while candidate in collection:
        candidate = f"{base}_{counter:02d}"
        counter += 1
    return candidate


def create_graph_node(
    project_dir: Path,
    node_type: str,
    data: dict[str, Any],
    *,
    modified_by: str = "ui",
) -> dict[str, Any]:
    from graph.api import upsert_node
    from graph.runtime_state import save_graph_projection
    from graph.store import GraphStore

    if not isinstance(data, dict):
        raise ValueError("Graph node payload must be an object")

    normalized_type = str(node_type or "").strip().lower().rstrip("s")
    collection_name, id_field = _entity_collection_and_id_field(normalized_type)
    store = GraphStore(project_dir)
    graph = store.load()
    registry = getattr(graph, collection_name)

    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Entity name is required")

    node_id = str(data.get(id_field) or "").strip() or _next_entity_id(registry, normalized_type, name)
    description = str(data.get("description") or "").strip()
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    if normalized_type == "cast":
        payload = {
            "cast_id": node_id,
            "name": name,
            "display_name": name,
            "source_name": name,
            "personality": description,
            "story_summary": str(metadata.get("story_summary") or ""),
            "identity": {
                "physical_description": str(metadata.get("physical_description") or description),
                "wardrobe_description": str(metadata.get("wardrobe_description") or ""),
            },
        }
    elif normalized_type == "location":
        payload = {
            "location_id": node_id,
            "name": name,
            "description": description,
            "atmosphere": str(metadata.get("atmosphere") or ""),
            "story_summary": str(metadata.get("story_summary") or ""),
            "location_type": str(metadata.get("location_type") or "exterior"),
        }
    else:
        payload = {
            "prop_id": node_id,
            "name": name,
            "description": description,
            "narrative_significance": str(metadata.get("narrative_significance") or ""),
            "story_summary": str(metadata.get("story_summary") or ""),
        }

    upsert_node(
        graph,
        normalized_type,
        payload,
        {
            "source_prose_chunk": f"UI entity creation: {name}",
            "generated_by": modified_by,
            "confidence": 1.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    save_graph_projection(graph, project_dir, store=store)
    schedule_graph_persistence(
        project_dir,
        operation="create",
        node_type=normalized_type,
        node_id=node_id,
        actor=modified_by,
        payload={"node": getattr(graph, collection_name)[node_id].model_dump()},
    )
    record_review_entity_change(project_dir, normalized_type, node_id, action="created")
    mark_pipeline_invalidation(
        project_dir,
        4,
        "entity_created",
        source=modified_by,
        subject_type=normalized_type,
        subject_id=node_id,
        clear_approvals=("referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt"),
    )
    return getattr(graph, collection_name)[node_id].model_dump()


def delete_graph_node(
    project_dir: Path,
    node_type: str,
    node_id: str,
    *,
    modified_by: str = "ui",
) -> None:
    from graph.runtime_state import save_graph_projection
    from graph.store import GraphStore

    normalized_type = str(node_type or "").strip().lower().rstrip("s")
    collection_name, _id_field = _entity_collection_and_id_field(normalized_type)
    store = GraphStore(project_dir)
    graph = store.load()
    registry = getattr(graph, collection_name)
    if node_id not in registry:
        raise KeyError(node_id)

    if normalized_type == "cast":
        if any(dialogue.cast_id == node_id for dialogue in graph.dialogue.values()):
            raise ValueError(f"Cannot delete cast '{node_id}' while dialogue still references it")
        for scene in graph.scenes.values():
            scene.cast_present = [cast_id for cast_id in scene.cast_present if cast_id != node_id]
        for state_id in [state_id for state_id, state in graph.cast_frame_states.items() if state.cast_id == node_id]:
            graph.cast_frame_states.pop(state_id, None)
    elif normalized_type == "location":
        if any(scene.location_id == node_id for scene in graph.scenes.values()) or any(
            frame.location_id == node_id for frame in graph.frames.values()
        ):
            raise ValueError(f"Cannot delete location '{node_id}' while scenes or frames still reference it")
        for state_id in [state_id for state_id, state in graph.location_frame_states.items() if state.location_id == node_id]:
            graph.location_frame_states.pop(state_id, None)
    elif normalized_type == "prop":
        for scene in graph.scenes.values():
            scene.props_present = [prop_id for prop_id in scene.props_present if prop_id != node_id]
        for state_id in [state_id for state_id, state in graph.prop_frame_states.items() if state.prop_id == node_id]:
            graph.prop_frame_states.pop(state_id, None)

    graph.edges = [
        edge for edge in graph.edges
        if edge.source_id != node_id and edge.target_id != node_id
    ]
    registry.pop(node_id, None)
    save_graph_projection(graph, project_dir, store=store)
    schedule_graph_persistence(
        project_dir,
        operation="delete",
        node_type=normalized_type,
        node_id=node_id,
        actor=modified_by,
        payload={},
    )
    record_review_entity_change(project_dir, normalized_type, node_id, action="deleted")
    mark_pipeline_invalidation(
        project_dir,
        4,
        "entity_deleted",
        source=modified_by,
        subject_type=normalized_type,
        subject_id=node_id,
        clear_approvals=("referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt"),
    )


def record_review_entity_change(
    project_dir: Path,
    node_type: str,
    node_id: str,
    *,
    action: str,
    image_path: str | None = None,
) -> None:
    normalized_type = str(node_type or "").strip().lower().rstrip("s")
    if normalized_type not in {"cast", "location", "prop"}:
        return

    state = load_workspace_state(project_dir)
    entries = [item for item in state.get("reviewEntityChanges") or [] if isinstance(item, dict)]
    existing = next(
        (
            item
            for item in entries
            if item.get("nodeType") == normalized_type and item.get("nodeId") == node_id
        ),
        None,
    )
    if existing is None:
        existing = {
            "nodeType": normalized_type,
            "nodeId": node_id,
            "actions": [],
        }
        entries.append(existing)

    actions = [str(item).strip() for item in existing.get("actions") or [] if str(item).strip()]
    if action not in actions:
        actions.append(action)
    existing["actions"] = actions
    if image_path:
        existing["imagePath"] = image_path
    existing["updatedAt"] = datetime.now(timezone.utc).isoformat()
    state["reviewEntityChanges"] = entries
    save_workspace_state(project_dir, state)


def load_review_entity_changes(project_dir: Path) -> list[dict[str, Any]]:
    state = load_workspace_state(project_dir)
    return [item for item in state.get("reviewEntityChanges") or [] if isinstance(item, dict)]


def clear_review_entity_changes(project_dir: Path) -> None:
    state = load_workspace_state(project_dir)
    state["reviewEntityChanges"] = []
    save_workspace_state(project_dir, state)


def attach_entity_image(
    project_dir: Path,
    entity_type: str,
    entity_id: str,
    target_path: Path,
    *,
    modified_by: str = "ui_upload",
) -> dict[str, Any]:
    rel_path = target_path.relative_to(project_dir).as_posix()
    if entity_type == "cast":
        updates = {
            "composite_path": rel_path,
            "composite_status": "complete",
        }
    elif entity_type == "location":
        updates = {
            "primary_image_path": rel_path,
            "image_status": "complete",
        }
    else:
        updates = {
            "image_path": rel_path,
        }
    updated = patch_graph_node(project_dir, entity_type, entity_id, updates, modified_by=modified_by)
    record_review_entity_change(project_dir, entity_type, entity_id, action="image_updated", image_path=rel_path)
    mark_pipeline_invalidation(
        project_dir,
        4,
        "entity_reference_image_updated",
        source=modified_by,
        subject_type=entity_type,
        subject_id=entity_id,
        clear_approvals=("referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt"),
    )
    return updated


def build_frame_context(project_dir: Path, frame_id: str) -> dict[str, Any] | None:
    graph = load_graph(project_dir)
    frames = graph.get("frames") or {}
    if not isinstance(frames, dict):
        return None
    frame = frames.get(frame_id)
    if not isinstance(frame, dict):
        return None

    scenes = graph.get("scenes") or {}
    dialogue = graph.get("dialogue") or {}
    cast_states = graph.get("cast_frame_states") or {}
    prop_states = graph.get("prop_frame_states") or {}
    location_states = graph.get("location_frame_states") or {}

    frame_scene = scenes.get(frame.get("scene_id")) if isinstance(scenes, dict) else None
    frame_dialogue = []
    if isinstance(dialogue, dict):
        for dialogue_id in frame.get("dialogue_ids") or []:
            item = dialogue.get(dialogue_id)
            if isinstance(item, dict):
                frame_dialogue.append(item)

    frame_cast_states = []
    if isinstance(cast_states, dict):
        frame_cast_states = [
            state
            for state in cast_states.values()
            if isinstance(state, dict) and state.get("frame_id") == frame_id
        ]
        frame_cast_states.sort(key=lambda item: str(item.get("cast_id") or ""))

    frame_prop_states = []
    if isinstance(prop_states, dict):
        frame_prop_states = [
            state
            for state in prop_states.values()
            if isinstance(state, dict) and state.get("frame_id") == frame_id
        ]
        frame_prop_states.sort(key=lambda item: str(item.get("prop_id") or ""))

    frame_location_states = []
    if isinstance(location_states, dict):
        frame_location_states = [
            state
            for state in location_states.values()
            if isinstance(state, dict) and state.get("frame_id") == frame_id
        ]

    return {
        "frame": frame,
        "scene": frame_scene if isinstance(frame_scene, dict) else None,
        "dialogue": frame_dialogue,
        "castStates": frame_cast_states,
        "propStates": frame_prop_states,
        "locationStates": frame_location_states,
    }


def workspace_state_path(project_dir: Path) -> Path:
    path = project_dir / "logs" / "ui_workspace_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ui_event_log_path(project_dir: Path) -> Path:
    path = project_dir / "logs" / "pipeline" / "ui_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ui_phase_report_path(project_dir: Path) -> Path:
    path = project_dir / "logs" / "pipeline" / "ui_phase_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_workspace_state(project_dir: Path) -> dict[str, Any]:
    data = load_json(
        workspace_state_path(project_dir),
        {
            "approvals": {},
            "changeRequests": [],
            "reviewEntityChanges": [],
            "pipelineInvalidations": {},
        },
    )
    if not isinstance(data, dict):
        data = {}
    return {
        "approvals": dict(data.get("approvals") or {}),
        "changeRequests": list(data.get("changeRequests") or []),
        "reviewEntityChanges": list(data.get("reviewEntityChanges") or []),
        "pipelineInvalidations": _normalize_pipeline_invalidations(data.get("pipelineInvalidations")),
    }


def save_workspace_state(project_dir: Path, state: dict[str, Any]) -> None:
    payload = {
        "approvals": dict(state.get("approvals") or {}),
        "changeRequests": list(state.get("changeRequests") or []),
        "reviewEntityChanges": list(state.get("reviewEntityChanges") or []),
        "pipelineInvalidations": _normalize_pipeline_invalidations(state.get("pipelineInvalidations")),
    }
    workspace_state_path(project_dir).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _normalize_pipeline_invalidations(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for raw_key, raw_payload in value.items():
        phase_key = str(raw_key or "").strip().lower()
        if not re.fullmatch(r"phase_\d+", phase_key):
            continue
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        try:
            phase_number = int(phase_key.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        normalized[phase_key] = {
            "phase": phase_number,
            "dirtyAt": str(payload.get("dirtyAt") or ""),
            "reason": str(payload.get("reason") or ""),
            "source": str(payload.get("source") or ""),
            "subjectType": str(payload.get("subjectType") or ""),
            "subjectId": str(payload.get("subjectId") or ""),
        }
    return normalized


def load_pipeline_invalidations(project_dir: Path) -> dict[str, dict[str, Any]]:
    state = load_workspace_state(project_dir)
    return _normalize_pipeline_invalidations(state.get("pipelineInvalidations"))


def dirty_pipeline_phases(project_dir: Path) -> list[int]:
    invalidations = load_pipeline_invalidations(project_dir)
    phases: list[int] = []
    for key in invalidations:
        try:
            phases.append(int(key.split("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    return sorted(set(phases))


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def mark_pipeline_invalidation(
    project_dir: Path,
    start_phase: int,
    reason: str,
    *,
    source: str = "ui",
    subject_type: str | None = None,
    subject_id: str | None = None,
    clear_approvals: tuple[str, ...] = (),
) -> dict[str, dict[str, Any]]:
    normalized_start = max(0, min(5, int(start_phase)))
    state = load_workspace_state(project_dir)
    invalidations = _normalize_pipeline_invalidations(state.get("pipelineInvalidations"))
    timestamp = datetime.now(timezone.utc).isoformat()

    for phase_number in range(normalized_start, 6):
        phase_key = f"phase_{phase_number}"
        invalidations[phase_key] = {
            "phase": phase_number,
            "dirtyAt": timestamp,
            "reason": str(reason or "").strip(),
            "source": str(source or "").strip(),
            "subjectType": str(subject_type or "").strip(),
            "subjectId": str(subject_id or "").strip(),
        }

    approvals = dict(state.get("approvals") or {})
    for approval_key in clear_approvals:
        approvals.pop(approval_key, None)
    state["approvals"] = approvals
    state["pipelineInvalidations"] = invalidations
    save_workspace_state(project_dir, state)
    return invalidations


def clear_pipeline_invalidations(
    project_dir: Path,
    *phase_numbers: int,
    not_after: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    state = load_workspace_state(project_dir)
    invalidations = _normalize_pipeline_invalidations(state.get("pipelineInvalidations"))
    cutoff = not_after
    if isinstance(cutoff, datetime):
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        else:
            cutoff = cutoff.astimezone(timezone.utc)
    for phase_number in phase_numbers:
        phase_key = f"phase_{int(phase_number)}"
        payload = invalidations.get(phase_key)
        if payload is None:
            continue
        if cutoff is None:
            invalidations.pop(phase_key, None)
            continue
        dirty_at = _parse_iso_datetime(payload.get("dirtyAt"))
        if dirty_at is None or dirty_at <= cutoff:
            invalidations.pop(phase_key, None)
    state["pipelineInvalidations"] = invalidations
    save_workspace_state(project_dir, state)
    return invalidations


def rewind_manifest_phases(
    project_dir: Path,
    start_phase: int,
    reason: str,
    *,
    source: str = "ui",
) -> dict[str, Any]:
    normalized_start = max(1, min(6, int(start_phase)))
    manifest_path = project_dir / "project_manifest.json"
    manifest = load_json(manifest_path, {})
    if not isinstance(manifest, dict):
        manifest = {}

    timestamp = datetime.now(timezone.utc).isoformat()
    phases = manifest.get("phases")
    if not isinstance(phases, dict):
        phases = {}

    for phase_number in range(normalized_start, 7):
        phase_key = f"phase_{phase_number}"
        phase_payload = phases.get(phase_key)
        if not isinstance(phase_payload, dict):
            phase_payload = {}
        phase_payload["status"] = "ready" if phase_number == normalized_start else "pending"
        phase_payload.pop("completedAt", None)
        phase_payload["invalidatedAt"] = timestamp
        phase_payload["invalidationReason"] = str(reason or "").strip()
        phase_payload["invalidationSource"] = str(source or "").strip()
        phases[phase_key] = phase_payload

    manifest["phases"] = phases
    manifest["updatedAt"] = timestamp
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def mark_project_file_change(
    project_dir: Path,
    rel_path: str | Path,
    *,
    source: str = "ui_write",
) -> dict[str, Any] | None:
    normalized = Path(rel_path).as_posix().lstrip("./")
    if not normalized:
        return None

    if normalized.startswith("source_files/"):
        reason = "concept_source_updated"
        mark_pipeline_invalidation(
            project_dir,
            1,
            reason,
            source=source,
            subject_type="file",
            subject_id=normalized,
            clear_approvals=("skeletonApprovedAt", "referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt"),
        )
        rewind_manifest_phases(project_dir, 1, reason, source=source)
        return {"startPhase": 1, "reason": reason}

    if normalized.startswith("creative_output/"):
        reason = "creative_output_updated"
        mark_pipeline_invalidation(
            project_dir,
            2,
            reason,
            source=source,
            subject_type="file",
            subject_id=normalized,
            clear_approvals=("skeletonApprovedAt", "referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt"),
        )
        rewind_manifest_phases(project_dir, 2, reason, source=source)
        return {"startPhase": 2, "reason": reason}

    if normalized.startswith("graph/"):
        reason = "graph_artifact_updated"
        mark_pipeline_invalidation(
            project_dir,
            4,
            reason,
            source=source,
            subject_type="file",
            subject_id=normalized,
            clear_approvals=("timelineApprovedAt", "videoApprovedAt"),
        )
        return {"startPhase": 4, "reason": reason}

    if normalized.startswith(("cast/", "locations/", "props/")):
        reason = "reference_asset_updated"
        mark_pipeline_invalidation(
            project_dir,
            4,
            reason,
            source=source,
            subject_type="file",
            subject_id=normalized,
            clear_approvals=("referencesApprovedAt", "timelineApprovedAt", "videoApprovedAt"),
        )
        return {"startPhase": 4, "reason": reason}

    if normalized.startswith("frames/prompts/"):
        reason = "frame_prompt_updated"
        mark_pipeline_invalidation(
            project_dir,
            4,
            reason,
            source=source,
            subject_type="file",
            subject_id=normalized,
            clear_approvals=("timelineApprovedAt", "videoApprovedAt"),
        )
        return {"startPhase": 4, "reason": reason}

    if normalized.startswith("video/prompts/"):
        reason = "video_prompt_updated"
        mark_pipeline_invalidation(
            project_dir,
            5,
            reason,
            source=source,
            subject_type="file",
            subject_id=normalized,
            clear_approvals=("videoApprovedAt",),
        )
        return {"startPhase": 5, "reason": reason}

    if normalized.startswith("frames/composed/"):
        reason = "frame_asset_updated"
        mark_pipeline_invalidation(
            project_dir,
            5,
            reason,
            source=source,
            subject_type="file",
            subject_id=normalized,
            clear_approvals=("timelineApprovedAt", "videoApprovedAt"),
        )
        return {"startPhase": 5, "reason": reason}

    return None


def append_ui_event(project_dir: Path, event: dict[str, Any]) -> None:
    path = ui_event_log_path(project_dir)
    payload = dict(event)
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_ui_events(project_dir: Path) -> list[dict[str, Any]]:
    path = ui_event_log_path(project_dir)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def timeline_overrides_path(project_dir: Path) -> Path:
    path = project_dir / "logs" / "ui_timeline_overrides.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_timeline_overrides(project_dir: Path) -> dict[str, Any]:
    data = load_json(
        timeline_overrides_path(project_dir),
        {
            "hiddenFrameIds": [],
            "expandedFrames": [],
            "frameOverrides": {},
            "dialogueOverrides": {},
        },
    )
    if not isinstance(data, dict):
        data = {}
    return {
        "hiddenFrameIds": list(data.get("hiddenFrameIds") or []),
        "expandedFrames": list(data.get("expandedFrames") or []),
        "frameOverrides": dict(data.get("frameOverrides") or {}),
        "dialogueOverrides": dict(data.get("dialogueOverrides") or {}),
    }


def save_timeline_overrides(project_dir: Path, overrides: dict[str, Any], *, mark_dirty: bool = True) -> None:
    path = timeline_overrides_path(project_dir)
    payload = {
        "hiddenFrameIds": list(overrides.get("hiddenFrameIds") or []),
        "expandedFrames": list(overrides.get("expandedFrames") or []),
        "frameOverrides": dict(overrides.get("frameOverrides") or {}),
        "dialogueOverrides": dict(overrides.get("dialogueOverrides") or {}),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if mark_dirty:
        mark_pipeline_invalidation(
            project_dir,
            5,
            "timeline_override_updated",
            source="timeline_override",
            clear_approvals=("timelineApprovedAt", "videoApprovedAt"),
        )


_VERSIONED_ASSET_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".svg",
    ".mp4",
    ".mov",
    ".webm",
}


def _project_file_url(project_dir: Path, rel_path: str | Path | None) -> str | None:
    if not rel_path:
        return None
    rel = Path(rel_path).as_posix().lstrip("./")
    if not rel:
        return None
    project_id = project_dir.name
    url = f"/api/projects/{project_id}/file/{rel}"
    target = (project_dir / rel).resolve()
    if target.exists() and target.is_file() and target.suffix.lower() in _VERSIONED_ASSET_SUFFIXES:
        stat = target.stat()
        return f"{url}?v={stat.st_mtime_ns}"
    return url


def _sequence_for_frame(frame: dict[str, Any], fallback: int) -> int:
    value = frame.get("sequenceIndex", fallback)
    try:
        return int(value)
    except Exception:
        return fallback


def pipeline_artifact_progress(
    project_dir: Path,
    manifest: dict[str, Any],
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph_data = graph if graph is not None else _graph_data(project_dir)
    manifest_frames = _materialized_manifest_frames(manifest, graph_data)
    expected_frame_count = len(manifest_frames)

    composed_frame_count = 0
    clip_count = 0
    for frame in manifest_frames:
        frame_id = str(frame.get("frameId") or "").strip()
        if not frame_id:
            continue
        if _resolve_frame_image_rel(project_dir, frame_id, frame.get("generatedImagePath")):
            composed_frame_count += 1
        if _resolve_existing_rel_path(
            project_dir,
            [
                f"video/clips/{frame_id}.mp4",
                f"video/clips/{frame_id}.mov",
                f"video/clips/{frame_id}.webm",
                f"video/clips/{frame_id}.*",
            ],
            frame.get("videoPath"),
        ):
            clip_count += 1

    return {
        "expectedFrameCount": expected_frame_count,
        "composedFrameCount": composed_frame_count,
        "clipCount": clip_count,
        "hasAnyComposedFrames": composed_frame_count > 0,
        "hasAnyClips": clip_count > 0,
        "allComposedFramesReady": expected_frame_count > 0 and composed_frame_count >= expected_frame_count,
        "allClipsReady": expected_frame_count > 0 and clip_count >= expected_frame_count,
    }


def _status_from_phase_files(project_dir: Path, manifest: dict[str, Any]) -> tuple[str, int]:
    phases = manifest.get("phases") or {}
    completed = 0
    total = 7
    for idx in range(total):
        phase = phases.get(f"phase_{idx}", {}) or {}
        if phase.get("status") == "complete":
            completed += 1

    creative_output = project_dir / "creative_output" / "creative_output.md"
    skeleton = project_dir / "creative_output" / "outline_skeleton.md"
    graph = _graph_data(project_dir)
    state = load_workspace_state(project_dir)
    approvals = state.get("approvals") or {}
    invalidations = _normalize_pipeline_invalidations(state.get("pipelineInvalidations"))
    dirty_preproduction = any(phase_key in invalidations for phase_key in ("phase_1", "phase_2", "phase_3"))
    dirty_phase_4 = "phase_4" in invalidations
    dirty_phase_5 = "phase_5" in invalidations
    artifact_progress = pipeline_artifact_progress(project_dir, manifest, graph)

    has_skeleton = skeleton.exists() or creative_output.exists()
    reference_expected = _expected_reference_entity_count(project_dir, manifest, graph)
    reference_ready = _completed_reference_entity_count(project_dir, manifest, graph)
    has_reference_assets = reference_expected > 0 and reference_ready >= reference_expected

    if dirty_preproduction:
        if approvals.get("skeletonApprovedAt"):
            return "generating_assets", max(28, round((completed / total) * 100))
        if has_skeleton:
            return "skeleton_review", max(24, round((completed / total) * 100))
        return "onboarding", max(0, round((completed / total) * 100))
    if dirty_phase_5:
        if approvals.get("timelineApprovedAt"):
            return "generating_video", max(82, round((completed / total) * 100))
        if artifact_progress["allComposedFramesReady"]:
            return "timeline_review", max(70, round((completed / total) * 100))
    if dirty_phase_4:
        if approvals.get("referencesApprovedAt"):
            return "generating_frames", max(58, round((completed / total) * 100))
        if has_reference_assets:
            return "reference_review", max(45, round((completed / total) * 100))
    if artifact_progress["allClipsReady"]:
        return "complete", max(92, round((completed / total) * 100))
    if approvals.get("timelineApprovedAt"):
        if not artifact_progress["allComposedFramesReady"]:
            return "generating_frames", max(58, round((completed / total) * 100))
        return "generating_video", max(82, round((completed / total) * 100))
    if artifact_progress["hasAnyClips"]:
        return "generating_video", max(82, round((completed / total) * 100))
    if artifact_progress["allComposedFramesReady"]:
        return "timeline_review", max(70, round((completed / total) * 100))
    if approvals.get("referencesApprovedAt") or artifact_progress["hasAnyComposedFrames"]:
        return "generating_frames", max(58, round((completed / total) * 100))
    if has_reference_assets:
        return "reference_review", max(45, round((completed / total) * 100))
    if approvals.get("skeletonApprovedAt") or has_skeleton:
        return "generating_assets", max(28, round((completed / total) * 100))
    return "onboarding", max(0, round((completed / total) * 100))


def classify_ui_gate(path: str) -> str:
    text = str(path or "").lower()
    if "/concept" in text:
        return "onboarding"
    if "/skeleton" in text:
        return "skeleton"
    if "/entities" in text or "/storyboard" in text:
        return "references"
    if "/timeline" in text:
        return "timeline"
    if "/chat" in text or "/graph" in text or "/workspace" in text:
        return "workspace"
    if "/video" in text:
        return "video"
    return "workspace"


def _artifact_stats(project_dir: Path, graph: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    count = lambda pattern: len(list(pattern))
    artifact_progress = pipeline_artifact_progress(project_dir, manifest, graph)
    return {
        "hasOnboardingConfig": (project_dir / "source_files" / "onboarding_config.json").exists(),
        "hasSkeleton": (project_dir / "creative_output" / "outline_skeleton.md").exists(),
        "hasCreativeOutput": (project_dir / "creative_output" / "creative_output.md").exists(),
        "hasGraph": (project_dir / "graph" / "narrative_graph.json").exists(),
        "hasDialogue": (project_dir / "dialogue.json").exists(),
        "castReferenceCount": count((project_dir / "cast" / "composites").glob("*.png")),
        "locationReferenceCount": count((project_dir / "locations" / "primary").glob("*.png")),
        "locationVariantCount": count((project_dir / "locations" / "variants").glob("*.png")),
        "propReferenceCount": count((project_dir / "props" / "generated").glob("*.png")),
        "imagePromptCount": count((project_dir / "frames" / "prompts").glob("*_image.json")),
        "videoPromptCount": count((project_dir / "video" / "prompts").glob("*_video.json")),
        "composedFrameCount": artifact_progress["composedFrameCount"],
        "clipCount": artifact_progress["clipCount"],
        "projectCoverPresent": (project_dir / "reports" / "project_cover.png").exists(),
        "sceneCount": len(graph.get("scenes") or {}) if isinstance(graph.get("scenes"), dict) else 0,
        "frameCount": len(graph.get("frames") or {}) if isinstance(graph.get("frames"), dict) else len(manifest.get("frames") or []),
        "expectedFrameCount": artifact_progress["expectedFrameCount"],
        "dialogueNodeCount": len(graph.get("dialogue") or {}) if isinstance(graph.get("dialogue"), dict) else 0,
    }


def _route_stats(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "route": "",
            "gate": "workspace",
            "total": 0,
            "success": 0,
            "errors": 0,
            "avgDurationMs": 0.0,
            "lastStatus": None,
            "lastError": None,
            "lastAt": None,
        }
    )
    duration_sums: dict[str, float] = defaultdict(float)
    for event in events:
        route = str(event.get("path") or "")
        bucket = buckets[route]
        bucket["route"] = route
        bucket["gate"] = str(event.get("gate") or classify_ui_gate(route))
        bucket["total"] += 1
        if int(event.get("statusCode") or 0) < 400:
            bucket["success"] += 1
        else:
            bucket["errors"] += 1
            if event.get("error"):
                bucket["lastError"] = event.get("error")
        duration_sums[route] += float(event.get("durationMs") or 0.0)
        bucket["lastStatus"] = event.get("statusCode")
        bucket["lastAt"] = event.get("timestamp")
    rows = []
    for route, bucket in buckets.items():
        total = max(1, int(bucket["total"]))
        bucket["avgDurationMs"] = round(duration_sums[route] / total, 2)
        rows.append(bucket)
    rows.sort(key=lambda item: (-int(item["errors"]), -int(item["total"]), str(item["route"])))
    return rows


def _ui_break_findings(workflow: dict[str, Any], artifacts: dict[str, Any], route_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    approvals = workflow.get("approvals") or {}
    findings: list[dict[str, Any]] = []

    def add(severity: str, code: str, message: str) -> None:
        findings.append({"severity": severity, "code": code, "message": message})

    if not artifacts["hasOnboardingConfig"]:
        add("error", "missing_onboarding", "Onboarding config is missing.")
    if approvals.get("skeletonApprovedAt") and not artifacts["hasSkeleton"]:
        add("error", "approved_without_skeleton", "Skeleton was approved but outline_skeleton.md is missing.")
    if approvals.get("skeletonApprovedAt") and not artifacts["hasCreativeOutput"]:
        add("warning", "approved_without_creative_output", "Skeleton was approved but creative_output.md is missing.")
    if artifacts["hasCreativeOutput"] and not artifacts["hasGraph"]:
        add("error", "creative_output_without_graph", "Creative output exists but the narrative graph is missing.")
    if approvals.get("referencesApprovedAt") and (
        artifacts["castReferenceCount"] + artifacts["locationReferenceCount"] + artifacts["propReferenceCount"] == 0
    ):
        add("error", "approved_without_references", "References were approved but no cast/location/prop reference images exist.")
    expected_frames = int(artifacts.get("expectedFrameCount") or artifacts.get("frameCount") or 0)
    if approvals.get("timelineApprovedAt") and expected_frames and artifacts["composedFrameCount"] < expected_frames:
        add(
            "error",
            "approved_without_frames",
            f"Timeline was approved but only {artifacts['composedFrameCount']} of {expected_frames} composed frames exist.",
        )
    if approvals.get("videoApprovedAt") and expected_frames and artifacts["clipCount"] < expected_frames:
        add(
            "error",
            "approved_without_clips",
            f"Video was approved but only {artifacts['clipCount']} of {expected_frames} rendered clips exist.",
        )
    if artifacts["frameCount"] and artifacts["imagePromptCount"] < artifacts["frameCount"]:
        add("warning", "missing_image_prompts", "Image prompt count is lower than graph frame count.")
    if artifacts["frameCount"] and artifacts["videoPromptCount"] < artifacts["frameCount"]:
        add("warning", "missing_video_prompts", "Video prompt count is lower than graph frame count.")
    if artifacts["hasGraph"] and artifacts["sceneCount"] == 0:
        add("error", "empty_scene_graph", "Narrative graph exists but contains no scenes.")

    failing_routes = [row for row in route_rows if int(row.get("errors") or 0) > 0]
    if failing_routes:
        top = failing_routes[0]
        add(
            "warning",
            "ui_route_failures",
            f"UI route failures detected. Worst route: {top.get('route')} ({top.get('errors')} errors).",
        )

    return findings


def build_ui_phase_report(project_id: str, project_dir: Path) -> dict[str, Any]:
    manifest = load_json(project_dir / "project_manifest.json", {})
    onboarding = load_json(project_dir / "source_files" / "onboarding_config.json", {})
    graph = load_graph(project_dir)
    workflow = load_workspace_state(project_dir)
    status, progress = _status_from_phase_files(project_dir, manifest)
    events = load_ui_events(project_dir)
    routes = _route_stats(events)
    artifacts = _artifact_stats(project_dir, graph, manifest)
    findings = _ui_break_findings(workflow, artifacts, routes)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectId": project_id,
        "projectName": manifest.get("projectName") or onboarding.get("projectName") or project_id,
        "status": status,
        "progress": progress,
        "approvals": workflow.get("approvals") or {},
        "changeRequests": workflow.get("changeRequests") or [],
        "artifacts": artifacts,
        "eventSummary": {
            "totalEvents": len(events),
            "errorEvents": sum(1 for event in events if int(event.get("statusCode") or 0) >= 400),
            "lastEventAt": events[-1].get("timestamp") if events else None,
        },
        "routes": routes,
        "breakFindings": findings,
    }


def write_ui_phase_report(project_id: str, project_dir: Path) -> Path:
    report = build_ui_phase_report(project_id, project_dir)
    path = ui_phase_report_path(project_dir)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def load_ui_phase_report(project_dir: Path) -> dict[str, Any]:
    data = load_json(ui_phase_report_path(project_dir), {})
    return data if isinstance(data, dict) else {}


def _load_project_cover_meta(project_dir: Path) -> dict[str, Any]:
    meta = load_json(project_dir / "reports" / "project_cover_meta.json", {})
    return meta if isinstance(meta, dict) else {}


def _build_project_summary(
    project_id: str,
    project_dir: Path,
    manifest: dict[str, Any],
    onboarding: dict[str, Any],
) -> dict[str, Any]:
    status, progress = _status_from_phase_files(project_dir, manifest)
    phase0 = (manifest.get("phases") or {}).get("phase_0", {}) or {}
    created_at = phase0.get("completedAt") or datetime.now(timezone.utc).isoformat()
    updated_at = manifest.get("updatedAt") or created_at
    cover_meta = _load_project_cover_meta(project_dir)
    cover_rel = cover_meta.get("imagePath") or ("reports/project_cover.png" if (project_dir / "reports" / "project_cover.png").exists() else None)
    return {
        "id": project_id,
        "name": manifest.get("projectName") or onboarding.get("projectName") or project_id,
        "description": onboarding.get("extraDetails", ""),
        "status": status,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "creativityLevel": onboarding.get("creativeFreedom", "balanced"),
        "generationMode": "assisted",
        "progress": progress,
        "coverImageUrl": _project_file_url(project_dir, cover_rel),
        "coverSummary": cover_meta.get("summary"),
    }


def _build_creative_concept(
    project_dir: Path,
    manifest: dict[str, Any],
    onboarding: dict[str, Any],
) -> dict[str, Any]:
    project_name = manifest.get("projectName") or onboarding.get("projectName") or project_dir.name
    pitch_md = read_text(project_dir / "source_files" / "pitch.md")
    source_summary = onboarding.get("extraDetails") or pitch_md.strip()
    first_line = next((line.strip() for line in source_summary.splitlines() if line.strip()), "")
    genres = onboarding.get("genre") or onboarding.get("genres") or []
    if isinstance(genres, str):
        genre_text = genres
    else:
        genre_text = ", ".join(str(item) for item in genres if str(item).strip())
    moods = onboarding.get("mood") or []
    if isinstance(moods, str):
        tone = moods
    else:
        tone = ", ".join(str(item) for item in moods if str(item).strip())

    return {
        "title": project_name,
        "logline": first_line or f"{project_name} concept",
        "synopsis": source_summary,
        "tone": tone,
        "genre": genre_text,
    }


def _graph_data(project_dir: Path) -> dict[str, Any]:
    graph_path = project_dir / "graph" / "narrative_graph.json"
    return load_json(graph_path, {})


def _parse_scene_drafts(project_dir: Path) -> list[dict[str, Any]]:
    """Fallback: parse ///SCENE tags from creative_output/scenes/ when graph is empty."""
    import re
    scenes_dir = project_dir / "creative_output" / "scenes"
    if not scenes_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for scene_file in sorted(scenes_dir.glob("scene_*_draft.md")):
        text = scene_file.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"///SCENE:\s*(.*)", text)
        if not m:
            continue
        tags: dict[str, str] = {}
        for pair in m.group(1).split("|"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                tags[k.strip()] = v.strip()
        scene_id = tags.get("id", scene_file.stem)
        cast_list = [c.strip() for c in tags.get("cast", "").split(",") if c.strip()]
        items.append(
            {
                "id": scene_id,
                "number": len(items) + 1,
                "heading": tags.get("title", scene_id),
                "description": f"{tags.get('mood', '')} — {tags.get('int_ext', '')}. {tags.get('time_of_day', '')}".strip(" —."),
                "location": tags.get("location", ""),
                "characters": cast_list,
                "estimatedFrames": 0,
            }
        )
    return items


def _build_skeleton_plan(project_dir: Path) -> dict[str, Any]:
    graph = _graph_data(project_dir)
    scenes = graph.get("scenes") or {}
    scene_order = graph.get("scene_order") or sorted(scenes.keys())
    skeleton_markdown = read_text(project_dir / "creative_output" / "outline_skeleton.md")

    items: list[dict[str, Any]] = []
    for scene_id in scene_order:
        scene = scenes.get(scene_id) or {}
        location_id = scene.get("location_id")
        items.append(
            {
                "id": scene_id,
                "number": scene.get("scene_number") or len(items) + 1,
                "heading": scene.get("scene_heading") or scene.get("title") or scene_id,
                "description": scene.get("emotional_arc") or scene.get("entry_conditions") or scene.get("title") or "",
                "location": location_id or "",
                "characters": list(scene.get("cast_present") or []),
                "estimatedFrames": int(scene.get("frame_count") or len(scene.get("frame_ids") or [])),
            }
        )

    # Fallback: parse scene drafts when graph hasn't been built yet
    if not items:
        items = _parse_scene_drafts(project_dir)

    return {
        "scenes": items,
        "totalScenes": len(items),
        "estimatedDuration": max(0, len(items) * 8),
        "markdown": skeleton_markdown,
    }


def _manifest_frame_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        frame.get("frameId"): frame
        for frame in (manifest.get("frames") or [])
        if isinstance(frame, dict) and frame.get("frameId")
    }


def _relative_image_path_from_url(image_url: str | None) -> str | None:
    if not image_url:
        return None
    image_url = str(image_url).split("?", 1)[0]
    project_prefix = "/api/projects/"
    legacy_prefix = "/api/project/file/"
    if image_url.startswith(project_prefix):
        parts = image_url.split("/", 5)
        if len(parts) >= 6 and parts[4] == "file":
            return parts[5]
    if image_url.startswith(legacy_prefix):
        return image_url[len(legacy_prefix):]
    return image_url


_IMAGE_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def _resolve_path_with_variants(project_dir: Path, rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    normalized = Path(rel_path).as_posix().lstrip("./")
    if not normalized:
        return None

    if any(token in normalized for token in ("*", "?", "[")):
        for match in sorted(project_dir.glob(normalized)):
            if match.is_file():
                return match.relative_to(project_dir).as_posix()
        return None

    target = project_dir / normalized
    if target.exists() and target.is_file():
        return normalized

    stem = target.with_suffix("")
    for suffix in _IMAGE_ASSET_SUFFIXES:
        variant = stem.with_suffix(suffix)
        if variant.exists() and variant.is_file():
            return variant.relative_to(project_dir).as_posix()
    return None


def _resolve_frame_image_rel(project_dir: Path, frame_id: str, declared: str | None = None) -> str | None:
    return _resolve_existing_rel_path(
        project_dir,
        [
            f"frames/composed/{frame_id}_gen.png",
            f"frames/composed/{frame_id}.png",
            f"frames/composed/{frame_id}_gen.*",
            f"frames/composed/{frame_id}.*",
        ],
        declared,
    )


def _build_expanded_frame(project_dir: Path, source_frame: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    rel_image = override.get("imageRel") or _relative_image_path_from_url(source_frame.get("imageUrl"))
    rel_video = override.get("videoRel") or _relative_image_path_from_url(source_frame.get("videoUrl"))
    source_duration = float(source_frame.get("duration") or 5)
    frame = {
        "id": override.get("id"),
        "storyboardId": override.get("storyboardId") or source_frame.get("storyboardId") or source_frame.get("id"),
        "sequence": 0,
        "imageUrl": _project_file_url(project_dir, rel_image),
        "videoUrl": _project_file_url(project_dir, rel_video),
        "prompt": override.get("prompt") or source_frame.get("prompt") or "",
        "status": "complete" if rel_image else "pending",
        "duration": max(2, min(15, float(override.get("duration") or source_duration or 5))),
        "dialogueId": override.get("dialogueId", source_frame.get("dialogueId")),
        "trimStart": float(override.get("trimStart") if override.get("trimStart") is not None else source_frame.get("trimStart") or 0),
        "trimEnd": float(override.get("trimEnd") if override.get("trimEnd") is not None else source_frame.get("trimEnd") or 0),
        "sourceFrameId": override.get("sourceFrameId") or source_frame.get("sourceFrameId") or source_frame.get("id"),
        "isExpanded": True,
        "direction": override.get("direction"),
    }
    return frame


def _entity_path_candidates(entity_type: str, entity_id: str) -> list[str]:
    if entity_type == "cast":
        return [
            f"cast/composites/{entity_id}_ref.png",
            f"cast/composites/{entity_id}.png",
            f"cast/composites/{entity_id}_ref.*",
            f"cast/composites/{entity_id}.*",
        ]
    if entity_type == "location":
        return [
            f"locations/primary/{entity_id}.png",
            f"locations/primary/{entity_id}.*",
        ]
    return [
        f"props/generated/{entity_id}.png",
        f"props/generated/{entity_id}.*",
    ]


def _resolve_existing_rel_path(project_dir: Path, candidates: list[str], declared: str | None = None) -> str | None:
    paths = ([declared] if declared else []) + candidates
    for rel in paths:
        if not rel:
            continue
        resolved = _resolve_path_with_variants(project_dir, rel)
        if resolved:
            return resolved
    if declared:
        normalized = Path(declared).as_posix().lstrip("./")
        if normalized:
            return normalized
    return None


def _graph_registry(graph: dict[str, Any], registry_name: str) -> dict[str, dict[str, Any]]:
    registry = graph.get(registry_name) or {}
    if not isinstance(registry, dict):
        return {}
    return {str(key): value for key, value in registry.items() if isinstance(value, dict)}


def _graph_entity_records(graph: dict[str, Any], registry_name: str, id_field: str) -> list[dict[str, Any]]:
    registry = _graph_registry(graph, registry_name)
    return [
        {id_field: entity_id, **item}
        for entity_id, item in registry.items()
    ]


def _sentence_limited_summary(*parts: Any) -> str:
    fragments: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text:
            fragments.append(text)
    if not fragments:
        return ""
    summary = " ".join(fragments)
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", summary) if item.strip()]
    if not sentences:
        sentences = [summary]
    return " ".join(sentences[:2]).strip()


def _entity_story_summary(entity_type: str, graph_item: dict[str, Any], manifest_item: dict[str, Any] | None = None) -> str:
    summary = str(graph_item.get("story_summary") or "").strip()
    if summary:
        return _sentence_limited_summary(summary)

    manifest_item = manifest_item or {}
    if entity_type == "cast":
        return _sentence_limited_summary(
            graph_item.get("arc_summary"),
            graph_item.get("role"),
            graph_item.get("personality"),
        )
    if entity_type == "location":
        return _sentence_limited_summary(
            graph_item.get("narrative_purpose"),
            graph_item.get("atmosphere"),
            graph_item.get("description"),
            manifest_item.get("name"),
        )
    return _sentence_limited_summary(
        graph_item.get("story_summary"),
        graph_item.get("narrative_significance"),
        graph_item.get("description"),
        manifest_item.get("name"),
    )


def _expected_reference_entity_count(project_dir: Path, manifest: dict[str, Any], graph: dict[str, Any]) -> int:
    cast_items = list(manifest.get("cast") or []) or _graph_entity_records(graph, "cast", "castId")
    location_items = list(manifest.get("locations") or []) or _graph_entity_records(graph, "locations", "locationId")
    prop_items = list(manifest.get("props") or []) or _graph_entity_records(graph, "props", "propId")
    return sum(
        1
        for item in [*cast_items, *location_items, *prop_items]
        if isinstance(item, dict)
    )


def _completed_reference_entity_count(project_dir: Path, manifest: dict[str, Any], graph: dict[str, Any]) -> int:
    def _count(items: list[dict[str, Any]], entity_type: str, id_key: str, path_key: str) -> int:
        completed = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get(id_key) or "").strip()
            if not entity_id:
                continue
            rel_path = _resolve_existing_rel_path(
                project_dir,
                _entity_path_candidates(entity_type, entity_id),
                item.get(path_key),
            )
            if rel_path and (project_dir / rel_path).exists():
                completed += 1
        return completed

    cast_items = list(manifest.get("cast") or []) or _graph_entity_records(graph, "cast", "castId")
    location_items = list(manifest.get("locations") or []) or _graph_entity_records(graph, "locations", "locationId")
    prop_items = list(manifest.get("props") or []) or _graph_entity_records(graph, "props", "propId")
    return (
        _count(cast_items, "cast", "castId", "compositePath")
        + _count(location_items, "location", "locationId", "primaryImagePath")
        + _count(prop_items, "prop", "propId", "imagePath")
    )


def _build_entities(project_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    graph = _graph_data(project_dir)
    entities: list[dict[str, Any]] = []

    graph_cast = _graph_registry(graph, "cast")
    seen_ids: set[tuple[str, str]] = set()

    for item in manifest.get("cast") or []:
        cast_id = item.get("castId")
        if not cast_id:
            continue
        graph_item = graph_cast.get(cast_id, {})
        rel_path = _resolve_existing_rel_path(
            project_dir,
            _entity_path_candidates("cast", cast_id),
            item.get("compositePath"),
        )
        entities.append(
            {
                "id": cast_id,
                "type": "cast",
                "name": graph_item.get("display_name") or graph_item.get("name") or item.get("name") or cast_id,
                "description": graph_item.get("description") or graph_item.get("personality") or item.get("description") or "",
                "storySummary": _entity_story_summary("cast", graph_item, item),
                "imageUrl": _project_file_url(project_dir, rel_path),
                "status": item.get("compositeStatus") or ("complete" if rel_path else "pending"),
                "metadata": graph_item,
            }
        )
        seen_ids.add(("cast", cast_id))

    for cast_id, graph_item in graph_cast.items():
        if ("cast", cast_id) in seen_ids:
            continue
        rel_path = _resolve_existing_rel_path(
            project_dir,
            _entity_path_candidates("cast", cast_id),
            graph_item.get("composite_path"),
        )
        entities.append(
            {
                "id": cast_id,
                "type": "cast",
                "name": graph_item.get("display_name") or graph_item.get("name") or cast_id,
                "description": graph_item.get("description") or graph_item.get("personality") or "",
                "storySummary": _entity_story_summary("cast", graph_item),
                "imageUrl": _project_file_url(project_dir, rel_path),
                "status": graph_item.get("composite_status") or ("complete" if rel_path and (project_dir / rel_path).exists() else "pending"),
                "metadata": graph_item,
            }
        )
        seen_ids.add(("cast", cast_id))

    graph_locations = _graph_registry(graph, "locations")
    for item in manifest.get("locations") or []:
        location_id = item.get("locationId")
        if not location_id:
            continue
        graph_item = graph_locations.get(location_id, {})
        rel_path = _resolve_existing_rel_path(
            project_dir,
            _entity_path_candidates("location", location_id),
            item.get("primaryImagePath"),
        )
        entities.append(
            {
                "id": location_id,
                "type": "location",
                "name": graph_item.get("name") or item.get("name") or location_id,
                "description": graph_item.get("description") or graph_item.get("atmosphere") or item.get("description") or "",
                "storySummary": _entity_story_summary("location", graph_item, item),
                "imageUrl": _project_file_url(project_dir, rel_path),
                "status": item.get("imageStatus") or ("complete" if rel_path else "pending"),
                "metadata": graph_item,
            }
        )
        seen_ids.add(("location", location_id))

    for location_id, graph_item in graph_locations.items():
        if ("location", location_id) in seen_ids:
            continue
        rel_path = _resolve_existing_rel_path(
            project_dir,
            _entity_path_candidates("location", location_id),
            graph_item.get("primary_image_path"),
        )
        entities.append(
            {
                "id": location_id,
                "type": "location",
                "name": graph_item.get("name") or location_id,
                "description": graph_item.get("description") or graph_item.get("atmosphere") or "",
                "storySummary": _entity_story_summary("location", graph_item),
                "imageUrl": _project_file_url(project_dir, rel_path),
                "status": graph_item.get("image_status") or ("complete" if rel_path and (project_dir / rel_path).exists() else "pending"),
                "metadata": graph_item,
            }
        )
        seen_ids.add(("location", location_id))

    graph_props = _graph_registry(graph, "props")
    for item in manifest.get("props") or []:
        prop_id = item.get("propId")
        if not prop_id:
            continue
        graph_item = graph_props.get(prop_id, {})
        rel_path = _resolve_existing_rel_path(
            project_dir,
            _entity_path_candidates("prop", prop_id),
            item.get("imagePath"),
        )
        entities.append(
            {
                "id": prop_id,
                "type": "prop",
                "name": graph_item.get("name") or item.get("name") or prop_id,
                "description": graph_item.get("description") or graph_item.get("narrative_significance") or item.get("description") or "",
                "storySummary": _entity_story_summary("prop", graph_item, item),
                "imageUrl": _project_file_url(project_dir, rel_path),
                "status": item.get("imageStatus") or ("complete" if rel_path else "pending"),
                "metadata": graph_item,
            }
        )
        seen_ids.add(("prop", prop_id))

    for prop_id, graph_item in graph_props.items():
        if ("prop", prop_id) in seen_ids:
            continue
        rel_path = _resolve_existing_rel_path(
            project_dir,
            _entity_path_candidates("prop", prop_id),
            graph_item.get("image_path"),
        )
        entities.append(
            {
                "id": prop_id,
                "type": "prop",
                "name": graph_item.get("name") or prop_id,
                "description": graph_item.get("description") or graph_item.get("narrative_significance") or "",
                "storySummary": _entity_story_summary("prop", graph_item),
                "imageUrl": _project_file_url(project_dir, rel_path),
                "status": graph_item.get("image_status") or ("complete" if rel_path and (project_dir / rel_path).exists() else "pending"),
                "metadata": graph_item,
            }
        )

    return entities


def _prompt_path(project_dir: Path, frame_id: str, kind: str) -> Path:
    return project_dir / ("video" if kind == "video" else "frames") / "prompts" / f"{frame_id}_{kind}.json"


def _visible_cast_ids_for_frame(graph: dict[str, Any], frame_id: str) -> list[str]:
    cast_states = graph.get("cast_frame_states") or {}
    if not isinstance(cast_states, dict):
        return []
    ids: list[str] = []
    for state in cast_states.values():
        if not isinstance(state, dict):
            continue
        if state.get("frame_id") != frame_id:
            continue
        cast_id = str(state.get("cast_id") or "").strip()
        frame_role = str(state.get("frame_role") or "").strip().lower()
        if cast_id and frame_role != "referenced":
            ids.append(cast_id)
    return sorted(dict.fromkeys(ids))


def _prop_ids_for_frame(graph: dict[str, Any], frame_id: str) -> list[str]:
    prop_states = graph.get("prop_frame_states") or {}
    if not isinstance(prop_states, dict):
        return []
    ids: list[str] = []
    for state in prop_states.values():
        if not isinstance(state, dict):
            continue
        if state.get("frame_id") != frame_id:
            continue
        prop_id = str(state.get("prop_id") or "").strip()
        if prop_id:
            ids.append(prop_id)
    return sorted(dict.fromkeys(ids))


def _materialized_manifest_frames(manifest: dict[str, Any], graph: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_frames = [
        frame
        for frame in (manifest.get("frames") or [])
        if isinstance(frame, dict) and frame.get("frameId")
    ]
    graph_frames = _graph_registry(graph, "frames")
    scene_registry = _graph_registry(graph, "scenes")
    order = list(graph.get("frame_order") or [])

    existing_ids = {
        str(frame.get("frameId") or "").strip()
        for frame in manifest_frames
        if str(frame.get("frameId") or "").strip()
    }

    for fallback_index, frame_id in enumerate(order or sorted(graph_frames.keys()), start=1):
        frame = graph_frames.get(frame_id)
        if not frame or frame_id in existing_ids:
            continue
        scene = scene_registry.get(str(frame.get("scene_id") or ""), {})
        cast_ids = _visible_cast_ids_for_frame(graph, frame_id) or list(scene.get("cast_present") or [])
        prop_ids = _prop_ids_for_frame(graph, frame_id)
        manifest_frames.append(
            {
                "frameId": frame_id,
                "sceneId": frame.get("scene_id"),
                "sequenceIndex": frame.get("sequence_index") or fallback_index,
                "castIds": cast_ids,
                "locationId": frame.get("location_id"),
                "propIds": prop_ids,
                "narrativeBeat": frame.get("narrative_beat") or frame.get("source_text") or "",
                "actionSummary": frame.get("action_summary") or "",
                "suggestedDuration": frame.get("suggested_duration") or 5,
                "dialogueIds": list(frame.get("dialogue_ids") or []),
                "dialogueRef": (frame.get("dialogue_ids") or [None])[0],
                "sourceText": frame.get("source_text") or "",
                "composition": frame.get("composition") or {},
                "background": frame.get("background") or {},
                "generatedImagePath": frame.get("composed_image_path"),
            }
        )

    return manifest_frames


def _build_timeline_frames(project_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    graph = _graph_data(project_dir)
    overrides = load_timeline_overrides(project_dir)
    hidden_ids = set(str(item) for item in overrides.get("hiddenFrameIds") or [])
    frame_overrides = overrides.get("frameOverrides") or {}
    frames = sorted(
        _materialized_manifest_frames(manifest, graph),
        key=lambda item: _sequence_for_frame(item, 10**9),
    )

    timeline: list[dict[str, Any]] = []
    base_by_id: dict[str, dict[str, Any]] = {}
    for frame in frames:
        frame_id = frame.get("frameId")
        if not frame_id:
            continue
        image_prompt = load_json(_prompt_path(project_dir, frame_id, "image"), {})
        video_prompt = load_json(_prompt_path(project_dir, frame_id, "video"), {})

        image_rel = _resolve_frame_image_rel(project_dir, frame_id, frame.get("generatedImagePath"))
        video_rel = _resolve_existing_rel_path(
            project_dir,
            [
                f"video/clips/{frame_id}.mp4",
                f"video/clips/{frame_id}.mov",
                f"video/clips/{frame_id}.webm",
                f"video/clips/{frame_id}.*",
            ],
            frame.get("videoPath"),
        )

        frame_override = frame_overrides.get(frame_id) or {}
        dialogue_id = frame_override.get("dialogueId", frame.get("dialogueRef"))

        base = {
            "id": frame_id,
            "storyboardId": frame_id,
            "sequence": _sequence_for_frame(frame, len(timeline) + 1),
            "imageUrl": _project_file_url(project_dir, image_rel),
            "videoUrl": _project_file_url(project_dir, video_rel),
            "prompt": image_prompt.get("prompt") or frame.get("narrativeBeat") or frame.get("sourceText") or "",
            "status": "complete" if image_rel else "pending",
            "duration": max(
                2,
                min(
                    15,
                    float(
                        video_prompt.get("duration")
                        or frame.get("suggestedDuration")
                        or 5
                    ),
                ),
            ),
            "dialogueId": dialogue_id,
            "trimStart": max(0.0, float(frame_override.get("trimStart") or 0)),
            "trimEnd": max(0.0, float(frame_override.get("trimEnd") or 0)),
            "sourceFrameId": frame_id,
            "isExpanded": False,
        }
        base_by_id[frame_id] = base

    expanded_before: dict[str, list[dict[str, Any]]] = {}
    expanded_after: dict[str, list[dict[str, Any]]] = {}
    for override in overrides.get("expandedFrames") or []:
        if not isinstance(override, dict):
            continue
        source_frame_id = override.get("sourceFrameId")
        if not source_frame_id or source_frame_id not in base_by_id:
            continue
        expanded_frame = _build_expanded_frame(project_dir, base_by_id[source_frame_id], override)
        bucket = expanded_before if override.get("direction") == "before" else expanded_after
        bucket.setdefault(source_frame_id, []).append(expanded_frame)

    for frame in frames:
        frame_id = frame.get("frameId")
        if not frame_id or frame_id not in base_by_id:
            continue
        timeline.extend(expanded_before.get(frame_id, []))
        if frame_id not in hidden_ids:
            timeline.append(base_by_id[frame_id])
        timeline.extend(expanded_after.get(frame_id, []))

    for index, frame in enumerate(timeline, start=1):
        frame["sequence"] = index

    return timeline


def _build_storyboard_frames(project_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    frames = _build_timeline_frames(project_dir, manifest)
    storyboard: list[dict[str, Any]] = []
    manifest_frames = {
        frame.get("frameId"): frame
        for frame in _materialized_manifest_frames(manifest, _graph_data(project_dir))
        if isinstance(frame, dict) and frame.get("frameId")
    }
    for frame in frames:
        source_frame_id = frame.get("sourceFrameId") or frame["id"]
        manifest_frame = manifest_frames.get(source_frame_id, {})
        storyboard.append(
            {
                "id": frame["id"],
                "sceneId": manifest_frame.get("sceneId") or "",
                "sequence": frame["sequence"],
                "description": manifest_frame.get("narrativeBeat") or manifest_frame.get("sourceText") or frame["prompt"],
                "shotType": ((manifest_frame.get("composition") or {}).get("shot") or manifest_frame.get("formulaTag") or ("expanded" if frame.get("isExpanded") else "frame")).title(),
                "imageUrl": frame["imageUrl"],
                "status": "approved" if frame["imageUrl"] else "pending",
            }
        )
    return storyboard


def _build_dialogue_blocks(project_dir: Path, timeline_frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dialogue_path = project_dir / "dialogue.json"
    graph = _graph_data(project_dir)
    overrides = load_timeline_overrides(project_dir)
    dialogue_overrides = overrides.get("dialogueOverrides") or {}
    raw = load_json(dialogue_path, {"dialogue": []})
    lines = raw.get("dialogue", []) if isinstance(raw, dict) else raw
    if not lines:
        graph_dialogue = _graph_registry(graph, "dialogue")
        lines = [
            {
                "dialogueId": dialogue_id,
                "line": item.get("line") or item.get("raw_line") or "",
                "rawLine": item.get("raw_line") or item.get("line") or "",
                "speaker": item.get("speaker") or item.get("cast_id") or "UNKNOWN",
                "castId": item.get("cast_id") or "",
                "primaryVisualFrame": item.get("primary_visual_frame"),
                "reactionFrameIds": item.get("reaction_frame_ids") or [],
                "frameId": item.get("primary_visual_frame"),
            }
            for dialogue_id, item in graph_dialogue.items()
        ]
    frame_index = {frame["id"]: frame["sequence"] for frame in timeline_frames}
    frame_duration = {frame["id"]: frame["duration"] for frame in timeline_frames}
    frames_by_dialogue: dict[str, list[str]] = {}
    for frame in timeline_frames:
        dialogue_id = frame.get("dialogueId")
        if dialogue_id:
            frames_by_dialogue.setdefault(dialogue_id, []).append(frame["id"])

    blocks: list[dict[str, Any]] = []
    for item in lines:
        if not isinstance(item, dict):
            continue
        dialogue_id = item.get("dialogueId")
        if not dialogue_id:
            continue
        linked_frame_ids = [fid for fid in frames_by_dialogue.get(dialogue_id, []) if fid in frame_index]
        if not linked_frame_ids:
            linked_frame_ids = [
                fid
                for fid in ([item.get("primaryVisualFrame"), *(item.get("reactionFrameIds") or [])] or [item.get("frameId")])
                if fid and fid in frame_index
            ]
        if not linked_frame_ids and item.get("frameId") in frame_index:
            linked_frame_ids = [item.get("frameId")]
        if not linked_frame_ids:
            continue

        sequences = sorted(frame_index[fid] for fid in linked_frame_ids)
        duration = round(sum(frame_duration.get(fid, 0) for fid in linked_frame_ids), 1) or float(max(2, len(linked_frame_ids) * 2))
        override = dialogue_overrides.get(dialogue_id) or {}
        blocks.append(
            {
                "id": dialogue_id,
                "text": override.get("text") or item.get("line") or item.get("rawLine") or "",
                "character": override.get("character") or item.get("speaker") or item.get("castId") or "UNKNOWN",
                "startFrame": int(override.get("startFrame") or sequences[0]),
                "endFrame": int(override.get("endFrame") or sequences[-1]),
                "duration": round(float(override.get("duration") or duration), 1),
                "linkedFrameIds": linked_frame_ids,
            }
        )

    return blocks


def _build_chat_history(project_dir: Path) -> list[dict[str, Any]]:
    history = load_json(project_dir / "logs" / "ui_chat_history.json", [])
    return history if isinstance(history, list) else []


def build_workspace_snapshot(project_id: str, project_dir: Path) -> dict[str, Any]:
    manifest = load_json(project_dir / "project_manifest.json", {})
    onboarding = load_json(project_dir / "source_files" / "onboarding_config.json", {})
    project = _build_project_summary(project_id, project_dir, manifest, onboarding)
    creative_concept = _build_creative_concept(project_dir, manifest, onboarding)
    skeleton_plan = _build_skeleton_plan(project_dir)
    entities = _build_entities(project_dir, manifest)
    timeline_frames = _build_timeline_frames(project_dir, manifest)
    dialogue_blocks = _build_dialogue_blocks(project_dir, timeline_frames)
    storyboard_frames = _build_storyboard_frames(project_dir, manifest)
    creative_output = read_text(project_dir / "creative_output" / "creative_output.md")
    report_path = project_dir / "reports" / "project_report.md"
    video_projection = project_dir / "reports" / "video_prompt_projection.md"
    final_export = _resolve_existing_rel_path(
        project_dir,
        [
            "video/export/*_final.mp4",
            "video/export/*_final.mov",
            "video/export/*_final.webm",
            "video/export/*.mp4",
            "video/export/*.mov",
            "video/export/*.webm",
        ],
        manifest.get("exportPath"),
    )
    cover_summary = project_dir / "reports" / "project_cover_summary.md"
    cover_image = project_dir / "reports" / "project_cover.png"
    cover_meta = project_dir / "reports" / "project_cover_meta.json"
    ui_phase_report = ui_phase_report_path(project_dir)
    workspace_state = load_workspace_state(project_dir)

    return {
        "project": project,
        "creativeConcept": creative_concept,
        "skeletonPlan": skeleton_plan,
        "scriptText": creative_output,
        "entities": entities,
        "storyboardFrames": storyboard_frames,
        "timelineFrames": timeline_frames,
        "dialogueBlocks": dialogue_blocks,
        "workers": [],
        "messages": _build_chat_history(project_dir),
        "workflow": workspace_state,
        "reports": {
            "projectReport": _project_file_url(project_dir, report_path.relative_to(project_dir)) if report_path.exists() else None,
            "videoPromptProjection": _project_file_url(project_dir, video_projection.relative_to(project_dir)) if video_projection.exists() else None,
            "finalExport": _project_file_url(project_dir, final_export),
            "projectCover": _project_file_url(project_dir, cover_image.relative_to(project_dir)) if cover_image.exists() else None,
            "projectCoverSummary": _project_file_url(project_dir, cover_summary.relative_to(project_dir)) if cover_summary.exists() else None,
            "projectCoverMeta": _project_file_url(project_dir, cover_meta.relative_to(project_dir)) if cover_meta.exists() else None,
            "greenlightReport": _project_file_url(project_dir, ui_phase_report.relative_to(project_dir)) if ui_phase_report.exists() else None,
            "uiPhaseReport": _project_file_url(project_dir, ui_phase_report.relative_to(project_dir)) if ui_phase_report.exists() else None,
        },
    }


def entity_upload_path(project_dir: Path, entity_id: str, entity_type: str, filename_suffix: str) -> Path:
    ext = Path(filename_suffix).suffix.lower() or ".png"
    if entity_type == "cast":
        return project_dir / "cast" / "composites" / f"{entity_id}_ref{ext}"
    if entity_type == "location":
        return project_dir / "locations" / "primary" / f"{entity_id}{ext}"
    return project_dir / "props" / "generated" / f"{entity_id}{ext}"


def frame_upload_path(project_dir: Path, frame_id: str, filename_suffix: str) -> Path:
    ext = Path(filename_suffix).suffix.lower() or ".png"
    return project_dir / "frames" / "composed" / f"{frame_id}_gen{ext}"

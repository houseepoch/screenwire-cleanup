"""UI-facing workspace snapshot helpers for the local Morpheus app."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

    graph = load_graph(project_dir)
    collection_name = graph_collection_name(node_type)
    collection = graph.get(collection_name) or {}
    if not isinstance(collection, dict):
        raise ValueError(f"Graph collection '{collection_name}' is not editable")
    existing = collection.get(node_id)
    if not isinstance(existing, dict):
        raise KeyError(node_id)

    updated = _deep_merge_node(existing, updates)
    provenance = dict(updated.get("provenance") or {})
    provenance["last_modified_at"] = datetime.now(timezone.utc).isoformat()
    provenance["last_modified_by"] = modified_by
    updated["provenance"] = provenance
    collection[node_id] = updated
    graph[collection_name] = collection
    save_graph(project_dir, graph)
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
        },
    )
    if not isinstance(data, dict):
        data = {}
    return {
        "approvals": dict(data.get("approvals") or {}),
        "changeRequests": list(data.get("changeRequests") or []),
    }


def save_workspace_state(project_dir: Path, state: dict[str, Any]) -> None:
    payload = {
        "approvals": dict(state.get("approvals") or {}),
        "changeRequests": list(state.get("changeRequests") or []),
    }
    workspace_state_path(project_dir).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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


def save_timeline_overrides(project_dir: Path, overrides: dict[str, Any]) -> None:
    path = timeline_overrides_path(project_dir)
    payload = {
        "hiddenFrameIds": list(overrides.get("hiddenFrameIds") or []),
        "expandedFrames": list(overrides.get("expandedFrames") or []),
        "frameOverrides": dict(overrides.get("frameOverrides") or {}),
        "dialogueOverrides": dict(overrides.get("dialogueOverrides") or {}),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _project_file_url(rel_path: str | Path | None) -> str | None:
    if not rel_path:
        return None
    rel = Path(rel_path).as_posix().lstrip("./")
    if not rel:
        return None
    return f"/api/project/file/{rel}"


def _sequence_for_frame(frame: dict[str, Any], fallback: int) -> int:
    value = frame.get("sequenceIndex", fallback)
    try:
        return int(value)
    except Exception:
        return fallback


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
    composed_dir = project_dir / "frames" / "composed"
    clips_dir = project_dir / "video" / "clips"
    state = load_workspace_state(project_dir)
    approvals = state.get("approvals") or {}

    has_clips = clips_dir.exists() and any(clips_dir.glob("*.mp4"))
    has_composed_frames = composed_dir.exists() and any(composed_dir.glob("*_gen.*"))
    has_skeleton = skeleton.exists() or creative_output.exists()
    has_reference_assets = any((project_dir / "cast" / "composites").glob("*.png")) or any(
        (project_dir / "locations" / "primary").glob("*.png")
    ) or any((project_dir / "props" / "generated").glob("*.png"))

    if has_clips:
        return "complete", max(92, round((completed / total) * 100))
    if approvals.get("timelineApprovedAt"):
        return "generating_video", max(82, round((completed / total) * 100))
    if has_composed_frames:
        return "timeline_review", max(70, round((completed / total) * 100))
    if approvals.get("referencesApprovedAt"):
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
        "composedFrameCount": count((project_dir / "frames" / "composed").glob("f_*_gen.*")),
        "clipCount": count((project_dir / "video" / "clips").glob("*.mp4")),
        "projectCoverPresent": (project_dir / "reports" / "project_cover.png").exists(),
        "sceneCount": len(graph.get("scenes") or {}) if isinstance(graph.get("scenes"), dict) else 0,
        "frameCount": len(graph.get("frames") or {}) if isinstance(graph.get("frames"), dict) else len(manifest.get("frames") or []),
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
    if approvals.get("timelineApprovedAt") and artifacts["composedFrameCount"] == 0:
        add("error", "approved_without_frames", "Timeline was approved but no composed frames exist.")
    if approvals.get("videoApprovedAt") and artifacts["clipCount"] == 0:
        add("error", "approved_without_clips", "Video was approved but no rendered clips exist.")
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
        "coverImageUrl": _project_file_url(cover_rel),
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
    prefix = "/api/project/file/"
    if image_url.startswith(prefix):
        return image_url[len(prefix):]
    return image_url


def _build_expanded_frame(source_frame: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    rel_image = override.get("imageRel") or _relative_image_path_from_url(source_frame.get("imageUrl"))
    source_duration = float(source_frame.get("duration") or 5)
    frame = {
        "id": override.get("id"),
        "storyboardId": override.get("storyboardId") or source_frame.get("storyboardId") or source_frame.get("id"),
        "sequence": 0,
        "imageUrl": _project_file_url(rel_image),
        "prompt": override.get("prompt") or source_frame.get("prompt") or "",
        "status": "complete" if rel_image else "pending",
        "duration": max(1, min(15, float(override.get("duration") or source_duration or 5))),
        "dialogueId": override.get("dialogueId", source_frame.get("dialogueId")),
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
        ]
    if entity_type == "location":
        return [
            f"locations/primary/{entity_id}.png",
        ]
    return [
        f"props/generated/{entity_id}.png",
    ]


def _resolve_existing_rel_path(project_dir: Path, candidates: list[str], declared: str | None = None) -> str | None:
    paths = ([declared] if declared else []) + candidates
    for rel in paths:
        if not rel:
            continue
        if (project_dir / rel).exists():
            return rel
    return declared or (candidates[0] if candidates else None)


def _build_entities(project_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    graph = _graph_data(project_dir)
    entities: list[dict[str, Any]] = []

    graph_cast = graph.get("cast") or {}
    for item in manifest.get("cast") or []:
        cast_id = item.get("castId")
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
                "imageUrl": _project_file_url(rel_path),
                "status": item.get("compositeStatus") or "pending",
                "metadata": graph_item,
            }
        )

    graph_locations = graph.get("locations") or {}
    for item in manifest.get("locations") or []:
        location_id = item.get("locationId")
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
                "imageUrl": _project_file_url(rel_path),
                "status": item.get("imageStatus") or "pending",
                "metadata": graph_item,
            }
        )

    graph_props = graph.get("props") or {}
    for item in manifest.get("props") or []:
        prop_id = item.get("propId")
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
                "imageUrl": _project_file_url(rel_path),
                "status": item.get("imageStatus") or "pending",
                "metadata": graph_item,
            }
        )

    return entities


def _prompt_path(project_dir: Path, frame_id: str, kind: str) -> Path:
    return project_dir / ("video" if kind == "video" else "frames") / "prompts" / f"{frame_id}_{kind}.json"


def _build_timeline_frames(project_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    overrides = load_timeline_overrides(project_dir)
    hidden_ids = set(str(item) for item in overrides.get("hiddenFrameIds") or [])
    frame_overrides = overrides.get("frameOverrides") or {}
    frames = sorted(
        (manifest.get("frames") or []),
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

        image_rel = frame.get("generatedImagePath") or f"frames/composed/{frame_id}_gen.png"
        if not (project_dir / image_rel).exists():
            image_rel = None

        frame_override = frame_overrides.get(frame_id) or {}
        dialogue_id = frame_override.get("dialogueId", frame.get("dialogueRef"))

        base = {
            "id": frame_id,
            "storyboardId": frame_id,
            "sequence": _sequence_for_frame(frame, len(timeline) + 1),
            "imageUrl": _project_file_url(image_rel),
            "prompt": image_prompt.get("prompt") or frame.get("narrativeBeat") or frame.get("sourceText") or "",
            "status": "complete" if image_rel else "pending",
            "duration": max(
                1,
                min(
                    15,
                    int(
                        video_prompt.get("duration")
                        or frame.get("suggestedDuration")
                        or 5
                    ),
                ),
            ),
            "dialogueId": dialogue_id,
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
        expanded_frame = _build_expanded_frame(base_by_id[source_frame_id], override)
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
    manifest_frames = _manifest_frame_map(manifest)
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
    overrides = load_timeline_overrides(project_dir)
    dialogue_overrides = overrides.get("dialogueOverrides") or {}
    raw = load_json(dialogue_path, {"dialogue": []})
    lines = raw.get("dialogue", []) if isinstance(raw, dict) else raw
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
            "projectReport": _project_file_url(report_path.relative_to(project_dir)) if report_path.exists() else None,
            "videoPromptProjection": _project_file_url(video_projection.relative_to(project_dir)) if video_projection.exists() else None,
            "projectCover": _project_file_url(cover_image.relative_to(project_dir)) if cover_image.exists() else None,
            "projectCoverSummary": _project_file_url(cover_summary.relative_to(project_dir)) if cover_summary.exists() else None,
            "projectCoverMeta": _project_file_url(cover_meta.relative_to(project_dir)) if cover_meta.exists() else None,
            "greenlightReport": _project_file_url(ui_phase_report.relative_to(project_dir)) if ui_phase_report.exists() else None,
            "uiPhaseReport": _project_file_url(ui_phase_report.relative_to(project_dir)) if ui_phase_report.exists() else None,
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

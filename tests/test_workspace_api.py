from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from graph.store import GraphStore
from tests.live_smoke_graph import build_live_smoke_graph
from workspace_api import (
    attach_entity_image,
    append_ui_event,
    build_workspace_snapshot,
    clear_pipeline_invalidations,
    create_graph_node,
    delete_graph_node,
    load_pipeline_invalidations,
    load_workspace_state,
    mark_project_file_change,
    patch_graph_node,
    rewind_manifest_phases,
    save_timeline_overrides,
    write_ui_phase_report,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_project(tmp_path: Path) -> tuple[str, Path]:
    project_id = "proj_001"
    project_dir = tmp_path / project_id
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Project One",
                "phases": {"phase_0": {"status": "complete"}},
                "frames": [{"frameId": "f_001"}],
            },
            indent=2,
        ),
    )
    _write(
        project_dir / "source_files" / "onboarding_config.json",
        json.dumps({"projectName": "Project One", "creativeFreedom": "balanced"}, indent=2),
    )
    _write(project_dir / "creative_output" / "outline_skeleton.md", "# Outline\n")
    _write(project_dir / "creative_output" / "creative_output.md", "# Creative Output\n")
    _write(
        project_dir / "graph" / "narrative_graph.json",
        json.dumps(
            {
                "scenes": {"scene_01": {"scene_id": "scene_01"}},
                "frames": {"f_001": {"frame_id": "f_001", "scene_id": "scene_01", "location_id": "loc_001"}},
                "locations": {"loc_001": {"location_id": "loc_001", "name": "Apartment"}},
                "dialogue": {},
            },
            indent=2,
        ),
    )
    _write(project_dir / "reports" / "project_cover_summary.md", "# Cover\n")
    cover_path = project_dir / "reports" / "project_cover.png"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    cover_path.write_bytes(b"png")
    _write(project_dir / "reports" / "project_cover_meta.json", json.dumps({"summary": "Cover summary"}, indent=2))
    return project_id, project_dir


def test_write_ui_phase_report_tracks_route_errors_and_artifact_findings(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps({"approvals": {"timelineApprovedAt": "2026-04-12T00:00:00Z"}, "changeRequests": []}, indent=2),
    )
    append_ui_event(
        project_dir,
        {
            "path": f"/api/projects/{project_id}/timeline/f_001/regenerate",
            "method": "POST",
            "statusCode": 500,
            "durationMs": 25,
            "error": "boom",
        },
    )

    report_path = write_ui_phase_report(project_id, project_dir)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report_path == project_dir / "logs" / "pipeline" / "ui_phase_report.json"
    assert report["eventSummary"]["totalEvents"] == 1
    assert report["eventSummary"]["errorEvents"] == 1
    assert any(row["route"].endswith("/timeline/f_001/regenerate") and row["errors"] == 1 for row in report["routes"])
    codes = {item["code"] for item in report["breakFindings"]}
    assert "approved_without_frames" in codes
    assert "ui_route_failures" in codes


def test_workspace_snapshot_exposes_ui_phase_report_link(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    write_ui_phase_report(project_id, project_dir)

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert snapshot["reports"]["greenlightReport"] == f"/api/projects/{project_id}/file/logs/pipeline/ui_phase_report.json"
    assert snapshot["reports"]["uiPhaseReport"] == f"/api/projects/{project_id}/file/logs/pipeline/ui_phase_report.json"
    assert str(snapshot["reports"]["projectCover"]).startswith(f"/api/projects/{project_id}/file/reports/project_cover.png?v=")


def test_workspace_snapshot_falls_back_to_graph_entities_and_frames(tmp_path: Path) -> None:
    project_id = "proj_graph_only"
    project_dir = tmp_path / project_id
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Graph First",
                "phases": {"phase_2": {"status": "complete"}},
            },
            indent=2,
        ),
    )
    _write(
        project_dir / "source_files" / "onboarding_config.json",
        json.dumps({"projectName": "Graph First", "creativeFreedom": "balanced"}, indent=2),
    )
    _write(project_dir / "creative_output" / "creative_output.md", "# Creative Output\n")
    _write(
        project_dir / "graph" / "narrative_graph.json",
        json.dumps(
            {
                "scene_order": ["scene_01"],
                "frame_order": ["f_001"],
                "cast": {
                    "cast_001": {
                        "cast_id": "cast_001",
                        "name": "Nova",
                        "display_name": "Nova",
                        "personality": "guarded courier",
                    }
                },
                "locations": {
                    "loc_001": {
                        "location_id": "loc_001",
                        "name": "Clocktower Roof",
                        "description": "wind-whipped rooftop with beacon towers",
                    }
                },
                "props": {
                    "prop_001": {
                        "prop_id": "prop_001",
                        "name": "Analog Key",
                        "description": "brass restart key",
                    }
                },
                "scenes": {
                    "scene_01": {
                        "scene_id": "scene_01",
                        "scene_number": 1,
                        "title": "Roofline Watch",
                        "location_id": "loc_001",
                        "cast_present": ["cast_001"],
                        "frame_ids": ["f_001"],
                    }
                },
                "frames": {
                    "f_001": {
                        "frame_id": "f_001",
                        "scene_id": "scene_01",
                        "sequence_index": 1,
                        "location_id": "loc_001",
                        "narrative_beat": "Nova scans the district from the roof edge.",
                        "source_text": "Nova scans the district from the roof edge.",
                        "suggested_duration": 4,
                        "dialogue_ids": ["dlg_001"],
                        "composition": {"shot": "wide"},
                        "background": {"camera_facing": "east"},
                    }
                },
                "cast_frame_states": {
                    "cast_001@f_001": {
                        "cast_id": "cast_001",
                        "frame_id": "f_001",
                        "frame_role": "subject",
                    }
                },
                "dialogue": {
                    "dlg_001": {
                        "dialogue_id": "dlg_001",
                        "speaker": "Nova",
                        "cast_id": "cast_001",
                        "line": "They're early.",
                        "raw_line": "They're early.",
                        "primary_visual_frame": "f_001",
                        "reaction_frame_ids": [],
                    }
                },
            },
            indent=2,
        ),
    )

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert snapshot["project"]["status"] == "generating_assets"
    assert [entity["id"] for entity in snapshot["entities"]] == ["cast_001", "loc_001", "prop_001"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    assert entities["cast_001"]["storySummary"] == "guarded courier"
    assert entities["prop_001"]["storySummary"] == "brass restart key"
    assert snapshot["timelineFrames"][0]["id"] == "f_001"
    assert snapshot["timelineFrames"][0]["prompt"] == "Nova scans the district from the roof edge."
    assert snapshot["storyboardFrames"][0]["shotType"] == "Wide"
    assert snapshot["dialogueBlocks"][0]["text"] == "They're early."


def test_workspace_snapshot_versions_asset_urls_when_files_exist(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Versioned Assets",
                "phases": {"phase_2": {"status": "complete"}},
                "cast": [{"castId": "cast_001", "compositePath": "cast/composites/cast_001_ref.png"}],
                "locations": [{"locationId": "loc_001", "primaryImagePath": "locations/primary/loc_001.png"}],
                "props": [{"propId": "prop_001", "imagePath": "props/generated/prop_001.png"}],
                "frames": [
                    {
                        "frameId": "f_001",
                        "sequenceIndex": 1,
                        "narrativeBeat": "Nova scans the district.",
                        "sourceText": "Nova scans the district.",
                        "composition": {"shot": "wide"},
                        "generatedImagePath": "frames/composed/f_001_gen.png",
                    }
                ],
            },
            indent=2,
        ),
    )

    for rel_path in [
        "cast/composites/cast_001_ref.png",
        "locations/primary/loc_001.png",
        "props/generated/prop_001.png",
        "frames/composed/f_001_gen.png",
    ]:
        asset = project_dir / rel_path
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"png")

    snapshot = build_workspace_snapshot(project_id, project_dir)
    entities = {entity["id"]: entity for entity in snapshot["entities"]}

    assert str(entities["cast_001"]["imageUrl"]).startswith(f"/api/projects/{project_id}/file/cast/composites/cast_001_ref.png?v=")
    assert str(entities["loc_001"]["imageUrl"]).startswith(f"/api/projects/{project_id}/file/locations/primary/loc_001.png?v=")
    assert str(entities["prop_001"]["imageUrl"]).startswith(f"/api/projects/{project_id}/file/props/generated/prop_001.png?v=")
    assert str(snapshot["timelineFrames"][0]["imageUrl"]).startswith(f"/api/projects/{project_id}/file/frames/composed/f_001_gen.png?v=")
    assert str(snapshot["storyboardFrames"][0]["imageUrl"]).startswith(f"/api/projects/{project_id}/file/frames/composed/f_001_gen.png?v=")


def test_workspace_snapshot_exposes_video_urls_for_existing_clips(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Versioned Clips",
                "phases": {"phase_5": {"status": "complete"}},
                "frames": [
                    {
                        "frameId": "f_001",
                        "sequenceIndex": 1,
                        "narrativeBeat": "Nova scans the district.",
                        "sourceText": "Nova scans the district.",
                        "generatedImagePath": "frames/composed/f_001_gen.png",
                        "videoPath": "video/clips/f_001.mp4",
                    }
                ],
            },
            indent=2,
        ),
    )

    for rel_path, payload in [
        ("frames/composed/f_001_gen.png", b"png"),
        ("video/clips/f_001.mp4", b"mp4"),
    ]:
        asset = project_dir / rel_path
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(payload)

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert str(snapshot["timelineFrames"][0]["videoUrl"]).startswith(
        f"/api/projects/{project_id}/file/video/clips/f_001.mp4?v="
    )


def test_workspace_snapshot_resolves_uploaded_frame_extensions(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Uploaded Frame Extension",
                "phases": {"phase_4": {"status": "complete"}},
                "frames": [
                    {
                        "frameId": "f_001",
                        "sequenceIndex": 1,
                        "narrativeBeat": "Nova scans the district.",
                        "sourceText": "Nova scans the district.",
                    }
                ],
            },
            indent=2,
        ),
    )

    image_path = project_dir / "frames" / "composed" / "f_001_gen.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"jpg")

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert str(snapshot["timelineFrames"][0]["imageUrl"]).startswith(f"/api/projects/{project_id}/file/frames/composed/f_001_gen.jpg?v=")
    assert str(snapshot["storyboardFrames"][0]["imageUrl"]).startswith(f"/api/projects/{project_id}/file/frames/composed/f_001_gen.jpg?v=")


def test_workspace_snapshot_preserves_fractional_timeline_duration_before_video_generation(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Fractional Duration",
                "phases": {"phase_2": {"status": "complete"}},
                "frames": [
                    {
                        "frameId": "f_001",
                        "sequenceIndex": 1,
                        "narrativeBeat": "Nova scans the district.",
                        "sourceText": "Nova scans the district.",
                        "composition": {"shot": "wide"},
                    }
                ],
            },
            indent=2,
        ),
    )
    _write(
        project_dir / "video" / "prompts" / "f_001_video.json",
        json.dumps({"duration": 2.7}, indent=2),
    )

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert snapshot["timelineFrames"][0]["duration"] == 2.7


def test_workspace_snapshot_waits_for_full_reference_pack_before_reference_review(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Project One",
                "phases": {"phase_2": {"status": "complete"}},
                "cast": [{"castId": "cast_001", "compositePath": "cast/composites/cast_001_ref.png"}],
                "locations": [{"locationId": "loc_001", "primaryImagePath": "locations/primary/loc_001.png"}],
                "props": [{"propId": "prop_001", "imagePath": "props/generated/prop_001.png"}],
                "frames": [{"frameId": "f_001"}],
            },
            indent=2,
        ),
    )

    cast_ref = project_dir / "cast" / "composites" / "cast_001_ref.png"
    cast_ref.parent.mkdir(parents=True, exist_ok=True)
    cast_ref.write_bytes(b"png")

    snapshot = build_workspace_snapshot(project_id, project_dir)
    assert snapshot["project"]["status"] == "generating_assets"

    location_ref = project_dir / "locations" / "primary" / "loc_001.png"
    location_ref.parent.mkdir(parents=True, exist_ok=True)
    location_ref.write_bytes(b"png")
    prop_ref = project_dir / "props" / "generated" / "prop_001.png"
    prop_ref.parent.mkdir(parents=True, exist_ok=True)
    prop_ref.write_bytes(b"png")

    refreshed = build_workspace_snapshot(project_id, project_dir)
    assert refreshed["project"]["status"] == "reference_review"


def test_workspace_snapshot_keeps_generating_frames_until_all_expected_frames_exist(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Partial Frames",
                "phases": {
                    "phase_0": {"status": "complete"},
                    "phase_1": {"status": "complete"},
                    "phase_2": {"status": "complete"},
                    "phase_3": {"status": "complete"},
                    "phase_4": {"status": "complete"},
                },
                "frames": [
                    {"frameId": "f_001", "sequenceIndex": 1, "generatedImagePath": "frames/composed/f_001_gen.png"},
                    {"frameId": "f_002", "sequenceIndex": 2},
                ],
            },
            indent=2,
        ),
    )
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:00:00Z",
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                },
                "changeRequests": [],
            },
            indent=2,
        ),
    )
    composed = project_dir / "frames" / "composed" / "f_001_gen.png"
    composed.parent.mkdir(parents=True, exist_ok=True)
    composed.write_bytes(b"png")

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert snapshot["project"]["status"] == "generating_frames"
    assert [frame["status"] for frame in snapshot["timelineFrames"]] == ["complete", "pending"]


def test_workspace_snapshot_keeps_generating_video_until_all_expected_clips_exist(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Partial Clips",
                "phases": {
                    "phase_0": {"status": "complete"},
                    "phase_1": {"status": "complete"},
                    "phase_2": {"status": "complete"},
                    "phase_3": {"status": "complete"},
                    "phase_4": {"status": "complete"},
                    "phase_5": {"status": "complete"},
                },
                "frames": [
                    {"frameId": "f_001", "sequenceIndex": 1, "generatedImagePath": "frames/composed/f_001_gen.png"},
                    {"frameId": "f_002", "sequenceIndex": 2, "generatedImagePath": "frames/composed/f_002_gen.png"},
                ],
            },
            indent=2,
        ),
    )
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:00:00Z",
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                },
                "changeRequests": [],
            },
            indent=2,
        ),
    )
    for frame_id in ("f_001", "f_002"):
        image = project_dir / "frames" / "composed" / f"{frame_id}_gen.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"png")
    clip = project_dir / "video" / "clips" / "f_001.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"mp4")

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert snapshot["project"]["status"] == "generating_video"


def test_workspace_snapshot_treats_dirty_frames_as_generating_even_when_files_exist(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Dirty Frames",
                "phases": {
                    "phase_0": {"status": "complete"},
                    "phase_1": {"status": "complete"},
                    "phase_2": {"status": "complete"},
                    "phase_3": {"status": "complete"},
                    "phase_4": {"status": "complete"},
                },
                "frames": [
                    {"frameId": "f_001", "sequenceIndex": 1, "generatedImagePath": "frames/composed/f_001_gen.png"},
                ],
            },
            indent=2,
        ),
    )
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "approvals": {"referencesApprovedAt": "2026-04-13T07:00:00Z"},
                "pipelineInvalidations": {"phase_4": {"phase": 4, "dirtyAt": "2026-04-13T07:01:00Z"}},
            },
            indent=2,
        ),
    )
    image = project_dir / "frames" / "composed" / "f_001_gen.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert snapshot["project"]["status"] == "generating_frames"


def test_graph_entity_mutations_refresh_projection_and_review_tracking(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj_mutations"
    graph = build_live_smoke_graph(project_dir)
    store = GraphStore(project_dir)
    store.save(graph)

    created = create_graph_node(
        project_dir,
        "prop",
        {
            "name": "Emergency Flare",
            "description": "red magnesium flare kept near the pager case",
        },
    )
    manifest = json.loads((project_dir / "project_manifest.json").read_text(encoding="utf-8"))
    assert any(item.get("propId") == created["prop_id"] for item in manifest.get("props") or [])

    patch_graph_node(
        project_dir,
        "cast",
        "cast_nova",
        {"personality": "hyper-vigilant courier"},
    )
    image_path = project_dir / "cast" / "composites" / "cast_nova_ref.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")
    attach_entity_image(project_dir, "cast", "cast_nova", image_path)

    state = load_workspace_state(project_dir)
    tracked = {
        (item["nodeType"], item["nodeId"]): item
        for item in state["reviewEntityChanges"]
    }
    assert ("cast", "cast_nova") in tracked
    assert "updated" in tracked[("cast", "cast_nova")]["actions"]
    assert "image_updated" in tracked[("cast", "cast_nova")]["actions"]
    assert set(state["pipelineInvalidations"]) >= {"phase_4", "phase_5"}


def test_graph_frame_patch_invalidates_downstream_without_reopening_reference_gate(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj_frame_dirty"
    graph = build_live_smoke_graph(project_dir)
    store = GraphStore(project_dir)
    store.save(graph)
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:00:00Z",
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                    "videoApprovedAt": "2026-04-13T07:10:00Z",
                },
                "changeRequests": [],
            },
            indent=2,
        ),
    )

    patch_graph_node(
        project_dir,
        "frame",
        "f_001",
        {"action_summary": "Nova pivots harder toward the train doors."},
    )

    state = load_workspace_state(project_dir)
    assert state["approvals"]["referencesApprovedAt"] == "2026-04-13T07:00:00Z"
    assert "timelineApprovedAt" not in state["approvals"]
    assert "videoApprovedAt" not in state["approvals"]
    assert set(state["pipelineInvalidations"]) >= {"phase_4", "phase_5"}


def test_save_timeline_overrides_marks_video_generation_dirty(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "approvals": {
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                    "videoApprovedAt": "2026-04-13T07:10:00Z",
                },
                "changeRequests": [],
            },
            indent=2,
        ),
    )

    save_timeline_overrides(
        project_dir,
        {
            "frameOverrides": {
                "f_001": {
                    "trimStart": 0.2,
                    "trimEnd": 0.1,
                }
            }
        },
    )

    invalidations = load_pipeline_invalidations(project_dir)
    state = load_workspace_state(project_dir)
    assert "phase_5" in invalidations
    assert "timelineApprovedAt" not in state["approvals"]
    assert "videoApprovedAt" not in state["approvals"]


def test_delete_graph_node_rejects_location_in_use(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj_delete_guard"
    graph = build_live_smoke_graph(project_dir)
    store = GraphStore(project_dir)
    store.save(graph)

    try:
        delete_graph_node(project_dir, "location", "loc_rooftop")
    except ValueError as exc:
        assert "still reference" in str(exc)
    else:
        raise AssertionError("Expected location deletion to fail while frames still reference it")


def test_rewind_manifest_phases_marks_requested_phase_ready(tmp_path: Path) -> None:
    _, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Project One",
                "phases": {f"phase_{idx}": {"status": "complete"} for idx in range(7)},
            },
            indent=2,
        ),
    )

    rewind_manifest_phases(project_dir, 2, "creative_output_updated", source="test")

    manifest = json.loads((project_dir / "project_manifest.json").read_text(encoding="utf-8"))
    assert manifest["phases"]["phase_1"]["status"] == "complete"
    assert manifest["phases"]["phase_2"]["status"] == "ready"
    assert manifest["phases"]["phase_3"]["status"] == "pending"
    assert manifest["phases"]["phase_6"]["status"] == "pending"


def test_mark_project_file_change_rewinds_preproduction_for_creative_output_updates(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Project One",
                "phases": {f"phase_{idx}": {"status": "complete"} for idx in range(7)},
                "frames": [{"frameId": "f_001"}],
            },
            indent=2,
        ),
    )
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "approvals": {
                    "skeletonApprovedAt": "2026-04-13T07:00:00Z",
                    "referencesApprovedAt": "2026-04-13T07:05:00Z",
                    "timelineApprovedAt": "2026-04-13T07:10:00Z",
                    "videoApprovedAt": "2026-04-13T07:15:00Z",
                }
            },
            indent=2,
        ),
    )

    mark_project_file_change(project_dir, "creative_output/creative_output.md", source="test")

    state = load_workspace_state(project_dir)
    manifest = json.loads((project_dir / "project_manifest.json").read_text(encoding="utf-8"))
    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert "skeletonApprovedAt" not in state["approvals"]
    assert "referencesApprovedAt" not in state["approvals"]
    assert "timelineApprovedAt" not in state["approvals"]
    assert "videoApprovedAt" not in state["approvals"]
    assert set(state["pipelineInvalidations"]) >= {"phase_2", "phase_3", "phase_4", "phase_5"}
    assert manifest["phases"]["phase_2"]["status"] == "ready"
    assert manifest["phases"]["phase_3"]["status"] == "pending"
    assert snapshot["project"]["status"] == "skeleton_review"


def test_mark_project_file_change_for_graph_artifacts_only_invalidates_downstream(tmp_path: Path) -> None:
    _, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:05:00Z",
                    "timelineApprovedAt": "2026-04-13T07:10:00Z",
                    "videoApprovedAt": "2026-04-13T07:15:00Z",
                }
            },
            indent=2,
        ),
    )

    mark_project_file_change(project_dir, "graph/narrative_graph.json", source="test")

    state = load_workspace_state(project_dir)
    assert state["approvals"]["referencesApprovedAt"] == "2026-04-13T07:05:00Z"
    assert "timelineApprovedAt" not in state["approvals"]
    assert "videoApprovedAt" not in state["approvals"]
    assert set(state["pipelineInvalidations"]) >= {"phase_4", "phase_5"}


def test_workspace_snapshot_prefers_dirty_preproduction_over_existing_complete_artifacts(tmp_path: Path) -> None:
    project_id, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "project_manifest.json",
        json.dumps(
            {
                "projectName": "Project One",
                "phases": {f"phase_{idx}": {"status": "complete"} for idx in range(7)},
                "frames": [
                    {
                        "frameId": "f_001",
                        "generatedImagePath": "frames/composed/f_001_gen.png",
                        "videoPath": "video/clips/f_001.mp4",
                    }
                ],
            },
            indent=2,
        ),
    )
    (project_dir / "frames" / "composed").mkdir(parents=True, exist_ok=True)
    (project_dir / "frames" / "composed" / "f_001_gen.png").write_bytes(b"png")
    (project_dir / "video" / "clips").mkdir(parents=True, exist_ok=True)
    (project_dir / "video" / "clips" / "f_001.mp4").write_bytes(b"mp4")
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "pipelineInvalidations": {
                    "phase_2": {"phase": 2, "dirtyAt": "2026-04-13T07:00:00Z"},
                    "phase_3": {"phase": 3, "dirtyAt": "2026-04-13T07:00:01Z"},
                }
            },
            indent=2,
        ),
    )

    snapshot = build_workspace_snapshot(project_id, project_dir)

    assert snapshot["project"]["status"] == "skeleton_review"


def test_clear_pipeline_invalidations_preserves_newer_dirty_state(tmp_path: Path) -> None:
    _, project_dir = _make_project(tmp_path)
    _write(
        project_dir / "logs" / "ui_workspace_state.json",
        json.dumps(
            {
                "pipelineInvalidations": {
                    "phase_4": {
                        "phase": 4,
                        "dirtyAt": "2026-04-13T07:15:00+00:00",
                        "reason": "graph_downstream_updated",
                    },
                    "phase_5": {
                        "phase": 5,
                        "dirtyAt": "2026-04-13T07:05:00+00:00",
                        "reason": "video_prompt_updated",
                    },
                }
            },
            indent=2,
        ),
    )

    clear_pipeline_invalidations(
        project_dir,
        4,
        5,
        not_after=datetime(2026, 4, 13, 7, 10, tzinfo=timezone.utc),
    )

    invalidations = load_workspace_state(project_dir)["pipelineInvalidations"]
    assert "phase_4" in invalidations
    assert "phase_5" not in invalidations

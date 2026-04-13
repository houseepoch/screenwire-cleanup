from __future__ import annotations

import json
from pathlib import Path

from workspace_api import (
    append_ui_event,
    build_workspace_snapshot,
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

    assert snapshot["reports"]["greenlightReport"] == "/api/project/file/logs/pipeline/ui_phase_report.json"
    assert snapshot["reports"]["uiPhaseReport"] == "/api/project/file/logs/pipeline/ui_phase_report.json"
    assert snapshot["reports"]["projectCover"] == "/api/project/file/reports/project_cover.png"

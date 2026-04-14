from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


def _load_server(monkeypatch, project_dir: Path):
    monkeypatch.setenv("PROJECT_DIR", str(project_dir))
    sys.modules.pop("server", None)
    import server

    return importlib.reload(server)


def test_build_pipeline_job_command_uses_resume_through_phase(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    command = server._build_pipeline_job_command(3)

    assert command[0] == sys.executable
    assert command[1].endswith("run_pipeline.py")
    assert command[2:] == [
        "--project",
        tmp_path.name,
        "--resume",
        "--through-phase",
        "3",
        "--live",
    ]


def test_build_pipeline_job_command_runs_frame_generation_phase_directly(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    command = server._build_pipeline_job_command(4)

    assert command[0] == sys.executable
    assert command[1].endswith("run_pipeline.py")
    assert command[2:] == [
        "--project",
        tmp_path.name,
        "--phase",
        "4",
        "--live",
    ]


def test_next_pipeline_target_prefers_preproduction_until_phase_three_complete(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    manifest = {
        "phases": {
            "phase_0": {"status": "complete"},
            "phase_1": {"status": "complete"},
            "phase_2": {"status": "complete"},
            "phase_3": {"status": "ready"},
        }
    }

    assert server._next_pipeline_target(manifest, {}) == ("preproduction_build", 3)


def test_next_pipeline_target_uses_approvals_for_later_resume_jobs(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    manifest = {
        "phases": {
            "phase_0": {"status": "complete"},
            "phase_1": {"status": "complete"},
            "phase_2": {"status": "complete"},
            "phase_3": {"status": "complete"},
            "phase_4": {"status": "ready"},
            "phase_5": {"status": "ready"},
        }
    }

    assert server._next_pipeline_target(manifest, {"referencesApprovedAt": "2026-04-13T07:00:00Z"}) == (
        "frame_generation",
        4,
    )
    assert server._next_pipeline_target(
        manifest | {"phases": manifest["phases"] | {"phase_4": {"status": "complete"}}},
        {"referencesApprovedAt": "2026-04-13T07:00:00Z", "timelineApprovedAt": "2026-04-13T07:05:00Z"},
    ) == ("video_generation", 5)


def test_next_pipeline_target_repairs_incomplete_frames_before_video(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    manifest = {
        "phases": {
            "phase_0": {"status": "complete"},
            "phase_1": {"status": "complete"},
            "phase_2": {"status": "complete"},
            "phase_3": {"status": "complete"},
            "phase_4": {"status": "complete"},
            "phase_5": {"status": "complete"},
        }
    }

    assert server._next_pipeline_target(
        manifest,
        {"referencesApprovedAt": "2026-04-13T07:00:00Z", "timelineApprovedAt": "2026-04-13T07:05:00Z"},
        {"expectedFrameCount": 4, "composedFrameCount": 3, "clipCount": 0},
    ) == ("frame_generation", 4)


def test_next_pipeline_target_repairs_incomplete_clips_even_after_phase_complete(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    manifest = {
        "phases": {
            "phase_0": {"status": "complete"},
            "phase_1": {"status": "complete"},
            "phase_2": {"status": "complete"},
            "phase_3": {"status": "complete"},
            "phase_4": {"status": "complete"},
            "phase_5": {"status": "complete"},
        }
    }

    assert server._next_pipeline_target(
        manifest,
        {"referencesApprovedAt": "2026-04-13T07:00:00Z", "timelineApprovedAt": "2026-04-13T07:05:00Z"},
        {"expectedFrameCount": 4, "composedFrameCount": 4, "clipCount": 3},
    ) == ("video_generation", 5)


def test_next_pipeline_target_replays_dirty_frame_phase_even_when_artifacts_exist(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    manifest = {
        "phases": {
            "phase_0": {"status": "complete"},
            "phase_1": {"status": "complete"},
            "phase_2": {"status": "complete"},
            "phase_3": {"status": "complete"},
            "phase_4": {"status": "complete"},
            "phase_5": {"status": "complete"},
        }
    }

    assert server._next_pipeline_target(
        manifest,
        {"referencesApprovedAt": "2026-04-13T07:00:00Z"},
        {"expectedFrameCount": 4, "composedFrameCount": 4, "clipCount": 4},
        {"phase_4": {"phase": 4}},
    ) == ("frame_generation", 4)


def test_next_pipeline_target_replays_dirty_preproduction_when_phase_three_is_invalidated(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    manifest = {
        "phases": {
            "phase_0": {"status": "complete"},
            "phase_1": {"status": "complete"},
            "phase_2": {"status": "complete"},
            "phase_3": {"status": "complete"},
            "phase_4": {"status": "complete"},
            "phase_5": {"status": "complete"},
        }
    }

    assert server._next_pipeline_target(
        manifest,
        {},
        {"expectedFrameCount": 4, "composedFrameCount": 4, "clipCount": 4},
        {"phase_2": {"phase": 2}, "phase_3": {"phase": 3}},
    ) == ("preproduction_build", 3)


def test_next_pipeline_target_replays_dirty_video_phase_even_when_clips_exist(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    manifest = {
        "phases": {
            "phase_0": {"status": "complete"},
            "phase_1": {"status": "complete"},
            "phase_2": {"status": "complete"},
            "phase_3": {"status": "complete"},
            "phase_4": {"status": "complete"},
            "phase_5": {"status": "complete"},
        }
    }

    assert server._next_pipeline_target(
        manifest,
        {"referencesApprovedAt": "2026-04-13T07:00:00Z", "timelineApprovedAt": "2026-04-13T07:05:00Z"},
        {"expectedFrameCount": 4, "composedFrameCount": 4, "clipCount": 4},
        {"phase_5": {"phase": 5}},
    ) == ("video_generation", 5)


def test_ensure_pipeline_catchup_respawns_frame_generation_when_artifacts_are_partial(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    (tmp_path / "project_manifest.json").write_text(
        json.dumps(
            {
                "projectName": "Auto Heal",
                "phases": {
                    "phase_0": {"status": "complete"},
                    "phase_1": {"status": "complete"},
                    "phase_2": {"status": "complete"},
                    "phase_3": {"status": "complete"},
                    "phase_4": {"status": "complete"},
                    "phase_5": {"status": "complete"},
                },
                "frames": [
                    {"frameId": "f_001", "generatedImagePath": "frames/composed/f_001_gen.png"},
                    {"frameId": "f_002"},
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    workspace_state = tmp_path / "logs" / "ui_workspace_state.json"
    workspace_state.parent.mkdir(parents=True, exist_ok=True)
    workspace_state.write_text(
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:00:00Z",
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    frame_path = tmp_path / "frames" / "composed" / "f_001_gen.png"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"png")

    calls: list[tuple[str, int, dict[str, object]]] = []

    async def _fake_spawn(job_name: str, target_phase: int, **kwargs):
        calls.append((job_name, target_phase, kwargs))
        return {}

    monkeypatch.setattr(server, "_spawn_pipeline_phase_job", _fake_spawn)
    server.ui_pipeline_jobs.clear()

    asyncio.run(server._ensure_pipeline_catchup(tmp_path.name))

    assert calls
    assert calls[0][0:2] == ("frame_generation", 4)
    assert calls[0][2]["prelaunch_message"] == "Applying review updates before frame generation..."


def test_project_scoped_file_route_serves_assets_for_active_project(tmp_path: Path, monkeypatch) -> None:
    asset_path = tmp_path / "cast" / "composites" / "cast_001_ref.png"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    server = _load_server(monkeypatch, tmp_path)
    client = TestClient(server.app)

    response = client.get(f"/api/projects/{tmp_path.name}/file/cast/composites/cast_001_ref.png")

    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


def test_project_scoped_thumbnail_route_serves_resized_derivative(tmp_path: Path, monkeypatch) -> None:
    asset_path = tmp_path / "frames" / "composed" / "f_001_gen.png"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 900), color=(32, 54, 88)).save(asset_path, "PNG")

    server = _load_server(monkeypatch, tmp_path)
    client = TestClient(server.app)

    response = client.get(f"/api/projects/{tmp_path.name}/thumbnail/frames/composed/f_001_gen.png?w=320&h=180&format=png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_normalize_video_request_duration_rounds_up_to_whole_seconds(tmp_path: Path, monkeypatch) -> None:
    server = _load_server(monkeypatch, tmp_path)

    assert server._normalize_video_request_duration(2.0) == 2
    assert server._normalize_video_request_duration(2.1) == 3
    assert server._normalize_video_request_duration(14.2) == 15
    assert server._normalize_video_request_duration(0.5) == 2


def test_spawn_pipeline_job_preserves_invalidations_created_after_start(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "project_manifest.json").write_text(
        json.dumps(
            {
                "projectName": "Mid Run Dirty",
                "phases": {f"phase_{idx}": {"status": "complete"} for idx in range(7)},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    workspace_state = tmp_path / "logs" / "ui_workspace_state.json"
    workspace_state.parent.mkdir(parents=True, exist_ok=True)
    workspace_state.write_text(json.dumps({"approvals": {}}, indent=2), encoding="utf-8")

    server = _load_server(monkeypatch, tmp_path)
    hold = asyncio.Event()
    catchup_calls: list[str] = []

    class _FakeProc:
        def __init__(self):
            self.returncode = None

        async def communicate(self):
            await hold.wait()
            self.returncode = 0
            return (b"", b"")

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProc()

    async def _fake_ensure(project_id: str):
        catchup_calls.append(project_id)

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(server, "_job_checkpoint_progress", lambda _target_phase: (4, 64))
    monkeypatch.setattr(server, "_ensure_pipeline_catchup", _fake_ensure)
    server.ui_pipeline_jobs.clear()

    async def _exercise() -> None:
        await server._spawn_pipeline_phase_job("frame_generation", 4)
        await asyncio.sleep(0.05)
        state = json.loads(workspace_state.read_text(encoding="utf-8"))
        state["pipelineInvalidations"] = {
            "phase_4": {
                "phase": 4,
                "dirtyAt": "9999-12-31T23:59:59+00:00",
                "reason": "graph_downstream_updated",
            }
        }
        workspace_state.write_text(json.dumps(state, indent=2), encoding="utf-8")
        hold.set()
        for _ in range(20):
            if catchup_calls:
                break
            await asyncio.sleep(0.05)

    asyncio.run(_exercise())

    state = json.loads(workspace_state.read_text(encoding="utf-8"))
    assert "phase_4" in state["pipelineInvalidations"]
    assert catchup_calls == [tmp_path.name]


def test_ensure_pipeline_catchup_starts_video_preflight_repair_for_invalid_timeline_approval(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "project_manifest.json").write_text(
        json.dumps(
            {
                "projectName": "Video Gate Reset",
                "phases": {
                    "phase_0": {"status": "complete"},
                    "phase_1": {"status": "complete"},
                    "phase_2": {"status": "complete"},
                    "phase_3": {"status": "complete"},
                    "phase_4": {"status": "complete"},
                    "phase_5": {"status": "ready"},
                },
                "frames": [
                    {"frameId": "f_001", "generatedImagePath": "frames/composed/f_001_gen.png"},
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    workspace_state = tmp_path / "logs" / "ui_workspace_state.json"
    workspace_state.parent.mkdir(parents=True, exist_ok=True)
    workspace_state.write_text(
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:00:00Z",
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    frame_path = tmp_path / "frames" / "composed" / "f_001_gen.png"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"png")

    server = _load_server(monkeypatch, tmp_path)
    phase_calls: list[tuple[str, int, dict[str, object]]] = []
    repair_calls: list[str] = []

    async def _fake_spawn(job_name: str, target_phase: int, **kwargs):
        phase_calls.append((job_name, target_phase, kwargs))
        return {}

    async def _fake_repair():
        repair_calls.append("repair")
        return {}

    monkeypatch.setattr(server, "_spawn_pipeline_phase_job", _fake_spawn)
    monkeypatch.setattr(server, "_spawn_video_preflight_repair_job", _fake_repair)
    monkeypatch.setattr(
        server,
        "_video_generation_preflight_error",
        lambda: "f_001: incomplete shot packet for video prompt assembly; missing shot, angle, movement",
    )
    server.ui_pipeline_jobs.clear()

    asyncio.run(server._ensure_pipeline_catchup(tmp_path.name))

    state = json.loads(workspace_state.read_text(encoding="utf-8"))
    approvals = state["approvals"]
    assert approvals["timelineApprovedAt"] == "2026-04-13T07:05:00Z"
    assert "pipelineInvalidations" not in state
    assert repair_calls == ["repair"]
    assert phase_calls == []


def test_clear_invalid_video_generation_state_can_preserve_timeline_approval(
    tmp_path: Path, monkeypatch
) -> None:
    workspace_state = tmp_path / "logs" / "ui_workspace_state.json"
    workspace_state.parent.mkdir(parents=True, exist_ok=True)
    workspace_state.write_text(
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:00:00Z",
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    server = _load_server(monkeypatch, tmp_path)

    asyncio.run(
        server._clear_invalid_video_generation_state(
            "repair failed",
            source="video_preflight_repair",
            preserve_approval=True,
        )
    )

    state = json.loads(workspace_state.read_text(encoding="utf-8"))
    assert state["approvals"]["timelineApprovedAt"] == "2026-04-13T07:05:00Z"
    assert state["pipelineInvalidations"]["phase_5"]["reason"] == "video_generation_preflight_failed"
    assert state["pipelineInvalidations"]["phase_5"]["source"] == "video_preflight_repair"


def test_workspace_snapshot_reports_video_generation_when_preflight_repair_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    server = _load_server(monkeypatch, tmp_path)
    server.ui_pipeline_jobs.clear()
    server.ui_pipeline_jobs["video_preflight_repair"] = {
        "id": "video_preflight_repair",
        "name": "Video Prep Repair",
        "status": "running",
        "progress": 12,
        "message": "Repairing timeline direction on 35 frame(s)...",
        "process": None,
        "phaseNumbers": [5],
        "activePhase": 5,
        "targetPhase": 5,
    }

    monkeypatch.setattr(
        server,
        "build_workspace_snapshot",
        lambda project_id, _project_dir: {
            "project": {
                "id": project_id,
                "name": "Video Resume",
                "status": "timeline_review",
                "progress": 82,
            }
        },
    )

    snapshot = server._workspace_snapshot(tmp_path.name)

    assert snapshot["project"]["status"] == "generating_video"
    assert snapshot["project"]["progress"] == 82


def test_cancel_ui_pipeline_job_clears_timeline_approval_and_removes_worker(
    tmp_path: Path, monkeypatch
) -> None:
    workspace_state = tmp_path / "logs" / "ui_workspace_state.json"
    workspace_state.parent.mkdir(parents=True, exist_ok=True)
    workspace_state.write_text(
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:00:00Z",
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    server = _load_server(monkeypatch, tmp_path)
    job = {
        "id": "video_generation",
        "name": "Video Generation",
        "status": "running",
        "progress": 83,
        "message": "Generating approved video clips...",
        "process": None,
        "task": None,
        "targetPhase": 5,
    }
    server.ui_pipeline_jobs.clear()
    server.ui_pipeline_jobs["video_generation"] = job

    asyncio.run(server._cancel_ui_pipeline_job(job))

    state = json.loads(workspace_state.read_text(encoding="utf-8"))
    assert "timelineApprovedAt" not in state["approvals"]
    assert state["pipelineInvalidations"]["phase_5"]["reason"] == "stopped_by_user"
    assert "video_generation" not in server.ui_pipeline_jobs


def test_cancel_project_workers_route_stops_running_video_generation(tmp_path: Path, monkeypatch) -> None:
    workspace_state = tmp_path / "logs" / "ui_workspace_state.json"
    workspace_state.parent.mkdir(parents=True, exist_ok=True)
    workspace_state.write_text(
        json.dumps(
            {
                "approvals": {
                    "referencesApprovedAt": "2026-04-13T07:00:00Z",
                    "timelineApprovedAt": "2026-04-13T07:05:00Z",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (tmp_path / "project_manifest.json").write_text(
        json.dumps({"projectName": "Stop Route", "phases": {}, "frames": []}, indent=2),
        encoding="utf-8",
    )

    server = _load_server(monkeypatch, tmp_path)
    server.ui_pipeline_jobs.clear()
    server.ui_pipeline_jobs["video_generation"] = {
        "id": "video_generation",
        "name": "Video Generation",
        "status": "running",
        "progress": 83,
        "message": "Generating approved video clips...",
        "process": None,
        "task": None,
        "targetPhase": 5,
    }
    client = TestClient(server.app)

    response = client.post(f"/api/projects/{tmp_path.name}/workers/cancel")

    assert response.status_code == 200
    assert response.json()["cancelled"] == ["video_generation"]
    state = json.loads(workspace_state.read_text(encoding="utf-8"))
    assert "timelineApprovedAt" not in state["approvals"]


def test_timeline_approval_starts_video_preflight_repair_when_direction_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "project_manifest.json").write_text(
        json.dumps(
            {
                "projectName": "Timeline Approval Guard",
                "phases": {
                    "phase_0": {"status": "complete"},
                    "phase_1": {"status": "complete"},
                    "phase_2": {"status": "complete"},
                    "phase_3": {"status": "complete"},
                    "phase_4": {"status": "complete"},
                    "phase_5": {"status": "ready"},
                },
                "frames": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    server = _load_server(monkeypatch, tmp_path)
    repair_calls: list[str] = []

    async def _fake_repair():
        repair_calls.append("repair")
        return {}

    monkeypatch.setattr(server, "_spawn_video_preflight_repair_job", _fake_repair)
    monkeypatch.setattr(
        server,
        "_video_generation_preflight_error",
        lambda: "f_001: incomplete shot packet for video prompt assembly; missing shot, angle, movement",
    )
    client = TestClient(server.app)

    response = client.post(f"/api/projects/{tmp_path.name}/approve", json={"gate": "timeline"})

    assert response.status_code == 200
    body = response.json()
    assert "timelineApprovedAt" in body["workflow"]["approvals"]
    assert repair_calls == ["repair"]

    state = json.loads((tmp_path / "logs" / "ui_workspace_state.json").read_text(encoding="utf-8"))
    assert "timelineApprovedAt" in state["approvals"]

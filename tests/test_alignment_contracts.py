from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import graph.api
import graph.continuity_validator
import graph.store
from graph.api import get_frame_context
from graph.continuity_validator import validate_continuity
from graph.frame_prompt_refiner import refine_video_prompt
from graph.materializer import materialize_manifest
from graph.reference_collector import ReferenceImageCollector
from graph.store import GraphStore
import run_pipeline
from run_pipeline import (
    _run_phase_2_postprocessing,
    _resolve_regen_image_size,
    phase_5_video,
    quality_gate_phase_1,
    quality_gate_phase_2,
    quality_gate_phase_5,
)
from tests.test_pipeline_smoke_e2e import build_live_smoke_graph


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_materialize_manifest_preserves_runtime_frame_fields(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    manifest_path = tmp_path / "project_manifest.json"
    _write_json(
        manifest_path,
        {
            "version": 7,
            "status": "phase_4_ready",
            "phases": {"phase_4": {"status": "ready"}},
            "frames": [
                {
                    "frameId": "f_002",
                    "compositionVersion": 3,
                    "videoDuration": 9,
                    "customField": "keep-me",
                }
            ],
        },
    )

    materialize_manifest(graph, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frame = next(item for item in manifest["frames"] if item["frameId"] == "f_002")

    assert frame["compositionVersion"] == 3
    assert frame["videoDuration"] == 9
    assert frame["customField"] == "keep-me"
    assert frame["castBibleSnapshot"]["frame_id"] == "f_002"
    assert manifest["status"] == "phase_4_ready"
    assert manifest["phases"]["phase_4"]["status"] == "ready"
    assert manifest["castBible"]["characterCount"] == 1


def test_graph_store_persists_versioned_cast_bible(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    store = GraphStore(tmp_path)

    ReferenceImageCollector(graph, tmp_path).sync_cast_bible(
        store=store,
        sequence_id=graph.project.project_id,
    )
    loaded = store.load_latest_cast_bible(sequence_id=graph.project.project_id)

    assert loaded is not None
    assert loaded.characters["cast_nova"].pose_for_frame("f_002") is not None
    assert (tmp_path / "graph" / "cast_bible" / "latest.json").exists()
    assert list((tmp_path / "graph" / "cast_bible" / "versions").glob("cast_bible_*.json"))


def test_validate_continuity_uses_registry_backed_cast_states(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    GraphStore(tmp_path).save(graph)
    ReferenceImageCollector(graph, tmp_path).sync_cast_bible(
        store=GraphStore(tmp_path),
        sequence_id=graph.project.project_id,
    )

    issues = validate_continuity(graph, fix=False, project_dir=tmp_path)

    assert isinstance(issues, list)


def test_frame_context_returns_registry_state_for_all_entities(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)

    graph.cast_frame_states["cast_nova@f_002"].emotion = "registry-cast"
    graph.prop_frame_states["prop_signal_pager@f_002"].condition = "registry-prop"
    graph.location_frame_states["loc_rooftop@f_002"].lighting_override = "registry-location"

    context = get_frame_context(graph, "f_002")

    assert context["cast_states"][0]["emotion"] == "registry-cast"
    assert context["prop_states"][0]["condition"] == "registry-prop"
    assert context["location_state"]["lighting_override"] == "registry-location"


def test_quality_gate_phase_5_flags_missing_video_prompt_json(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "project_manifest.json",
        {"frames": [{"frameId": "f_001"}]},
    )
    clip_path = tmp_path / "video" / "clips" / "f_001.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"0" * 12000)

    issues = quality_gate_phase_5(tmp_path)

    assert any("missing video prompt JSON" in issue for issue in issues)


def test_quality_gate_phase_1_allows_single_scene_short_project(tmp_path: Path) -> None:
    _write_json(tmp_path / "source_files" / "onboarding_config.json", {"outputSize": "short"})
    creative_dir = tmp_path / "creative_output"
    creative_dir.mkdir(parents=True, exist_ok=True)
    (creative_dir / "creative_output.md").write_text("x" * 6000, encoding="utf-8")
    (creative_dir / "outline_skeleton.md").write_text("# Outline\n", encoding="utf-8")
    scenes_dir = creative_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    (scenes_dir / "scene_01.md").write_text("One scene.\n", encoding="utf-8")

    issues = quality_gate_phase_1(tmp_path)

    assert not issues


def test_phase_1_resume_skips_agents_when_scene_drafts_already_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    creative_dir = tmp_path / "creative_output"
    scenes_dir = creative_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    (creative_dir / "outline_skeleton.md").write_text(
        "# Outline\n\nSCENE 1\n\nSCENE 2\n",
        encoding="utf-8",
    )
    (scenes_dir / "scene_01_draft.md").write_text(
        ("/// SCENE 1\n" + "alpha beat\n") * 40,
        encoding="utf-8",
    )
    (scenes_dir / "scene_02_draft.md").write_text(
        ("/// SCENE 2\n" + "beta beat\n") * 40,
        encoding="utf-8",
    )

    calls: list[str] = []

    def fake_run_agent(agent_id, *_args, **_kwargs):
        calls.append(agent_id)
        return subprocess.CompletedProcess(["fake"], 0, stdout="", stderr="")

    monkeypatch.setattr(run_pipeline, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(run_pipeline, "PIPELINE_LOGS_DIR", tmp_path / "logs" / "pipeline")
    monkeypatch.setattr(run_pipeline, "run_agent", fake_run_agent)
    monkeypatch.setattr(run_pipeline, "run_quality_gate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(run_pipeline, "advance_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_pipeline, "save_phase_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_pipeline, "verify_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_pipeline, "list_dir_files", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        run_pipeline,
        "collect_files_in",
        lambda directory: sorted(p for p in directory.rglob("*") if p.is_file()),
    )

    phase_timers: dict[str, object] = {}
    run_pipeline.phase_1_narrative(False, phase_timers)

    creative_output = (creative_dir / "creative_output.md").read_text(encoding="utf-8")
    checkpoint = json.loads(
        (tmp_path / "logs" / "pipeline" / "phase_1_checkpoint.json").read_text(encoding="utf-8")
    )

    assert calls == []
    assert "/// SCENE 1" in creative_output
    assert "/// SCENE 2" in creative_output
    assert checkpoint["stage"] == "assembly_complete"
    assert checkpoint["missing_scene_count"] == 0


def test_parse_args_accepts_live_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--project", "demo_project", "--live"],
    )

    args = run_pipeline.parse_args()

    assert args.project == "demo_project"
    assert args.live is True


def test_haiku_cli_worker_uses_unlimited_agent_timeout(tmp_path: Path, monkeypatch) -> None:
    timeout_seen: list[object] = []
    stream_output_seen: list[object] = []

    def fake_run_agent(_agent_id, _prompt_file, **kwargs):
        timeout_seen.append(kwargs.get("timeout"))
        stream_output_seen.append(kwargs.get("stream_output"))
        return subprocess.CompletedProcess(["fake"], 0, stdout="{}", stderr="")

    monkeypatch.setattr(run_pipeline, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(run_pipeline, "run_agent", fake_run_agent)
    monkeypatch.setattr(run_pipeline, "check_agent_result", lambda *_args, **_kwargs: None)

    result = run_pipeline._run_haiku_cli_worker({"frame_id": "f_001"}, dry_run=False)

    assert result["frame_id"] == "f_001"
    assert timeout_seen == [None]
    assert stream_output_seen == [False]


def test_quality_gate_phase_2_allows_single_protagonist_short_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    frames = [
        {"frameId": f"f_{idx:03d}", "castIds": ["cast_001"], "locationId": "loc_001"}
        for idx in range(1, 11)
    ]
    _write_json(
        tmp_path / "project_manifest.json",
        {"frames": frames, "cast": [{"castId": "cast_001", "role": "protagonist"}]},
    )
    _write_json(tmp_path / "source_files" / "onboarding_config.json", {"outputSize": "short"})
    _write_json(tmp_path / "dialogue.json", {"lines": ["a", "b", "c"]})
    _write_json(tmp_path / "cast" / "cast_001.json", {"role": "protagonist"})
    _write_json(tmp_path / "locations" / "loc_001.json", {"name": "Roof"})
    _write_json(tmp_path / "graph" / "narrative_graph.json", {"version": 1})

    class FakeStore:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def load(self):
            return SimpleNamespace(frame_order=[], frames={}, scenes={}, dialogue={})

    monkeypatch.setattr(graph.store, "GraphStore", FakeStore)
    monkeypatch.setattr(graph.api, "get_frame_cast_state_models", lambda *_args, **_kwargs: [])

    issues = quality_gate_phase_2(tmp_path)

    assert not any("cast profile" in issue for issue in issues)


def test_resolve_regen_image_size_prefers_current_key_and_rejects_conflicts(tmp_path: Path) -> None:
    prompt_file = tmp_path / "cast_001_composite.json"

    assert _resolve_regen_image_size(
        {"size": "portrait_9_16", "image_size": "portrait_9_16"},
        prompt_file=prompt_file,
    ) == "portrait_9_16"

    try:
        _resolve_regen_image_size(
            {"size": "portrait_9_16", "image_size": "landscape_16_9"},
            prompt_file=prompt_file,
        )
    except ValueError as exc:
        assert "conflicting size keys" in str(exc)
    else:
        raise AssertionError("Expected conflicting size keys to raise ValueError")


def test_phase_5_video_skips_refinement_without_xai_key(tmp_path: Path, monkeypatch) -> None:
    _write_json(tmp_path / "project_manifest.json", {"frames": [{"frameId": "f_001"}]})
    image_path = tmp_path / "frames" / "composed" / "f_001_gen.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")
    prompt_path = tmp_path / "video" / "prompts" / "f_001_video.json"
    _write_json(
        prompt_path,
        {
            "frame_id": "f_001",
            "prompt": "graph prompt",
            "duration": 5,
            "input_image_path": "frames/composed/f_001_gen.png",
        },
    )

    recorded: list[str] = []

    monkeypatch.setattr(run_pipeline, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(run_pipeline, "MANIFEST_PATH", tmp_path / "project_manifest.json")
    monkeypatch.setattr(run_pipeline, "collect_files_in", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(run_pipeline, "list_dir_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_pipeline, "save_phase_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_pipeline, "run_quality_gate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(run_pipeline, "advance_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        run_pipeline,
        "_generate_video_clip",
        lambda _fid, _img, prompt, _dur, _out, _dry: recorded.append(prompt) or SimpleNamespace(returncode=0),
    )
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    phase_5_video(False, {})

    assert recorded == ["graph prompt"]
    prompt_data = json.loads(prompt_path.read_text(encoding="utf-8"))
    assert prompt_data["prompt"] == "graph prompt"


def test_refine_video_prompt_preserves_graph_prompt_on_overflow(tmp_path: Path, monkeypatch) -> None:
    image_path = tmp_path / "frames" / "composed" / "f_001_gen.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")

    async def fake_call(*_args, **_kwargs) -> str:
        return "x" * 5000

    monkeypatch.setattr("graph.frame_prompt_refiner._call_grok_vision", fake_call)

    result = asyncio.run(
        refine_video_prompt(
            {
                "frame_id": "f_001",
                "prompt": "graph prompt",
                "input_image_path": "frames/composed/f_001_gen.png",
            },
            tmp_path,
            api_key="token",
        )
    )

    assert result["refined_by"] == "failed:PromptOverflow"
    assert result["prompt"] == "graph prompt"


def test_prompt_size_migration_skill_audits_and_applies(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    prompt_dir = root / "demo" / "cast" / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)

    legacy = prompt_dir / "legacy.json"
    duplicate = prompt_dir / "duplicate.json"
    conflict = prompt_dir / "conflict.json"
    canonical = prompt_dir / "canonical.json"

    _write_json(legacy, {"prompt": "legacy", "image_size": "portrait_9_16", "out_path": "a.png"})
    _write_json(
        duplicate,
        {"prompt": "dup", "size": "portrait_9_16", "image_size": "portrait_9_16", "out_path": "b.png"},
    )
    _write_json(
        conflict,
        {"prompt": "conflict", "size": "portrait_9_16", "image_size": "landscape_16_9", "out_path": "c.png"},
    )
    _write_json(canonical, {"prompt": "ok", "size": "landscape_16_9", "out_path": "d.png"})

    skill_path = Path(__file__).resolve().parents[1] / "skills" / "sw_migrate_prompt_size_keys"
    audit_report = tmp_path / "audit.json"

    audit = subprocess.run(
        [sys.executable, str(skill_path), "--roots", str(root), "--report", str(audit_report)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert audit.returncode == 1
    audit_summary = json.loads(audit_report.read_text(encoding="utf-8"))
    assert audit_summary["legacy_only"] == 1
    assert audit_summary["duplicate_equal"] == 1
    assert audit_summary["conflicts"] == 1

    apply_report = tmp_path / "apply.json"
    applied = subprocess.run(
        [sys.executable, str(skill_path), "--roots", str(root), "--apply", "--report", str(apply_report)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert applied.returncode == 1
    apply_summary = json.loads(apply_report.read_text(encoding="utf-8"))
    assert apply_summary["migrated"] == 1
    assert apply_summary["cleaned_duplicates"] == 1
    assert json.loads(legacy.read_text(encoding="utf-8"))["size"] == "portrait_9_16"
    assert "image_size" not in json.loads(legacy.read_text(encoding="utf-8"))
    assert "image_size" not in json.loads(duplicate.read_text(encoding="utf-8"))
    assert json.loads(conflict.read_text(encoding="utf-8"))["image_size"] == "landscape_16_9"


def test_run_phase_2_postprocessing_halts_on_validator_failure(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_stream(cmd, **_kwargs):
        calls.append(cmd[1].split("/")[-1])
        return SimpleNamespace(returncode=1 if "graph_validate_video_direction" in cmd[1] else 0)

    monkeypatch.setattr(run_pipeline, "_stream_subprocess", fake_stream)

    try:
        _run_phase_2_postprocessing(tmp_path)
    except RuntimeError as exc:
        assert "video-direction validation failed" in str(exc)
    else:
        raise AssertionError("Expected Phase 2 post-processing to halt on validator failure")

    assert calls == [
        "graph_assemble_prompts",
        "graph_materialize",
        "graph_validate_video_direction",
    ]


def test_graph_validate_video_direction_rejects_sparse_composition_payload(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.frames["f_002"].composition.movement = None
    GraphStore(str(tmp_path)).save(graph)

    skill_path = Path(__file__).resolve().parents[1] / "skills" / "graph_validate_video_direction"
    result = subprocess.run(
        [sys.executable, str(skill_path), "--project-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "composition.movement is missing" in result.stdout


def test_graph_validate_video_direction_halts_on_overlong_dialogue(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    long_line = " ".join(["They are already moving through the stairwell and cutting every exit route now."] * 12)
    graph.dialogue["dlg_001"].line = long_line
    graph.dialogue["dlg_001"].raw_line = long_line
    GraphStore(str(tmp_path)).save(graph)

    skill_path = Path(__file__).resolve().parents[1] / "skills" / "graph_validate_video_direction"
    result = subprocess.run(
        [sys.executable, str(skill_path), "--project-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "[PIPELINE HALT]" in result.stdout
    assert "exceeds Grok model maximum of 15s" in result.stdout


def test_server_project_api_reads_manifest_from_disk(tmp_path: Path, monkeypatch) -> None:
    manifest_path = tmp_path / "project_manifest.json"
    _write_json(manifest_path, {"version": 1, "status": "fresh", "frames": [{"frameId": "f_001"}]})

    monkeypatch.setenv("PROJECT_DIR", str(tmp_path))
    sys.modules.pop("server", None)
    server = importlib.import_module("server")
    server.reconciler.manifest = {"version": 0, "status": "stale", "frames": []}

    data = asyncio.run(server.get_current_project())

    assert data["status"] == "fresh"
    assert data["version"] == 1
    assert len(data["frames"]) == 1

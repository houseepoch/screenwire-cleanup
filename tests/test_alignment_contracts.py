from __future__ import annotations

import asyncio
import base64
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest
import graph.api
import graph.cc_parser
import graph.continuity_validator
import graph.dialogue_validator
import graph.prompt_pair_validator
import graph.store
from handlers.cast_image import CastImageHandler
from handlers.location_grid import build_grid_prompt
from handlers.frame import FrameHandler
from handlers.video_clip import VideoClipHandler
from handlers.reference_pack import build_reference_pack
from handlers.base import _is_retryable_http_error, adapt_input_for_model, classify_replicate_error
from handlers.models import CastImageInput, FrameInput, VideoClipInput
from graph.api import build_shot_packet, build_storyboard_grids, get_frame_cast_state_models, get_frame_context
from graph.cc_parser import extract_cast_tags, parse_cc_output, parse_creative_output, parse_skeleton
from graph.continuity_validator import validate_continuity
from graph.dialogue_validator import validate_dialogue_project
from graph.frame_prompt_refiner import refine_video_prompt
from graph.frame_enricher import apply_frame_enrichment
from graph.materializer import materialize_manifest
from graph.prompt_pair_validator import PromptPairCategory, PromptPairValidator
from graph.reference_collector import ReferenceImageCollector, cast_bible_snapshot_for_frame
from graph.schema import CastFrameRole, CastFrameState, CastIdentity, CastNode, CastVoice, FrameNode, NarrativeRole, PoseState, Posture, ProjectNode, Provenance, SceneNode, TimeOfDay
from graph.store import GraphStore
import run_pipeline
from run_pipeline import (
    _frame_prompt_requires_sensitive_context,
    _agent_prompt_cache_key,
    _run_phase_2_postprocessing,
    _resolve_regen_image_size,
    phase_5_video,
    quality_gate_phase_1,
    quality_gate_phase_2,
    quality_gate_phase_4,
    quality_gate_phase_5,
)
from tests.live_smoke_graph import build_live_smoke_graph


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_materialize_manifest_preserves_runtime_frame_fields(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.project.creative_freedom = "creative"
    graph.project.creative_freedom_permission = "Permission sentence."
    graph.project.creative_freedom_failure_modes = "Failure mode sentence."
    graph.project.dialogue_policy = "Dialogue policy sentence."
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
    assert manifest["creativeFreedom"] == "creative"
    assert manifest["creativeFreedomPermission"] == "Permission sentence."
    assert manifest["creativeFreedomFailureModes"] == "Failure mode sentence."
    assert manifest["dialoguePolicy"] == "Dialogue policy sentence."
    assert "stickinessLevel" not in manifest


def test_build_context_seed_surfaces_creative_freedom_and_dialogue_workflow(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "project_manifest.json",
        {"projectId": "sw_test", "projectName": "Test"},
    )
    _write_json(
        tmp_path / "source_files" / "onboarding_config.json",
        {
            "projectName": "Test",
            "projectId": "sw_test",
            "creativeFreedom": "creative",
            "creativeFreedomPermission": "Permission sentence.",
            "creativeFreedomFailureModes": "Failure mode sentence.",
            "dialoguePolicy": "Dialogue policy sentence.",
            "dialogueWorkflow": {
                "enabled": True,
                "version": "grok-4.2-recovery-universal",
                "agents": [{"name": "extraction_recovery", "runsOn": "skeleton_load"}],
            },
        },
    )

    seed = run_pipeline.build_context_seed(tmp_path)

    assert "## Creative Freedom (Extracted)" in seed
    assert "- **Tier**: creative" in seed
    assert "Permission sentence." in seed
    assert "Failure mode sentence." in seed
    assert "## Dialogue Workflow (Extracted)" in seed
    assert "grok-4.2-recovery-universal" in seed
    assert "`extraction_recovery`" in seed


def test_cc_parser_extracts_per_frame_eyeline_and_facing_overrides() -> None:
    creative_text = """///SCENE: id=scene_01 | title=Test | location=loc_room | time_of_day=dusk | int_ext=INT | cast=cast_mei_lin,cast_min_zhu
/// cast:Mei Lin,Min Zhu | cam:north | looking_at:Mei Lin=Coin Pouch,Min Zhu=Room | facing_towards:Mei Lin=profile_left,Min Zhu=three_quarter_right
Mei studies the pouch while Min Zhu watches the room.
"""
    scenes = {
        "scene_01": SceneNode(
            scene_id="scene_01",
            scene_number=1,
            title="Test",
            location_id="loc_room",
            cast_present=["cast_mei_lin", "cast_min_zhu"],
        )
    }
    warnings: list[str] = []
    frames, cast_states = graph.cc_parser.extract_frame_markers(
        creative_text,
        scenes,
        {
            "mei lin": "cast_mei_lin",
            "min zhu": "cast_min_zhu",
            "coin pouch": "prop_coin_pouch",
            "room": "loc_room",
        },
        warnings,
    )

    assert len(frames) == 1
    state_map = {state.cast_id: state for state in cast_states}
    assert state_map["cast_mei_lin"].looking_at == "prop_coin_pouch"
    assert state_map["cast_min_zhu"].looking_at == "loc_room"
    assert state_map["cast_mei_lin"].facing_direction == "profile_left"
    assert state_map["cast_min_zhu"].facing_direction == "three_quarter_right"


def test_extract_dialogue_supports_single_src_line_ranges(tmp_path: Path) -> None:
    smoke_graph = build_live_smoke_graph(tmp_path)
    frames = [smoke_graph.frames[frame_id] for frame_id in smoke_graph.frame_order]
    warnings: list[str] = []

    dialogue = graph.cc_parser.extract_dialogue(
        "///DLG: speaker=Nova | cast_id=cast_nova | src_lines=2 | perf=hushed\n",
        "Action beat\nThey're early.\nAftermath beat\n",
        frames,
        {"nova": "cast_nova"},
        warnings,
    )

    assert dialogue[0].raw_line == "They're early."
    assert not any("no resolved raw_line" in warning for warning in warnings)


def test_shot_packet_single_subject_focus_drops_offscreen_listener(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    lyra = CastNode(
        cast_id="cast_lyra",
        name="Lyra",
        identity=CastIdentity(
            age_descriptor="30s",
            physical_description="focused listener with a guarded stare",
            wardrobe_description="dark jacket over a soft knit top",
        ),
        voice=CastVoice(),
        provenance=Provenance(source_prose_chunk="Lyra remains offscreen during Nova's clean single."),
    )
    graph.cast[lyra.cast_id] = lyra
    graph.cast_frame_states["cast_lyra@f_002"] = CastFrameState(
        cast_id=lyra.cast_id,
        frame_id="f_002",
        frame_role=CastFrameRole.SUBJECT,
        posture=Posture.STANDING,
        screen_position="frame_right",
        looking_at="cast_nova",
        provenance=Provenance(source_prose_chunk="Lyra is present in the room but should stay offscreen."),
    )
    graph.frames["f_002"].cinematic_tag.ai_prompt_language = (
        "Close-up of single person speaking, isolated framing, no other people visible, shallow depth of field, eye-level"
    )
    graph.frames["f_002"].cinematic_tag.definition = (
        "Clean Single — Eye Level. CU or MCU, eye-level, shallow DOF, subject centered or rule-of-thirds."
    )
    graph.frames["f_002"].action_summary = "Nova delivers the line in a clean single while Lyra stays offscreen."

    packet = build_shot_packet(graph, "f_002")

    assert packet.subject_count == 1
    assert packet.visible_cast_ids == ["cast_nova"]


def test_dialogue_validator_strict_flags_rewritten_line(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.project.creative_freedom = "strict"
    graph.project.creative_freedom_permission = "Strict permission."
    graph.project.dialogue_policy = "Word for word only."
    graph.dialogue["dlg_001"].raw_line = "They're early."
    graph.dialogue["dlg_001"].line = "They are early."

    store = GraphStore(tmp_path)
    store.save(graph)

    _write_json(
        tmp_path / "source_files" / "onboarding_config.json",
        {
            "projectName": "Pipeline Smoke Live",
            "projectId": "sw_pipeline_smoke_live",
            "creativeFreedom": "strict",
            "creativeFreedomPermission": "Strict permission.",
            "creativeFreedomFailureModes": "No drift.",
            "dialoguePolicy": "Word for word only.",
            "dialogueWorkflow": {"enabled": True, "version": "grok-4.2-recovery-universal"},
        },
    )
    _write_json(
        tmp_path / "video" / "prompts" / "f_002_video.json",
        {
            "frame_id": "f_002",
            "dialogue_present": True,
            "dialogue_line": "They are early.",
        },
    )

    report = validate_dialogue_project(tmp_path)

    assert report["status"] == "fail"
    assert any("Strict tier requires word-for-word dialogue." in issue["problem"] for issue in report["issues"])


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


def test_cast_bible_snapshot_excludes_future_pose_leakage(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    store = GraphStore(tmp_path)

    collector = ReferenceImageCollector(graph, tmp_path)
    collector.sync_cast_bible(store=store, sequence_id=graph.project.project_id)
    bible = store.load_latest_cast_bible(sequence_id=graph.project.project_id)

    assert bible is not None
    sheet = bible.characters["cast_nova"]
    early_pose = PoseState(
        pose="sitting_profile_left",
        frame_id="f_002",
        modifiers=["hands_on_laptop"],
    )
    late_pose = PoseState(
        pose="screams_unhinged_dialogue",
        frame_id="f_999",
        modifiers=["mouth_open", "leaning_forward"],
    )
    sheet.frame_poses["f_002"] = early_pose
    sheet.pose_history = [early_pose, late_pose]
    sheet.current_pose = late_pose

    snapshot = cast_bible_snapshot_for_frame(
        bible,
        graph,
        "f_002",
        ["cast_nova"],
    )

    assert snapshot is not None
    character = snapshot["characters"][0]
    assert character["pose"]["pose"] == "sitting_profile_left"
    history_frames = [entry["frame_id"] for entry in character["recent_pose_history"]]
    assert "f_999" not in history_frames
    assert all(int(frame.split("_")[-1]) <= 2 for frame in history_frames)


def test_reference_collector_omits_storyboard_cell_when_guidance_disabled(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    build_storyboard_grids(graph)

    grid = graph.storyboard_grids["grid_01"]
    grid.cell_image_dir = "frames/storyboards/grid_01/frames"
    legacy_cell = tmp_path / "frames" / "storyboards" / "grid_01" / "frames" / "f_001_cell.png"
    legacy_cell.parent.mkdir(parents=True, exist_ok=True)
    legacy_cell.write_bytes(b"legacy-cell")

    refs = ReferenceImageCollector(graph, tmp_path).get_frame_references("f_001")

    assert refs.storyboard_cell is None


def test_reference_collector_uses_shot_packet_visible_cast_when_frame_states_missing(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.cast_frame_states.clear()
    graph.frames["f_002"].composed_image_path = "frames/composed/f_002_gen.png"
    (tmp_path / "frames" / "composed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "frames" / "composed" / "f_001_gen.png").write_bytes(b"prev")
    (tmp_path / "cast" / "composites").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cast" / "composites" / "cast_nova_ref.png").write_bytes(b"cast-ref")
    (tmp_path / "locations" / "primary").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), color="white").save(
        tmp_path / "locations" / "primary" / "loc_rooftop.png"
    )

    refs = ReferenceImageCollector(graph, tmp_path).get_flat_reference_list("f_002")
    rels = [path.relative_to(tmp_path).as_posix() for path in refs]

    assert "cast/composites/cast_nova_ref.png" in rels


def test_reference_collector_extracts_location_variants_on_demand(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    (tmp_path / "locations" / "primary").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 600), color="white").save(
        tmp_path / "locations" / "primary" / "loc_rooftop.png"
    )

    refs = ReferenceImageCollector(graph, tmp_path).get_flat_reference_list("f_002")
    rels = [path.relative_to(tmp_path).as_posix() for path in refs]

    assert any(rel.startswith("locations/variants/loc_rooftop_") for rel in rels)
    assert (tmp_path / "locations" / "variants" / "loc_rooftop_north.png").exists()


def test_frame_enrichment_normalizes_list_ambient_motion(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)

    apply_frame_enrichment(
        graph,
        {
            "frame_id": "f_001",
            "environment": {
                "atmosphere": {
                    "ambient_motion": ["floor_undulation", "candle_flicker"],
                }
            },
        },
    )

    assert (
        graph.frames["f_001"].environment.atmosphere.ambient_motion
        == "floor_undulation, candle_flicker"
    )


def test_graph_store_save_coerces_legacy_list_ambient_motion(tmp_path: Path, recwarn) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.frames["f_001"].environment.atmosphere.ambient_motion = [
        "floor_undulation",
        "candle_flicker",
    ]

    store = GraphStore(tmp_path)
    graph_path = store.save(graph)
    raw = json.loads(graph_path.read_text(encoding="utf-8"))
    reloaded = store.load()

    assert raw["frames"]["f_001"]["environment"]["atmosphere"]["ambient_motion"] == (
        "floor_undulation, candle_flicker"
    )
    assert reloaded.frames["f_001"].environment.atmosphere.ambient_motion == (
        "floor_undulation, candle_flicker"
    )
    assert not recwarn


def test_replicate_502_is_retryable_and_transient() -> None:
    request = httpx.Request("POST", "https://api.replicate.com/v1/models/demo/predictions")
    response = httpx.Response(502, request=request)
    exc = httpx.HTTPStatusError("502 Bad Gateway", request=request, response=response)

    detail = classify_replicate_error("Server error '502 Bad Gateway'", "")

    assert _is_retryable_http_error(exc) is True
    assert detail["failure_type"] == "UPSTREAM_TRANSIENT"
    assert detail["is_retryable"] is True


def test_replicate_prediction_interrupted_is_transient() -> None:
    detail = classify_replicate_error(
        "Prediction interrupted; please retry (code: PA)",
        "",
    )

    assert detail["failure_type"] == "UPSTREAM_TRANSIENT"
    assert detail["is_retryable"] is True


def test_xai_image_rescue_caps_reference_count_and_writes_output(tmp_path: Path) -> None:
    ref_paths: list[Path] = []
    for idx in range(7):
        path = tmp_path / f"ref_{idx}.png"
        Image.new("RGB", (1600, 900), color=(20 * idx, 40, 80)).save(path)
        ref_paths.append(path)

    called: dict[str, object] = {}

    handler = FrameHandler(replicate_token="replicate-token", xai_key="xai-token")

    async def _fake_upload_many(paths: list[Path]) -> list[str]:
        called["uploaded_path"] = paths[0]
        return ["data:image/jpeg;base64,rescue"]

    class _FakeImageResponse:
        @property
        async def image(self) -> bytes:
            return b"rescued-image"

    class _FakeImageClient:
        async def sample(self, **kwargs):
            called["prompt"] = kwargs["prompt"]
            called["model"] = kwargs["model"]
            called["image_url"] = kwargs.get("image_url")
            called["image_urls"] = kwargs.get("image_urls")
            called["image_format"] = kwargs.get("image_format")
            called["aspect_ratio"] = kwargs.get("aspect_ratio")
            called["resolution"] = kwargs.get("resolution")
            return _FakeImageResponse()

    class _FakeXAIClient:
        def __init__(self):
            self.image = _FakeImageClient()

        async def close(self):
            return None

    handler.upload_many = _fake_upload_many  # type: ignore[method-assign]
    handler._xai_sdk_client = _FakeXAIClient()  # type: ignore[assignment]

    output_path = tmp_path / "rescued.png"
    prediction, model = asyncio.run(
        handler._try_xai_image_rescue(
            {
                "prompt": "rescued prompt",
                "aspect_ratio": "16:9",
                "resolution": "4K",
                "output_format": "png",
            },
            reference_paths=ref_paths,
            output_path=output_path,
            error_detail={"failure_type": "UPSTREAM_TRANSIENT"},
        )
    )

    assert isinstance(called["uploaded_path"], Path)
    assert called["uploaded_path"].name == "xai_rescue.jpg"
    assert called["image_url"] == "data:image/jpeg;base64,rescue"
    assert called["image_urls"] is None
    assert called["model"] == "grok-imagine-image-pro"
    assert called["image_format"] == "base64"
    assert called["aspect_ratio"] == "16:9"
    assert called["resolution"] == "2k"
    assert output_path.read_bytes() == b"rescued-image"
    assert prediction["status"] == "succeeded"
    assert model == "grok-imagine-image-pro"


def test_cast_image_handler_normalizes_non_upscaled_output_to_png(tmp_path: Path) -> None:
    handler = CastImageHandler(replicate_token="replicate-token")

    async def _fake_run_model_chain(*args, **kwargs):
        return {"status": "succeeded"}, "prunaai/p-image"

    async def _fake_download_output(url: str, path: Path):
        Image.new("RGB", (64, 64), color="white").save(path, format="JPEG")

    handler._run_model_chain = _fake_run_model_chain  # type: ignore[method-assign]
    handler.extract_output_url = lambda prediction: "https://example.com/cast.jpg"  # type: ignore[method-assign]
    handler.download_output = _fake_download_output  # type: ignore[method-assign]

    result = asyncio.run(
        handler.generate(
            CastImageInput(
                cast_id="cast_nova",
                prompt="Nova full-body cast reference",
                media_style="graphic_novel",
                output_dir=tmp_path,
            )
        )
    )

    assert result.success is True
    assert result.image_path is not None
    with Image.open(result.image_path) as image:
        assert image.format == "PNG"


def test_reference_pack_stitches_cast_and_props_and_resizes_location(tmp_path: Path) -> None:
    cast_dir = tmp_path / "cast" / "composites"
    prop_dir = tmp_path / "props" / "generated"
    loc_dir = tmp_path / "locations" / "variants"
    prev_dir = tmp_path / "frames" / "composed"
    for directory in (cast_dir, prop_dir, loc_dir, prev_dir):
        directory.mkdir(parents=True, exist_ok=True)

    refs: list[Path] = []
    for idx in range(2):
        path = cast_dir / f"cast_{idx}_ref.png"
        Image.new("RGB", (1600, 1600), color="white").save(path)
        refs.append(path)
    for idx in range(2):
        path = prop_dir / f"prop_{idx}.png"
        Image.new("RGB", (900, 900), color="gray").save(path)
        refs.append(path)
    location = loc_dir / "loc_room_north.png"
    Image.new("RGB", (2048, 1152), color="blue").save(location)
    refs.append(location)
    previous = prev_dir / "f_001_gen.png"
    Image.new("RGB", (3840, 2160), color="red").save(previous)
    refs.append(previous)

    packed = build_reference_pack(
        pack_dir=tmp_path / "packed",
        prompt_text="Prompt text",
        reference_images=refs,
    )

    names = sorted(path.name for path in packed.reference_images)

    assert packed.storyboard_image is None
    assert names == ["cast_sheet.jpg", "location.jpg", "previous.jpg", "prop_sheet.jpg"]
    assert packed.prompt_text == "Prompt text"
    assert packed.prompt_sheet_image is None


def test_reference_pack_splits_long_prompt_into_text_plus_prompt_sheet(tmp_path: Path) -> None:
    cast_dir = tmp_path / "cast" / "composites"
    cast_dir.mkdir(parents=True, exist_ok=True)
    cast = cast_dir / "cast_ref.png"
    Image.new("RGB", (1600, 1600), color="white").save(cast)
    prompt = "\n".join(f"Line {idx} with dense prompt text for overflow handling." for idx in range(200))

    packed = build_reference_pack(
        pack_dir=tmp_path / "packed",
        prompt_text=prompt,
        reference_images=[cast],
        include_prompt_image=True,
    )

    assert packed.prompt_sheet_image is not None
    assert packed.prompt_sheet_image.exists()
    assert "prompt_sheet.png" in [path.name for path in packed.reference_images]
    assert 0 < len(packed.prompt_text) < len(prompt)


def test_frame_input_supports_sensitive_context_flag(tmp_path: Path) -> None:
    inp = FrameInput(
        frame_id="f_001",
        prompt="prompt",
        output_dir=tmp_path,
        sensitive_context=True,
    )

    assert inp.sensitive_context is True


def test_parse_cc_output_reconciles_grok_cast_artifacts(tmp_path: Path) -> None:
    creative_dir = tmp_path / "creative_output"
    creative_dir.mkdir(parents=True, exist_ok=True)

    skeleton_text = """
///CAST: id=cast_monday | name=Monday
///CAST: id=cast_blaire | name=Blaire
///CAST: id=cast_sedona | name=Sedona
///LOCATION: id=loc_apartment | name=Apartment
///LOCATION: id=loc_retreat | name=Retreat Hall
///LOCATION: id=loc_fire_street | name=Fire Street
///SCENE: id=scene_01 | title=Apartment Call | location=loc_apartment | time_of_day=night | int_ext=INT | cast=cast_monday,cast_blaire | props=
///SCENE: id=scene_02 | title=Retreat Dose | location=loc_retreat | time_of_day=afternoon | int_ext=INT | cast=cast_monday,cast_sedona | props=
///SCENE: id=scene_03 | title=Fire Memory | location=loc_fire_street | time_of_day=night | int_ext=EXT | cast=cast_monday | props=
""".strip()
    creative_text = """
///SCENE: id=scene_01 | title=Apartment Call | location=loc_apartment | time_of_day=night | int_ext=INT | cast=cast_monday,cast_blaire | props=
/// cast:Mond ay | cam:north
Monday answers the call from Blaire, staring into the dark apartment.

/// cast:Monday,Blaire | cam:north | dlg
                    MONDAY
          (shocked)
    You knew?

///SCENE: id=scene_02 | title=Retreat Dose | location=loc_retreat | time_of_day=afternoon | int_ext=INT | cast=cast_monday,cast_sedona | props=
/// cast:All | cam:east
Monday and Sedona pass the drops around the room.

///SCENE: id=scene_03 | title=Fire Memory | location=loc_fire_street | time_of_day=night | int_ext=EXT | cast=cast_monday | props=
/// cast:Monday,SecondFirefighter | cam:south | dlg
A second firefighter steps into view beside Monday.

                    SECOND FIREFIGHTER
          (solemn)
    Stay back.
""".strip()

    (creative_dir / "outline_skeleton.md").write_text(skeleton_text, encoding="utf-8")
    (creative_dir / "creative_output.md").write_text(creative_text, encoding="utf-8")

    parsed_graph = parse_cc_output(
        tmp_path,
        ProjectNode(project_id="sw_test_parser_reconcile", title="Parser Reconcile"),
    )

    frame_1_states = {
        state.cast_id: state
        for state in get_frame_cast_state_models(parsed_graph, "f_001")
    }
    assert "cast_monday" in frame_1_states
    assert "cast_mond_ay" not in frame_1_states

    frame_2_states = {
        state.cast_id: state
        for state in get_frame_cast_state_models(parsed_graph, "f_002")
    }
    assert frame_2_states["cast_blaire"].frame_role == CastFrameRole.REFERENCED

    frame_3_cast_ids = {
        state.cast_id
        for state in get_frame_cast_state_models(parsed_graph, "f_003")
        if state.frame_role != CastFrameRole.REFERENCED
    }
    assert frame_3_cast_ids == {"cast_monday", "cast_sedona"}
    assert all(not key.startswith("group@") for key in parsed_graph.cast_frame_states)

    assert "cast_second_firefighter" in parsed_graph.cast
    assert "cast_second_firefighter" in parsed_graph.scenes["scene_03"].cast_present
    assert parsed_graph.dialogue["dlg_002"].cast_id == "cast_second_firefighter"


def test_parse_cc_output_recovers_inline_dialogue_from_untagged_frames(tmp_path: Path) -> None:
    creative_dir = tmp_path / "creative_output"
    creative_dir.mkdir(parents=True, exist_ok=True)

    skeleton_text = """
///CAST: id=cast_monday | name=Monday
///CAST: id=cast_rowan | name=Rowan
///LOCATION: id=loc_house | name=House
///SCENE: id=scene_01 | title=House Intro | location=loc_house | time_of_day=day | int_ext=INT | cast=cast_monday,cast_rowan | props=
    """.strip()
    creative_text = """
///SCENE: id=scene_01 | title=House Intro | location=loc_house | time_of_day=day | int_ext=INT | cast=cast_monday,cast_rowan | props=
/// cast:Monday | cam:north | dlg
                    MONDAY
          (guarded)
    I'm Monday.

/// cast:Monday,Rowan | cam:north
                    ROWAN
          (accusing)
    You're not supposed to be here.
    """.strip()

    (creative_dir / "outline_skeleton.md").write_text(skeleton_text, encoding="utf-8")
    (creative_dir / "creative_output.md").write_text(creative_text, encoding="utf-8")

    parsed_graph = parse_cc_output(
        tmp_path,
        ProjectNode(project_id="sw_test_inline_dialogue_recovery", title="Inline Dialogue Recovery"),
    )

    assert len(parsed_graph.dialogue) == 2
    recovered = next(node for node in parsed_graph.dialogue.values() if node.raw_line == "You're not supposed to be here.")
    assert recovered.cast_id == "cast_rowan"
    assert recovered.primary_visual_frame == "f_002"
    assert parsed_graph.frames["f_002"].dialogue_ids == [recovered.dialogue_id]
    assert parsed_graph.frames["f_002"].is_dialogue is True


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


def test_parse_creative_output_inline_dialogue_fallback_handles_alias_and_bad_dlg_flag() -> None:
    skeleton_text = """
///CAST: id=cast_monday | name=Monday | role=protagonist | gender=female | age=30s | build=slender | hair=medium,messy,brown | skin=pale | clothing=hoodie | personality=guarded | wardrobe=oversized hoodie | state_tags=base
///CAST: id=cast_sedona | name=Sedona | role=catalyst | gender=female | age=30s | build=petite | hair=long,wavy,blonde | skin=light | clothing=robes | personality=performative | wardrobe=flowing robes | state_tags=base
///LOCATION: id=loc_room | name=Room | type=interior | atmosphere=quiet | description=A simple room
///LOCATION_DIR: id=loc_room | direction=south | description=Door and hallway | features=door | depth=foreground chair, hallway beyond
///LOCATION_DIR: id=loc_room | direction=north | description=Blank wall | features=wall | depth=foreground table, wall behind
///SCENE: id=scene_01 | title=Test Scene | location=loc_room | time_of_day=night | int_ext=INT | cast=cast_monday,cast_sedona | mood=tense | pacing=measured | cast_states=cast_monday:base,cast_sedona:base | props=
""".strip()

    creative_text = """
///SCENE: id=scene_01 | title=Test Scene | location=loc_room | time_of_day=night | int_ext=INT | cast=cast_monday,cast_sedona | mood=tense | pacing=measured | cast_states=cast_monday:base,cast_sedona:base | props=

/// cast:Monday | cam:south | dlg
The worn couch creaks under Monday as she sits across from the glowing screen.

/// cast:Sedona | cam:north | dlg
                    SEDONA (NINA)
          (shouting, unhinged)
    This is unacceptable! Do you have any idea what you've done?
""".strip()

    warnings: list[str] = []
    name_map = parse_skeleton(skeleton_text, warnings)["name_map"]
    parsed = parse_creative_output(creative_text, skeleton_text, name_map, warnings)

    frames = {frame.frame_id: frame for frame in parsed["frames"]}
    dialogue = parsed["dialogue"]

    assert frames["f_001"].is_dialogue is False
    assert frames["f_001"].dialogue_ids == []
    assert frames["f_002"].dialogue_ids == ["dlg_001"]
    assert len(dialogue) == 1
    assert dialogue[0].speaker == "Sedona"
    assert dialogue[0].cast_id == "cast_sedona"
    assert dialogue[0].raw_line == "This is unacceptable! Do you have any idea what you've done?"


def test_parse_args_accepts_live_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--project", "demo_project", "--live"],
    )

    args = run_pipeline.parse_args()

    assert args.project == "demo_project"
    assert args.live is True


def test_frame_enricher_cli_worker_uses_unlimited_agent_timeout(tmp_path: Path, monkeypatch) -> None:
    timeout_seen: list[object] = []
    stream_output_seen: list[object] = []

    def fake_run_agent(_agent_id, _prompt_file, **kwargs):
        timeout_seen.append(kwargs.get("timeout"))
        stream_output_seen.append(kwargs.get("stream_output"))
        return subprocess.CompletedProcess(["fake"], 0, stdout="{}", stderr="")

    monkeypatch.setattr(run_pipeline, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(run_pipeline, "run_agent", fake_run_agent)
    monkeypatch.setattr(run_pipeline, "check_agent_result", lambda *_args, **_kwargs: None)

    result = run_pipeline._run_frame_enricher_cli_worker({"frame_id": "f_001"}, dry_run=False)

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


def test_quality_gate_phase_2_flags_low_dialogue_density(
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
    _write_json(tmp_path / "dialogue.json", {"lines": ["a", "b", "c", "d", "e"]})
    _write_json(tmp_path / "cast" / "cast_001.json", {"role": "protagonist"})
    _write_json(tmp_path / "locations" / "loc_001.json", {"name": "Roof"})
    _write_json(tmp_path / "graph" / "narrative_graph.json", {"version": 1})

    frame_order = [frame["frameId"] for frame in frames]
    fake_frames = {
        frame_id: SimpleNamespace(
            frame_id=frame_id,
            scene_id="scene_01",
            is_dialogue=idx < 2,
            dialogue_ids=["dlg_001"] if idx < 2 else [],
        )
        for idx, frame_id in enumerate(frame_order)
    }

    class FakeStore:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def load(self):
            return SimpleNamespace(
                frame_order=frame_order,
                frames=fake_frames,
                scenes={"scene_01": SimpleNamespace(cast_present=["cast_001"])},
                dialogue={},
                cast={"cast_001": SimpleNamespace(name="Monday", display_name="Monday")},
            )

    monkeypatch.setattr(graph.store, "GraphStore", FakeStore)
    monkeypatch.setattr(
        graph.api,
        "get_frame_cast_state_models",
        lambda *_args, **_kwargs: [SimpleNamespace(cast_id="cast_001", frame_role="subject")],
    )

    issues = quality_gate_phase_2(tmp_path)

    assert any("Dialogue density too low" in issue for issue in issues)


def test_quality_gate_phase_2_exempts_voice_over_primary_speaker_visibility(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_json(
        tmp_path / "project_manifest.json",
        {
            "frames": [{"frameId": "f_001", "castIds": [], "locationId": "loc_001"}],
            "cast": [{"castId": "cast_female_voice_over", "role": "supporting"}],
        },
    )
    _write_json(tmp_path / "source_files" / "onboarding_config.json", {"outputSize": "short"})
    _write_json(tmp_path / "dialogue.json", {"lines": ["a", "b", "c"]})
    _write_json(tmp_path / "cast" / "cast_female_voice_over.json", {"role": "supporting"})
    _write_json(tmp_path / "locations" / "loc_001.json", {"name": "Void"})
    _write_json(tmp_path / "graph" / "narrative_graph.json", {"version": 1})

    class FakeStore:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def load(self):
            return SimpleNamespace(
                frame_order=["f_001"],
                frames={
                    "f_001": SimpleNamespace(
                        frame_id="f_001",
                        scene_id="scene_01",
                        is_dialogue=True,
                        dialogue_ids=["dlg_001"],
                    )
                },
                scenes={"scene_01": SimpleNamespace(cast_present=[])},
                dialogue={
                    "dlg_001": SimpleNamespace(
                        primary_visual_frame="f_001",
                        cast_id="cast_female_voice_over",
                        speaker="Female Voice Over",
                    )
                },
                cast={
                    "cast_female_voice_over": SimpleNamespace(
                        name="Female Voice Over",
                        display_name="Female Voice Over",
                    )
                },
            )

    monkeypatch.setattr(graph.store, "GraphStore", FakeStore)
    monkeypatch.setattr(graph.api, "get_frame_cast_state_models", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        graph.api,
        "build_shot_packet",
        lambda *_args, **_kwargs: SimpleNamespace(visible_cast_ids=[]),
    )

    issues = quality_gate_phase_2(tmp_path)

    assert not any("omit the speaker from visible cast" in issue for issue in issues)


def test_extract_cast_tags_normalizes_display_names_and_flags_duplicates() -> None:
    warnings: list[str] = []
    nodes = extract_cast_tags(
        """
///CAST: id=cast_monday | name=Mondy
///CAST: id=cast_sedona | name=Sedona Arizona
///CAST: id=cast_mondy | name=Monday
        """.strip(),
        warnings,
    )

    by_id = {node.cast_id: node for node in nodes}
    assert by_id["cast_monday"].display_name == "Monday"
    assert by_id["cast_monday"].source_name == "Mondy"
    assert by_id["cast_sedona"].display_name == "Sedona"
    assert any("near-duplicates" in warning for warning in warnings)


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


def test_generate_video_clip_retries_retryable_upstream_failures(tmp_path: Path, monkeypatch) -> None:
    image_path = tmp_path / "frames" / "composed" / "f_001_gen.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")
    out_path = tmp_path / "video" / "clips" / "f_001.mp4"

    attempts: list[int] = []

    def fake_stream(*_args, **_kwargs):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            return SimpleNamespace(returncode=1, stdout="Server error '502 Bad Gateway'", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(run_pipeline, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(run_pipeline, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(run_pipeline, "_stream_subprocess", fake_stream)
    monkeypatch.setattr(run_pipeline.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_pipeline, "VIDEO_GEN_RETRIES", 3)

    result = run_pipeline._generate_video_clip(
        "f_001",
        image_path,
        "prompt",
        5,
        out_path,
        dry_run=False,
    )

    assert result.returncode == 0
    assert attempts == [1, 2]


def test_video_clip_handler_uses_prepared_frame_for_replicate(tmp_path: Path, monkeypatch) -> None:
    image_path = tmp_path / "frames" / "composed" / "f_001_gen.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3840, 2160), color=(12, 34, 56)).save(image_path, format="PNG")

    recorded: dict[str, object] = {}

    handler = VideoClipHandler(replicate_token="replicate")

    async def fake_upload(path: Path) -> str:
        recorded["uploaded_path"] = str(path)
        recorded["uploaded_size"] = path.stat().st_size
        return "data:image/jpeg;base64,AAAA"

    async def fake_download(url: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        recorded["url"] = url
        return output_path

    async def fake_predict(model: str, pred_input: dict, *, extra_headers=None):
        recorded["model"] = model
        recorded["pred_input"] = pred_input
        recorded["headers"] = extra_headers or {}
        return {"status": "starting", "id": "pred_123"}

    async def fake_resolve(prediction: dict, *, extra_headers=None):
        recorded["resolve_headers"] = extra_headers or {}
        return {
            "status": "succeeded",
            "output": "https://example.test/video.mp4",
        }

    monkeypatch.setattr(handler, "upload_to_replicate", fake_upload)
    monkeypatch.setattr(handler, "download_output", fake_download)
    monkeypatch.setattr(handler, "_replicate_predict", fake_predict)
    monkeypatch.setattr(handler, "_resolve_prediction", fake_resolve)

    result = asyncio.run(
        handler.generate(
            VideoClipInput(
                frame_id="f_001",
                dialogue_text="",
                motion_prompt="Test video prompt",
                frame_image_path=image_path,
                suggested_duration=5,
                output_dir=tmp_path,
                run_id="test",
                phase="phase_5",
            )
        )
    )

    assert result.success is True
    assert result.model_used == "xai/grok-imagine-video"
    assert recorded["url"] == "https://example.test/video.mp4"
    assert recorded["pred_input"]["image"].startswith("data:image/jpeg;base64,")
    assert recorded["pred_input"]["resolution"] == "720p"
    assert recorded["uploaded_path"].endswith("_video_input.jpg")
    assert recorded["uploaded_size"] < image_path.stat().st_size


def test_refine_video_prompt_accepts_long_refined_prompt(tmp_path: Path, monkeypatch) -> None:
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

    assert result["refined_by"] == "grok-vision"
    assert result["original_graph_prompt"] == "graph prompt"
    assert result["prompt"] == "x" * 5000


def test_frame_prompt_requires_sensitive_context_for_vial_administration() -> None:
    assert _frame_prompt_requires_sensitive_context(
        {
            "prompt": "Hudson administers vial drops while Sedona opens her mouth under peer pressure.",
        }
    ) is True


def test_frame_prompt_sensitive_context_stays_false_for_normal_dialogue() -> None:
    assert _frame_prompt_requires_sensitive_context(
        {
            "prompt": "Blaire gestures earnestly with the glass while speaking persuasively in the apartment.",
        }
    ) is False


def test_location_grid_prompt_includes_fixed_directional_label_layout() -> None:
    prompt = build_grid_prompt("Test room with four directional views.", template_type="interior")

    assert "Directional label layout is fixed and must be followed exactly:" in prompt
    assert "NORTH = top-left panel, label in the bottom-right inner corner;" in prompt
    assert "EAST = top-right panel, label in the bottom-left inner corner;" in prompt
    assert "WEST = bottom-left panel, label in the top-right inner corner;" in prompt
    assert "SOUTH = bottom-right panel, label in the top-left inner corner." in prompt


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
    monkeypatch.setattr(run_pipeline, "_reconcile_scene_cast_presence", lambda _project_dir: None)

    try:
        _run_phase_2_postprocessing(tmp_path)
    except RuntimeError as exc:
        assert "video-direction validation failed" in str(exc)
    else:
        raise AssertionError("Expected Phase 2 post-processing to halt on validator failure")

    assert calls == [
        "graph_assemble_prompts",
        "concatenate_project_snapshot.py",
        "graph_validate_dialogue",
        "prompt_pair_validator.py",
        "graph_materialize",
        "graph_validate_video_direction",
    ]


def test_detect_resume_phase_reruns_phase_1_when_manifest_complete_but_artifacts_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    _write_json(
        tmp_path / "project_manifest.json",
        {
            "phases": {
                "phase_0": {"status": "complete"},
                "phase_1": {"status": "complete"},
                "phase_2": {"status": "ready"},
            }
        },
    )

    monkeypatch.setattr(run_pipeline, "MANIFEST_PATH", tmp_path / "project_manifest.json")
    monkeypatch.setattr(run_pipeline, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(
        run_pipeline,
        "_phase_reuse_status",
        lambda phase_num, _project_dir: (False, ["outline_skeleton.md missing"]) if phase_num == 1 else (True, []),
    )

    assert run_pipeline.detect_resume_phase() == 1


def test_verify_prerequisites_rejects_incomplete_phase_artifacts_even_if_manifest_is_complete(
    tmp_path: Path, monkeypatch
) -> None:
    _write_json(
        tmp_path / "project_manifest.json",
        {
            "phases": {
                "phase_0": {"status": "complete"},
                "phase_1": {"status": "complete"},
                "phase_2": {"status": "ready"},
            }
        },
    )

    monkeypatch.setattr(run_pipeline, "MANIFEST_PATH", tmp_path / "project_manifest.json")
    monkeypatch.setattr(run_pipeline, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(
        run_pipeline,
        "_phase_reuse_status",
        lambda phase_num, _project_dir: (False, ["graph/narrative_graph.json missing"]) if phase_num == 1 else (True, []),
    )

    with pytest.raises(SystemExit):
        run_pipeline.verify_prerequisites(2)


def test_prompt_pair_validator_rejects_subject_count_contradiction(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    shot_packet = build_shot_packet(graph, "f_001").model_copy(
        update={
            "subject_count": 0,
            "visible_cast_ids": [],
            "current_beat": "The group of nine locks together and chants in unison.",
            "video_optimized_prompt_block": "A diverse group of nine tightens into an intimate cluster.",
        }
    )
    image_prompt = {
        "frame_id": "f_001",
        "scene_id": "scene_01",
        "prompt": "CONTINUITY:\n- The group of nine locks together.\nSUBJECT COUNT:\n- Exactly 0 visible subject(s).",
    }
    video_prompt = {
        "frame_id": "f_001",
        "scene_id": "scene_01",
        "prompt": "A diverse group of nine chants together.\nNEGATIVE CONSTRAINTS:\n- Do not exceed 0 visible subject(s).",
        "dialogue_present": False,
        "dialogue_fit_status": "no_dialogue",
        "dialogue_turn_count": 0,
    }

    issues = PromptPairValidator().validate(image_prompt, video_prompt, shot_packet)

    assert any(issue.category == PromptPairCategory.SUBJECT_COUNT_CONSISTENCY for issue in issues)


def test_prompt_pair_validator_ignores_neighboring_group_mentions_for_single_subject(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    shot_packet = build_shot_packet(graph, "f_001").model_copy(
        update={
            "subject_count": 1,
            "visible_cast_ids": ["cast_nova"],
            "current_beat": "Nova stands alone on the rooftop, listening.",
            "video_optimized_prompt_block": "Single-subject close coverage on Nova.",
            "continuity_deltas": [
                "Previous beat: the group of nine locks together and chants in unison.",
                "Next beat: the group of nine spills into the hallway.",
            ],
        }
    )
    image_prompt = {
        "frame_id": "f_001",
        "scene_id": "scene_01",
        "prompt": "Nova alone in frame.\nSUBJECT COUNT:\n- Exactly 1 visible subject(s).",
    }
    video_prompt = {
        "frame_id": "f_001",
        "scene_id": "scene_01",
        "prompt": "Single-subject coverage of Nova only.\nNEGATIVE CONSTRAINTS:\n- Do not exceed 1 visible subject(s).",
        "dialogue_present": False,
        "dialogue_fit_status": "no_dialogue",
        "dialogue_turn_count": 0,
    }

    issues = PromptPairValidator().validate(image_prompt, video_prompt, shot_packet)

    assert not any(issue.category == PromptPairCategory.SUBJECT_COUNT_CONSISTENCY for issue in issues)


def test_build_shot_packet_inferrs_collective_subjects_from_neighboring_group_beats(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.cast["cast_orin"] = CastNode(
        cast_id="cast_orin",
        name="Orin",
        display_name="Orin",
        source_name="Orin",
        identity=CastIdentity(physical_description="tall man with a shaved head"),
        role=NarrativeRole.SUPPORTING,
    )
    graph.scenes["scene_01"].cast_present.append("cast_orin")
    graph.scenes["scene_01"].frame_ids.append("f_003")
    graph.scenes["scene_01"].frame_count = 3

    graph.frames["f_001"].next_frame_id = "f_002"
    graph.frames["f_002"].previous_frame_id = "f_001"
    graph.frames["f_002"].next_frame_id = "f_003"
    graph.frames["f_002"].narrative_beat = "The group of two tightens together in unison."
    graph.frames["f_002"].action_summary = "The group of two tightens together in unison."

    graph.frames["f_003"] = graph.frames["f_002"].model_copy(
        update={
            "frame_id": "f_003",
            "sequence_index": 3,
            "previous_frame_id": "f_002",
            "next_frame_id": None,
            "narrative_beat": "Nova and Orin lock eyes and hold the roofline together.",
            "action_summary": "Nova and Orin hold the roofline together.",
            "is_dialogue": False,
            "dialogue_ids": [],
        }
    )
    graph.frame_order.append("f_003")

    graph.cast_frame_states["cast_orin@f_001"] = CastFrameState(
        cast_id="cast_orin",
        frame_id="f_001",
        frame_role=CastFrameRole.SUBJECT,
        posture=Posture.STANDING,
        facing_direction="toward_camera",
        screen_position="frame_right",
    )
    graph.cast_frame_states["cast_orin@f_003"] = CastFrameState(
        cast_id="cast_orin",
        frame_id="f_003",
        frame_role=CastFrameRole.SUBJECT,
        posture=Posture.STANDING,
        facing_direction="toward_camera",
        screen_position="frame_right",
    )
    graph.cast_frame_states["cast_nova@f_003"] = CastFrameState(
        cast_id="cast_nova",
        frame_id="f_003",
        frame_role=CastFrameRole.SUBJECT,
        posture=Posture.STANDING,
        facing_direction="toward_camera",
        screen_position="frame_left",
    )
    del graph.cast_frame_states["cast_nova@f_002"]

    shot_packet = build_shot_packet(graph, "f_002")

    assert shot_packet.visible_cast_ids == ["cast_nova", "cast_orin"]
    assert shot_packet.subject_count == 2


def test_build_shot_packet_inferrs_named_dialogue_pair_without_frame_states(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.cast["cast_orin"] = CastNode(
        cast_id="cast_orin",
        name="Orin",
        display_name="Orin",
        source_name="Orin",
        identity=CastIdentity(physical_description="lean man with tired eyes"),
        role=NarrativeRole.SUPPORTING,
    )
    graph.scenes["scene_01"].cast_present.append("cast_orin")
    frame = graph.frames["f_002"]
    frame.composition.shot = "two_shot"
    frame.narrative_beat = "Nova tells Orin the signal changed while they hold eye contact."
    frame.action_summary = "Nova and Orin share a tense exchange."
    frame.source_text = """NOVA\n          (urgent)\n    They're early, Orin."""
    graph.cast_frame_states = {
        key: value
        for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_002")
    }

    shot_packet = build_shot_packet(graph, "f_002")

    assert shot_packet.visible_cast_ids[:2] == ["cast_nova", "cast_orin"]
    assert shot_packet.subject_count == 2


def test_build_shot_packet_expands_single_explicit_state_for_collective_named_beat(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.cast["cast_orin"] = CastNode(
        cast_id="cast_orin",
        name="Orin",
        display_name="Orin",
        source_name="Orin",
        identity=CastIdentity(physical_description="lean man with tired eyes"),
        role=NarrativeRole.SUPPORTING,
    )
    graph.scenes["scene_01"].cast_present.append("cast_orin")

    frame = graph.frames["f_002"]
    frame.narrative_beat = "Nova greets the group with a nod, handing the signal chip to Orin and Blaire."
    frame.action_summary = "Nova greets Orin and Blaire while the group crowds in."
    graph.cast["cast_blaire"] = CastNode(
        cast_id="cast_blaire",
        name="Blaire",
        display_name="Blaire",
        source_name="Blaire",
        identity=CastIdentity(physical_description="short woman with cropped hair"),
        role=NarrativeRole.SUPPORTING,
    )
    graph.scenes["scene_01"].cast_present.append("cast_blaire")
    graph.cast_frame_states = {
        "cast_nova@f_002": CastFrameState(
            cast_id="cast_nova",
            frame_id="f_002",
            frame_role=CastFrameRole.SUBJECT,
            posture=Posture.STANDING,
            facing_direction="toward_camera",
            screen_position="frame_left",
        )
    }

    shot_packet = build_shot_packet(graph, "f_002")

    assert shot_packet.subject_count >= 3
    assert shot_packet.visible_cast_ids[:3] == ["cast_nova", "cast_orin", "cast_blaire"]


def test_quality_gate_phase_4_flags_prompt_subject_count_contradictions(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.frames["f_001"].narrative_beat = "The group of nine tightens together in the room."
    graph.frames["f_001"].action_summary = "The group of nine tightens together in the room."
    GraphStore(str(tmp_path)).save(graph)

    _write_json(
        tmp_path / "project_manifest.json",
        {"frames": [{"frameId": "f_001"}, {"frameId": "f_002"}]},
    )
    (tmp_path / "frames" / "composed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "frames" / "composed" / "f_001_gen.png").write_bytes(b"x" * 20000)
    (tmp_path / "frames" / "composed" / "f_002_gen.png").write_bytes(b"x" * 20000)
    _write_json(
        tmp_path / "frames" / "prompts" / "f_001_image.json",
        {
            "frame_id": "f_001",
            "scene_id": "scene_01",
            "prompt": "The group of nine tightens together.\nSUBJECT COUNT:\n- Exactly 0 visible subject(s).",
        },
    )
    _write_json(
        tmp_path / "video" / "prompts" / "f_001_video.json",
        {
            "frame_id": "f_001",
            "scene_id": "scene_01",
            "prompt": "A diverse group of nine tightens together.\nNEGATIVE CONSTRAINTS:\n- Do not exceed 0 visible subject(s).",
            "dialogue_present": False,
            "dialogue_fit_status": "no_dialogue",
            "dialogue_turn_count": 0,
        },
    )
    _write_json(
        tmp_path / "frames" / "prompts" / "f_002_image.json",
        {"frame_id": "f_002", "scene_id": "scene_01", "prompt": "Nova watches the skyline."},
    )
    _write_json(
        tmp_path / "video" / "prompts" / "f_002_video.json",
        {
            "frame_id": "f_002",
            "scene_id": "scene_01",
            "prompt": "Nova watches the skyline.",
            "dialogue_present": True,
            "dialogue_fit_status": "fits",
            "dialogue_turn_count": 1,
            "dialogue_line": "Keep moving.",
        },
    )

    issues = quality_gate_phase_4(tmp_path)

    assert any("prompt subject-count contradiction" in issue for issue in issues)


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


def test_graph_validate_video_direction_fix_breaks_high_tension_pose_repetition(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    scene = graph.scenes["scene_01"]
    scene.title = "Confrontation in the Safehouse"
    scene.pacing = "tense"
    scene.mood_keywords = ["tense", "confrontational"]
    scene.frame_ids = []
    scene.frame_count = 5

    template_frame = graph.frames["f_002"].model_copy(deep=True)
    template_state = graph.cast_frame_states["cast_nova@f_002"].model_copy(deep=True)
    graph.frames.clear()
    graph.frame_order.clear()
    graph.cast_frame_states.clear()
    graph.dialogue.clear()
    graph.dialogue_order.clear()

    frame_ids = [f"f_{idx:03d}" for idx in range(1, 6)]
    for idx, frame_id in enumerate(frame_ids, start=1):
        frame = template_frame.model_copy(deep=True)
        frame.frame_id = frame_id
        frame.sequence_index = idx
        frame.scene_id = scene.scene_id
        frame.previous_frame_id = frame_ids[idx - 2] if idx > 1 else None
        frame.next_frame_id = frame_ids[idx] if idx < len(frame_ids) else None
        frame.action_summary = f"Nova holds the argument beat {idx}"
        frame.source_text = f"NOVA confronts the room on beat {idx}."
        frame.visual_flow_element = "reaction" if idx >= 3 else "dialogue"
        frame.composition.angle = "eye_level"
        frame.composition.movement = "static"
        graph.frames[frame_id] = frame
        graph.frame_order.append(frame_id)
        scene.frame_ids.append(frame_id)

        state = template_state.model_copy(deep=True)
        state.frame_id = frame_id
        state.posture = Posture.STANDING
        state.facing_direction = "toward_camera"
        state.screen_position = "frame_center"
        graph.cast_frame_states[f"cast_nova@{frame_id}"] = state

    GraphStore(str(tmp_path)).save(graph)

    skill_path = Path(__file__).resolve().parents[1] / "skills" / "graph_validate_video_direction"
    result = subprocess.run(
        [sys.executable, str(skill_path), "--project-dir", str(tmp_path), "--fix"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Auto-diversified" in result.stdout

    reloaded = GraphStore(str(tmp_path)).load()
    signatures = []
    for frame_id in frame_ids:
        state = get_frame_cast_state_models(reloaded, frame_id)[0]
        posture = getattr(getattr(state, "posture", None), "value", getattr(state, "posture", None))
        signatures.append(f"{posture}|{state.facing_direction}")

    dominant_count = max(signatures.count(sig) for sig in set(signatures))
    assert dominant_count <= 2


def test_frame_handler_locally_renders_loading_spinner_without_remote_call(tmp_path: Path) -> None:
    handler = FrameHandler(replicate_token="test-token")

    async def _forbidden_remote(*args, **kwargs):
        raise AssertionError("remote model chain should not be called for loading-wheel inserts")

    handler._run_model_chain = _forbidden_remote  # type: ignore[method-assign]
    inp = FrameInput(
        frame_id="f_012",
        prompt="Generate one final finished frame.\nOutput a simple white loading spinner centered on pure black.",
        output_dir=tmp_path,
    )

    result = asyncio.run(handler.generate(inp))

    assert result.success is True
    assert result.model_used == "local/loading_wheel"
    assert result.image_path and result.image_path.exists()


def test_frame_handler_retries_long_prompt_with_prompt_sheet_overflow(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SCREENWIRE_PROMPT_IMAGE_TRIGGER_CHARS", "50")

    cast_dir = tmp_path / "cast" / "composites"
    cast_dir.mkdir(parents=True, exist_ok=True)
    cast_ref = cast_dir / "cast_ref.png"
    Image.new("RGB", (1200, 1200), color="white").save(cast_ref)

    handler = FrameHandler(replicate_token="test-token")
    seen_inputs: list[dict] = []
    output_path = tmp_path / "rescued.png"

    async def _fake_upload_many(paths: list[Path]) -> list[str]:
        return [f"uri:{path.name}" for path in paths]

    async def _fake_run_model_chain(handler_name: str, base_input: dict, **kwargs):
        seen_inputs.append(dict(base_input))
        if len(seen_inputs) == 1:
            return {"status": "failed", "error": "code: PA", "logs": "heartbeat interrupted"}, "google/nano-banana-2"
        output_path.write_bytes(b"frame-bytes")
        return {"status": "succeeded", "local_output_path": str(output_path)}, "google/nano-banana-pro"

    async def _forbidden_capacity(*args, **kwargs):
        raise AssertionError("capacity rescue should not run when prompt-sheet retry succeeds")

    async def _forbidden_xai(*args, **kwargs):
        raise AssertionError("xai rescue should not run when prompt-sheet retry succeeds")

    handler.upload_many = _fake_upload_many  # type: ignore[method-assign]
    handler._run_model_chain = _fake_run_model_chain  # type: ignore[method-assign]
    handler._try_capacity_rescue = _forbidden_capacity  # type: ignore[method-assign]
    handler._try_xai_image_rescue = _forbidden_xai  # type: ignore[method-assign]

    inp = FrameInput(
        frame_id="f_099",
        prompt="\n".join(f"Long prompt line {idx} with extra detail for overflow retry." for idx in range(50)),
        output_dir=tmp_path,
        reference_images=[cast_ref],
    )

    result = asyncio.run(handler.generate(inp))

    assert result.success is True
    assert len(seen_inputs) == 2
    assert len(seen_inputs[1]["prompt"]) < len(inp.prompt)
    assert any("prompt_sheet.png" in uri for uri in seen_inputs[1].get("image_input", []))


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


def test_phase1_agent_cache_key_is_stable_across_parallel_workers(tmp_path: Path) -> None:
    creative_key = _agent_prompt_cache_key(
        agent_id="creative_coordinator",
        project_dir=tmp_path,
        model="grok-4.20-reasoning",
        cacheable_system_prompt="base prompt body",
        trigger_msg="Execute the CRITICAL OVERRIDE at the end of your system prompt. Follow ONLY those instructions. Do not stop or wait for input.",
    )
    prose_key = _agent_prompt_cache_key(
        agent_id="prose_worker_scene_07",
        project_dir=tmp_path,
        model="grok-4.20-reasoning",
        cacheable_system_prompt="base prompt body",
        trigger_msg="Execute the CRITICAL OVERRIDE at the end of your system prompt. Follow ONLY those instructions. Do not stop or wait for input.",
    )
    frame_enricher_key = _agent_prompt_cache_key(
        agent_id="frame_enricher_worker_f_007",
        project_dir=tmp_path,
        model="grok-4-1-fast-reasoning",
        cacheable_system_prompt="base prompt body",
        trigger_msg="Execute your instructions now. Work autonomously through all steps in your system prompt. Do not stop or wait for input.",
    )

    assert creative_key == prose_key
    assert creative_key != frame_enricher_key

"""
Live pipeline smoke test.

This test makes real generation calls through the running local ScreenWire
server and writes a persistent inspection fixture to:
    tests/projects/test-<timestamp>/

It intentionally exercises the current graph-driven path:
    graph -> materialize -> prompt assembly -> asset generation ->
    storyboard generation -> frame generation -> video generation
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from graph.api import build_storyboard_grids, get_frame_context
from graph.grid_generate import GRID_SPECS, PROMPT_TEMPLATE, generate_grid_guide, split_grid
from graph.materializer import materialize_all
from graph.prompt_assembler import assemble_all_prompts
from graph.schema import (
    CastFrameRole,
    CastFrameState,
    CastIdentity,
    CastNode,
    CastVoice,
    DialogueNode,
    FormulaTag,
    FrameAtmosphere,
    FrameBackground,
    FrameComposition,
    FrameDirecting,
    FrameEnvironment,
    FrameLighting,
    FrameNode,
    LightingDirection,
    LightingQuality,
    LocationFrameState,
    LocationNode,
    NarrativeGraph,
    NarrativeRole,
    Posture,
    ProjectNode,
    PropFrameRole,
    PropFrameState,
    PropNode,
    Provenance,
    SceneNode,
    TimeOfDay,
    VoiceNode,
)
from graph.store import GraphStore


SERVER_URL = "http://localhost:8000"
IMAGE_TIMEOUT_SECONDS = 600
VIDEO_TIMEOUT_SECONDS = 900
MIN_IMAGE_BYTES = 10_000
MIN_VIDEO_BYTES = 100_000

TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR / "projects" / f"test-{TIMESTAMP}"
SUMMARY_PATH = PROJECT_DIR / "smoke_summary.json"

STYLE_PREFIX = (
    "live action, clinical midnight thriller photography with hard edge practicals "
    "and cool city spill carving subjects out of shadow. "
)


def _prov(chunk: str, generated_by: str = "pipeline_smoke_live") -> Provenance:
    return Provenance(
        source_prose_chunk=chunk,
        generated_by=generated_by,
        confidence=1.0,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _write_text(project_dir: Path, rel_path: str, text: str) -> None:
    path = project_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _server_available() -> bool:
    try:
        response = httpx.get(f"{SERVER_URL}/docs", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def _post_json(url: str, body: dict, timeout: int) -> dict:
    response = httpx.post(url, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _ensure_seed_files(project_dir: Path) -> None:
    for rel_dir in [
        "source_files",
        "graph",
        "logs/scene_coordinator",
        "logs/pipeline",
    ]:
        (project_dir / rel_dir).mkdir(parents=True, exist_ok=True)

    _write_text(
        project_dir,
        "project_manifest.json",
        json.dumps(
            {
                "projectId": "sw_pipeline_smoke_live",
                "projectName": "Pipeline Smoke Live",
                "status": "phase_3_ready",
                "version": 3,
                "phases": {
                    "phase_0": {"status": "complete"},
                    "phase_1": {"status": "complete"},
                    "phase_2": {"status": "complete"},
                    "phase_3": {"status": "ready"},
                },
            },
            indent=2,
        ),
    )
    _write_text(
        project_dir,
        "source_files/onboarding_config.json",
        json.dumps(
            {
                "projectName": "Pipeline Smoke Live",
                "projectId": "sw_pipeline_smoke_live",
                "mediaStyle": "live_clear",
                "mediaStylePrefix": STYLE_PREFIX,
                "aspectRatio": "16:9",
            },
            indent=2,
        ),
    )
    _write_text(
        project_dir,
        "source_files/pitch.md",
        (
            "Nova holds the roofline, the pager vibrates early, and the warning pushes "
            "her into motion before the watchers can close the gap."
        ),
    )


def build_live_smoke_graph(project_dir: Path) -> NarrativeGraph:
    _ensure_seed_files(project_dir)

    graph = NarrativeGraph(
        project=ProjectNode(
            project_id="sw_pipeline_smoke_live",
            title="Pipeline Smoke Live",
            media_style="live_clear",
            media_style_prefix=STYLE_PREFIX,
            aspect_ratio="16:9",
            source_files=["source_files/pitch.md"],
        ),
    )

    cast = CastNode(
        cast_id="cast_nova",
        name="Nova",
        identity=CastIdentity(
            age_descriptor="30s",
            gender="female",
            build="lean",
            hair_color="auburn",
            hair_length="shoulder-length",
            physical_description="lean woman with shoulder-length auburn hair and a hard watchful stare",
            wardrobe_description="weatherproof black coat over a charcoal sweater",
            clothing=["weatherproof black coat", "charcoal sweater"],
        ),
        voice=CastVoice(
            voice_description="low controlled alto with clipped urgency",
            tone="guarded",
            delivery_style="precise",
            tempo="measured",
        ),
        role=NarrativeRole.PROTAGONIST,
        composite_path="cast/composites/cast_nova_ref.png",
        composite_status="pending",
        voice_profile_path="voices/voice_nova.json",
        voice_status="profiled",
        provenance=_prov("Nova is the rooftop lookout carrying the pager."),
    )
    graph.cast[cast.cast_id] = cast
    graph.voices["voice_nova"] = VoiceNode(
        voice_id="voice_nova",
        cast_id=cast.cast_id,
        character_name=cast.name,
        voice_description=cast.voice.voice_description,
        tone=cast.voice.tone,
        delivery_style=cast.voice.delivery_style,
        tempo=cast.voice.tempo,
        voice_profile_path=cast.voice_profile_path,
        voice_status="profiled",
        provenance=_prov("Nova speaks in a low controlled alto."),
    )

    location = LocationNode(
        location_id="loc_rooftop",
        name="Service Rooftop",
        description="a narrow rooftop service lane boxed by vents, antennas, and a low concrete ledge",
        atmosphere="cold, elevated, exposed",
        scenes_used=["scene_01"],
        primary_image_path="locations/primary/loc_rooftop.png",
        image_status="pending",
        provenance=_prov("The rooftop overlooks the surveillance district."),
    )
    graph.locations[location.location_id] = location

    prop = PropNode(
        prop_id="prop_signal_pager",
        name="signal pager",
        description="a scratched black pager with a cracked green screen and a pulsing alert light",
        associated_cast=[cast.cast_id],
        scenes_used=["scene_01"],
        introduction_frame="f_002",
        image_path="props/generated/prop_signal_pager.png",
        provenance=_prov("Nova carries a pager that starts vibrating too early."),
    )
    graph.props[prop.prop_id] = prop

    scene = SceneNode(
        scene_id="scene_01",
        scene_number=1,
        title="Early Warning",
        location_id=location.location_id,
        time_of_day=TimeOfDay.NIGHT,
        cast_present=[cast.cast_id],
        props_present=[prop.prop_id],
        mood_keywords=["tense", "watchful"],
        frame_ids=["f_001", "f_002"],
        frame_count=2,
        provenance=_prov("Nova catches the warning before the contact arrives."),
    )
    graph.scenes[scene.scene_id] = scene
    graph.scene_order.append(scene.scene_id)

    frame_1 = FrameNode(
        frame_id="f_001",
        scene_id=scene.scene_id,
        sequence_index=1,
        formula_tag=FormulaTag.F07,
        narrative_beat="Nova scans the district from the rooftop ledge.",
        source_text="Nova holds the roofline and watches the district for movement.",
        location_id=location.location_id,
        time_of_day=TimeOfDay.NIGHT,
        environment=FrameEnvironment(
            lighting=FrameLighting(
                direction=LightingDirection.SIDE_RIGHT,
                quality=LightingQuality.HARSH,
                color_temp="cool_blue",
                motivated_source="district signage reflecting off steel housings",
                shadow_behavior="hard vent shadows cutting across the roof surface",
            ),
            atmosphere=FrameAtmosphere(
                ambient_motion="distant tower beacons pulsing through haze",
            ),
            foreground_objects=["vent housing", "low concrete ledge"],
            midground_detail="Nova posted near the ledge with the city behind her",
            background_depth="the district skyline dropping away into surveillance towers",
        ),
        composition=FrameComposition(
            shot="wide establishing shot",
            angle="eye_level",
            focus="character",
        ),
        background=FrameBackground(
            camera_facing="east",
            visible_description="the surveillance district skyline blinking beyond the ledge",
            background_sound="distant traffic wash",
        ),
        directing=FrameDirecting(
            dramatic_purpose="establish the watch position before the warning lands",
            beat_turn="The silence is about to break",
            pov_owner=cast.cast_id,
            viewer_knowledge_delta="Nova is exposed on the roof with no cover",
            tension_source="She is waiting for a signal that does not come fast enough",
            camera_motivation="Hold the whole roof to expose how alone she is",
            background_life="tower beacons pulse across the skyline",
        ),
        action_summary="Nova scans the district from the rooftop ledge.",
        suggested_duration=5,
        next_frame_id="f_002",
        provenance=_prov("Nova scans the district from the rooftop ledge."),
    )
    frame_2 = FrameNode(
        frame_id="f_002",
        scene_id=scene.scene_id,
        sequence_index=2,
        formula_tag=FormulaTag.F11,
        narrative_beat="The pager vibrates and Nova reads the warning.",
        source_text="The pager vibrates in Nova's hand and she mutters that they are early.",
        location_id=location.location_id,
        time_of_day=TimeOfDay.NIGHT,
        is_dialogue=True,
        dialogue_ids=["dlg_001"],
        environment=FrameEnvironment(
            lighting=FrameLighting(
                direction=LightingDirection.SIDE_RIGHT,
                quality=LightingQuality.SOFT,
                color_temp="cool_green",
                motivated_source="pager screen spill against Nova's hands",
                shadow_behavior="soft ledge shadows around her coat sleeves",
            ),
            atmosphere=FrameAtmosphere(
                ambient_motion="the pager's alert light strobing against damp concrete",
            ),
            foreground_objects=["signal pager"],
            midground_detail="Nova under the parapet lip with the pager close to camera",
            background_depth="the skyline blown into muted bokeh",
        ),
        composition=FrameComposition(
            shot="medium close-up",
            angle="eye_level",
            movement="slow_push",
            focus="prop",
        ),
        background=FrameBackground(
            camera_facing="east",
            visible_description="the surveillance district skyline blinking beyond the ledge",
            background_sound="distant traffic wash",
        ),
        directing=FrameDirecting(
            dramatic_purpose="pivot from surveillance into immediate threat",
            beat_turn="Nova understands the watchers are moving early",
            pov_owner=cast.cast_id,
            viewer_knowledge_delta="The warning has arrived too late to feel safe",
            power_dynamic="The unseen watchers hold the tempo",
            tension_source="The pager compresses the time Nova has left",
            camera_motivation="Push toward the pager as the warning lands",
            movement_motivation="Let Nova's hands absorb the shock of the alert",
            movement_path="coat pocket to eye line",
            reaction_target=prop.prop_id,
            background_life="the skyline keeps flashing behind the message",
        ),
        action_summary="Nova pulls the pager from her coat and reads the alert.",
        suggested_duration=5,
        continuity_chain=True,
        previous_frame_id="f_001",
        provenance=_prov("Nova reads the pager warning and says they are early."),
    )
    graph.frames[frame_1.frame_id] = frame_1
    graph.frames[frame_2.frame_id] = frame_2
    graph.frame_order.extend([frame_1.frame_id, frame_2.frame_id])

    graph.dialogue["dlg_001"] = DialogueNode(
        dialogue_id="dlg_001",
        scene_id=scene.scene_id,
        order=1,
        speaker=cast.name,
        cast_id=cast.cast_id,
        start_frame="f_002",
        end_frame="f_002",
        primary_visual_frame="f_002",
        line="[hushed] They're early.",
        raw_line="They're early.",
        performance_direction="hushed",
        env_intensity="quiet",
        env_atmosphere=["traffic wash"],
        provenance=_prov("Nova quietly says they are early."),
    )
    graph.dialogue_order.append("dlg_001")

    graph.cast_frame_states["cast_nova@f_001"] = CastFrameState(
        cast_id=cast.cast_id,
        frame_id="f_001",
        frame_role=CastFrameRole.SUBJECT,
        posture=Posture.STANDING,
        emotion="wary",
        screen_position="frame_left",
        looking_at="distance",
        provenance=_prov("Nova watches the district from frame left."),
    )
    graph.cast_frame_states["cast_nova@f_002"] = CastFrameState(
        cast_id=cast.cast_id,
        frame_id="f_002",
        frame_role=CastFrameRole.SUBJECT,
        posture=Posture.STANDING,
        action="reading the vibrating pager",
        emotion="determined",
        screen_position="frame_center",
        props_held=[prop.prop_id],
        provenance=_prov("Nova reads the vibrating pager at center frame."),
    )
    graph.prop_frame_states["prop_signal_pager@f_002"] = PropFrameState(
        prop_id=prop.prop_id,
        frame_id="f_002",
        holder_cast_id=cast.cast_id,
        spatial_position="in_hand",
        frame_role=PropFrameRole.ACTIVE_HELD,
        provenance=_prov("The pager stays in Nova's hand."),
    )
    graph.location_frame_states["loc_rooftop@f_002"] = LocationFrameState(
        location_id=location.location_id,
        frame_id="f_002",
        atmosphere_override="colder and more compressed as the pager lights her face",
        condition_modifiers=["fresh rain beads on the ledge"],
        lighting_override="the pager glow adds a sharp green accent to the roofline",
        provenance=_prov("The pager changes how the rooftop reads on Nova's face."),
    )

    return graph


def _materialize_and_prompt(graph: NarrativeGraph, project_dir: Path) -> tuple[dict, dict]:
    return materialize_all(graph, project_dir), assemble_all_prompts(graph, project_dir)


def _generate_image_from_prompt(prompt_data: dict, project_dir: Path) -> dict:
    output_path = project_dir / prompt_data["out_path"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    reference_images = []
    for ref in prompt_data.get("ref_images", []):
        ref_path = project_dir / ref
        if ref_path.exists():
            reference_images.append(str(ref_path))

    body = {
        "prompt": prompt_data["prompt"],
        "image_size": prompt_data.get("size", "landscape_16_9"),
        "output_path": str(output_path),
        "output_format": "png",
        "reference_images": reference_images,
    }
    result = _post_json(f"{SERVER_URL}/internal/generate-frame", body, IMAGE_TIMEOUT_SECONDS)
    return {
        "response": result,
        "output_path": str(output_path),
        "size_bytes": output_path.stat().st_size,
    }


def _generate_storyboard_from_prompt(prompt_data: dict, project_dir: Path) -> dict:
    output_dir = project_dir / prompt_data["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    grid = prompt_data["grid"]
    spec = GRID_SPECS[grid]
    guide = generate_grid_guide(grid, output_dir)

    cell_block = "\n\n".join(
        f"[Cell {idx + 1}] {cell_prompt}"
        for idx, cell_prompt in enumerate(prompt_data["cell_prompts"])
    )
    prompt = PROMPT_TEMPLATE.format(
        cols=spec["cols"],
        rows=spec["rows"],
        cell_prompts=cell_block,
    )

    reference_images = [str(guide)]
    for ref in prompt_data.get("refs", []):
        ref_path = project_dir / ref
        if ref_path.exists():
            reference_images.append(str(ref_path))

    composite_path = output_dir / "composite.png"
    body = {
        "prompt": prompt,
        "image_size": "landscape_16_9",
        "output_path": str(composite_path),
        "output_format": "png",
        "reference_images": reference_images,
    }
    result = _post_json(f"{SERVER_URL}/internal/generate-frame", body, IMAGE_TIMEOUT_SECONDS)
    frame_ids = prompt_data.get("frame_ids", [])
    frame_paths = split_grid(composite_path, grid, output_dir / "frames",
                             frame_ids=frame_ids)
    return {
        "response": result,
        "output_path": str(composite_path),
        "frame_paths": [str(path) for path in frame_paths],
        "size_bytes": composite_path.stat().st_size,
    }


def _generate_video_from_prompt(prompt_data: dict, project_dir: Path) -> dict:
    output_path = project_dir / "video" / "clips" / f"{prompt_data['frame_id']}.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    image_path = project_dir / prompt_data["input_image_path"]
    body = {
        "model": "xai/grok-imagine-video",
        "prompt": prompt_data["prompt"],
        "output_path": str(output_path),
        "duration": int(prompt_data["duration"]),
        "image_path": str(image_path),
        "resolution": "720p",
        "extra_params": {},
    }
    result = _post_json(f"{SERVER_URL}/internal/generate-video", body, VIDEO_TIMEOUT_SECONDS)
    return {
        "response": result,
        "output_path": str(output_path),
        "size_bytes": output_path.stat().st_size,
    }


def _write_summary(
    project_dir: Path,
    materialize_counts: dict,
    prompt_counts: dict,
    responses: dict,
) -> None:
    image_prompt = _read_json(project_dir / "frames" / "prompts" / "f_002_image.json")
    video_prompt = _read_json(project_dir / "video" / "prompts" / "f_002_video.json")
    storyboard_prompt = _read_json(project_dir / "frames" / "storyboard_prompts" / "grid_01_grid.json")
    manifest = _read_json(project_dir / "project_manifest.json")

    summary = {
        "project_dir": str(project_dir),
        "materialize_counts": materialize_counts,
        "prompt_counts": prompt_counts,
        "manifest_status": manifest.get("status"),
        "responses": responses,
        "key_expectations": {
            "frame_prompt_ref_images": image_prompt["ref_images"],
            "video_prompt_input_image_path": video_prompt["input_image_path"],
            "video_prompt_dialogue_line": video_prompt["dialogue_line"],
            "storyboard_grid": storyboard_prompt["grid"],
        },
    }
    _write_text(project_dir, SUMMARY_PATH.name, json.dumps(summary, indent=2))


class PipelineSmokeE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _server_available():
            raise RuntimeError(f"Live smoke requires a running server at {SERVER_URL}")

        PROJECT_DIR.mkdir(parents=True, exist_ok=True)

        graph = build_live_smoke_graph(PROJECT_DIR)
        cls.store = GraphStore(PROJECT_DIR)
        cls.store.save(graph)
        graph = cls.store.load()

        build_storyboard_grids(graph)
        cls.store.save(graph)
        graph = cls.store.load()

        cls.materialize_counts, cls.prompt_counts = _materialize_and_prompt(graph, PROJECT_DIR)
        cls.responses = {}

        cls.responses["cast_nova"] = _generate_image_from_prompt(
            _read_json(PROJECT_DIR / "cast" / "prompts" / "cast_nova_composite.json"),
            PROJECT_DIR,
        )
        cls.responses["loc_rooftop"] = _generate_image_from_prompt(
            _read_json(PROJECT_DIR / "locations" / "prompts" / "loc_rooftop_location.json"),
            PROJECT_DIR,
        )
        cls.responses["prop_signal_pager"] = _generate_image_from_prompt(
            _read_json(PROJECT_DIR / "props" / "prompts" / "prop_signal_pager_prop.json"),
            PROJECT_DIR,
        )

        storyboard_prompt = _read_json(
            PROJECT_DIR / "frames" / "storyboard_prompts" / "grid_01_grid.json"
        )
        cls.responses["storyboard_grid_01"] = _generate_storyboard_from_prompt(
            storyboard_prompt,
            PROJECT_DIR,
        )

        graph = cls.store.load()
        grid = graph.storyboard_grids["grid_01"]
        grid.composite_image_path = "frames/storyboards/grid_01/composite.png"
        grid.cell_image_dir = "frames/storyboards/grid_01/frames"
        grid.storyboard_status = "generated"
        cls.store.save(graph)
        graph = cls.store.load()

        cls.materialize_counts, cls.prompt_counts = _materialize_and_prompt(graph, PROJECT_DIR)

        # Promote storyboard cell → composed frame (no separate frame generation)
        import shutil
        cell_src = PROJECT_DIR / "frames" / "storyboards" / "grid_01" / "frames" / "f_002.png"
        composed_dst = PROJECT_DIR / "frames" / "composed" / "f_002_gen.png"
        composed_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cell_src, composed_dst)
        cls.responses["frame_f_002"] = {
            "response": {"promoted_from": str(cell_src)},
            "output_path": str(composed_dst),
            "size_bytes": composed_dst.stat().st_size,
        }

        graph = cls.store.load()
        graph.frames["f_002"].composed_image_path = "frames/composed/f_002_gen.png"
        graph.frames["f_002"].status = "generated"
        cls.store.save(graph)
        graph = cls.store.load()

        cls.materialize_counts, cls.prompt_counts = _materialize_and_prompt(graph, PROJECT_DIR)

        cls.responses["video_f_002"] = _generate_video_from_prompt(
            _read_json(PROJECT_DIR / "video" / "prompts" / "f_002_video.json"),
            PROJECT_DIR,
        )

        graph = cls.store.load()
        graph.frames["f_002"].video_path = "video/clips/f_002.mp4"
        cls.store.save(graph)
        cls.graph = cls.store.load()
        cls.materialize_counts, cls.prompt_counts = _materialize_and_prompt(cls.graph, PROJECT_DIR)
        _write_summary(PROJECT_DIR, cls.materialize_counts, cls.prompt_counts, cls.responses)

    def test_live_media_outputs_exist(self) -> None:
        for rel_path in [
            "cast/composites/cast_nova_ref.png",
            "locations/primary/loc_rooftop.png",
            "props/generated/prop_signal_pager.png",
            "frames/storyboards/grid_01/composite.png",
            "frames/storyboards/grid_01/frames/f_001.png",
            "frames/storyboards/grid_01/frames/f_002.png",
            "frames/composed/f_002_gen.png",
            "video/clips/f_002.mp4",
        ]:
            self.assertTrue((PROJECT_DIR / rel_path).exists(), rel_path)

        for rel_path in [
            "cast/composites/cast_nova_ref.png",
            "locations/primary/loc_rooftop.png",
            "props/generated/prop_signal_pager.png",
            "frames/storyboards/grid_01/composite.png",
            "frames/composed/f_002_gen.png",
        ]:
            self.assertGreater((PROJECT_DIR / rel_path).stat().st_size, MIN_IMAGE_BYTES, rel_path)

        self.assertGreater(
            (PROJECT_DIR / "video" / "clips" / "f_002.mp4").stat().st_size,
            MIN_VIDEO_BYTES,
        )

    def test_prompt_assembly_matches_live_graph_connections(self) -> None:
        context = get_frame_context(self.graph, "f_002")
        manifest = _read_json(PROJECT_DIR / "project_manifest.json")
        frames = {frame["frameId"]: frame for frame in manifest["frames"]}
        image_prompt = _read_json(PROJECT_DIR / "frames" / "prompts" / "f_002_image.json")
        video_prompt = _read_json(PROJECT_DIR / "video" / "prompts" / "f_002_video.json")
        storyboard_prompt = _read_json(PROJECT_DIR / "frames" / "storyboard_prompts" / "grid_01_grid.json")

        self.assertEqual(len(context["cast_states"]), 1)
        self.assertEqual(len(context["prop_states"]), 1)
        self.assertEqual(
            context["location_state"]["lighting_override"],
            "the pager glow adds a sharp green accent to the roofline",
        )

        self.assertEqual(
            image_prompt["ref_images"],
            [
                "frames/storyboards/grid_01/frames/f_002.png",
                "cast/composites/cast_nova_ref.png",
                "locations/primary/loc_rooftop.png",
                "props/generated/prop_signal_pager.png",
            ],
        )
        self.assertIn("sample storyboard image provided", image_prompt["prompt"])
        self.assertIn("reading the vibrating pager", image_prompt["prompt"])
        self.assertIn("dramatic purpose: pivot from surveillance into immediate threat", image_prompt["prompt"])

        self.assertEqual(video_prompt["input_image_path"], "frames/composed/f_002_gen.png")
        self.assertEqual(video_prompt["dialogue_line"], "They're early.")
        self.assertEqual(video_prompt["target_api"], "grok-video")
        self.assertIn(
            'Nova(30s, female, lean build, shoulder-length auburn hair, wearing weatherproof black coat over a charcoal sweater): "They\'re early."',
            video_prompt["prompt"],
        )

        self.assertEqual(storyboard_prompt["grid"], "3x3")
        self.assertEqual(storyboard_prompt["frame_ids"], ["f_001", "f_002"])
        self.assertEqual(
            storyboard_prompt["refs"],
            [
                "cast/composites/cast_nova_ref.png",
                "locations/primary/loc_rooftop.png",
            ],
        )

        self.assertEqual(frames["f_002"]["castIds"], ["cast_nova"])
        self.assertEqual(frames["f_002"]["propIds"], ["prop_signal_pager"])
        self.assertEqual(frames["f_002"]["generatedImagePath"], "frames/composed/f_002_gen.png")
        self.assertEqual(frames["f_002"]["videoPath"], "video/clips/f_002.mp4")

    def test_summary_records_real_generation_metadata(self) -> None:
        summary = _read_json(SUMMARY_PATH)

        self.assertEqual(summary["project_dir"], str(PROJECT_DIR))
        for key in [
            "cast_nova",
            "loc_rooftop",
            "prop_signal_pager",
            "storyboard_grid_01",
            "frame_f_002",
            "video_f_002",
        ]:
            self.assertIn(key, summary["responses"])
            self.assertIn("response", summary["responses"][key])

        # API-generated assets have prediction_id; promoted frames do not
        for key in ["cast_nova", "loc_rooftop", "prop_signal_pager",
                    "storyboard_grid_01", "video_f_002"]:
            self.assertIn("prediction_id", summary["responses"][key]["response"])

        # frame_f_002 is promoted from storyboard cell, not API-generated
        self.assertIn("promoted_from", summary["responses"]["frame_f_002"]["response"])


if __name__ == "__main__":
    unittest.main()

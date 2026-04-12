from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph.api import build_storyboard_grids
from graph.prompt_assembler import (
    MAX_VIDEO_PROMPT_CHARS,
    _serialize_video_prompt_sections,
    assemble_all_prompts,
    assemble_image_prompt,
    assemble_video_prompt,
)
from graph.reference_collector import ReferenceImageCollector
from graph.schema import (
    CastFrameRole,
    CastFrameState,
    CastIdentity,
    CastNode,
    CastVoice,
    NarrativeRole,
    Posture,
    Provenance,
)
from tests.test_pipeline_smoke_e2e import build_live_smoke_graph


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _span_graph(tmp_path: Path):
    graph = build_live_smoke_graph(tmp_path)

    rafe = CastNode(
        cast_id="cast_rafe",
        name="Rafe",
        identity=CastIdentity(
            age_descriptor="40s",
            gender="male",
            build="broad",
            hair_color="black",
            hair_length="short",
            physical_description="broad man with cropped black hair and a tired guarded stare",
            wardrobe_description="dark field jacket over a rain-marked button-down",
        ),
        voice=CastVoice(
            voice_description="rough baritone worn down by the weather",
            tone="guarded",
            delivery_style="contained",
            tempo="measured",
        ),
        role=NarrativeRole.ALLY,
        provenance=Provenance(source_prose_chunk="Rafe receives Nova's warning."),
    )
    graph.cast[rafe.cast_id] = rafe

    scene = graph.scenes["scene_01"]
    if rafe.cast_id not in scene.cast_present:
        scene.cast_present.append(rafe.cast_id)

    frame_1 = graph.frames["f_001"]
    frame_2 = graph.frames["f_002"]

    frame_1.is_dialogue = True
    frame_1.dialogue_ids = ["dlg_001"]
    frame_1.action_summary = "Nova clocks the warning and turns toward Rafe before the full line lands."
    frame_1.suggested_duration = None
    frame_1.next_frame_id = "f_002"

    frame_2.action_summary = "Nova delivers the warning directly to Rafe."
    frame_2.suggested_duration = None
    frame_2.dialogue_ids = ["dlg_001"]
    frame_2.next_frame_id = "f_003"

    frame_3 = frame_2.model_copy(
        deep=True,
        update={
            "frame_id": "f_003",
            "sequence_index": 3,
            "narrative_beat": "Rafe absorbs the warning while Nova's line finishes over him.",
            "source_text": "Rafe freezes as Nova finishes warning him that the stairwell is compromised.",
            "action_summary": "Rafe locks up, absorbing the warning while Nova finishes speaking off camera.",
            "previous_frame_id": "f_002",
            "next_frame_id": None,
            "suggested_duration": None,
            "dialogue_ids": ["dlg_001"],
            "directing": frame_2.directing.model_copy(
                update={
                    "dramatic_purpose": "land the warning on Rafe instead of replaying Nova's face",
                    "reaction_target": rafe.cast_id,
                }
            ),
        },
    )
    graph.frames["f_003"] = frame_3
    graph.frame_order = ["f_001", "f_002", "f_003"]
    scene.frame_ids = graph.frame_order[:]
    scene.frame_count = len(scene.frame_ids)

    graph.dialogue["dlg_001"].start_frame = "f_001"
    graph.dialogue["dlg_001"].end_frame = "f_003"
    graph.dialogue["dlg_001"].primary_visual_frame = "f_002"
    graph.dialogue["dlg_001"].reaction_frame_ids = ["f_003"]
    graph.dialogue["dlg_001"].line = "[urgent, under breath] They jumped the checkpoint early, cut the feeds, and they'll hit the stairwell before backup can seal the doors."
    graph.dialogue["dlg_001"].raw_line = "They jumped the checkpoint early, cut the feeds, and they'll hit the stairwell before backup can seal the doors."
    graph.dialogue["dlg_001"].performance_direction = "urgent, under breath"
    graph.dialogue["dlg_001"].env_intensity = "urgent"

    graph.cast_frame_states["cast_rafe@f_001"] = CastFrameState(
        cast_id=rafe.cast_id,
        frame_id="f_001",
        frame_role=CastFrameRole.OBJECT,
        posture=Posture.STANDING,
        emotion="wary",
        screen_position="frame_right",
        looking_at="cast_nova",
        provenance=Provenance(source_prose_chunk="Rafe waits on Nova's right."),
    )
    graph.cast_frame_states["cast_rafe@f_002"] = CastFrameState(
        cast_id=rafe.cast_id,
        frame_id="f_002",
        frame_role=CastFrameRole.PARTIAL,
        posture=Posture.STANDING,
        emotion="startled",
        screen_position="frame_right",
        looking_at="cast_nova",
        provenance=Provenance(source_prose_chunk="Rafe stays in partial over-shoulder coverage."),
    )
    graph.cast_frame_states["cast_rafe@f_003"] = CastFrameState(
        cast_id=rafe.cast_id,
        frame_id="f_003",
        frame_role=CastFrameRole.SUBJECT,
        posture=Posture.STANDING,
        emotion="startled",
        screen_position="frame_center",
        looking_at="cast_nova",
        provenance=Provenance(source_prose_chunk="Rafe absorbs the warning head-on."),
    )
    graph.cast_frame_states["cast_nova@f_003"] = CastFrameState(
        cast_id="cast_nova",
        frame_id="f_003",
        frame_role=CastFrameRole.PARTIAL,
        posture=Posture.STANDING,
        emotion="determined",
        screen_position="frame_left",
        looking_at="cast_rafe",
        provenance=Provenance(source_prose_chunk="Nova stays as partial foreground coverage."),
    )

    return graph


def test_storyboard_grids_split_on_dialogue_turn(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)

    grids = build_storyboard_grids(graph)

    assert [grid.frame_ids for grid in grids] == [["f_001"], ["f_002"]]
    assert [grid.break_reason for grid in grids] == ["dialogue_turn_change", "end"]
    assert [(grid.rows, grid.cols) for grid in grids] == [(1, 1), (1, 1)]


def test_structured_image_prompt_uses_shot_packet_and_no_dialogue_text(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    build_storyboard_grids(graph)

    prompt = assemble_image_prompt(graph, "f_002", project_dir=tmp_path)

    assert prompt["shot_packet_path"] == "frames/shot_packets/f_002.json"
    assert prompt["dialogue_present"] is True
    assert "SHOT INTENT:" in prompt["prompt"]
    assert "CONTINUITY:" in prompt["prompt"]
    assert "POSE LOCK:" in prompt["prompt"]
    assert "AUDIO CONTEXT:" in prompt["prompt"]
    assert "NEGATIVE CONSTRAINTS:" in prompt["prompt"]
    assert "They're early." not in prompt["prompt"]
    assert "Do not render subtitles" in prompt["prompt"]
    assert prompt["cast_bible_snapshot"]["frame_id"] == "f_002"


def test_cast_bible_sync_produces_frame_scoped_pose_lock(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)

    bible = ReferenceImageCollector(graph, tmp_path).build_cast_bible(
        sequence_id=graph.project.project_id,
    )
    sheet = bible.characters["cast_nova"]
    pose = sheet.pose_for_frame("f_002")

    assert pose is not None
    assert pose.frame_id == "f_002"
    assert pose.pose.startswith("standing")
    assert any(mod.startswith("screen_position:") for mod in pose.modifiers)


def test_structured_video_prompt_keeps_dialogue_in_audio_section(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    build_storyboard_grids(graph)

    prompt = assemble_video_prompt(graph, "f_002")

    assert prompt["target_api"] == "grok-video"
    assert prompt["dialogue_present"] is True
    assert prompt["dialogue_line"] == "They're early."
    assert "AUDIO:" in prompt["prompt"]
    assert '"They\'re early."' in prompt["prompt"]
    assert "No subtitles" in prompt["prompt"]


def test_structured_video_prompt_prefers_upstream_optimized_block(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.frames["f_002"].video_optimized_prompt_block = (
        "Nova leans into frame under cold corridor light, warning landing fast while the background hum stays present."
    )

    prompt = assemble_video_prompt(graph, "f_002")

    assert prompt["video_optimized_prompt_block"] == graph.frames["f_002"].video_optimized_prompt_block
    assert graph.frames["f_002"].video_optimized_prompt_block in prompt["prompt"]


def test_assemble_all_prompts_writes_shot_packets(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    build_storyboard_grids(graph)

    counts = assemble_all_prompts(graph, tmp_path)

    assert counts["shot_packets"] == 2
    packet = _load_json(tmp_path / "frames" / "shot_packets" / "f_002.json")
    image_prompt = _load_json(tmp_path / "frames" / "prompts" / "f_002_image.json")
    video_prompt = _load_json(tmp_path / "video" / "prompts" / "f_002_video.json")

    assert packet["audio"]["dialogue_present"] is True
    assert packet["subject_count"] == 1
    assert image_prompt["shot_packet_path"] == "frames/shot_packets/f_002.json"
    assert video_prompt["shot_packet_path"] == "frames/shot_packets/f_002.json"


def test_dialogue_span_roles_surface_in_prompt_metadata_and_sections(tmp_path: Path) -> None:
    graph = _span_graph(tmp_path)

    prelap_prompt = assemble_image_prompt(graph, "f_001", project_dir=tmp_path)
    speaker_prompt = assemble_video_prompt(graph, "f_002")
    reaction_prompt = assemble_video_prompt(graph, "f_003")

    assert prelap_prompt["dialogue_coverage_roles"] == ["prelap_entry"]
    assert speaker_prompt["dialogue_coverage_roles"] == ["speaker_sync"]
    assert reaction_prompt["dialogue_coverage_roles"] == ["listener_reaction"]
    assert "DIALOGUE COVERAGE:" in prelap_prompt["prompt"]
    assert "change only one visual axis" in speaker_prompt["prompt"]
    assert "AUDIO:" in speaker_prompt["prompt"]
    assert "listener reaction" in reaction_prompt["prompt"]


def test_dialogue_span_duration_is_distributed_across_frames(tmp_path: Path) -> None:
    graph = _span_graph(tmp_path)

    prelap_prompt = assemble_video_prompt(graph, "f_001")
    speaker_prompt = assemble_video_prompt(graph, "f_002")
    reaction_prompt = assemble_video_prompt(graph, "f_003")

    assert prelap_prompt["dialogue_fit_status"] == "span_allocated"
    assert speaker_prompt["dialogue_fit_status"] == "span_allocated"
    assert reaction_prompt["dialogue_fit_status"] == "span_allocated"

    assert 2 <= prelap_prompt["duration"] <= 15
    assert 2 <= speaker_prompt["duration"] <= 15
    assert 2 <= reaction_prompt["duration"] <= 15
    assert speaker_prompt["duration"] >= prelap_prompt["duration"]
    assert speaker_prompt["duration"] >= reaction_prompt["duration"]

    allocation = speaker_prompt["duration_allocation_details"][0]
    assert allocation["dialogue_id"] == "dlg_001"
    assert allocation["span_index"] == 2
    assert allocation["span_length"] == 3
    assert allocation["allocated_seconds"] >= 2


def test_video_prompt_serializer_drops_background_before_tier1_blocks() -> None:
    sections = [
        "Generate a cinematic motion clip.\nKeep the beat intact.\nMatch the previous framing.",
        "SHOT INTENT:\nHold the speaker in a medium close-up.\nKeep the listener soft in frame.",
        "BACKGROUND:\n" + "\n".join(f"Layered atmospheric detail {idx}" for idx in range(400)),
        "MOTION CONTINUITY:\n" + "\n".join(f"Motion cue {idx}" for idx in range(40)),
        "AUDIO:\n" + "\n".join(f'Speaker: "Line {idx}" | delivery urgent, clipped, breath held' for idx in range(40)),
    ]

    prompt = _serialize_video_prompt_sections(sections)

    assert len(prompt) <= MAX_VIDEO_PROMPT_CHARS
    assert "BACKGROUND:" not in prompt
    assert "MOTION CONTINUITY:" in prompt
    assert "AUDIO:" in prompt


def test_video_prompt_serializer_raises_when_tier1_alone_exceeds_limit() -> None:
    sections = [
        "MOTION CONTINUITY:\n" + "\n".join("x" * 220 for _ in range(12)),
        "AUDIO:\n" + "\n".join("y" * 220 for _ in range(12)),
    ]

    with pytest.raises(ValueError, match="tier1_block_sizes"):
        _serialize_video_prompt_sections(sections)


def test_assemble_video_prompt_rejects_sparse_shot_packet(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.frames["f_002"].composition.movement = None

    with pytest.raises(ValueError, match="incomplete shot packet"):
        assemble_video_prompt(graph, "f_002")

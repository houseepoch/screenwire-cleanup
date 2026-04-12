from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from graph.api import build_shot_packet, build_storyboard_grids
from graph.prompt_assembler import (
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


def test_storyboard_grids_keep_small_sequential_beats_together(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)

    grids = build_storyboard_grids(graph)

    assert [grid.frame_ids for grid in grids] == [["f_001", "f_002"]]
    assert [grid.break_reason for grid in grids] == ["end"]
    assert [(grid.rows, grid.cols) for grid in grids] == [(1, 2)]


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
    assert "Recent transition trail:" not in prompt["prompt"]
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


def test_reference_collector_stitches_three_plus_cast_refs(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)

    additions = [
        ("cast_blaire", "Blaire", "frame_left", (220, 120, 120, 255)),
        ("cast_hudson", "Hudson", "frame_center", (120, 180, 220, 255)),
        ("cast_sedona", "Sedona", "frame_right", (180, 140, 220, 255)),
    ]
    for cast_id, name, screen_position, color in additions:
        graph.cast[cast_id] = CastNode(
            cast_id=cast_id,
            name=name,
            identity=CastIdentity(
                age_descriptor="30s",
                physical_description=f"{name} reference portrait",
                wardrobe_description=f"{name} wardrobe",
            ),
            voice=CastVoice(),
            provenance=Provenance(source_prose_chunk=f"{name} added for stitched ref test."),
            composite_path=f"cast/composites/{cast_id}_ref.png",
        )
        graph.cast_frame_states[f"{cast_id}@f_002"] = CastFrameState(
            cast_id=cast_id,
            frame_id="f_002",
            frame_role=CastFrameRole.SUBJECT,
            screen_position=screen_position,
            posture=Posture.STANDING,
            looking_at="cast_nova",
            provenance=Provenance(source_prose_chunk=f"{name} visible in frame."),
        )
        ref_path = tmp_path / "cast" / "composites" / f"{cast_id}_ref.png"
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (512, 768), color).save(ref_path)

    collector = ReferenceImageCollector(graph, tmp_path)
    refs = collector.get_flat_reference_list("f_002")
    stitched = [path for path in refs if "group_refs" in str(path)]

    assert len(stitched) == 1
    assert stitched[0].exists()
    assert not any(path.name == "cast_blaire_ref.png" for path in refs)
    with Image.open(stitched[0]) as image:
        assert image.width > image.height


def test_group_cast_image_prompt_uses_stitched_group_guidance(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)

    additions = [
        ("cast_blaire", "Blaire", "frame_left"),
        ("cast_hudson", "Hudson", "frame_center"),
        ("cast_sedona", "Sedona", "frame_right"),
    ]
    for cast_id, name, screen_position in additions:
        graph.cast[cast_id] = CastNode(
            cast_id=cast_id,
            name=name,
            identity=CastIdentity(
                age_descriptor="30s",
                physical_description=f"{name} reference portrait",
                wardrobe_description=f"{name} wardrobe",
            ),
            voice=CastVoice(),
            provenance=Provenance(source_prose_chunk=f"{name} added for group prompt test."),
        )
        graph.cast_frame_states[f"{cast_id}@f_002"] = CastFrameState(
            cast_id=cast_id,
            frame_id="f_002",
            frame_role=CastFrameRole.SUBJECT,
            posture=Posture.STANDING,
            screen_position=screen_position,
            looking_at="cast_nova",
            provenance=Provenance(source_prose_chunk=f"{name} visible in frame."),
        )

    graph.frames["f_002"].action_summary = "The group crowds together as Nova absorbs the warning."
    prompt = assemble_image_prompt(graph, "f_002", project_dir=tmp_path)

    assert "POSE LOCK:" not in prompt["prompt"]
    assert "Use the stitched group cast reference as the authority" in prompt["prompt"]
    assert "Visible cast left-to-right in the frame:" in prompt["prompt"]
    assert "Use the stitched group cast reference as the authoritative left-to-right blocking map." in prompt["prompt"]


def test_large_group_prompt_uses_tableau_guidance_and_drops_roster_delta(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)

    additions = [
        ("cast_blaire", "Blaire", "frame_left"),
        ("cast_hudson", "Hudson", "frame_center_left"),
        ("cast_sedona", "Sedona", "frame_center"),
        ("cast_rowan", "Rowan", "frame_center_right"),
        ("cast_monday", "Monday", "frame_right"),
    ]
    for cast_id, name, screen_position in additions:
        graph.cast[cast_id] = CastNode(
            cast_id=cast_id,
            name=name,
            identity=CastIdentity(
                age_descriptor="30s",
                physical_description=f"{name} reference portrait",
                wardrobe_description=f"{name} wardrobe",
            ),
            voice=CastVoice(),
            provenance=Provenance(source_prose_chunk=f"{name} added for large group prompt test."),
        )
        graph.cast_frame_states[f"{cast_id}@f_002"] = CastFrameState(
            cast_id=cast_id,
            frame_id="f_002",
            frame_role=CastFrameRole.SUBJECT,
            posture=Posture.STANDING,
            screen_position=screen_position,
            looking_at="cast_nova",
            provenance=Provenance(source_prose_chunk=f"{name} visible in frame."),
        )

    graph.frames["f_002"].action_summary = "The full group surges into frame around Nova in one chaotic welcoming tableau."
    prompt = assemble_image_prompt(graph, "f_002", project_dir=tmp_path)

    assert "Preserve the full visible ensemble as one coherent group tableau with 6 people." in prompt["prompt"]
    assert "Treat the remaining visible cast as one coherent moving cluster rather than isolated hero poses." in prompt["prompt"]
    assert "Cast entering frame:" not in prompt["prompt"]


def test_profile_two_shot_prompt_rewrites_blocking_and_drops_pose_lock(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    lyra = CastNode(
        cast_id="cast_lyra",
        name="Lyra",
        identity=CastIdentity(
            age_descriptor="30s",
            physical_description="focused woman with a guarded stare",
            wardrobe_description="dark jacket over a soft knit top",
        ),
        voice=CastVoice(),
        provenance=Provenance(source_prose_chunk="Lyra challenges Nova in a profile faceoff."),
    )
    graph.cast[lyra.cast_id] = lyra
    graph.cast_frame_states["cast_lyra@f_002"] = CastFrameState(
        cast_id=lyra.cast_id,
        frame_id="f_002",
        frame_role=CastFrameRole.SUBJECT,
        posture=Posture.STANDING,
        action="holds the accusation",
        screen_position="frame_right",
        looking_at="cast_nova",
        provenance=Provenance(source_prose_chunk="Lyra faces Nova directly."),
    )
    graph.frames["f_002"].cinematic_tag.ai_prompt_language = (
        "Profile 50/50. Both in profile, facing each other, frame split down the middle."
    )
    graph.frames["f_002"].action_summary = "Nova and Lyra square off in a profile confrontation."

    prompt = assemble_image_prompt(graph, "f_002", project_dir=tmp_path)

    assert "POSE LOCK:" not in prompt["prompt"]
    assert "- Nova | at frame_left | facing profile_right" in prompt["prompt"]
    assert "- Lyra | at frame_right | facing profile_left" in prompt["prompt"]


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


def test_assemble_all_prompts_clears_stale_storyboard_prompt_files(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    build_storyboard_grids(graph)

    stale = tmp_path / "frames" / "storyboard_prompts" / "grid_stale_grid.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}", encoding="utf-8")

    counts = assemble_all_prompts(graph, tmp_path)

    assert counts["storyboard_prompts"] == 0
    assert not stale.exists()


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


def test_video_prompt_serializer_preserves_background_and_tier1_blocks() -> None:
    sections = [
        "Generate a cinematic motion clip.\nKeep the beat intact.\nMatch the previous framing.",
        "SHOT INTENT:\nHold the speaker in a medium close-up.\nKeep the listener soft in frame.",
        "BACKGROUND:\n" + "\n".join(f"Layered atmospheric detail {idx}" for idx in range(400)),
        "MOTION CONTINUITY:\n" + "\n".join(f"Motion cue {idx}" for idx in range(40)),
        "AUDIO:\n" + "\n".join(f'Speaker: "Line {idx}" | delivery urgent, clipped, breath held' for idx in range(40)),
    ]

    prompt = _serialize_video_prompt_sections(sections)

    assert "BACKGROUND:" in prompt
    assert "MOTION CONTINUITY:" in prompt
    assert "AUDIO:" in prompt


def test_video_prompt_serializer_preserves_very_large_tier1_blocks() -> None:
    sections = [
        "MOTION CONTINUITY:\n" + "\n".join("x" * 220 for _ in range(12)),
        "AUDIO:\n" + "\n".join("y" * 220 for _ in range(12)),
    ]

    prompt = _serialize_video_prompt_sections(sections)

    assert "MOTION CONTINUITY:" in prompt
    assert "AUDIO:" in prompt
    assert len(prompt) > 4096


def test_assemble_video_prompt_rejects_sparse_shot_packet(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.frames["f_002"].composition.movement = None

    with pytest.raises(ValueError, match="incomplete shot packet"):
        assemble_video_prompt(graph, "f_002")


def test_screen_presence_prompt_counts_on_screen_subject(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    graph.frames["f_002"].action_summary = (
        "Nova smiles serenely on the laptop screen while the group chat erupts with excited confirmations."
    )
    graph.frames["f_002"].narrative_beat = graph.frames["f_002"].action_summary
    graph.frames["f_002"].is_dialogue = False
    graph.frames["f_002"].dialogue_ids = []
    graph.cast_frame_states = {
        key: value for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_002")
    }

    packet = build_shot_packet(graph, "f_002")
    prompt = assemble_image_prompt(graph, "f_002", project_dir=tmp_path)

    assert packet.subject_count == 1
    assert packet.visible_cast_ids == ["cast_nova"]
    assert "Exactly 1 visible subject(s), counted within the laptop or video-call screen." in prompt["prompt"]
    assert "Treat the on-screen caller or listener as the visible subject" in prompt["prompt"]
    assert "LOCATION INVARIANTS:" not in prompt["prompt"]
    assert "POSE LOCK:" not in prompt["prompt"]
    assert "DIALOGUE COVERAGE:" not in prompt["prompt"]
    assert "BLOCKING:" not in prompt["prompt"]


def test_pure_black_prompt_drops_cinematic_scene_baggage(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    frame = graph.frames["f_001"]
    frame.action_summary = "The screen cuts abruptly to pure black."
    frame.narrative_beat = frame.action_summary
    frame.source_text = frame.action_summary
    frame.dialogue_ids = []
    frame.is_dialogue = False
    graph.cast_frame_states = {
        key: value for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_001")
    }

    prompt = assemble_image_prompt(graph, "f_001", project_dir=tmp_path)

    assert "Output clean pure black only." in prompt["prompt"]
    assert "LOCATION INVARIANTS:" not in prompt["prompt"]
    assert "POSE LOCK:" not in prompt["prompt"]
    assert "Exactly 0 visible human subject(s)." in prompt["prompt"]
    assert "Render pure black only." in prompt["prompt"]


def test_fade_to_black_transition_is_classified_as_pure_black(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    frame = graph.frames["f_001"]
    frame.action_summary = "The image fades to black. After a beat, the screen lights up again for the post-credits scene."
    frame.narrative_beat = frame.action_summary
    frame.source_text = frame.action_summary
    frame.dialogue_ids = []
    frame.is_dialogue = False
    graph.cast_frame_states = {
        key: value for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_001")
    }

    image_prompt = assemble_image_prompt(graph, "f_001", project_dir=tmp_path)
    video_prompt = assemble_video_prompt(graph, "f_001")

    assert "Output clean pure black only." in image_prompt["prompt"]
    assert "LOCATION INVARIANTS:" not in image_prompt["prompt"]
    assert "Hold on full-frame pure black with no visible imagery." in video_prompt["prompt"]
    assert "LOCATION INVARIANTS:" not in video_prompt["prompt"]


def test_hand_object_prompt_keeps_anonymous_hand_constraint(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    frame = graph.frames["f_001"]
    frame.action_summary = "A single hand lowers into frame and crushes the pager against the tabletop."
    frame.narrative_beat = frame.action_summary
    frame.source_text = frame.action_summary
    frame.dialogue_ids = []
    frame.is_dialogue = False
    graph.cast_frame_states = {
        key: value for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_001")
    }

    packet = build_shot_packet(graph, "f_001")
    prompt = assemble_image_prompt(graph, "f_001", project_dir=tmp_path)

    assert packet.subject_count == 1
    assert "Exactly 1 visible human subject(s), expressed only as anonymous hands if present." in prompt["prompt"]
    assert "Do not invent a face, torso, or additional people around the hand-driven action." in prompt["prompt"]


def test_object_macro_prompt_drops_room_level_baggage(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    frame = graph.frames["f_001"]
    frame.action_summary = "Extreme close-up of a mortar and pestle grinding bright red cherry pulp into coarse powder."
    frame.narrative_beat = frame.action_summary
    frame.source_text = frame.action_summary
    frame.dialogue_ids = []
    frame.is_dialogue = False
    graph.cast_frame_states = {
        key: value for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_001")
    }

    prompt = assemble_image_prompt(graph, "f_001", project_dir=tmp_path)

    assert "SPECIAL HANDLING:" in prompt["prompt"]
    assert "LOCATION INVARIANTS:" not in prompt["prompt"]
    assert "BACKGROUND:" not in prompt["prompt"]
    assert "BLOCKING:" not in prompt["prompt"]


def test_environment_transition_prompt_drops_prior_location_baggage(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    frame = graph.frames["f_001"]
    frame.action_summary = "The screen emerges from black into a psychedelic sunset exploding over Topanga Canyon."
    frame.narrative_beat = frame.action_summary
    frame.source_text = frame.action_summary
    frame.dialogue_ids = []
    frame.is_dialogue = False
    graph.cast_frame_states = {
        key: value for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_001")
    }

    prompt = assemble_image_prompt(graph, "f_001", project_dir=tmp_path)

    assert "SPECIAL HANDLING:" in prompt["prompt"]
    assert "landscape or environmental transition" in prompt["prompt"]
    assert "LOCATION INVARIANTS:" not in prompt["prompt"]
    assert "BLOCKING:" not in prompt["prompt"]


def test_title_card_prompt_allows_authored_text_and_drops_room_baggage(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    frame = graph.frames["f_001"]
    frame.action_summary = "Screen suddenly flashes with bold title card 'THIS IS NOT A CULT!' burning brightly"
    frame.narrative_beat = frame.action_summary
    frame.source_text = frame.action_summary
    frame.dialogue_ids = []
    frame.is_dialogue = False
    graph.cast_frame_states = {
        key: value for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_001")
    }

    prompt = assemble_image_prompt(graph, "f_001", project_dir=tmp_path)

    assert "authored title-card insert" in prompt["prompt"]
    assert "Render a bold authored title-card insert" in prompt["prompt"]
    assert "LOCATION INVARIANTS:" not in prompt["prompt"]
    assert "BLOCKING:" not in prompt["prompt"]
    assert (
        "Do not add captions, subtitles, browser chrome, logos, or any extra UI beyond the authored title text."
        in prompt["prompt"]
    )


def test_hand_tool_manipulation_uses_hand_mode_and_drops_room_baggage(tmp_path: Path) -> None:
    graph = build_live_smoke_graph(tmp_path)
    frame = graph.frames["f_001"]
    frame.action_summary = "Pestle driven rhythmically into mortar, grinding cherry pits into coarse red powder"
    frame.narrative_beat = frame.action_summary
    frame.source_text = frame.action_summary
    frame.dialogue_ids = []
    frame.is_dialogue = False
    graph.cast_frame_states = {
        key: value for key, value in graph.cast_frame_states.items()
        if not key.endswith("@f_001")
    }

    prompt = assemble_image_prompt(graph, "f_001", project_dir=tmp_path)

    assert "hand-driven action" in prompt["prompt"]
    assert "Exactly 1 visible human subject(s), expressed only as anonymous hands if present." in prompt["prompt"]
    assert "LOCATION INVARIANTS:" not in prompt["prompt"]
    assert "BLOCKING:" not in prompt["prompt"]

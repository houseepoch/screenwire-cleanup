from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from graph.schema import (
    CastFrameRole,
    CastFrameState,
    CastIdentity,
    CastNode,
    CastVoice,
    DialogueNode,
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
)


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
        provenance=_prov("Nova is the rooftop lookout carrying the pager."),
    )
    graph.cast[cast.cast_id] = cast

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
            movement="static",
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

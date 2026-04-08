"""
Graph Materializer — Export graph to flat files
=================================================

Exports the NarrativeGraph into the flat file format that existing
ScreenWire skills and downstream agents expect:
  - cast/{cast_id}.json
  - locations/{location_id}.json
  - props/{prop_id}.json
  - voices/{voice_id}.json
  - dialogue.json
  - project_manifest.json updates
  - logs/scene_coordinator/visual_analysis.json
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import NarrativeGraph


def materialize_all(graph: NarrativeGraph, project_dir: str | Path) -> dict:
    """Export the full graph to flat files. Returns summary."""
    project_dir = Path(project_dir)
    counts = {}

    counts["cast"] = materialize_cast_profiles(graph, project_dir)
    counts["locations"] = materialize_location_profiles(graph, project_dir)
    counts["props"] = materialize_prop_profiles(graph, project_dir)
    counts["voices"] = materialize_voice_profiles(graph, project_dir)
    counts["dialogue"] = materialize_dialogue(graph, project_dir / "dialogue.json")
    counts["visual_analysis"] = materialize_visual_analysis(
        graph, project_dir / "logs" / "scene_coordinator" / "visual_analysis.json"
    )
    counts["manifest"] = materialize_manifest(graph, project_dir / "project_manifest.json")

    return counts


def materialize_cast_profiles(graph: NarrativeGraph, project_dir: Path) -> int:
    """Write cast/{cast_id}.json for each cast member."""
    cast_dir = project_dir / "cast"
    cast_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for cast_id, cast in graph.cast.items():
        profile = {
            "castId": cast.cast_id,
            "name": cast.name,
            "physicalDescription": cast.identity.physical_description,
            "wardrobeDescription": cast.identity.wardrobe_description,
            "personality": cast.personality,
            "role": cast.role.value if hasattr(cast.role, 'value') else cast.role,
            "arcSummary": cast.arc_summary,
            "relationships": cast.relationships,
            "wardrobe": cast.identity.wardrobe_description or ", ".join(cast.identity.clothing),
            "firstAppearance": cast.first_appearance,
            "scenesPresent": cast.scenes_present,
            "dialogueLineCount": cast.dialogue_line_count,
            "sceneCount": cast.scene_count,
            "importanceScore": cast.importance_score,
            "voiceNotes": cast.voice_notes,
            # Full identity breakdown
            "identity": {
                "ageDescriptor": cast.identity.age_descriptor,
                "gender": cast.identity.gender,
                "ethnicity": cast.identity.ethnicity,
                "build": cast.identity.build,
                "skin": cast.identity.skin,
                "hairLength": cast.identity.hair_length,
                "hairStyle": cast.identity.hair_style,
                "hairColor": cast.identity.hair_color,
                "clothing": cast.identity.clothing,
                "clothingStyle": cast.identity.clothing_style,
                "clothingFabric": cast.identity.clothing_fabric,
                "clothingFit": cast.identity.clothing_fit,
                "footwear": cast.identity.footwear,
                "accessories": cast.identity.accessories,
            },
            # Voice sub-profile
            "voice": {
                "voiceDescription": cast.voice.voice_description,
                "qualityPrefix": cast.voice.quality_prefix,
                "tone": cast.voice.tone,
                "pitch": cast.voice.pitch,
                "accent": cast.voice.accent,
                "deliveryStyle": cast.voice.delivery_style,
                "tempo": cast.voice.tempo,
                "emotionalRange": cast.voice.emotional_range,
                "vocalStyle": cast.voice.vocal_style,
            },
            "stateVariants": {k: {"stateTag": v.state_tag, "description": v.description,
                                  "derivedFrom": v.derived_from, "imagePath": v.image_path,
                                  "triggerFrame": v.trigger_frame,
                                  "activeThrough": v.active_through}
                              for k, v in cast.state_variants.items()},
            # Generation tracking
            "compositePath": cast.composite_path,
            "compositeStatus": cast.composite_status,
            "voiceProfilePath": cast.voice_profile_path,
            "voiceStatus": cast.voice_status,
        }
        path = cast_dir / f"{cast_id}.json"
        path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        count += 1

    return count


def materialize_location_profiles(graph: NarrativeGraph, project_dir: Path) -> int:
    """Write locations/{location_id}.json for each location."""
    loc_dir = project_dir / "locations"
    loc_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for loc_id, loc in graph.locations.items():
        profile = {
            "locationId": loc.location_id,
            "name": loc.name,
            "parentLocationId": loc.parent_location_id,
            "description": loc.description,
            "atmosphere": loc.atmosphere,
            "narrativePurpose": loc.narrative_purpose,
            "locationType": loc.location_type,
            "materialPalette": loc.material_palette,
            "architectureKeywords": loc.architecture_keywords,
            "flora": loc.flora,
            "scenesUsed": loc.scenes_used,
            "timeOfDayVariants": loc.time_of_day_variants,
            "moodPerScene": loc.mood_per_scene,
            "directions": {
                d: (view.model_dump() if view else None)
                for d, view in [
                    ("north", loc.directions.north),
                    ("south", loc.directions.south),
                    ("east", loc.directions.east),
                    ("west", loc.directions.west),
                    ("exterior", loc.directions.exterior),
                ]
            } if loc.directions else {},
            "primaryImagePath": loc.primary_image_path,
            "imageStatus": loc.image_status,
        }
        path = loc_dir / f"{loc_id}.json"
        path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        count += 1

    return count


def materialize_prop_profiles(graph: NarrativeGraph, project_dir: Path) -> int:
    """Write props/{prop_id}.json for each prop."""
    prop_dir = project_dir / "props"
    prop_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for prop_id, prop in graph.props.items():
        profile = {
            "propId": prop.prop_id,
            "name": prop.name,
            "description": prop.description,
            "narrativeSignificance": prop.narrative_significance,
            "materialContext": prop.material_context,
            "scenesUsed": prop.scenes_used,
            "associatedCast": prop.associated_cast,
            "introductionFrame": prop.introduction_frame,
            "imagePath": prop.image_path,
        }
        path = prop_dir / f"{prop_id}.json"
        path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        count += 1

    return count


def materialize_voice_profiles(graph: NarrativeGraph, project_dir: Path) -> int:
    """Write voices/{voice_id}.json for each voice profile."""
    voice_dir = project_dir / "voices"
    voice_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for voice_id, voice in graph.voices.items():
        profile = {
            "voiceId": voice.voice_id,
            "castId": voice.cast_id,
            "characterName": voice.character_name,
            "voiceDescription": voice.voice_description,
            "qualityPrefix": voice.quality_prefix,
            "tone": voice.tone,
            "pitch": voice.pitch,
            "accent": voice.accent,
            "deliveryStyle": voice.delivery_style,
            "tempo": voice.tempo,
            "emotionalRange": voice.emotional_range,
            "vocalStyle": voice.vocal_style,
            "voiceProfilePath": voice.voice_profile_path,
            "voiceStatus": voice.voice_status,
        }
        path = voice_dir / f"{voice_id}.json"
        path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        count += 1

    return count


def materialize_dialogue(graph: NarrativeGraph, output_path: Path) -> int:
    """Write dialogue.json with all dialogue lines and full metadata."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    for did in graph.dialogue_order:
        dnode = graph.dialogue.get(did)
        if not dnode:
            continue
        lines.append({
            "dialogueId": dnode.dialogue_id,
            "sceneId": dnode.scene_id,
            "frameId": dnode.primary_visual_frame,
            "startFrame": dnode.start_frame,
            "endFrame": dnode.end_frame,
            "primaryVisualFrame": dnode.primary_visual_frame,
            "reactionFrameIds": dnode.reaction_frame_ids,
            "speaker": dnode.speaker,
            "castId": dnode.cast_id,
            "line": dnode.line,
            "rawLine": dnode.raw_line,
            "performanceDirection": dnode.performance_direction,
            "envTags": dnode.env_tags,
            "envLocation": dnode.env_location,
            "envDistance": dnode.env_distance,
            "envMedium": dnode.env_medium,
            "envIntensity": dnode.env_intensity,
            "envAtmosphere": dnode.env_atmosphere,
            "order": dnode.order,
        })

    output_path.write_text(
        json.dumps({"dialogue": lines}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(lines)


def materialize_visual_analysis(graph: NarrativeGraph, output_path: Path) -> int:
    """Write visual_analysis.json from graph visual direction."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    visual = graph.visual
    analysis = {
        "mediaStyle": visual.media_style,
        "stylePrefix": visual.style_prefix,
        "styleDirection": visual.style_direction,
        "genreInfluence": visual.genre_influence,
        "moodPalette": visual.mood_palette,
        "visualTonePerAct": visual.visual_tone_per_act,
        "generationPriority": [],
        "entitiesToSkip": [],
        "skipReason": {},
    }

    # Build generation priority: mood boards -> cast by dialogue count -> locations -> props
    priority = ["mood_001"]
    cast_sorted = sorted(graph.cast.values(), key=lambda c: c.dialogue_line_count, reverse=True)
    priority.extend(c.cast_id for c in cast_sorted)
    priority.extend(graph.locations.keys())
    priority.extend(graph.props.keys())
    analysis["generationPriority"] = priority

    output_path.write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return 1


def materialize_manifest(graph: NarrativeGraph, manifest_path: Path) -> int:
    """Update project_manifest.json with frames, cast, locations, props, voices arrays."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing manifest or create new — ALWAYS preserve phases and status
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {}

    # Preserve phase statuses (other processes write these, we must not drop them)
    existing_phases = manifest.get("phases", {})
    existing_status = manifest.get("status", "")
    existing_version = manifest.get("version", 0)

    # Project metadata from onboarding
    manifest["mediaStyle"] = graph.project.media_style
    manifest["mediaStylePrefix"] = graph.project.media_style_prefix
    manifest["outputSize"] = graph.project.output_size
    manifest["stickinessLevel"] = graph.project.stickiness_level

    # Cast array
    manifest["cast"] = [
        {
            "castId": c.cast_id,
            "name": c.name,
            "role": c.role.value if hasattr(c.role, 'value') else c.role,
            "profilePath": f"cast/{c.cast_id}.json",
            "compositePath": c.composite_path,
            "voiceProfilePath": c.voice_profile_path,
            "dialogueLineCount": c.dialogue_line_count,
        }
        for c in graph.cast.values()
    ]

    # Locations array
    manifest["locations"] = [
        {
            "locationId": loc.location_id,
            "name": loc.name,
            "profilePath": f"locations/{loc.location_id}.json",
            "primaryImagePath": loc.primary_image_path,
        }
        for loc in graph.locations.values()
    ]

    # Props array
    manifest["props"] = [
        {
            "propId": p.prop_id,
            "name": p.name,
            "profilePath": f"props/{p.prop_id}.json",
            "imagePath": p.image_path,
        }
        for p in graph.props.values()
    ]

    # Voices array
    manifest["voices"] = [
        {
            "voiceId": v.voice_id,
            "castId": v.cast_id,
            "characterName": v.character_name,
            "voiceProfilePath": f"voices/{v.voice_id}.json",
            "voiceStatus": v.voice_status,
        }
        for v in graph.voices.values()
    ]

    # Frames array
    manifest["frames"] = []
    for fid in graph.frame_order:
        frame = graph.frames.get(fid)
        if not frame:
            continue

        cast_ids = [cs.cast_id for cs in frame.cast_states
                    if cs.frame_role not in ("referenced",)]
        prop_ids = [ps.prop_id for ps in frame.prop_states]

        manifest["frames"].append({
            "frameId": frame.frame_id,
            "sceneId": frame.scene_id,
            "sequenceIndex": frame.sequence_index,
            "formulaTag": frame.formula_tag.value if frame.formula_tag else None,
            "castIds": cast_ids,
            "locationId": frame.location_id,
            "propIds": prop_ids,
            "narrativeBeat": frame.narrative_beat,
            "actionSummary": frame.action_summary,
            "suggestedDuration": frame.suggested_duration,
            "isDialogue": frame.is_dialogue,
            "dialogueIds": frame.dialogue_ids,
            "dialogueRef": frame.dialogue_ids[0] if frame.dialogue_ids else None,
            "sourceText": frame.source_text,
            "timeOfDay": frame.time_of_day.value if frame.time_of_day else None,
            "emotionalArc": frame.emotional_arc.value if frame.emotional_arc else None,
            "visualFlowElement": frame.visual_flow_element,
            "composition": frame.composition.model_dump(),
            "background": frame.background.model_dump(),
            "directing": frame.directing.model_dump(),
            "continuityChain": frame.continuity_chain,
            "previousFrameId": frame.previous_frame_id,
            "nextFrameId": frame.next_frame_id,
            "status": frame.status,
            "generatedImagePath": frame.composed_image_path,
            "videoPath": frame.video_path,
        })

    # Chained frame groups
    manifest["chainedFrameGroups"] = [
        {
            "chainId": g.chain_id,
            "sceneId": g.scene_id,
            "locationId": g.location_id,
            "frameIds": g.frame_ids,
            "frameCount": g.frame_count,
            "castPresent": g.cast_present,
            "propsPresent": g.props_present,
            "storyboardImagePath": g.storyboard_image_path,
            "storyboardStatus": g.storyboard_status,
        }
        for g in graph.chained_frame_groups.values()
    ]

    manifest["dialoguePath"] = "dialogue.json"

    # Restore preserved fields that other processes own
    manifest["phases"] = existing_phases
    if existing_status:
        manifest["status"] = existing_status
    manifest["version"] = existing_version + 1

    # Write
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(manifest.get("frames", []))

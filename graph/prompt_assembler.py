"""
Prompt Assembler — Deterministic graph → prompt construction
=============================================================

Builds Chinese bilingual image prompts and video motion prompts
directly from graph data. No LLM involved.

Image prompts follow the 6-segment Chinese bilingual template from
the Production Coordinator spec. Video prompts follow the Video Agent
layered structure. Both are assembled from structured graph fields.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Optional

from .schema import (
    NarrativeGraph, FrameNode, CastFrameState, CastNode,
    LocationNode, SceneNode, DialogueNode, PropFrameState,
    LocationFrameState, FormulaTag, LightingDirection, LightingQuality,
    Posture, EmotionalArc, FrameComposition, FrameEnvironment,
)
from .api import get_frame_context


# ═══════════════════════════════════════════════════════════════════════════════
# LOOKUP TABLES
# ═══════════════════════════════════════════════════════════════════════════════

# Media type → style prefix (from Scene Coordinator spec)
MEDIA_STYLE_PREFIX = {
    "live_action": "Photorealistic cinematic still, professional lighting, shallow depth of field, ",
    "anime": "High-quality anime illustration, clean linework, vibrant colors, studio-quality anime art style, ",
    "cinematic": "Cinematic film still, photorealistic, dramatic lighting, ",
    "2d_cartoon": "Professional 2D animation style, clean cel-shaded, expressive character design, ",
    "3d_animation": "High-quality 3D animated render, Pixar-quality, clean geometry, professional lighting, ",
    "realistic_3d": "Photorealistic 3D render, raytraced lighting, physically-based materials, cinematic, ",
    "noir": "Film noir style, high contrast black and white, dramatic shadows, ",
    "painterly": "Oil painting style, painterly rendering, expressive brushwork, ",
    "comic": "Comic book art style, bold ink lines, flat colors, ",
}

# Formula tag → lens spec + shot description
FORMULA_LENS = {
    "F01": ("75mm T2.0", "Character portrait, emotional lighting"),
    "F02": ("50mm T2.8", "Two-shot, relationship staging"),
    "F03": ("35mm T4.0", "Wide group framing"),
    "F04": ("85mm T1.8", "Tight MCU, shallow DOF"),
    "F05": ("65mm T2.5", "Over-shoulder dialogue"),
    "F06": ("40mm T3.2", "Wide dialogue with environment"),
    "F07": ("24mm T5.6", "Deep focus wide establishing shot"),
    "F08": ("100mm macro T2.8", "Detail shot, tight on texture"),
    "F09": ("35mm T3.2", "Movement through space, doorway framing"),
    "F10": ("28mm T2.8", "Dynamic action, motion energy"),
    "F11": ("50mm T2.0", "Character-object interaction"),
    "F12": ("40mm T4.0", "Time passage, symbolic"),
    "F13": ("85mm T1.4", "Dreamlike, soft edges"),
    "F14": ("35mm T2.8", "Beat-synced visual"),
    "F15": ("50mm T2.0", "Lyric visualization"),
    "F16": ("35mm T2.8", "Performance shot"),
    "F17": ("35mm T2.8", "Liminal transition"),
    "F18": ("50mm T2.0", "Dramatic emphasis"),
}

# Video formula tag → shot type + camera defaults
FORMULA_VIDEO = {
    "F01": ("Medium close-up", "Static or very slow push", "Character expression, subtle body language"),
    "F02": ("Medium two-shot", "Static", "Character-to-character dynamics"),
    "F03": ("Wide group shot", "Slow pan or static", "Group movement"),
    "F04": ("Close-up", "Static, subtle drift", "Speaking emotional delivery"),
    "F05": ("Over-shoulder", "Static", "Speaker + listener reaction"),
    "F06": ("Medium wide", "Static or gentle tracking", "Both characters + environment"),
    "F07": ("Wide establishing", "Slow pan or gentle crane", "Environmental motion, atmosphere"),
    "F08": ("Extreme close-up", "Static or very slow push", "Texture, light, detail"),
    "F09": ("Medium", "Tracking or dolly", "Movement through space"),
    "F10": ("Medium or wide", "Tracking alongside", "Full body locomotion"),
    "F11": ("Medium close-up", "Static, push into detail", "Hands + object"),
    "F12": ("Variable", "Time-lapse suggestion", "Light shift, symbolic change"),
    "F13": ("Close-up or medium", "Gentle drift, soft focus", "Dreamlike, slow subtle movement"),
    "F17": ("Medium", "Slow, liminal", "Bridge motion"),
    "F18": ("Dramatic angle", "Slow push or crane", "Dramatic weight"),
}

# Time of day → Chinese descriptor
TIME_CHINESE = {
    "dawn": "破晓微光",
    "morning": "清晨",
    "midday": "正午阳光",
    "afternoon": "午后斜阳",
    "dusk": "黄昏暮色",
    "night": "夜色深沉",
}

# Lighting direction → Chinese
LIGHTING_DIR_CHINESE = {
    "top": "从头顶",
    "side_left": "从左侧",
    "side_right": "从右侧",
    "side_raking": "以低角度侧面",
    "back": "从身后",
    "rim": "勾勒轮廓",
    "under": "从下方",
    "ambient": "环境光均匀",
    "split": "分割式",
}

# Lighting quality → Chinese
LIGHTING_QUAL_CHINESE = {
    "harsh": "强烈的",
    "soft": "柔和的",
    "diffused": "散射的",
    "dappled": "斑驳的",
    "volumetric": "体积感的",
    "flickering": "摇曳的",
}

# Posture → Chinese
POSTURE_CHINESE = {
    "standing": "站立",
    "sitting": "端坐",
    "crouching": "蹲伏",
    "kneeling": "跪着",
    "lying": "躺卧",
    "walking": "行走",
    "running": "奔跑",
    "leaning": "倚靠",
    "hunched": "弓身",
}

# Material → anti-CG imperfection anchor
MATERIAL_IMPERFECTION = {
    "wood": "木纹裂缝",
    "metal": "金属氧化痕迹",
    "stone": "石面风化纹理",
    "silk": "织物褶皱与磨损",
    "leather": "皮革使用痕迹",
    "bamboo": "竹节纹理与裂痕",
    "ceramic": "釉面细裂纹",
    "paper": "纸张泛黄褶皱",
    "cloth": "织物褶皱与磨损",
    "skin": "皮肤毛孔与细纹",
    "lattice": "木纹裂缝",
    "lacquer": "漆面微裂",
}

# Film stock by mood
FILM_STOCK = {
    "warm": "Kodak 5219 500T",
    "cool": "Fujifilm Eterna 500T",
    "neutral": "Kodak 5207 250D",
    "dramatic": "Kodak 5219 500T pushed +1",
    "soft": "Fujifilm Eterna Vivid 160T",
}


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE PROMPT ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════════


def assemble_image_prompt(graph: NarrativeGraph, frame_id: str) -> dict:
    """Build a complete image composition prompt for a frame.

    Returns dict with:
        prompt: str — the full Chinese bilingual prompt
        ref_images: list[str] — reference image paths
        size: str — aspect ratio size param
        out_path: str — output file path
    """
    ctx = get_frame_context(graph, frame_id)
    frame = ctx["frame"]
    scene = ctx["scene"] or {}
    visual = ctx["visual"]
    world = ctx["world"]

    style_prefix = visual.get("style_prefix") or MEDIA_STYLE_PREFIX.get(
        visual.get("media_type", "live_action"), MEDIA_STYLE_PREFIX["live_action"]
    )

    # ── Segment 1: Scene description (场景描述)
    # Prefer frame-level time_of_day, fall back to scene
    frame_tod = frame.get("time_of_day") or scene.get("time_of_day", "")
    time_cn = TIME_CHINESE.get(frame_tod, "")
    location = ctx["location"] or {}
    loc_desc = location.get("description", "")
    loc_atmosphere = location.get("atmosphere", "")

    # Check for location state override
    loc_state = ctx.get("location_state")
    if loc_state:
        if loc_state.get("atmosphere_override"):
            loc_atmosphere = loc_state["atmosphere_override"]
        modifiers = loc_state.get("condition_modifiers", [])
        if modifiers:
            loc_atmosphere += "，" + "，".join(modifiers)
        if loc_state.get("lighting_override"):
            loc_atmosphere += "，" + loc_state["lighting_override"]

    scene_desc = f"{time_cn}，{loc_desc}" if loc_desc else time_cn
    if loc_atmosphere:
        scene_desc += f"。{loc_atmosphere}"

    # ── Segment 2: Character action & emotion (人物动作与情绪)
    char_segments = []
    for cs in ctx.get("cast_states", []):
        if cs.get("frame_role") in ("referenced", None):
            continue
        name = _get_cast_name(ctx["cast"], cs["cast_id"])
        action = cs.get("action", "")
        emotion = cs.get("emotion", "")
        posture = cs.get("posture", "")
        posture_cn = POSTURE_CHINESE.get(posture, "")
        clothing_state = cs.get("clothing_state", "base")
        clothing_current = cs.get("clothing_current", [])
        hair_state = cs.get("hair_state")
        injury = cs.get("injury")
        eye_direction = cs.get("eye_direction")

        parts = []
        if name:
            parts.append(name)
        # Wardrobe override (only if changed from base)
        if clothing_state != "base" and clothing_current:
            parts.append(f"身穿{'，'.join(clothing_current)}")
        # Hair state override
        if hair_state:
            parts.append(f"，发型{hair_state}")
        # Injury
        if injury:
            parts.append(f"，{injury}")
        # Action/posture
        if action:
            parts.append(f"正在{action}")
        elif posture_cn:
            parts.append(posture_cn)
        # Emotion
        if emotion:
            parts.append(f"，表情{emotion}")
        # Eye direction
        if eye_direction:
            parts.append(f"，目光{eye_direction}")

        if parts:
            char_segments.append("".join(parts))

    char_desc = "。".join(char_segments) if char_segments else ""

    # ── Segment 3: Environmental details (环境细节)
    env = frame.get("environment", {})
    env_parts = []
    fg = env.get("foreground_objects", [])
    if fg:
        env_parts.append("前景：" + "，".join(fg[:3]))
    mid = env.get("midground_detail")
    if mid:
        env_parts.append(f"中景：{mid}")
    bg = env.get("background_depth")
    if bg:
        env_parts.append(f"远景：{bg}")
    atmo = env.get("atmosphere", {})
    particles = atmo.get("particles")
    if particles:
        env_parts.append(f"空气中{particles}在光线中漂浮")
    ambient = atmo.get("ambient_motion")
    if ambient:
        env_parts.append(ambient)

    # Background enrichment from FrameBackground
    bg_data = frame.get("background", {})
    if bg_data.get("visible_description"):
        env_parts.append(f"背景：{bg_data['visible_description']}")
    if bg_data.get("depth_layers"):
        for layer in bg_data["depth_layers"][:2]:
            env_parts.append(layer)

    env_desc = "。".join(env_parts) if env_parts else ""

    # ── Segment 4: Lighting (光影描述)
    lighting = env.get("lighting", {})
    light_parts = []
    source = lighting.get("motivated_source", "")
    quality = LIGHTING_QUAL_CHINESE.get(lighting.get("quality", ""), "")
    color = lighting.get("color_temp", "")
    direction = LIGHTING_DIR_CHINESE.get(lighting.get("direction", ""), "")
    shadows = lighting.get("shadow_behavior", "")

    if source and direction:
        light_parts.append(f"{source}的{quality}{color}光{direction}照射")
    elif quality and color:
        light_parts.append(f"{quality}{color}光线")
    if shadows:
        light_parts.append(f"，{shadows}")

    light_desc = "".join(light_parts) if light_parts else ""

    # ── Segment 5: Camera technical suffix
    tag = frame.get("formula_tag", "F07")
    lens_spec, shot_desc = FORMULA_LENS.get(tag, ("50mm T2.0", "Medium shot"))
    comp = frame.get("composition", {})
    if comp.get("lens"):
        lens_spec = comp["lens"]

    # Pick film stock from mood
    mood = (scene.get("mood_keywords") or ["neutral"])[0]
    film = FILM_STOCK.get("warm" if mood in ("intimate", "warm", "hopeful", "romantic") else
                          "cool" if mood in ("tense", "fearful", "cold") else
                          "dramatic" if mood in ("dramatic", "hostile", "intense") else
                          "neutral", FILM_STOCK["neutral"])

    camera_suffix = f"Camera: ARRI Alexa, Cooke S4 {lens_spec}, {film}. {shot_desc}."

    # ── Segment 6: Anti-CG + imperfection anchors
    materials = env.get("materials_present", [])
    imperfections = []
    for mat in materials[:2]:
        for key, anchor in MATERIAL_IMPERFECTION.items():
            if key in mat.lower():
                imperfections.append(anchor)
                break
    if not imperfections:
        imperfections.append("银盐颗粒")  # Default: film grain
    imperfections.append("镜头边缘轻微暗角")  # Always: lens vignetting

    anti_cg = "，".join(imperfections) + "。非数码渲染，非CG，非插画。画面内无任何文字。"

    # ── Continuity prefix
    continuity_prefix = ""
    if frame.get("continuity_chain"):
        continuity_prefix = "同一场景，保持环境光线一致。"

    # ── Assemble full prompt
    segments = [s for s in [scene_desc, char_desc, env_desc, light_desc] if s]
    chinese_body = "。".join(segments)
    full_prompt = f"{style_prefix}{continuity_prefix}{chinese_body}。{camera_suffix} {anti_cg}"

    # ── Reference images
    ref_images = resolve_ref_images(graph, frame_id)

    # ── Size from aspect ratio
    ar = graph.project.aspect_ratio
    size_map = {"16:9": "landscape_16_9", "9:16": "portrait_9_16", "4:3": "landscape_4_3", "1:1": "square_hd"}
    size = size_map.get(ar, "landscape_16_9")

    return {
        "frame_id": frame_id,
        "prompt": full_prompt,
        "ref_images": ref_images,
        "size": size,
        "out_path": f"frames/composed/{frame_id}_gen.png",
        "formula_tag": tag,
        "style_prefix_used": style_prefix,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO PROMPT ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════════


def assemble_video_prompt(graph: NarrativeGraph, frame_id: str) -> dict:
    """Build a video generation prompt for a frame (grok-video only).

    Returns dict with:
        prompt: str — the full video prompt (dialogue included in AUDIO section)
        duration: int — clip duration in seconds (from Morpheus or formula heuristic)
        target_api: str — always "grok-video"
        input_image_path: str
        dialogue_line: str or None — raw dialogue text if present
        action_summary: str — concise action description
        frame_id, scene_id, sequence_index
    """
    ctx = get_frame_context(graph, frame_id)
    frame = ctx["frame"]
    scene = ctx["scene"] or {}
    visual = ctx["visual"]

    style_prefix = visual.get("style_prefix") or MEDIA_STYLE_PREFIX.get(
        visual.get("media_type", "live_action"), MEDIA_STYLE_PREFIX["live_action"]
    )
    # Replace "still"/"photo" words that freeze video output
    style_prefix = style_prefix.replace("still,", "frame,").replace("photograph", "film")
    style_prefix = style_prefix.replace("Photorealistic cinematic still",
                                         "Cinematic realism, cinematic frame")

    tag = frame.get("formula_tag", "F07")
    shot_type, camera_default, motion_focus = FORMULA_VIDEO.get(
        tag, ("Medium shot", "Static", "General motion")
    )

    # ── Action summary (Morpheus-authored, concise physical action)
    action_summary = frame.get("action_summary", "")

    # ── Environmental motion
    env = frame.get("environment", {})
    atmo = env.get("atmosphere", {})
    env_motion_parts = []
    if atmo.get("ambient_motion"):
        env_motion_parts.append(atmo["ambient_motion"])
    if atmo.get("particles"):
        env_motion_parts.append(f"{atmo['particles']} drifting in the air")
    if atmo.get("weather"):
        env_motion_parts.append(f"{atmo['weather']} falling")
    env_motion = ". ".join(env_motion_parts) if env_motion_parts else "Subtle atmospheric movement."

    # ── Background
    bg = env.get("background_depth", "")
    bg_data = frame.get("background", {})
    bg_parts = []
    if bg:
        bg_parts.append(bg)
    if bg_data.get("visible_description"):
        bg_parts.append(bg_data["visible_description"])
    if bg_data.get("background_action"):
        bg_parts.append(bg_data["background_action"])
    bg_section = f"Background: {'. '.join(bg_parts)}." if bg_parts else ""

    # ── Camera motion
    comp = frame.get("composition", {})
    camera_move = comp.get("movement") or camera_default
    camera_section = f"Camera: {camera_move}."

    # ── Character performance (enriched with action_summary)
    perf_parts = []
    for cs in ctx.get("cast_states", []):
        if cs.get("frame_role") in ("referenced", None):
            continue
        name = _get_cast_name(ctx["cast"], cs["cast_id"])
        # Build physical appearance descriptor for video model identification
        appearance = _get_cast_appearance(ctx["cast"], cs)
        action = cs.get("action", "")
        emotion = cs.get("emotion", "")
        posture = cs.get("posture", "")
        eye_dir = cs.get("eye_direction", "")
        intensity = cs.get("emotion_intensity", "")

        desc = f"{name} ({appearance})" if appearance else (name or "Character")
        if action:
            desc += f" {action}"
        if emotion:
            qual = f" ({intensity})" if intensity else ""
            desc += f", expression showing {emotion}{qual}"
        if posture and posture not in ("standing",):
            desc += f", {posture}"
        if eye_dir:
            desc += f", eyes {eye_dir}"
        perf_parts.append(desc)

    perf_section = ". ".join(perf_parts) if perf_parts else ""

    # If Morpheus provided an action_summary, prepend it — it's the directorial intent
    if action_summary:
        perf_section = f"{action_summary}. {perf_section}" if perf_section else action_summary

    # ── Blocking transition (current frame → next frame)
    blocking_parts = []
    if frame.get("next_frame_id"):
        try:
            next_ctx = get_frame_context(graph, frame["next_frame_id"])
            for cs in ctx.get("cast_states", []):
                if cs.get("frame_role") in ("referenced", None):
                    continue
                name = _get_cast_name(ctx["cast"], cs["cast_id"])
                next_cs = None
                for ncs in next_ctx.get("cast_states", []):
                    if ncs.get("cast_id") == cs.get("cast_id"):
                        next_cs = ncs
                        break
                if next_cs:
                    transitions = []
                    if cs.get("facing_direction") != next_cs.get("facing_direction") and next_cs.get("facing_direction"):
                        transitions.append(f"turns from {cs.get('facing_direction', 'current position')} to face {next_cs['facing_direction']}")
                    if cs.get("screen_position") != next_cs.get("screen_position") and next_cs.get("screen_position"):
                        transitions.append(f"moves from {cs.get('screen_position', 'current position')} to {next_cs['screen_position']}")
                    if cs.get("posture") != next_cs.get("posture") and next_cs.get("posture"):
                        transitions.append(f"shifts from {cs.get('posture', 'current posture')} to {next_cs['posture']}")
                    if transitions:
                        blocking_parts.append(f"{name}: {'; '.join(transitions)}")
        except Exception:
            pass  # Next frame not found — skip blocking transition

    blocking_section = ""
    if blocking_parts:
        blocking_section = "Character blocking: " + ". ".join(blocking_parts) + "."

    # ── Emotional beat
    arc = frame.get("emotional_arc", "")
    beat_section = ""
    if arc:
        beat_map = {"rising": "Building tension.", "falling": "Release, exhale.",
                    "peak": "The moment everything changes.", "static": "Held breath.",
                    "release": "Resolution settling."}
        beat_section = beat_map.get(arc, "")

    # ── Dialogue handling — all frames use grok-video, dialogue goes in AUDIO section
    dialogue_nodes = ctx.get("dialogue", [])
    dialogue_text = ""
    dialogue_line_raw = None
    dialogue_line_all = []
    dialogue_delivery = ""
    primary_voice_tempo = ""
    duration = 5  # default

    if frame.get("is_dialogue") and dialogue_nodes:
        primary = dialogue_nodes[0]  # First dialogue node audible in this frame
        speaker_voice = _get_cast_voice_profile(ctx["cast"], primary.get("cast_id", ""))
        dialogue_line_all = [
            n.get("raw_line", "").strip()
            for n in dialogue_nodes
            if n.get("raw_line", "").strip()
        ]
        combined_line = " ".join(dialogue_line_all).strip()

        if combined_line:
            dialogue_line_raw = combined_line
            dialogue_text = f'Speaking: "{combined_line}"'

        dialogue_delivery, primary_voice_tempo = _build_dialogue_delivery(
            primary,
            speaker_voice,
        )

    # ── Audio section — always build for grok-video
    audio_section = ""
    audio_layers = []
    if dialogue_nodes:
        env_tags = dialogue_nodes[0].get("env_atmosphere", [])
        if env_tags:
            audio_layers.append(", ".join(env_tags))
    if not audio_layers:
        # Derive from environment
        if atmo.get("weather"):
            audio_layers.append(atmo["weather"])
        if atmo.get("ambient_motion"):
            audio_layers.append(atmo["ambient_motion"])

    if bg_data.get("background_sound"):
        audio_layers.append(bg_data["background_sound"])

    # Dialogue frames: dialogue text leads the AUDIO section
    if dialogue_text:
        env_audio = ", ".join(audio_layers) if audio_layers else ""
        audio_parts = [f"AUDIO: {dialogue_text}"]
        if dialogue_delivery:
            audio_parts.append(f"Voice delivery: {dialogue_delivery}")
        if env_audio:
            audio_parts.append(f"Ambient audio: {env_audio}")
        audio_section = ". ".join(audio_parts)
    elif audio_layers:
        audio_section = "AUDIO: " + ", ".join(audio_layers)

    # ── Duration: dialogue length estimation → Morpheus-authored → formula heuristics
    # grok-video generates audio natively — duration must fit the dialogue
    if frame.get("is_dialogue") and dialogue_line_all:
        duration = _estimate_dialogue_duration(
            dialogue_line_all,
            tempo=primary_voice_tempo,
            env_intensity=dialogue_nodes[0].get("env_intensity", ""),
        )
    else:
        morpheus_duration = frame.get("suggested_duration")
        if morpheus_duration and 3 <= morpheus_duration <= 15:
            duration = morpheus_duration
        else:
            duration_map = {
                "F07": 8, "F08": 4, "F18": 8, "F10": 5, "F12": 10, "F17": 10,
                "F01": 5, "F04": 5, "F05": 5, "F11": 4, "F03": 6,
            }
            duration = duration_map.get(tag, 5)

    # ── Assemble
    parts = [style_prefix.strip()]
    if env_motion:
        parts.append(env_motion)
    if bg_section:
        parts.append(bg_section)
    if camera_section:
        parts.append(camera_section)
    if perf_section:
        parts.append(perf_section)
    if blocking_section:
        parts.append(blocking_section)
    if beat_section:
        parts.append(beat_section)
    if audio_section:
        parts.append(audio_section)

    full_prompt = " ".join(parts)

    return {
        "frame_id": frame_id,
        "scene_id": frame.get("scene_id", ""),
        "sequence_index": frame.get("sequence_index", 0),
        "prompt": full_prompt,
        "duration": duration,
        "target_api": "grok-video",
        "input_image_path": frame.get("composed_image_path") or f"frames/composed/{frame_id}_gen.png",
        "dialogue_line": dialogue_line_raw,
        "voice_delivery": dialogue_delivery,
        "voice_tempo": primary_voice_tempo,
        "action_summary": action_summary,
        "formula_tag": tag,
        "shot_type": shot_type,
        "camera_motion": camera_move,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CAST COMPOSITE & LOCATION PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════


def assemble_composite_prompt(graph: NarrativeGraph, cast_id: str) -> dict:
    """Build a cast composite reference image prompt."""
    cast = graph.cast.get(cast_id)
    if not cast:
        raise KeyError(f"Cast {cast_id} not found")

    style_prefix = graph.project.visual.style_prefix or MEDIA_STYLE_PREFIX.get(
        graph.project.visual.media_type, MEDIA_STYLE_PREFIX["live_action"]
    )

    identity = cast.identity
    parts = [style_prefix + "Full body character portrait, head to toe visible."]

    # Age, ethnicity, gender, build
    demo = []
    if identity.age_descriptor:
        demo.append(identity.age_descriptor)
    if identity.ethnicity:
        demo.append(identity.ethnicity)
    if identity.gender:
        demo.append(identity.gender)
    if identity.build:
        demo.append(f"{identity.build} build")
    if demo:
        parts.append(" ".join(demo) + ".")

    # Hair
    hair = []
    if identity.hair_color:
        hair.append(identity.hair_color)
    if identity.hair_length:
        hair.append(identity.hair_length)
    if identity.hair_style:
        hair.append(identity.hair_style)
    hair.append("hair")
    if any([identity.hair_color, identity.hair_length, identity.hair_style]):
        parts.append(" ".join(hair) + ".")

    # Skin
    if identity.skin:
        parts.append(f"{identity.skin} skin.")

    # Wardrobe
    wardrobe = identity.wardrobe_description or ", ".join(identity.clothing) if identity.clothing else ""
    if wardrobe:
        parts.append(f"Wearing {wardrobe}.")

    # Footwear
    if identity.footwear:
        parts.append(f"Footwear: {identity.footwear}.")

    parts.append("Neutral dark background, soft key light from upper left with rim light from behind, three-quarter view, standing pose.")

    return {
        "cast_id": cast_id,
        "prompt": " ".join(parts),
        "size": "portrait_9_16",
        "out_path": f"cast/composites/{cast_id}_ref.png",
    }


def assemble_location_prompt(graph: NarrativeGraph, location_id: str) -> dict:
    """Build a location establishing shot prompt."""
    loc = graph.locations.get(location_id)
    if not loc:
        raise KeyError(f"Location {location_id} not found")

    style_prefix = graph.project.visual.style_prefix or MEDIA_STYLE_PREFIX.get(
        graph.project.visual.media_type, MEDIA_STYLE_PREFIX["live_action"]
    )

    # Find first scene that uses this location for mood
    mood = ""
    for sid in loc.scenes_used[:1]:
        scene = graph.scenes.get(sid)
        if scene and scene.mood_keywords:
            mood = ", ".join(scene.mood_keywords)

    parts = [
        style_prefix + "Cinematic wide establishing shot.",
        f"{loc.name}: {loc.description}." if loc.description else f"{loc.name}.",
    ]
    if loc.atmosphere:
        parts.append(loc.atmosphere + ".")
    if mood:
        parts.append(f"Mood: {mood}.")
    parts.append("No characters, environmental focus, professional cinematography composition.")

    ar = graph.project.aspect_ratio
    size_map = {"16:9": "landscape_16_9", "9:16": "portrait_9_16", "4:3": "landscape_4_3", "1:1": "square_hd"}

    return {
        "location_id": location_id,
        "prompt": " ".join(parts),
        "size": size_map.get(ar, "landscape_16_9"),
        "out_path": f"locations/primary/{location_id}.png",
    }


def assemble_prop_prompt(graph: NarrativeGraph, prop_id: str) -> dict:
    """Build a prop reference image prompt."""
    prop = graph.props.get(prop_id)
    if not prop:
        raise KeyError(f"Prop {prop_id} not found")

    style_prefix = graph.project.visual.style_prefix or MEDIA_STYLE_PREFIX.get(
        graph.project.visual.media_type, MEDIA_STYLE_PREFIX["live_action"]
    )

    parts = [
        style_prefix + f"Detailed product-shot style image of {prop.name}.",
        prop.description + "." if prop.description else "",
        "Centered composition, clean presentation, slight dramatic lighting.",
    ]

    ar = graph.project.aspect_ratio
    size_map = {"16:9": "landscape_16_9", "9:16": "portrait_9_16", "4:3": "landscape_4_3", "1:1": "square_hd"}

    return {
        "prop_id": prop_id,
        "prompt": " ".join(p for p in parts if p),
        "size": size_map.get(ar, "landscape_16_9"),
        "out_path": f"props/generated/{prop_id}.png",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# REFERENCE IMAGE RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════


def resolve_ref_images(graph: NarrativeGraph, frame_id: str) -> list[str]:
    """Build the reference image list for a frame generation call."""
    frame = graph.frames.get(frame_id)
    if not frame:
        return []

    refs = []

    # 0. Scene storyboard reference (only include if file exists on disk)
    if frame.scene_id:
        storyboard_path = Path(f"frames/storyboards/{frame.scene_id}_storyboard.png")
        if storyboard_path.exists():
            refs.append(str(storyboard_path))

    # 1. Continuity chain: previous composed frame FIRST
    if frame.continuity_chain and frame.previous_frame_id:
        prev = graph.frames.get(frame.previous_frame_id)
        if prev and prev.composed_image_path:
            refs.append(prev.composed_image_path)

    # 2. Cast composites (max 4-5)
    # Prefer frame-level cast_states; fall back to graph-level registry if empty
    _cast_states = frame.cast_states
    if not _cast_states:
        _cast_states = [
            cfs for key, cfs in graph.cast_frame_states.items()
            if key.endswith(f"@{frame_id}")
        ]
    cast_ids_in_frame = [cs.cast_id for cs in _cast_states
                         if cs.frame_role not in ("referenced",)]
    for cid in cast_ids_in_frame[:5]:
        cast = graph.cast.get(cid)
        if cast:
            # Check if frame has an active state variant with its own image
            frame_cs = None
            for cs in _cast_states:
                if cs.cast_id == cid:
                    frame_cs = cs
                    break
            variant_path = None
            if frame_cs and frame_cs.active_state_tag != "base":
                variant = cast.state_variants.get(frame_cs.active_state_tag)
                if variant and variant.image_path:
                    variant_path = variant.image_path
            if variant_path:
                refs.append(variant_path)
            elif cast.composite_path:
                refs.append(cast.composite_path)

    # 3. Location primary image
    if frame.location_id:
        loc = graph.locations.get(frame.location_id)
        if loc and loc.primary_image_path:
            refs.append(loc.primary_image_path)

    # 4. Prop reference images (max 3, avoid exceeding 14-image cap)
    if frame.prop_states:
        for ps in frame.prop_states[:3]:
            prop = graph.props.get(ps.prop_id)
            if prop and prop.image_path:
                refs.append(prop.image_path)

    return refs


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH ASSEMBLY — Write all prompts to disk
# ═══════════════════════════════════════════════════════════════════════════════


MAX_STORYBOARD_PANELS = 8  # Max panels a single storyboard image can usefully render

TAG_LABELS = {
    "F01": "Character Focus", "F02": "Two-Shot", "F03": "Group",
    "F04": "Close-Up Dialogue", "F05": "Over-Shoulder", "F06": "Wide Dialogue",
    "F07": "Establishing", "F08": "Detail", "F09": "Transition",
    "F10": "Motion", "F11": "Prop Interaction", "F12": "Time Passage",
    "F13": "Flashback", "F17": "Scene Bridge", "F18": "Dramatic Emphasis",
}

# Priority tags for storyboard sampling — prefer narrative-driving shots
_KEY_TAGS = {"F07", "F01", "F02", "F18", "F09", "F10", "F17"}


def _sample_key_frames(frame_ids: list[str], graph, max_panels: int) -> list[str]:
    """Select the most narratively important frames for storyboard panels.

    Prioritizes: establishing shots, character focus, two-shots, dramatic emphasis,
    transitions, and scene bridges. Ensures first and last frames are always included.
    Evenly spaces remaining selections to cover the full scene arc.
    """
    if len(frame_ids) <= max_panels:
        return frame_ids

    frames = [(fid, graph.frames[fid]) for fid in frame_ids]

    # Always include first and last
    selected_ids = {frame_ids[0], frame_ids[-1]}

    # Build index for fast lookup
    id_to_idx = {fid: i for i, fid in enumerate(frame_ids)}

    # Score frames by narrative importance
    scored = []
    for fid, frame in frames:
        tag_str = str(frame.formula_tag.value if hasattr(frame.formula_tag, 'value') else frame.formula_tag or "F08")
        priority = 2 if tag_str in _KEY_TAGS else 1
        cast_states = frame.cast_states
        if not cast_states:
            cast_states = [cfs for k, cfs in graph.cast_frame_states.items() if k.endswith(f"@{fid}")]
        if len(cast_states) >= 2:
            priority += 1
        scored.append((fid, priority))

    # Divide scene into (max_panels - 2) segments, pick best from each
    budget = max_panels - len(selected_ids)
    if budget > 0:
        total = len(frame_ids)
        segment_size = total / budget
        for seg_i in range(budget):
            seg_start = int(seg_i * segment_size)
            seg_end = int((seg_i + 1) * segment_size)
            segment_frames = [(fid, p) for fid, p in scored
                              if fid not in selected_ids
                              and seg_start <= id_to_idx[fid] < seg_end]
            if segment_frames:
                best = max(segment_frames, key=lambda x: x[1])
                selected_ids.add(best[0])

    # Hard cap — if somehow over budget, trim middle frames
    result = [fid for fid in frame_ids if fid in selected_ids]
    if len(result) > max_panels:
        # Keep first, last, and evenly spaced middle
        step = (len(result) - 1) / (max_panels - 1)
        indices = [round(i * step) for i in range(max_panels)]
        result = [result[i] for i in sorted(set(indices))]

    return result


def assemble_sceneboard_prompt(graph: NarrativeGraph, scene_id: str) -> dict:
    """Build a multi-panel storyboard prompt for a scene.

    Samples up to MAX_STORYBOARD_PANELS key frames to create a readable
    storyboard image. Prioritizes establishing shots, character moments,
    and dramatic beats.

    Returns dict with:
        prompt: str — multi-panel storyboard instruction
        ref_images: list[str] — all cast/location/prop references for the scene
        size: str — always landscape_16_9 at max resolution
        out_path: str — storyboard output path
        frame_ids: list[str] — frames included in storyboard
        total_scene_frames: int — total frames in the scene
    """
    scene = graph.scenes.get(scene_id)
    if not scene:
        raise KeyError(f"Scene {scene_id} not found")

    visual = graph.project.visual
    style_prefix = visual.style_prefix or MEDIA_STYLE_PREFIX.get(
        visual.media_type, MEDIA_STYLE_PREFIX["live_action"]
    )

    # Gather all frames for this scene in order
    all_frame_ids = [fid for fid in graph.frame_order
                     if graph.frames.get(fid) and graph.frames[fid].scene_id == scene_id]

    # Sample key frames for storyboard panels
    sampled_ids = _sample_key_frames(all_frame_ids, graph, MAX_STORYBOARD_PANELS)
    panel_count = len(sampled_ids)

    # Build panel descriptions
    panels = []
    for i, fid in enumerate(sampled_ids):
        frame = graph.frames[fid]
        raw_tag = frame.formula_tag
        tag_str = str(raw_tag.value if hasattr(raw_tag, 'value') else raw_tag or "F08")
        tag_label = TAG_LABELS.get(tag_str, "Shot")
        beat = frame.narrative_beat or frame.action_summary or "Visual beat"
        # Truncate long beats for prompt efficiency
        if len(beat) > 150:
            beat = beat[:147] + "..."
        panels.append(f"Panel {i + 1} ({tag_label}): {beat}")

    panel_text = "\n".join(panels)

    # Gather cast descriptions for the scene
    cast_descs = []
    for cid in scene.cast_present[:6]:
        cast = graph.cast.get(cid)
        if cast:
            identity = cast.identity
            parts = [cast.name]
            if identity.age_descriptor:
                parts.append(identity.age_descriptor)
            if identity.ethnicity:
                parts.append(identity.ethnicity)
            if identity.gender:
                parts.append(identity.gender)
            if identity.build:
                parts.append(f"{identity.build} build")
            if identity.wardrobe_description:
                parts.append(f"wearing {identity.wardrobe_description}")
            cast_descs.append(", ".join(parts))

    cast_text = "; ".join(cast_descs) if cast_descs else ""

    # Location description
    loc = graph.locations.get(scene.location_id) if scene.location_id else None
    loc_text = f"{loc.name}: {loc.description}" if loc else ""

    # Mood
    mood_text = ", ".join(scene.mood_keywords) if scene.mood_keywords else ""

    # Assemble prompt — explicit panel count instruction
    prompt_parts = [
        f"{style_prefix} {panel_count}-panel cinematic storyboard arranged in a 2-row grid.",
        f"Exactly {panel_count} panels with clear borders, numbered sequentially.",
        "Consistent character appearance across all panels. Each panel is one key moment.",
        "",
        panel_text,
    ]
    if cast_text:
        prompt_parts.append(f"\nCharacters: {cast_text}")
    if loc_text:
        prompt_parts.append(f"Location: {loc_text}")
    if mood_text:
        prompt_parts.append(f"Mood: {mood_text}")

    full_prompt = "\n".join(prompt_parts)

    # Gather reference images (cast composites, location, props)
    ref_images = []
    for cid in scene.cast_present[:5]:
        cast = graph.cast.get(cid)
        if cast and cast.composite_path:
            ref_images.append(cast.composite_path)
    if loc and loc.primary_image_path:
        ref_images.append(loc.primary_image_path)
    for pid in scene.props_present[:3]:
        prop = graph.props.get(pid)
        if prop and prop.image_path:
            ref_images.append(prop.image_path)

    return {
        "scene_id": scene_id,
        "prompt": full_prompt,
        "ref_images": ref_images,
        "size": "landscape_16_9",
        "out_path": f"frames/storyboards/{scene_id}_storyboard.png",
        "frame_ids": sampled_ids,
        "frame_count": panel_count,
        "total_scene_frames": len(all_frame_ids),
    }


def assemble_all_prompts(graph: NarrativeGraph, project_dir: str | Path) -> dict:
    """Assemble all image + video prompts and write to disk.

    Writes:
        frames/prompts/{frame_id}_image.json
        video/prompts/{frame_id}_video.json
        cast/prompts/{cast_id}_composite.json
        locations/prompts/{location_id}_location.json
        props/prompts/{prop_id}_prop.json

    Returns summary dict.
    """
    project_dir = Path(project_dir)
    frame_prompt_dir = project_dir / "frames" / "prompts"
    video_prompt_dir = project_dir / "video" / "prompts"
    cast_prompt_dir = project_dir / "cast" / "prompts"
    loc_prompt_dir = project_dir / "locations" / "prompts"
    prop_prompt_dir = project_dir / "props" / "prompts"
    storyboard_prompt_dir = project_dir / "frames" / "storyboard_prompts"

    for d in [frame_prompt_dir, video_prompt_dir, cast_prompt_dir, loc_prompt_dir, prop_prompt_dir, storyboard_prompt_dir]:
        d.mkdir(parents=True, exist_ok=True)

    counts = {"image_prompts": 0, "video_prompts": 0, "composite_prompts": 0,
              "location_prompts": 0, "prop_prompts": 0, "storyboard_prompts": 0}

    # Frame prompts
    for frame_id in graph.frame_order:
        try:
            img = assemble_image_prompt(graph, frame_id)
            (frame_prompt_dir / f"{frame_id}_image.json").write_text(
                json.dumps(img, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["image_prompts"] += 1

            vid = assemble_video_prompt(graph, frame_id)
            (video_prompt_dir / f"{frame_id}_video.json").write_text(
                json.dumps(vid, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["video_prompts"] += 1
        except Exception as e:
            print(f"WARNING: Failed to assemble prompts for {frame_id}: {e}")

    # Cast composite prompts
    for cast_id in graph.cast:
        try:
            cp = assemble_composite_prompt(graph, cast_id)
            (cast_prompt_dir / f"{cast_id}_composite.json").write_text(
                json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["composite_prompts"] += 1
        except Exception as e:
            print(f"WARNING: Failed to assemble composite for {cast_id}: {e}")

    # Location prompts
    for loc_id in graph.locations:
        try:
            lp = assemble_location_prompt(graph, loc_id)
            (loc_prompt_dir / f"{loc_id}_location.json").write_text(
                json.dumps(lp, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["location_prompts"] += 1
        except Exception as e:
            print(f"WARNING: Failed to assemble location for {loc_id}: {e}")

    # Prop prompts
    for prop_id in graph.props:
        try:
            pp = assemble_prop_prompt(graph, prop_id)
            (prop_prompt_dir / f"{prop_id}_prop.json").write_text(
                json.dumps(pp, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["prop_prompts"] += 1
        except Exception as e:
            print(f"WARNING: Failed to assemble prop for {prop_id}: {e}")

    # Scene storyboard prompts (deduplicate scene_order)
    for scene_id in dict.fromkeys(graph.scene_order):
        try:
            sb = assemble_sceneboard_prompt(graph, scene_id)
            (storyboard_prompt_dir / f"{scene_id}_storyboard.json").write_text(
                json.dumps(sb, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["storyboard_prompts"] += 1
        except Exception as e:
            print(f"WARNING: Failed to assemble storyboard for {scene_id}: {e}")

    return counts


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_cast_name(cast_list: list[dict], cast_id: str) -> str:
    """Find cast name from context cast list."""
    for c in cast_list:
        if c.get("cast_id") == cast_id:
            return c.get("name", "")
    return ""


def _get_cast_appearance(cast_list: list[dict], cast_state: dict) -> str:
    """Build a brief physical appearance descriptor for video model identification."""
    for c in cast_list:
        if c.get("cast_id") == cast_state.get("cast_id"):
            identity = c.get("identity", {})
            parts = []
            if identity.get("age_descriptor"):
                parts.append(identity["age_descriptor"])
            if identity.get("ethnicity"):
                parts.append(identity["ethnicity"])
            if identity.get("gender"):
                parts.append(identity["gender"])
            if identity.get("build"):
                parts.append(f"{identity['build']} build")
            if identity.get("hair_color") and identity.get("hair_length"):
                parts.append(f"{identity['hair_color']} {identity['hair_length']} hair")
            elif identity.get("hair_color"):
                parts.append(f"{identity['hair_color']} hair")
            if identity.get("skin"):
                parts.append(f"{identity['skin']} skin")
            # Use current clothing from frame state if available, else identity wardrobe
            clothing = cast_state.get("clothing_current", [])
            if not clothing and identity.get("wardrobe_description"):
                parts.append(f"wearing {identity['wardrobe_description']}")
            elif clothing:
                parts.append(f"wearing {', '.join(clothing[:2])}")
            return ", ".join(parts)
    return ""


def _get_cast_voice_profile(cast_list: list[dict], cast_id: str) -> dict:
    """Return structured voice metadata plus prose voice notes for a cast member."""
    for c in cast_list:
        if c.get("cast_id") == cast_id:
            voice = c.get("voice") or {}
            return {
                "voice_description": voice.get("voice_description", ""),
                "tone": voice.get("tone", ""),
                "pitch": voice.get("pitch", ""),
                "accent": voice.get("accent", ""),
                "delivery_style": voice.get("delivery_style", ""),
                "tempo": voice.get("tempo", ""),
                "emotional_range": voice.get("emotional_range", ""),
                "vocal_style": voice.get("vocal_style", ""),
                "voice_notes": c.get("voice_notes", ""),
            }
    return {}


def _build_dialogue_delivery(dialogue_node: dict, speaker_voice: dict) -> tuple[str, str]:
    """Compose a concise delivery string for native-audio video generation."""
    performance_direction = (dialogue_node.get("performance_direction") or "").strip()
    delivery_style = (
        speaker_voice.get("delivery_style")
        or speaker_voice.get("vocal_style")
        or _summarize_voice_notes(speaker_voice.get("voice_notes", ""))
    )
    tone = (speaker_voice.get("tone") or "").strip()
    pitch = (speaker_voice.get("pitch") or "").strip()
    accent = (speaker_voice.get("accent") or "").strip()
    env_intensity = (dialogue_node.get("env_intensity") or "").strip()
    env_distance = (dialogue_node.get("env_distance") or "").strip()
    tempo = (
        speaker_voice.get("tempo")
        or _infer_voice_tempo(
            performance_direction,
            delivery_style,
            speaker_voice.get("voice_notes", ""),
            env_intensity,
        )
    ).strip()

    parts: list[str] = []
    for value in (
        performance_direction,
        delivery_style,
        tone,
        pitch,
        accent,
        f"{tempo} tempo" if tempo else "",
        f"{env_intensity} projection" if env_intensity else "",
        f"{env_distance} distance" if env_distance else "",
    ):
        cleaned = _normalize_ws(value)
        if cleaned and not _delivery_fragment_redundant(cleaned, parts):
            parts.append(cleaned)
    return ", ".join(parts), tempo


def _summarize_voice_notes(voice_notes: str) -> str:
    """Trim freeform voice notes into a short prompt-safe phrase."""
    cleaned = _normalize_ws(voice_notes)
    if not cleaned:
        return ""
    first_clause = re.split(r"[.;:\n]+", cleaned, maxsplit=1)[0].strip()
    words = first_clause.split()
    if len(words) > 12:
        first_clause = " ".join(words[:12])
    return first_clause


def _infer_voice_tempo(*signals: str) -> str:
    """Infer broad tempo buckets from performance and voice hints."""
    combined = " ".join(_normalize_ws(signal).lower() for signal in signals if signal)
    if not combined:
        return "measured"
    if any(token in combined for token in ("rapid", "fast", "hurried", "urgent", "breathless", "rushed", "clipped", "shouting", "shout", "yelling", "energetic")):
        return "fast"
    if any(token in combined for token in ("slow", "measured", "deliberate", "careful", "whisper", "quiet", "soft")):
        return "slow"
    return "measured"


def _estimate_dialogue_duration(lines: list[str], tempo: str = "", env_intensity: str = "") -> int:
    """Estimate clip duration for native-audio video generation."""
    cleaned_lines = [_normalize_ws(line) for line in lines if _normalize_ws(line)]
    if not cleaned_lines:
        return 5

    combined = " ".join(cleaned_lines)
    if _count_sentences(combined) > 1:
        return 15

    units = max(_count_dialogue_units(combined), 1)
    tempo_lower = (tempo or "").lower()
    env_lower = (env_intensity or "").lower()
    units_per_second = 2.6

    if "fast" in tempo_lower:
        units_per_second = 3.4
    elif any(token in tempo_lower for token in ("slow", "measured", "deliberate")):
        units_per_second = 2.0

    if any(token in env_lower for token in ("whisper", "quiet", "soft")):
        units_per_second = min(units_per_second, 2.2)
    elif any(token in env_lower for token in ("loud", "shouting", "shout", "yelling")):
        units_per_second = max(units_per_second, 3.0)

    return max(4, min(15, math.ceil(units / units_per_second) + 2))


def _count_sentences(text: str) -> int:
    """Count sentence-like segments in dialogue text."""
    segments = [segment for segment in re.split(r"[.!?。！？]+", _normalize_ws(text)) if segment.strip()]
    return len(segments)


def _count_dialogue_units(text: str) -> int:
    """Estimate spoken units across alphabetic and CJK dialogue."""
    cleaned = _normalize_ws(text)
    if not cleaned:
        return 0
    word_count = len(re.findall(r"\b[\w']+\b", cleaned))
    cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", cleaned))
    return word_count + math.ceil(cjk_count / 2)


def _normalize_ws(text: str) -> str:
    """Collapse repeated whitespace for prompt-safe fragments."""
    return re.sub(r"\s+", " ", text or "").strip()


def _delivery_fragment_redundant(candidate: str, existing_parts: list[str]) -> bool:
    """Skip delivery fragments that substantially repeat existing phrasing."""
    candidate_lower = candidate.lower()
    candidate_words = {
        word for word in re.findall(r"[a-z']+", candidate_lower)
        if len(word) > 2
    }
    for existing in existing_parts:
        existing_lower = existing.lower()
        if candidate_lower in existing_lower or existing_lower in candidate_lower:
            return True
        existing_words = {
            word for word in re.findall(r"[a-z']+", existing_lower)
            if len(word) > 2
        }
        if candidate_words and existing_words:
            overlap = candidate_words & existing_words
            overlap_ratio = len(overlap) / min(len(candidate_words), len(existing_words))
            if len(overlap) >= 2 and overlap_ratio >= 0.5:
                return True
    return False

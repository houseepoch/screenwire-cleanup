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

# Media style → style prefix
MEDIA_STYLE_PREFIX = {
    "new_digital_anime":  "anime modern, a high-fidelity, polished 2D digital anime illustration aesthetic with clean, defined linework, smooth gradient shading, and advanced photorealistic material rendering, featuring a high-contrast palette. ",
    "live_retro_grain":   "live action- Captured using a refined, fine-grain vintage analog film emulation, defined by diffused, shadowless studio portraiture lighting, an intentionally warm color grade saturating beige textiles and skin tones. ",
    "chiaroscuro_live":   "live action, A moody, high-contrast cinematic film aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient moonlight, finished with crushed black levels, enveloping heavy shadows, and a subtle 35mm film grain. ",
    "chiaroscuro_3d":     "3d computer generated graphic art unreal game play render, A moody, high-contrast aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient moonlight, finished with crushed black levels, enveloping heavy shadows. ",
    "chiaroscuro_anime":  "anime modern, a high-fidelity, polished 2D digital anime illustration aesthetic A moody, high-contrast aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient. ",
    "black_ink_anime":    "anime, gritty, 2D cel-shaded animation aesthetic defined by thick, variable-weight black ink outlines and stark, high-contrast hard shadows using pure black blocking, featuring a desaturated foreground color palette set against a stylized retro broadcast film grain. ",
    "live_soft_light":    "live action, A bright, nostalgic 35mm cinematic film aesthetic characterized by very soft, diffused naturalistic lighting and a shallow depth of field, featuring a muted pastel color palette with creamy, pristine skin tones, finished with a gentle film grain and a warm, inviting vintage studio grade. ",
    "live_clear":         "live action, stark, high-contrast modern digital photography aesthetic defined by dramatic, directional overhead spotlighting that intensely isolates the luminous subject. The color palette is strictly minimalist, emphasizing stark whites and natural warm tones that sharply contrast with the deep, light-absorbing shadows, captured with ultra-sharp clinical resolution and pristine clarity. ",
}

# Formula tag → shot framing description (no camera hardware)
FORMULA_SHOT = {
    "F01": "Character portrait, emotional lighting",
    "F02": "Two-shot, relationship staging",
    "F03": "Wide group framing",
    "F04": "Tight MCU, shallow DOF",
    "F05": "Over-shoulder dialogue",
    "F06": "Wide dialogue with environment",
    "F07": "Deep focus wide establishing shot",
    "F08": "Detail shot, tight on texture",
    "F09": "Movement through space, doorway framing",
    "F10": "Dynamic action, motion energy",
    "F11": "Character-object interaction",
    "F12": "Time passage, symbolic",
    "F13": "Dreamlike, soft edges",
    "F14": "Beat-synced visual",
    "F15": "Lyric visualization",
    "F16": "Performance shot",
    "F17": "Liminal transition",
    "F18": "Dramatic emphasis",
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

# DialogueNode.env_medium → audio quality modifier for delivery instructions
ENV_MEDIUM_AUDIO = {
    "radio":     "transmitted through radio — static-filtered, compressed frequency",
    "comms":     "over communications channel — digital clarity with slight compression",
    "phone":     "through phone speaker — tinny, narrow frequency band",
    "muffled":   "muffled through barrier — dampened highs, reduced clarity",
    "intercom":  "over intercom — reverberant, speaker distortion",
    "whisper":   "whispered — breathy, minimal vocalization",
    "distant":   "distant — reduced volume, environmental reverb",
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

# Screen-space → world-space cardinal direction mapping
# Key: camera_facing direction. Value: {screen_side: world_direction}
SPATIAL_WORLD_MAP = {
    'north': {'left': 'west',  'right': 'east',  'center': None, 'behind': 'north'},
    'south': {'left': 'east',  'right': 'west',  'center': None, 'behind': 'south'},
    'east':  {'left': 'north', 'right': 'south', 'center': None, 'behind': 'east'},
    'west':  {'left': 'south', 'right': 'north', 'center': None, 'behind': 'west'},
}

# World direction → Chinese
WORLD_DIR_CHINESE = {
    'north': '北',
    'south': '南',
    'east':  '东',
    'west':  '西',
}

# Emotion label → (facial_descriptors, body_descriptors)
EMOTION_EXPRESSION_MAP: dict[str, tuple[str, str]] = {
    "contemplative":       ("softened gaze, slightly furrowed brow, lips gently pressed",
                            "chin tilted slightly down, shoulders relaxed"),
    "fearful":             ("wide eyes, raised eyebrows, parted lips, visible tension in jaw",
                            "shoulders raised, body slightly recoiled"),
    "controlled_fury":     ("jaw clenched, eyes narrowed, nostrils flared, lips pressed into a thin line",
                            "spine rigid, hands tightly closed at sides"),
    "nervous":             ("darting eyes, lips slightly parted, faint tension around mouth corners",
                            "weight shifting, hands fidgeting or clasped"),
    "determined":          ("eyes steady and focused, jaw set, chin level",
                            "shoulders squared, posture upright and firm"),
    "grief":               ("eyes glistening, brow drawn inward, lower lip trembling",
                            "shoulders curved inward, head bowed slightly"),
    "restrained_anger":    ("jaw tight, eyes fixed and hard, expression deliberately neutral",
                            "stillness in the body suggesting coiled tension"),
    "serene":              ("eyes soft and half-lidded, gentle expression, relaxed mouth",
                            "shoulders dropped, body loose and unhurried"),
    "amused":              ("eyes crinkling at corners, lips curved in a subtle smile",
                            "slight tilt of the head, easy relaxed posture"),
    "suspicious":          ("eyes narrowed, one brow slightly raised, mouth faintly drawn",
                            "head tilted back marginally, weight balanced and watchful"),
    "desperate":           ("eyes wide and urgent, brow creased, mouth open with tension",
                            "leaning forward, hands outstretched or pressed to chest"),
    "hopeful":             ("eyes wide and bright, soft upturn at the mouth corners",
                            "chin lifted slightly, posture open and forward-leaning"),
    "resigned":            ("eyes downcast, expression flat and heavy, mouth slack",
                            "shoulders slumped, body weighted and still"),
    "bitter":              ("tight-lipped with a faint downward curl, eyes dull and hard",
                            "arms drawn inward, posture slightly withdrawn"),
    "defiant":             ("chin raised, eyes direct and unyielding, mouth set firm",
                            "stance wide, chest forward, arms loose but ready"),
    "vulnerable":          ("eyes exposed and searching, lips parted, brow softly raised",
                            "arms close to body, slight inward curve of the torso"),
    "conflicted":          ("eyes shifting, brow furrowed, mouth caught between expressions",
                            "body half-turned, weight undecided between directions"),
    "relieved":            ("eyes closing briefly, soft slack in the jaw, breath released",
                            "shoulders dropping, chest expanding with a slow exhale"),
    "awed":                ("eyes wide and still, mouth slightly open, expression suspended",
                            "body motionless, head tilted slightly upward"),
    "disgusted":           ("upper lip curling, nose wrinkling, eyes narrowing",
                            "head pulled back, body leaning away"),
    "ashamed":             ("eyes averted downward, cheeks flushed, chin tucked",
                            "shoulders curved forward, body turning inward"),
    "proud":               ("eyes bright and level, slight lift at the chin, quiet smile",
                            "spine tall, chest open, unhurried stillness"),
    "tender":              ("eyes soft and warm, faint smile, expression open and unhurried",
                            "body angled toward the subject, hands relaxed and near"),
    "playful":             ("eyes bright with mischief, lips quirked, expression light",
                            "loose easy posture, weight shifted to one side"),
    "melancholic":         ("eyes distant and liquid, expression quiet and aching",
                            "shoulders low, head inclined, breath slow"),
    "startled":            ("eyes snapping wide, eyebrows shooting up, breath caught",
                            "body jolting back, hands raised instinctively"),
    "wary":                ("eyes scanning, expression carefully neutral, jaw slightly set",
                            "weight on the balls of the feet, body coiled and alert"),
    "furious":             ("eyes blazing, brow slammed down, mouth open or bared",
                            "every muscle taut, body surging forward with force"),
    "joyful":              ("eyes bright and full, wide genuine smile, expression unguarded",
                            "body open and expansive, energy radiating outward"),
    "sorrowful":           ("eyes red-rimmed and wet, brow collapsed inward, mouth trembling",
                            "body folded inward, head bowed, breath ragged"),
    "longing":             ("eyes soft and faraway, lips gently parted, expression aching",
                            "body turned toward the absent subject, one hand drifting"),
    "confused":            ("brow furrowed, eyes searching, head tilted with uncertainty",
                            "slight pause in movement, hands hovering indecisively"),
    "embarrassed":         ("eyes quickly averted, cheeks and ears flushed, tight small smile",
                            "chin ducking, shoulders rising, body turning slightly away"),
    "quiet_determination": ("jaw level, eyes calm and fixed, breath controlled",
                            "spine straight, hands at rest, stillness that reads as resolve"),
    "bitter_amusement":    ("lips twisted in a humorless half-smile, eyes cold and knowing",
                            "head tilted, arms loosely crossed, an air of tired cynicism"),
}



# ═══════════════════════════════════════════════════════════════════════════════
# STYLE PREFIX RESOLUTION — Single authoritative source
# ═══════════════════════════════════════════════════════════════════════════════


def _resolve_style_prefix(graph: NarrativeGraph) -> str:
    """Resolve the media style prefix DETERMINISTICALLY.

    Priority chain (first non-empty wins):
    1. ProjectNode.media_style_prefix — set from onboarding_config.json, exact user selection
    2. MEDIA_STYLE_PREFIX lookup from ProjectNode.media_style
    3. Fallback to live_clear prefix

    NEVER uses VisualDirection.style_prefix — that field is LLM-authored and
    has historically produced wrong prefixes.
    """
    # 1. Onboarding-supplied prefix (authoritative)
    if graph.project.media_style_prefix:
        return graph.project.media_style_prefix

    # 2. Lookup from media_style slug
    media_style = graph.project.media_style or "live_clear"
    return MEDIA_STYLE_PREFIX.get(media_style, MEDIA_STYLE_PREFIX["live_clear"])


def _resolve_world_position(camera_facing: str, screen_position: str) -> Optional[str]:
    """Derive world-space cardinal direction from camera facing + screen position.

    Args:
        camera_facing: e.g. 'east', 'camera_facing_north', 'West'
        screen_position: e.g. 'frame_left', 'foreground_right', 'background_center'

    Returns:
        World-space direction string ('north'/'south'/'east'/'west') or None.
    """
    if not camera_facing or not screen_position:
        return None

    # Normalize camera_facing: lowercase, strip prefix
    facing = camera_facing.lower().strip()
    if facing.startswith("camera_facing_"):
        facing = facing[len("camera_facing_"):]

    # Parse screen side from position value
    pos = screen_position.lower()
    if "left" in pos:
        side = "left"
    elif "right" in pos:
        side = "right"
    else:
        side = "center"

    mapping = SPATIAL_WORLD_MAP.get(facing)
    if not mapping:
        return None
    return mapping.get(side)


# ═══════════════════════════════════════════════════════════════════════════════
# EXPRESSION RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════


def _resolve_expression(emotion: str, intensity: float = 0.5) -> str:
    """Translate an emotion label into concrete facial/body descriptors.

    Intensity bands:
      <0.4  → 'subtle ' + facial only
      0.4–0.7 → facial only
      >0.7  → facial + body combined

    Fuzzy fallback: splits on underscore and tries partial matches before
    returning the raw label unchanged.
    """
    if not emotion:
        return emotion

    key = emotion.lower().strip()

    # Direct lookup
    entry = EMOTION_EXPRESSION_MAP.get(key)

    # Fuzzy lookup if not found
    if entry is None:
        words = key.split("_")
        # Try individual words first
        for w in words:
            entry = EMOTION_EXPRESSION_MAP.get(w)
            if entry:
                break
        # Try partial substring match if still nothing
        if entry is None:
            for map_key, val in EMOTION_EXPRESSION_MAP.items():
                if key in map_key or map_key in key:
                    entry = val
                    break

    # Graceful fallback — return raw label
    if entry is None:
        return emotion

    facial, body = entry

    if intensity < 0.4:
        return f"subtle {facial}"
    elif intensity <= 0.7:
        return facial
    else:
        return f"{facial}, {body}"


def _compact_text(value: str | None) -> str:
    """Collapse whitespace in a free-text field."""
    if not value:
        return ""
    return " ".join(str(value).split())


def _resolve_directing_ref(graph: NarrativeGraph, ctx: dict, ref: str | None) -> str:
    """Resolve IDs or symbolic references used in FrameDirecting into readable labels."""
    ref_value = _compact_text(ref)
    if not ref_value:
        return ""
    if ref_value == "audience":
        return "audience"
    if ref_value.startswith("cast_"):
        return _get_cast_name(ctx.get("cast", []), ref_value) or ref_value
    if ref_value.startswith("prop_"):
        prop = graph.props.get(ref_value)
        return prop.name if prop else ref_value
    if ref_value.startswith("loc_"):
        loc = graph.locations.get(ref_value)
        return loc.name if loc else ref_value
    return ref_value.replace("_", " ")


def _extract_directing_data(graph: NarrativeGraph, ctx: dict, frame: dict) -> dict[str, str]:
    """Normalize the optional FrameDirecting block for prompt assembly."""
    directing = frame.get("directing") or {}
    if not isinstance(directing, dict):
        directing = {}

    return {
        "dramatic_purpose": _compact_text(directing.get("dramatic_purpose")),
        "beat_turn": _compact_text(directing.get("beat_turn")),
        "pov_owner": _resolve_directing_ref(graph, ctx, directing.get("pov_owner")),
        "viewer_knowledge_delta": _compact_text(directing.get("viewer_knowledge_delta")),
        "power_dynamic": _compact_text(directing.get("power_dynamic")),
        "tension_source": _compact_text(directing.get("tension_source")),
        "camera_motivation": _compact_text(directing.get("camera_motivation")),
        "movement_motivation": _compact_text(directing.get("movement_motivation")),
        "movement_path": _compact_text(directing.get("movement_path")),
        "reaction_target": _resolve_directing_ref(graph, ctx, directing.get("reaction_target")),
        "background_life": _compact_text(directing.get("background_life")),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE PROMPT ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════════


def assemble_image_prompt(graph: NarrativeGraph, frame_id: str,
                          project_dir: str | Path | None = None) -> dict:
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
    directing = _extract_directing_data(graph, ctx, frame)

    style_prefix = _resolve_style_prefix(graph)

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
        damage = loc_state.get("damage_level", "none")
        if damage and damage not in ("none", ""):
            loc_atmosphere += f"，环境损坏程度：{damage}"

    scene_desc = f"{time_cn}，{loc_desc}" if loc_desc else time_cn
    if loc_atmosphere:
        scene_desc += f"。{loc_atmosphere}"

    # Location texture anchors — material palette + architecture keywords
    _mat_pal = location.get("material_palette", [])
    _arch_kw = location.get("architecture_keywords", [])
    if _mat_pal or _arch_kw:
        _texture_parts = []
        if _arch_kw:
            _texture_parts.append(f"建筑风格：{'，'.join(_arch_kw[:4])}")
        if _mat_pal:
            _texture_parts.append(f"材质：{'，'.join(_mat_pal[:4])}")
        scene_desc += "，" + "，".join(_texture_parts)

    # ── Segment 2: Character action & emotion (人物动作与情绪)
    char_segments = []
    for cs in ctx.get("cast_states", []):
        if cs.get("frame_role") in ("referenced", None):
            continue
        name = _get_cast_name(ctx["cast"], cs["cast_id"])
        appearance = _get_cast_appearance(ctx["cast"], cs)
        action = cs.get("action", "")
        emotion = cs.get("emotion", "")
        emotion_intensity = cs.get("emotion_intensity")
        posture = cs.get("posture", "")
        posture_cn = POSTURE_CHINESE.get(posture, "")
        clothing_state = cs.get("clothing_state", "base")
        clothing_current = cs.get("clothing_current", [])
        hair_state = cs.get("hair_state")
        injury = cs.get("injury")
        eye_direction = cs.get("eye_direction")
        facing_direction = cs.get("facing_direction")
        screen_position = cs.get("screen_position")
        looking_at = cs.get("looking_at")
        spatial_position = cs.get("spatial_position")
        props_held = cs.get("props_held", [])

        parts = []
        if name and appearance:
            parts.append(f"{name} ({appearance})")
        elif name:
            parts.append(name)
        # Screen position & spatial placement
        if screen_position:
            parts.append(f"，位于画面{screen_position}")
        elif spatial_position:
            parts.append(f"，位于{spatial_position}")
        # World-space spatial anchor (camera_facing + screen_position → cardinal room side)
        _img_camera_facing = frame.get("background", {}).get("camera_facing")
        if _img_camera_facing and screen_position:
            _world_dir = _resolve_world_position(_img_camera_facing, screen_position)
            if _world_dir:
                _world_cn = WORLD_DIR_CHINESE.get(_world_dir, _world_dir)
                parts.append(f"，位于房间{_world_cn}侧")
        # Facing direction
        if facing_direction:
            parts.append(f"，面朝{facing_direction}")
        # Wardrobe — use override if non-base state, else fall back to identity wardrobe
        if clothing_state and clothing_state != "base":
            if clothing_current:
                parts.append(f"，身穿{'，'.join(clothing_current)}")
            # If non-base state but no clothing_current, don't fall back to base wardrobe
            # (the outfit has changed but details weren't specified)
        else:
            identity_wardrobe = _get_cast_wardrobe(ctx.get("cast", []), cs.get("cast_id", ""))
            if identity_wardrobe:
                parts.append(f"，身穿{identity_wardrobe}")
        # Hair state override
        if hair_state:
            parts.append(f"，发型{hair_state}")
        # Injury
        if injury:
            parts.append(f"，{injury}")
        # Action/posture
        if action:
            parts.append(f"，正在{action}")
        elif posture_cn:
            parts.append(f"，{posture_cn}")
        # Props held
        if props_held:
            _prop_state_lookup = {ps["prop_id"]: ps for ps in ctx.get("prop_states", [])}
            prop_names = []
            for pid in props_held[:3]:
                prop = graph.props.get(pid)
                base_name = prop.name if prop else pid
                _ps = _prop_state_lookup.get(pid, {})
                _cond = (_ps.get("condition") or "").strip().lower()
                if _cond and _cond not in ("intact", "base", "normal", ""):
                    prop_names.append(f"{_cond} {base_name}")
                else:
                    prop_names.append(base_name)
            parts.append(f"，手持{'、'.join(prop_names)}")
        # Emotion — translated to concrete facial/body descriptors
        if emotion:
            expression = _resolve_expression(emotion, emotion_intensity if emotion_intensity is not None else 0.5)
            parts.append(f"，{expression}")
        # Eye direction / looking at
        if looking_at:
            parts.append(f"，目光注视{looking_at}")
        elif eye_direction:
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

    # Weather & temperature
    weather = atmo.get("weather")
    if weather:
        env_parts.append(f"天气：{weather}")
    temp_feel = atmo.get("temperature_feel")
    if temp_feel:
        env_parts.append(f"体感{temp_feel}")

    # Background enrichment from FrameBackground + location directions
    bg_data = frame.get("background", {})
    camera_facing = bg_data.get("camera_facing")
    if camera_facing:
        env_parts.append(f"镜头朝向{camera_facing}")
    # Auto-resolve visible_description from location directions if not explicitly set
    visible_desc = bg_data.get("visible_description", "")
    if not visible_desc and camera_facing and location:
        directions = location.get("directions", {})
        if isinstance(directions, dict):
            visible_desc = directions.get(camera_facing, "")
    if visible_desc:
        env_parts.append(f"背景：{visible_desc}")
    if bg_data.get("background_action"):
        env_parts.append(f"背景动作：{bg_data['background_action']}")
    if directing.get("background_life"):
        env_parts.append(f"背景生活：{directing['background_life']}")
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

    # ── Segment 5: Shot framing from FrameComposition (no camera hardware)
    tag = frame.get("formula_tag", "F07")
    shot_desc = FORMULA_SHOT.get(tag, "Medium shot")
    comp = frame.get("composition", {})

    # Enrich shot description from FrameComposition fields
    comp_extras = []
    if comp.get("shot"):
        shot_desc = comp["shot"]  # Override formula default with graph data
    if comp.get("angle"):
        comp_extras.append(f"angle: {comp['angle']}")
    if comp.get("placement"):
        comp_extras.append(f"subject {comp['placement']}")
    if comp.get("grouping"):
        comp_extras.append(f"grouping: {comp['grouping']}")
    if comp.get("blocking"):
        comp_extras.append(f"blocking: {comp['blocking']}")
    if comp.get("focus"):
        comp_extras.append(f"focus on {comp['focus']}")
    if comp.get("rule"):
        comp_extras.append(comp["rule"])
    if comp.get("transition"):
        comp_extras.append(f"transition: {comp['transition']}")
    if directing.get("camera_motivation"):
        comp_extras.append(f"motivation: {directing['camera_motivation']}")
    if directing.get("movement_path"):
        comp_extras.append(f"path: {directing['movement_path']}")

    comp_suffix = f" ({', '.join(comp_extras)})" if comp_extras else ""
    framing_suffix = f"{shot_desc}{comp_suffix}." if shot_desc else ""

    directorial_parts = []
    if directing.get("dramatic_purpose"):
        directorial_parts.append(f"dramatic purpose: {directing['dramatic_purpose']}")
    if directing.get("pov_owner"):
        directorial_parts.append(f"POV aligned with {directing['pov_owner']}")
    if directing.get("power_dynamic"):
        directorial_parts.append(f"power dynamic: {directing['power_dynamic']}")
    if directing.get("beat_turn"):
        directorial_parts.append(f"beat turn: {directing['beat_turn']}")
    if directing.get("viewer_knowledge_delta"):
        directorial_parts.append(f"viewer learns: {directing['viewer_knowledge_delta']}")
    if directing.get("reaction_target"):
        directorial_parts.append(f"reacting to {directing['reaction_target']}")
    if directing.get("tension_source"):
        directorial_parts.append(f"tension source: {directing['tension_source']}")
    if directing.get("movement_motivation"):
        directorial_parts.append(f"movement motivation: {directing['movement_motivation']}")
    directorial_suffix = (
        " Narrative intent: " + "; ".join(directorial_parts) + "."
        if directorial_parts else ""
    )

    # ── Continuity prefix
    continuity_prefix = ""
    if frame.get("continuity_chain"):
        continuity_prefix = "同一场景，保持环境光线一致。"

    # ── Dialogue context for image (NO dialogue text — just who speaks to whom)
    dialogue_cue = ""
    dialogue_nodes_img = ctx.get("dialogue", [])
    if frame.get("is_dialogue") and dialogue_nodes_img:
        for dn in dialogue_nodes_img:
            if dn.get("primary_visual_frame") == frame_id:
                speaker_name = _get_cast_name(ctx["cast"], dn.get("cast_id", ""))
                # Find who they're speaking to (other cast in frame)
                listeners = []
                for cs in ctx.get("cast_states", []):
                    if cs.get("cast_id") != dn.get("cast_id") and cs.get("frame_role") in ("subject", "object"):
                        ln = _get_cast_name(ctx["cast"], cs["cast_id"])
                        if ln:
                            listeners.append(ln)
                if speaker_name and listeners:
                    dialogue_cue = f"{speaker_name}正在对{'、'.join(listeners)}说话"
                elif speaker_name:
                    dialogue_cue = f"{speaker_name}正在说话"
                break
    # Reaction frame — listener is the focus
    if not dialogue_cue and dialogue_nodes_img:
        for dn in dialogue_nodes_img:
            if frame_id in (dn.get("reaction_frame_ids") or []):
                listener_names = []
                speaker_id = dn.get("cast_id", "")
                speaker_name = _get_cast_name(ctx["cast"], speaker_id)
                for cs in ctx.get("cast_states", []):
                    if cs.get("cast_id") != speaker_id and cs.get("frame_role") in ("subject", "object"):
                        ln = _get_cast_name(ctx["cast"], cs["cast_id"])
                        if ln:
                            listener_names.append(ln)
                if listener_names and speaker_name:
                    dialogue_cue = f"{'、'.join(listener_names)}正在倾听{speaker_name}"
                elif listener_names:
                    dialogue_cue = f"{'、'.join(listener_names)}正在倾听"
                break

    # ── Instruction bridge — cinematic direction between style prefix and scene data
    instruction_bridge = (
        "Use the reference images to craft a cinematic scene depicting the following "
        "actions in a natural interaction with the environment. Compose a production-quality "
        "frame as seen in televised media — consistent character appearance across all frames, "
        "natural poses, grounded lighting, and deliberate camera composition. "
    )

    # ── Assemble full prompt
    segments = [s for s in [scene_desc, char_desc, env_desc, light_desc] if s]
    if dialogue_cue:
        segments.append(dialogue_cue)
    chinese_body = "。".join(segments)
    full_prompt = f"{style_prefix}{instruction_bridge}{continuity_prefix}{chinese_body}。{framing_suffix}{directorial_suffix} 画面内无任何文字。"

    # ── Reference images
    ref_images = resolve_ref_images(graph, frame_id, project_dir=project_dir)

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
        "directing": frame.get("directing", {}),
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
    location = ctx["location"] or {}
    directing = _extract_directing_data(graph, ctx, frame)

    style_prefix = _resolve_style_prefix(graph)
    # Replace "still"/"photo" words that freeze video output
    style_prefix = style_prefix.replace("still,", "frame,").replace("photograph", "film")

    tag = frame.get("formula_tag", "F07")
    shot_type, camera_default, motion_focus = FORMULA_VIDEO.get(
        tag, ("Medium shot", "Static", "General motion")
    )

    # ── Time of day (must match image prompt for lighting consistency)
    frame_tod = frame.get("time_of_day") or scene.get("time_of_day", "")
    tod_section = f"Time of day: {frame_tod}." if frame_tod else ""

    # ── Continuity prefix (match image prompt behavior)
    continuity_prefix = ""
    if frame.get("continuity_chain"):
        continuity_prefix = "Continuous scene — maintain consistent lighting, environment, and character positions."

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
    if atmo.get("temperature_feel"):
        env_motion_parts.append(f"{atmo['temperature_feel']} atmosphere")
    if directing.get("background_life"):
        env_motion_parts.append(directing["background_life"])
    if directing.get("movement_motivation"):
        env_motion_parts.append(f"motion motivation: {directing['movement_motivation']}")
    env_motion = ". ".join(env_motion_parts) if env_motion_parts else "Subtle atmospheric movement."

    # ── Depth layers (foreground → midground → background)
    depth_parts = []
    fg = env.get("foreground_objects", [])
    if fg:
        depth_parts.append(f"Foreground: {', '.join(fg[:3])}")
    mid = env.get("midground_detail")
    if mid:
        depth_parts.append(f"Midground: {mid}")
    depth_section = ". ".join(depth_parts) + "." if depth_parts else ""

    # ── Background
    bg = env.get("background_depth", "")
    bg_data = frame.get("background", {})
    bg_parts = []
    vid_camera_facing = bg_data.get("camera_facing")
    if vid_camera_facing:
        bg_parts.append(f"camera facing {vid_camera_facing}")
    if bg:
        bg_parts.append(bg)
    # Auto-resolve visible_description from location directions if not set
    vid_visible_desc = bg_data.get("visible_description", "")
    if not vid_visible_desc and vid_camera_facing and location:
        vid_directions = location.get("directions", {})
        if isinstance(vid_directions, dict):
            vid_visible_desc = vid_directions.get(vid_camera_facing, "")
    if vid_visible_desc:
        bg_parts.append(vid_visible_desc)
    if bg_data.get("background_action"):
        bg_parts.append(bg_data["background_action"])
    if directing.get("background_life"):
        bg_parts.append(directing["background_life"])
    if bg_data.get("depth_layers"):
        for layer in bg_data["depth_layers"][:2]:
            bg_parts.append(layer)
    bg_section = f"Background: {'. '.join(bg_parts)}." if bg_parts else ""

    # ── Location state override (condition/damage/atmosphere modifiers)
    loc_state = ctx.get("location_state")
    loc_state_section = ""
    if loc_state:
        loc_state_parts = []
        atmo_override = loc_state.get("atmosphere_override", "")
        if atmo_override:
            loc_state_parts.append(atmo_override)
        condition_mods = loc_state.get("condition_modifiers", [])
        if condition_mods:
            loc_state_parts.extend(condition_mods)
        lighting_override = loc_state.get("lighting_override", "")
        if lighting_override:
            loc_state_parts.append(lighting_override)
        damage = loc_state.get("damage_level", "")
        if damage and damage not in ("none", ""):
            loc_state_parts.append(f"environment damage: {damage}")
        if loc_state_parts:
            loc_state_section = "Location state: " + ", ".join(loc_state_parts) + "."

    # ── Lighting (carry image prompt lighting into video for consistency)
    lighting = env.get("lighting", {})
    light_parts_v = []
    if lighting.get("motivated_source"):
        light_parts_v.append(f"lit by {lighting['motivated_source']}")
    if lighting.get("quality"):
        light_parts_v.append(f"{lighting['quality']} light")
    if lighting.get("color_temp"):
        light_parts_v.append(f"{lighting['color_temp']} tone")
    if lighting.get("direction"):
        light_parts_v.append(f"from {lighting['direction']}")
    if lighting.get("shadow_behavior"):
        light_parts_v.append(f"shadows: {lighting['shadow_behavior']}")
    lighting_section = f"Lighting: {', '.join(light_parts_v)}." if light_parts_v else ""

    # ── Camera motion + FrameComposition
    comp = frame.get("composition", {})
    camera_move = comp.get("movement") or camera_default
    camera_parts = [f"Camera: {camera_move}"]
    if comp.get("shot"):
        camera_parts.append(comp["shot"])
    if comp.get("angle"):
        camera_parts.append(f"{comp['angle']} angle")
    if comp.get("focus"):
        camera_parts.append(f"focus on {comp['focus']}")
    if comp.get("transition"):
        camera_parts.append(f"transition: {comp['transition']}")
    if directing.get("camera_motivation"):
        camera_parts.append(f"motivation: {directing['camera_motivation']}")
    if directing.get("movement_path"):
        camera_parts.append(f"path: {directing['movement_path']}")
    camera_section = f"{', '.join(camera_parts)}."

    # ── Character performance (enriched with blocking + positioning)
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
        facing = cs.get("facing_direction", "")
        screen_pos = cs.get("screen_position", "")
        looking_at = cs.get("looking_at", "")
        spatial_pos = cs.get("spatial_position", "")
        hair_state = cs.get("hair_state", "")
        injury = cs.get("injury", "")
        props_held = cs.get("props_held", [])

        desc = f"{name} ({appearance})" if appearance else (name or "Character")
        # Position in frame
        if screen_pos:
            desc += f" at {screen_pos}"
        elif spatial_pos:
            desc += f" at {spatial_pos}"
        # World-space spatial anchor
        _vid_camera_facing = bg_data.get("camera_facing")
        if _vid_camera_facing and screen_pos:
            _vid_world_dir = _resolve_world_position(_vid_camera_facing, screen_pos)
            if _vid_world_dir:
                desc += f", positioned on {_vid_world_dir} side of the space"
        # Facing
        if facing:
            desc += f", facing {facing}"
        # Physical state modifiers
        if hair_state:
            desc += f", hair {hair_state}"
        if injury:
            desc += f", {injury}"
        # Action
        if action:
            desc += f", {action}"
        # Props
        if props_held:
            _vprop_state_lookup = {ps["prop_id"]: ps for ps in ctx.get("prop_states", [])}
            prop_names = []
            for pid in props_held[:3]:
                prop = graph.props.get(pid)
                base_name = prop.name if prop else pid
                _vps = _vprop_state_lookup.get(pid, {})
                _vcond = (_vps.get("condition") or "").strip().lower()
                if _vcond and _vcond not in ("intact", "base", "normal", ""):
                    prop_names.append(f"{_vcond} {base_name}")
                else:
                    prop_names.append(base_name)
            desc += f", holding {', '.join(prop_names)}"
        if emotion:
            expression = _resolve_expression(emotion, float(intensity) if intensity is not None and intensity != "" else 0.5)
            desc += f", {expression}"
        if posture and posture not in ("standing",):
            desc += f", {posture}"
        # Gaze
        if looking_at:
            desc += f", looking at {looking_at}"
        elif eye_dir:
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
                    cur_facing = cs.get("facing_direction")
                    nxt_facing = next_cs.get("facing_direction")
                    if cur_facing and nxt_facing and cur_facing != nxt_facing:
                        transitions.append(f"turns from {cur_facing} to face {nxt_facing}")
                    elif nxt_facing and not cur_facing:
                        transitions.append(f"turns to face {nxt_facing}")
                    cur_pos = cs.get("screen_position")
                    nxt_pos = next_cs.get("screen_position")
                    if cur_pos and nxt_pos and cur_pos != nxt_pos:
                        transitions.append(f"moves from {cur_pos} to {nxt_pos}")
                    elif nxt_pos and not cur_pos:
                        transitions.append(f"moves to {nxt_pos}")
                    cur_posture = cs.get("posture")
                    nxt_posture = next_cs.get("posture")
                    if cur_posture and nxt_posture and cur_posture != nxt_posture:
                        transitions.append(f"shifts from {cur_posture} to {nxt_posture}")
                    elif nxt_posture and not cur_posture:
                        transitions.append(f"shifts to {nxt_posture}")
                    if transitions:
                        blocking_parts.append(f"{name}: {'; '.join(transitions)}")
        except Exception:
            pass  # Next frame not found — skip blocking transition

    blocking_section = ""
    if blocking_parts:
        blocking_section = "Character blocking: " + ". ".join(blocking_parts) + "."

    # ── Emotional beat
    arc = frame.get("emotional_arc", "")
    beat_parts = []
    if arc:
        beat_map = {"rising": "Building tension.", "falling": "Release, exhale.",
                    "peak": "The moment everything changes.", "static": "Held breath.",
                    "release": "Resolution settling."}
        mapped = beat_map.get(arc, "")
        if mapped:
            beat_parts.append(mapped)
    if directing.get("dramatic_purpose"):
        beat_parts.append(f"Dramatic purpose: {directing['dramatic_purpose']}.")
    if directing.get("beat_turn"):
        beat_parts.append(f"Beat turn: {directing['beat_turn']}.")
    if directing.get("pov_owner"):
        beat_parts.append(f"POV aligned with {directing['pov_owner']}.")
    if directing.get("viewer_knowledge_delta"):
        beat_parts.append(f"Viewer learns: {directing['viewer_knowledge_delta']}.")
    if directing.get("power_dynamic"):
        beat_parts.append(f"Power dynamic: {directing['power_dynamic']}.")
    if directing.get("tension_source"):
        beat_parts.append(f"Tension source: {directing['tension_source']}.")
    if directing.get("reaction_target"):
        beat_parts.append(f"Reaction target: {directing['reaction_target']}.")
    beat_section = " ".join(beat_parts)

    # ── Dialogue handling — all frames use grok-video, dialogue goes in AUDIO section
    # Dialogue resolved via temporal span (covers J-cuts, L-cuts, and direct IDs)
    dialogue_nodes = ctx.get("dialogue", [])
    dialogue_text = ""
    dialogue_line_raw = None
    dialogue_line_all = []
    dialogue_delivery = ""
    primary_voice_tempo = ""
    duration = 5  # default

    # Process dialogue if ANY dialogue is audible (not just is_dialogue flag)
    # This captures J-cuts (audio before visual) and L-cuts (audio over reaction)
    if dialogue_nodes:
        # Group by speaker for multi-speaker handling
        speakers_seen: dict[str, list[dict]] = {}
        for dn in dialogue_nodes:
            cid = dn.get("cast_id", "unknown")
            speakers_seen.setdefault(cid, []).append(dn)

        # Build per-speaker dialogue lines
        per_speaker_lines = []
        for cid, speaker_dns in speakers_seen.items():
            speaker_name = _get_cast_name(ctx["cast"], cid) or "Character"
            speaker_voice = _get_cast_voice_profile(ctx["cast"], cid)
            lines = [dn.get("raw_line", "").strip() for dn in speaker_dns if dn.get("raw_line", "").strip()]
            if lines:
                combined = " ".join(lines)
                delivery, tempo = _build_dialogue_delivery(speaker_dns[0], speaker_voice)
                per_speaker_lines.append({
                    "name": speaker_name,
                    "line": combined,
                    "delivery": delivery,
                    "tempo": tempo,
                })

        if per_speaker_lines:
            dialogue_line_all = [s["line"] for s in per_speaker_lines]
            dialogue_line_raw = " ".join(dialogue_line_all)

            if len(per_speaker_lines) == 1:
                # Single speaker
                s = per_speaker_lines[0]
                dialogue_text = f'{s["name"]} speaking: "{s["line"]}"'
                dialogue_delivery = s["delivery"]
                primary_voice_tempo = s["tempo"]
            else:
                # Multi-speaker — label each speaker's line
                parts = []
                for s in per_speaker_lines:
                    parts.append(f'{s["name"]}: "{s["line"]}"')
                dialogue_text = "Dialogue: " + " / ".join(parts)
                # Use first speaker's delivery for primary tempo
                dialogue_delivery = per_speaker_lines[0]["delivery"]
                primary_voice_tempo = per_speaker_lines[0]["tempo"]

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
    if bg_data.get("background_music"):
        audio_layers.append(f"music: {bg_data['background_music']}")

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
        audio_section = "AUDIO: No dialogue — visual-only frame. Ambient: " + ", ".join(audio_layers)
    else:
        audio_section = "AUDIO: No dialogue — visual-only frame."

    # ── Duration: Morpheus suggested_duration is the starting point, but
    # dialogue frames MUST have enough time for the spoken words. If Morpheus
    # underestimates, the dialogue-based duration wins.
    morpheus_duration = frame.get("suggested_duration")
    if dialogue_line_all:
        dialogue_duration = _estimate_dialogue_duration(
            dialogue_line_all,
            tempo=primary_voice_tempo,
            env_intensity=dialogue_nodes[0].get("env_intensity", ""),
        )
        if morpheus_duration and 3 <= morpheus_duration <= 30:
            # Take the LONGER of Morpheus and dialogue estimate — dialogue must fit
            duration = max(morpheus_duration, dialogue_duration)
        else:
            duration = dialogue_duration
    elif morpheus_duration and 3 <= morpheus_duration <= 30:
        duration = morpheus_duration
    else:
        duration_map = {
            "F07": 8, "F08": 4, "F18": 8, "F10": 5, "F12": 10, "F17": 10,
            "F01": 5, "F04": 5, "F05": 5, "F11": 4, "F03": 6,
        }
        duration = duration_map.get(tag, 5)

    # ── Dialogue pacing direction: when dialogue exists, add pacing instruction
    # based on how much dialogue must fit within the frame duration
    dialogue_pacing = ""
    if dialogue_line_all:
        combined_dialogue = " ".join(_normalize_ws(l) for l in dialogue_line_all if _normalize_ws(l))
        word_count = len(combined_dialogue.split())
        words_per_sec = word_count / max(duration - 1, 1)  # reserve ~1s for breath/pause
        if words_per_sec > 3.5:
            dialogue_pacing = "Deliver dialogue at a brisk, urgent pace to fit within the frame duration."
        elif words_per_sec > 2.5:
            dialogue_pacing = "Deliver dialogue at a natural, conversational pace."
        elif words_per_sec > 1.5:
            dialogue_pacing = "Deliver dialogue at a measured, deliberate pace with room for pauses."
        else:
            dialogue_pacing = "Deliver dialogue slowly with weight and intentional pauses between phrases."

    # ── Assemble (video prompts omit style prefix, time of day, lighting —
    #    those are baked into the composed image already)
    parts = []
    if continuity_prefix:
        parts.append(continuity_prefix)
    if env_motion:
        parts.append(env_motion)
    if depth_section:
        parts.append(depth_section)
    if bg_section:
        parts.append(bg_section)
    if loc_state_section:
        parts.append(loc_state_section)
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
    if dialogue_pacing:
        parts.append(dialogue_pacing)

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
        "dialogue_pacing": dialogue_pacing,
        "voice_tempo": primary_voice_tempo,
        "action_summary": action_summary,
        "formula_tag": tag,
        "shot_type": shot_type,
        "camera_motion": camera_move,
        "directing": frame.get("directing", {}),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CAST COMPOSITE & LOCATION PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════


def assemble_composite_prompt(graph: NarrativeGraph, cast_id: str) -> dict:
    """Build a cast composite reference image prompt."""
    cast = graph.cast.get(cast_id)
    if not cast:
        raise KeyError(f"Cast {cast_id} not found")

    style_prefix = _resolve_style_prefix(graph)

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

    style_prefix = _resolve_style_prefix(graph)

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


def assemble_location_direction_prompts(graph: NarrativeGraph, location_id: str) -> list[dict]:
    """Build directional view prompts for a location.

    Generates one prompt per cardinal direction that has a description in the
    location's directions data. Each directional view uses the primary location
    image as a reference to maintain spatial and stylistic consistency.

    Returns a list of prompt dicts, one per direction that needs generation.
    """
    loc = graph.locations.get(location_id)
    if not loc:
        raise KeyError(f"Location {location_id} not found")

    style_prefix = _resolve_style_prefix(graph)
    ar = graph.project.aspect_ratio
    size_map = {"16:9": "landscape_16_9", "9:16": "portrait_9_16",
                "4:3": "landscape_4_3", "1:1": "square_hd"}
    size = size_map.get(ar, "landscape_16_9")

    direction_labels = {
        "north": "facing north",
        "south": "facing south, turned around from the primary view",
        "east": "facing east, turned right from the primary view",
        "west": "facing west, turned left from the primary view",
        "exterior": "exterior establishing view — stepping outside, looking outward from the entrance",
    }

    prompts = []
    for direction in ("north", "south", "east", "west", "exterior"):
        view = getattr(loc.directions, direction, None)
        if not view or not view.description:
            continue
        # Skip if already generated
        if view.image_path and view.image_status == "generated":
            continue

        facing_label = direction_labels[direction]
        features = ", ".join(view.key_features) if view.key_features else ""
        depth = view.depth_description or ""

        if direction == "exterior":
            instruction_bridge = (
                f"Use the reference image to craft a cinematic exterior establishing shot of the same location. "
                f"Step outside and look outward from the entrance — show the facade, surroundings, and environmental context. "
                f"Maintain the same architectural style, materials, and atmosphere as the reference."
            )
        else:
            instruction_bridge = (
                f"Use the reference image to craft a cinematic interior view of the same location, "
                f"now {facing_label}. Maintain the same architectural style, materials, lighting "
                f"character, and atmosphere as the reference — this is the same room seen from a "
                f"different angle."
            )

        prompt_parts = [
            style_prefix,
            instruction_bridge,
            f"Location: {loc.name}.",
            f"Visible in this direction: {view.description}.",
        ]
        if features:
            prompt_parts.append(f"Key features: {features}.")
        if depth:
            prompt_parts.append(f"Depth: {depth}.")
        prompt_parts.append(
            "No characters, environmental focus, professional cinematography composition."
        )

        out_path = f"locations/direction/{location_id}_{direction}.png"

        ref_images = []
        if loc.primary_image_path:
            ref_images.append(loc.primary_image_path)

        prompts.append({
            "location_id": location_id,
            "direction": direction,
            "prompt": " ".join(prompt_parts),
            "ref_images": ref_images,
            "size": size,
            "out_path": out_path,
        })

    return prompts


def assemble_prop_prompt(graph: NarrativeGraph, prop_id: str) -> dict:
    """Build a prop reference image prompt."""
    prop = graph.props.get(prop_id)
    if not prop:
        raise KeyError(f"Prop {prop_id} not found")

    style_prefix = _resolve_style_prefix(graph)

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


def resolve_ref_images(graph: NarrativeGraph, frame_id: str,
                       project_dir: str | Path | None = None) -> list[str]:
    """Build the reference image list for a frame generation call.

    All paths are resolved relative to *project_dir* (when supplied) so that
    existence checks work regardless of the process CWD.  Returned paths are
    still relative to project_dir for portability — the caller is responsible
    for making them absolute before handing them to the generation server.
    """
    frame = graph.frames.get(frame_id)
    if not frame:
        return []

    base = Path(project_dir) if project_dir else None

    def _exists(rel_path: str | Path) -> bool:
        p = Path(rel_path)
        if base and not p.is_absolute():
            return (base / p).exists()
        return p.exists()

    refs = []

    # 0. Chain storyboard reference — find which chain this frame belongs to
    for chain in graph.chained_frame_groups.values():
        if frame_id in chain.frame_ids:
            if chain.storyboard_image_path and _exists(chain.storyboard_image_path):
                refs.append(chain.storyboard_image_path)
            else:
                # Fallback: check expected path on disk
                fallback = f"frames/storyboards/{chain.chain_id}_storyboard.png"
                if _exists(fallback):
                    refs.append(fallback)
            break

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

    # 3. Location image — prefer directional view matching camera_facing
    if frame.location_id:
        loc = graph.locations.get(frame.location_id)
        if loc:
            direction_image_used = False
            bg = frame.background
            if bg and bg.camera_facing and loc.directions:
                facing = bg.camera_facing.lower().replace("camera_facing_", "")
                direction_view = getattr(loc.directions, facing, None)
                if direction_view and direction_view.image_path and _exists(direction_view.image_path):
                    refs.append(direction_view.image_path)
                    direction_image_used = True
            if not direction_image_used and loc.primary_image_path:
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


def assemble_chain_storyboard_prompt(graph: NarrativeGraph, chain_id: str,
                                     project_dir: str | Path | None = None) -> dict:
    """Build a multi-panel storyboard prompt for a chained frame group.

    Each chain is a continuous sequence of frames at the same scene+location.
    The storyboard covers all frames in the chain and becomes the reference
    image for every frame within it.

    Returns dict with:
        chain_id: str
        scene_id: str
        location_id: str
        prompt: str — multi-panel storyboard instruction
        ref_images: list[str] — cast/location/prop references for the chain
        size: str — always landscape_16_9
        out_path: str — storyboard output path
        frame_ids: list[str] — frames included in storyboard panels
        all_frame_ids: list[str] — all frames in the chain
    """
    chain = graph.chained_frame_groups.get(chain_id)
    if not chain:
        raise KeyError(f"ChainedFrameGroup {chain_id} not found")

    style_prefix = _resolve_style_prefix(graph)

    # All frames in the chain, in order
    all_frame_ids = [fid for fid in chain.frame_ids if graph.frames.get(fid)]

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
        try:
            img_prompt_data = assemble_image_prompt(graph, fid, project_dir=project_dir)
            panel_body = img_prompt_data["prompt"]
            sp = img_prompt_data.get("style_prefix_used", "")
            if sp and panel_body.startswith(sp):
                panel_body = panel_body[len(sp):]
            # Trim trailing no-text instruction if present
            no_text_marker = "画面内无任何文字"
            no_text_idx = panel_body.find(no_text_marker)
            if no_text_idx > 0:
                panel_body = panel_body[:no_text_idx].rstrip("。，. ,")
            if len(panel_body) > 400:
                panel_body = panel_body[:397] + "..."
        except Exception:
            panel_body = frame.narrative_beat or frame.action_summary or "Visual beat"
            if len(panel_body) > 150:
                panel_body = panel_body[:147] + "..."
        panels.append(f"Panel {i + 1} ({tag_label}): {panel_body}")

    panel_text = "\n".join(panels)

    # Location description
    loc = graph.locations.get(chain.location_id) if chain.location_id else None
    loc_text = f"{loc.name}: {loc.description}" if loc else ""

    # Scene mood
    scene = graph.scenes.get(chain.scene_id)
    mood_text = ", ".join(scene.mood_keywords) if scene and scene.mood_keywords else ""

    # Assemble prompt
    prompt_parts = [
        f"{panel_count}-panel cinematic storyboard arranged in a 2-row grid.",
        f"Exactly {panel_count} panels with clear borders, numbered sequentially.",
        "Consistent character appearance across all panels. Each panel is one key moment.",
        f"All panels share the same continuous location — maintain spatial consistency.",
        "",
        panel_text,
    ]
    if loc_text:
        prompt_parts.append(f"\nLocation: {loc_text}")
    if mood_text:
        prompt_parts.append(f"Mood: {mood_text}")
    prompt_parts.append(f"\nStyle: {style_prefix.strip()}")

    full_prompt = "\n".join(prompt_parts)

    # Gather reference images by querying every frame in the chain — same
    # images the frames themselves would use (minus storyboard self-reference).
    # Deduplicate while preserving insertion order.
    seen: set[str] = set()
    ref_images: list[str] = []
    for fid in all_frame_ids:
        for ref in resolve_ref_images(graph, fid, project_dir=project_dir):
            # Skip storyboard paths — can't reference ourselves
            if "storyboard" in ref:
                continue
            if ref not in seen:
                seen.add(ref)
                ref_images.append(ref)

    out_path = f"frames/storyboards/{chain_id}_storyboard.png"

    return {
        "chain_id": chain_id,
        "scene_id": chain.scene_id,
        "location_id": chain.location_id,
        "prompt": full_prompt,
        "ref_images": ref_images,
        "size": "landscape_16_9",
        "out_path": out_path,
        "frame_ids": sampled_ids,
        "frame_count": panel_count,
        "all_frame_ids": all_frame_ids,
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
            img = assemble_image_prompt(graph, frame_id, project_dir=project_dir)
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

    # Location prompts — primary + directional views
    loc_direction_prompt_dir = project_dir / "locations" / "direction_prompts"
    loc_direction_prompt_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "locations" / "direction").mkdir(parents=True, exist_ok=True)

    for loc_id in graph.locations:
        try:
            lp = assemble_location_prompt(graph, loc_id)
            (loc_prompt_dir / f"{loc_id}_location.json").write_text(
                json.dumps(lp, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["location_prompts"] += 1
        except Exception as e:
            print(f"WARNING: Failed to assemble location for {loc_id}: {e}")

        # Directional view prompts
        try:
            dir_prompts = assemble_location_direction_prompts(graph, loc_id)
            for dp in dir_prompts:
                direction = dp["direction"]
                fname = f"{loc_id}_{direction}.json"
                (loc_direction_prompt_dir / fname).write_text(
                    json.dumps(dp, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                counts["location_prompts"] += 1
        except Exception as e:
            print(f"WARNING: Failed to assemble direction prompts for {loc_id}: {e}")

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

    # Chain storyboard prompts — one per ChainedFrameGroup
    for chain_id, chain in graph.chained_frame_groups.items():
        try:
            sb = assemble_chain_storyboard_prompt(graph, chain_id, project_dir=project_dir)
            (storyboard_prompt_dir / f"{chain_id}_storyboard.json").write_text(
                json.dumps(sb, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            # Update the chain's prompt path on the graph
            chain.storyboard_prompt_path = f"frames/storyboard_prompts/{chain_id}_storyboard.json"
            counts["storyboard_prompts"] += 1
        except Exception as e:
            print(f"WARNING: Failed to assemble storyboard for {chain_id}: {e}")

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


def _get_cast_wardrobe(cast_list: list[dict], cast_id: str) -> str:
    """Return baseline wardrobe from CastIdentity (wardrobe_description or clothing list, 60-char max)."""
    for c in cast_list:
        if c.get("cast_id") == cast_id:
            identity = c.get("identity", {})
            wardrobe = identity.get("wardrobe_description", "")
            if not wardrobe:
                clothing = identity.get("clothing", [])
                wardrobe = ", ".join(clothing) if clothing else ""
            return wardrobe[:60]
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
                _hair_parts = [identity["hair_length"], identity["hair_color"]]
                if identity.get("hair_style"):
                    _hair_parts.append(identity["hair_style"])
                parts.append(" ".join(_hair_parts) + " hair")
            elif identity.get("hair_color"):
                if identity.get("hair_style"):
                    parts.append(f"{identity['hair_color']} {identity['hair_style']} hair")
                else:
                    parts.append(f"{identity['hair_color']} hair")
            elif identity.get("hair_style"):
                parts.append(f"{identity['hair_style']} hair")
            if identity.get("skin"):
                parts.append(f"{identity['skin']} skin")
            # Use current clothing from frame state if available, else identity wardrobe
            clothing = cast_state.get("clothing_current", [])
            if not clothing and identity.get("wardrobe_description"):
                parts.append(f"wearing {identity['wardrobe_description']}")
            elif clothing:
                parts.append(f"wearing {', '.join(clothing[:2])}")
            accessories = identity.get("accessories", [])
            if accessories:
                parts.append(f"wearing {', '.join(accessories[:3])}")
            return ", ".join(parts)
    return ""


def _get_cast_voice_profile(cast_list: list[dict], cast_id: str) -> dict:
    """Return structured voice metadata, personality, and prose voice notes for a cast member."""
    for c in cast_list:
        if c.get("cast_id") == cast_id:
            voice = c.get("voice") or {}
            return {
                "personality": c.get("personality", ""),
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
    personality = _summarize_voice_notes(speaker_voice.get("personality", ""))
    voice_desc = _summarize_voice_notes(speaker_voice.get("voice_description", ""))
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
    env_medium = (dialogue_node.get("env_medium") or "").strip()
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
    # Personality grounds how this character speaks — lead with it
    if personality:
        parts.append(personality)
    # Voice description provides holistic voice profile context
    if voice_desc and not _delivery_fragment_redundant(voice_desc, parts):
        parts.append(voice_desc)
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
    # Audio medium modifier — affects playback quality/texture of the voice
    if env_medium and env_medium in ENV_MEDIUM_AUDIO:
        parts.append(f"Voice {ENV_MEDIUM_AUDIO[env_medium]}")
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
    """Estimate clip duration for native-audio video generation.

    Uses word-count / tempo calculation for ALL dialogue regardless of
    sentence count.  No hardcoded cap — duration scales with content.
    """
    cleaned_lines = [_normalize_ws(line) for line in lines if _normalize_ws(line)]
    if not cleaned_lines:
        return 5

    combined = " ".join(cleaned_lines)
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

    return max(4, math.ceil(units / units_per_second) + 2)


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

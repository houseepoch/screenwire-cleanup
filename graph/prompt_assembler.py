"""
Prompt Assembler — Deterministic graph → prompt construction
=============================================================

Builds image generation prompts and video motion prompts directly
from structured graph data. No LLM involved.

Final image and video prompts are assembled from a canonical ShotPacket
per frame. Storyboards remain continuity-guidance refs only and are not
treated as final-frame outputs.

Shared core sections (SHOT INTENT, CONTINUITY, VISUAL ANCHORS, AUDIO CONTEXT)
are built once by _build_core_sections() and consumed by both
assemble_image_prompt() and assemble_video_prompt().

Video prompt note: prompts are preserved in full. action_summary should still
carry the most important environmental context because it leads the prompt and
helps downstream refiners/generators prioritize the right beat.
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
    LocationFrameState, LightingDirection, LightingQuality,
    Posture, EmotionalArc, FrameComposition, FrameEnvironment,
)
from .reference_collector import (
    ReferenceImageCollector,
    cast_bible_snapshot_for_frame,
)
from .feature_flags import ENABLE_STORYBOARD_GUIDANCE
from .store import GraphStore
from .api import (
    get_frame_context,
    get_frame_cast_state_models,
    get_frame_prop_state_models,
    build_shot_packet,
)
from telemetry import current_run_id


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

# Media style → kinetic motion physics hint for video lead lines.
# Injected as a 2-3 word constraint so motion physics survive style_prefix omission in video.
KINETIC_STYLE_HINT: dict[str, str] = {
    # Live-action variants
    "live_action":        "Motion physics: live-action",
    "live_retro_grain":   "Motion physics: live-action",
    "chiaroscuro_live":   "Motion physics: live-action",
    "live_soft_light":    "Motion physics: live-action",
    "live_clear":         "Motion physics: live-action",
    # Anime-cel variants
    "anime":              "Motion physics: anime-cel",
    "anime_cel":          "Motion physics: anime-cel",
    "new_digital_anime":  "Motion physics: anime-cel",
    "chiaroscuro_anime":  "Motion physics: anime-cel",
    "black_ink_anime":    "Motion physics: anime-cel",
    # 3D render variants
    "3d_render":          "Motion physics: 3d-render",
    "chiaroscuro_3d":     "Motion physics: 3d-render",
}

# Native runtime floor/ceiling enforced by server.py for grok-video clips.
MIN_VIDEO_DURATION_SECONDS = 2
MAX_VIDEO_DURATION_SECONDS = 15
# Cinematic tag family → default duration (seconds)
# D=dialogue, E=establishment, R=revealer, A=action, C=cast/portrait,
# T=transitional, S=stylistic, M=music
DURATION_BY_CINEMATIC_FAMILY = {
    "D": 5,
    "E": 7,
    "R": 5,
    "A": 5,
    "C": 4,
    "T": 4,
    "S": 5,
    "M": 4,
}

# Cinematic tag modifier → camera movement directive
_CINEMATIC_MODIFIER_CAMERA: dict[str, str] = {
    "+push":     "Slow forward dolly",
    "+pull":     "Slow backward dolly",
    "+pan":      "Slow pan",
    "+pan_l":    "Slow pan left",
    "+pan_r":    "Slow pan right",
    "+tilt":     "Slow tilt",
    "+tilt_up":  "Slow tilt up",
    "+tilt_dn":  "Slow tilt down",
    "+crane":    "Slow crane",
    "+static":   "Static",
    "+handheld": "Handheld",
    "+orbit":    "Slow orbit",
    "+zoom_in":  "Slow zoom in",
    "+zoom_out": "Slow zoom out",
}


def _ct_field(ct: dict | object, key: str) -> str:
    """Safe accessor for cinematic_tag dict or model."""
    if isinstance(ct, dict):
        return (ct.get(key) or "")
    return (getattr(ct, key, None) or "")


def _modifier_to_camera_movement(modifier: str) -> str:
    """Translate a cinematic tag modifier string (+push, etc.) to camera movement prose."""
    if not modifier:
        return ""
    return _CINEMATIC_MODIFIER_CAMERA.get(modifier.strip(), "")


def _resolve_shot_description(frame: dict) -> str:
    """Return the shot/composition directive from cinematic_tag.ai_prompt_language."""
    ct = frame.get("cinematic_tag") if isinstance(frame, dict) else getattr(frame, "cinematic_tag", {})
    ct = ct or {}
    return _ct_field(ct, "ai_prompt_language")


DIALOGUE_SHOT_COMPOSITION_LIBRARY = {
    "speaker_sync": {
        "label": "speaker sync",
        "image_rule": "Keep the active speaker dominant and fully readable; the listener can stay secondary or partial, but eyelines and geography must stay locked.",
        "video_rule": "Let expression, breath, and small hand behavior carry the line. Do not restage the room. Preserve staging and vary only crop, angle, or a gentle push.",
        "duration_weight": 1.35,
        "padding_weight": 0.75,
    },
    "listener_reaction": {
        "label": "listener reaction",
        "image_rule": "Hold on the listener's response while dialogue continues under the shot. The speaker can fall off-camera or remain only as soft partial coverage.",
        "video_rule": "Treat this as a reaction hold: tiny eye shifts, breath, and posture changes only. Do not restage the room.",
        "duration_weight": 1.0,
        "padding_weight": 0.45,
    },
    "prelap_entry": {
        "label": "prelap entry",
        "image_rule": "The voice can begin before the speaker becomes the visual focus. Frame the receiver or space so the upcoming line feels motivated without a hard staging reset.",
        "video_rule": "Use this as the dialogue lead-in. Keep motion minimal and let the audio cue pull the shot into the next angle.",
        "duration_weight": 0.8,
        "padding_weight": 0.35,
    },
    "carryover_tail": {
        "label": "carryover tail",
        "image_rule": "Let the spoken line finish over aftermath or reaction coverage. Preserve the established blocking and avoid introducing a new visual idea.",
        "video_rule": "Use a restrained hold so the end of the line lands cleanly before the cut.",
        "duration_weight": 0.9,
        "padding_weight": 0.4,
    },
    "bridge_coverage": {
        "label": "bridge coverage",
        "image_rule": "Bridge between adjacent dialogue angles while holding the same cast positions, prop placement, and room geography.",
        "video_rule": "Do not restage the room. This clip should stitch the exchange together with minimal variation: one visual axis may change, but not the whole blocking plan.",
        "duration_weight": 1.05,
        "padding_weight": 0.5,
    },
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

# Screen-space → world-space cardinal direction mapping
# Key: camera_facing direction. Value: {screen_side: world_direction}
SPATIAL_WORLD_MAP = {
    'north': {'left': 'west',  'right': 'east',  'center': None, 'behind': 'north'},
    'south': {'left': 'east',  'right': 'west',  'center': None, 'behind': 'south'},
    'east':  {'left': 'north', 'right': 'south', 'center': None, 'behind': 'east'},
    'west':  {'left': 'south', 'right': 'north', 'center': None, 'behind': 'west'},
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
    directing = frame.get("directing") if isinstance(frame, dict) else getattr(frame, "directing", None)
    directing = directing or {}
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


SIZE_PRESET_MAP = {
    "16:9": "landscape_16_9",
    "9:16": "portrait_9_16",
    "4:3": "landscape_4_3",
    "3:2": "landscape_3_2",
    "1:1": "square_hd",
}


def _format_section(title: str, lines: list[str]) -> str:
    cleaned = [_compact_text(line) for line in lines if _compact_text(line)]
    if not cleaned:
        return ""
    return f"{title}:\n" + "\n".join(f"- {line}" for line in cleaned)


def _shot_intent_lines(packet) -> list[str]:
    intent = packet.shot_intent
    return [
        " | ".join(part for part in [
            intent.shot,
            f"{intent.angle} angle" if intent.angle else "",
            intent.movement,
            f"focus on {intent.focus}" if intent.focus else "",
        ] if part),
        intent.dramatic_purpose or "",
        intent.beat_turn or "",
        f"POV owner: {intent.pov_owner}" if intent.pov_owner else "",
        f"Viewer learns: {intent.viewer_knowledge_delta}" if intent.viewer_knowledge_delta else "",
        f"Power dynamic: {intent.power_dynamic}" if intent.power_dynamic else "",
        f"Tension source: {intent.tension_source}" if intent.tension_source else "",
        f"Camera motivation: {intent.camera_motivation}" if intent.camera_motivation else "",
        f"Movement motivation: {intent.movement_motivation}" if intent.movement_motivation else "",
        f"Movement path: {intent.movement_path}" if intent.movement_path else "",
        f"Reaction target: {intent.reaction_target}" if intent.reaction_target else "",
    ]


_PURE_BLACK_TOKENS = (
    "pure black",
    "black screen",
    "cuts abruptly to pure black",
    "fade to black",
    "fades to black",
    "fades fully to black",
    "image fades to black",
)
_LOADING_WHEEL_TOKENS = (
    "loading wheel",
    "loading spinner",
    "spinner on black",
)
_SCREEN_MEDIATED_TOKENS = (
    "screen within screen",
    "laptop screen",
    "video feed",
    "virtual window",
    "camera on",
    "camera off",
    "chat explodes",
    "inset video",
)
_HAND_ACTION_TOKENS = (
    "hand",
    "hands",
    "finger",
    "fingers",
    "thumb",
    "forefinger",
)
_HAND_MANIPULATION_TOKENS = (
    "driven",
    "grinding",
    "crushing",
    "probe",
    "probing",
    "extract",
    "lifted",
    "dropped",
)
_OBJECT_MACRO_TOKENS = (
    "cutting board",
    "bowl",
    "cherries",
    "cherry",
    "mortar",
    "pestle",
    "splatter",
    "powder",
    "pit",
    "pulp",
)
_LANDSCAPE_TRANSITION_TOKENS = (
    "sunset",
    "horizon",
    "canyon",
    "sky",
    "landscape",
)
_TITLE_CARD_TOKENS = (
    "title card",
    "burns brightly",
    "screen suddenly flashes",
    "flashes with a bold title card",
)
_SINGLE_SUBJECT_DIRECTIVE_TOKENS = (
    "single person",
    "single speaker",
    "isolated framing",
    "clean single",
    "single_subject",
)
_PROFILE_TWO_SHOT_TOKENS = (
    "profile 50/50",
    "profile view",
    "facing each other",
    "frame split evenly",
)


def _frame_text(frame) -> str:
    parts = [
        frame.get("narrative_beat", "") if isinstance(frame, dict) else getattr(frame, "narrative_beat", ""),
        frame.get("action_summary", "") if isinstance(frame, dict) else getattr(frame, "action_summary", ""),
        frame.get("source_text", "") if isinstance(frame, dict) else getattr(frame, "source_text", ""),
    ]
    return " ".join(_compact_text(str(part)) for part in parts if _compact_text(str(part))).lower()


def _classify_prompt_mode(packet, frame) -> str:
    text = _frame_text(frame)
    if any(token in text for token in _PURE_BLACK_TOKENS):
        return "pure_black"
    if any(token in text for token in _LOADING_WHEEL_TOKENS):
        return "loading_wheel"
    if any(token in text for token in _TITLE_CARD_TOKENS):
        return "title_card"
    if any(token in text for token in _SCREEN_MEDIATED_TOKENS):
        return "screen_presence"
    if not packet.visible_cast_ids and any(token in text for token in _HAND_ACTION_TOKENS):
        return "hand_object_action"
    if packet.subject_count == 0 and any(token in text for token in _OBJECT_MACRO_TOKENS):
        if any(token in text for token in _HAND_MANIPULATION_TOKENS):
            return "hand_object_action"
        return "object_macro"
    if packet.subject_count == 0 and any(token in text for token in _LANDSCAPE_TRANSITION_TOKENS):
        return "environment_transition"
    return "standard"


def _effective_subject_count(packet, mode: str) -> int:
    if packet.subject_count:
        return packet.subject_count
    if mode in {"screen_presence", "hand_object_action"}:
        return 1
    return 0


def _special_handling_lines(mode: str) -> list[str]:
    if mode == "pure_black":
        return [
            "This beat is an intentional authored blackout transition.",
            "Render a clean full-frame black screen only.",
            "No visible objects, no silhouettes, no gradients, no light leaks, and no film grain texture.",
        ]
    if mode == "loading_wheel":
        return [
            "This beat is a minimal UI-like transition card, not a cinematic scene.",
            "Render a single white loading spinner centered on pure black.",
            "No browser chrome, no window frame, no text, no logos, and no extra icons.",
        ]
    if mode == "screen_presence":
        return [
            "The visible person is mediated through the laptop or video-call screen.",
            "Keep the on-screen caller or listener visibly present rather than replacing the beat with a UI-only insert.",
        ]
    if mode == "title_card":
        return [
            "This beat is an authored graphic title-card insert, not a photographed room scene.",
            "Render only the intended slogan or title treatment from the beat description with clean bold typography.",
        ]
    if mode == "hand_object_action":
        return [
            "This beat is carried by anonymous hands and object interaction, not a full human portrait.",
            "If hands are visible, keep them anonymous and tightly framed; do not invent a face, torso, or extra people.",
        ]
    if mode == "object_macro":
        return [
            "This beat is carried by tightly framed objects, surfaces, and material detail rather than a human portrait.",
            "Keep the frame macro and tactile; do not introduce faces, bodies, or unrelated environment expansion.",
        ]
    if mode == "environment_transition":
        return [
            "This beat is a landscape or environmental transition with no visible human subject.",
            "Keep the frame focused on atmosphere, horizon, and place rather than introducing people or close-up objects.",
        ]
    return []


def _is_single_subject_dialogue_coverage(packet, mode: str) -> bool:
    if mode != "standard":
        return False
    if _effective_subject_count(packet, mode) != 1:
        return False
    if not packet.audio.dialogue_present:
        return False
    shot = _compact_text(packet.shot_intent.shot).lower()
    return shot in {"close_up", "medium_shot", "medium_close_up", "closeup"}


def _is_group_cast_frame(packet, mode: str) -> bool:
    return mode == "standard" and _effective_subject_count(packet, mode) >= 3


def _cast_name_tokens(graph: NarrativeGraph) -> set[str]:
    names: set[str] = set()
    for cast in graph.cast.values():
        name = _compact_text(getattr(cast, "name", "") or "")
        if name:
            names.add(name.lower())
    return names


def _line_name(line: str) -> str:
    return _compact_text((line or "").split("|", 1)[0].strip("- ").strip())


def _screen_position_rank(value: str) -> int:
    token = (value or "").strip().lower()
    order = {
        "frame_left_edge": 0,
        "frame_left": 1,
        "frame_left_third": 2,
        "frame_center_left": 3,
        "frame_center": 4,
        "frame_center_right": 5,
        "frame_right_third": 6,
        "frame_right": 7,
        "frame_right_edge": 8,
    }
    if token in order:
        return order[token]
    if "left" in token:
        return 1
    if "right" in token:
        return 7
    if "center" in token:
        return 4
    return 4


def _line_screen_position_rank(line: str) -> int:
    match = re.search(r"\|\s*at\s+([^|]+)", line or "", flags=re.IGNORECASE)
    if match:
        return _screen_position_rank(match.group(1))
    return 4


def _image_background_lines(
    graph: NarrativeGraph,
    packet,
    *,
    mode: str,
    single_subject_dialogue: bool,
) -> list[str]:
    lines = [
        line for line in packet.background
        if not _compact_text(line).lower().startswith("sound cue ")
    ]
    if single_subject_dialogue:
        cast_names = _cast_name_tokens(graph)
        lines = [
            line for line in lines
            if not any(name in _compact_text(line).lower() for name in cast_names)
        ]
        return lines[:4]
    if _is_large_group_cast_frame(packet, mode):
        visual_lines = [line for line in lines if "sound cue " not in _compact_text(line).lower()]
        return visual_lines[:3]
    if _is_group_cast_frame(packet, mode):
        visual_lines = [line for line in lines if "sound cue " not in _compact_text(line).lower()]
        return visual_lines[:4]
    return lines


def _image_location_lines(packet, *, mode: str, single_subject_dialogue: bool) -> list[str]:
    lines = list(packet.location_invariants)
    if _is_large_group_cast_frame(packet, mode):
        return lines[:4]
    if _is_group_cast_frame(packet, mode):
        return lines[:5]
    if single_subject_dialogue:
        return lines[:5]
    return lines


def _is_large_group_cast_frame(packet, mode: str) -> bool:
    return _is_group_cast_frame(packet, mode) and _effective_subject_count(packet, mode) >= 6


def _image_cast_lines(packet, *, mode: str) -> list[str]:
    lines = list(packet.cast_invariants)
    if not _is_group_cast_frame(packet, mode):
        return lines

    ordered_names = [
        _line_name(line)
        for line in sorted(packet.blocking, key=_line_screen_position_rank)
        if _line_name(line)
    ]
    if not ordered_names:
        ordered_names = [_line_name(line) for line in lines if _line_name(line)]
    if _is_large_group_cast_frame(packet, mode):
        anchors = ordered_names[:5]
        summary = [
            "Use the stitched group cast reference as the authority for each visible character's identity, wardrobe, and relative placement.",
            f"Preserve the full visible ensemble as one coherent group tableau with {_effective_subject_count(packet, mode)} people.",
        ]
        if anchors:
            summary.append("Primary left-to-right anchors: " + ", ".join(anchors) + ".")
        summary.append("Keep any remaining visible cast in stitched-sheet order without inventing extras.")
        return summary
    visible_names = ", ".join(ordered_names) if ordered_names else "the visible group"
    return [
        "Use the stitched group cast reference as the authority for each visible character's identity, wardrobe, and relative placement.",
        f"Visible cast left-to-right in the frame: {visible_names}.",
    ]


def _line_action(line: str) -> str:
    parts = [_compact_text(part) for part in (line or "").split("|")]
    if len(parts) >= 4 and parts[3]:
        return parts[3]
    return "holds position"


def _blocking_names(lines: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for line in lines:
        name = _line_name(line)
        key = name.lower()
        if name and key not in seen:
            names.append(name)
            seen.add(key)
    return names


def _shot_descriptor_text(packet, frame) -> str:
    parts = [
        _resolve_shot_description(frame),
        _compact_text(packet.shot_intent.shot),
        _compact_text(packet.shot_intent.dramatic_purpose),
        _compact_text(packet.current_beat),
    ]
    return " ".join(part for part in parts if part).lower()


def _is_single_subject_focus(packet, mode: str, frame, blocking_lines: list[str]) -> bool:
    if mode != "standard":
        return False
    if len(_blocking_names(blocking_lines)) != 1:
        return False
    shot_text = _shot_descriptor_text(packet, frame)
    return any(token in shot_text for token in _SINGLE_SUBJECT_DIRECTIVE_TOKENS)


def _is_profile_two_shot(packet, mode: str, frame, blocking_lines: list[str]) -> bool:
    if mode != "standard":
        return False
    if len(_blocking_names(blocking_lines)) != 2:
        return False
    shot_text = _shot_descriptor_text(packet, frame)
    return any(token in shot_text for token in _PROFILE_TWO_SHOT_TOKENS)


def _filter_pose_snapshot(snapshot: dict | None, allowed_names: list[str]) -> dict | None:
    if not snapshot or not allowed_names:
        return snapshot
    allowed = {name.lower() for name in allowed_names}
    characters = [
        character
        for character in snapshot.get("characters", [])
        if _compact_text(str(character.get("name") or character.get("character_id") or "")).lower() in allowed
    ]
    if not characters:
        return None
    filtered = dict(snapshot)
    filtered["characters"] = characters
    return filtered


def _image_blocking_lines(packet, *, mode: str, profile_two_shot: bool = False) -> list[str]:
    lines = list(packet.blocking)
    if profile_two_shot:
        names = _blocking_names(lines)
        if len(names) == 2:
            left_action = _line_action(lines[0])
            right_action = _line_action(lines[1]) if len(lines) > 1 else "holds position"
            return [
                f"{names[0]} | at frame_left | facing profile_right | {left_action} | looking at {names[1]}",
                f"{names[1]} | at frame_right | facing profile_left | {right_action} | looking at {names[0]}",
            ]
    if not _is_group_cast_frame(packet, mode):
        return lines

    ordered = sorted(lines, key=_line_screen_position_rank)
    anchors = ordered[:3] if _is_large_group_cast_frame(packet, mode) else ordered[:4]
    remaining = [_line_name(line) for line in ordered[4:] if _line_name(line)]
    summary = [
        "Use the stitched group cast reference as the authoritative left-to-right blocking map. Preserve relative spacing and screen positions."
    ]
    summary.extend(anchors)
    if remaining:
        summary.append(
            "Additional visible group members remain established in the stitched reference: "
            + ", ".join(remaining)
            + "."
        )
    if _is_large_group_cast_frame(packet, mode):
        summary.append("Treat the remaining visible cast as one coherent moving cluster rather than isolated hero poses.")
    return summary


def _subject_count_lines(packet, mode: str, override: int | None = None) -> list[str]:
    effective_count = override if override is not None else _effective_subject_count(packet, mode)
    if mode == "screen_presence":
        return [f"Exactly {effective_count} visible subject(s), counted within the laptop or video-call screen."]
    if mode == "hand_object_action":
        return [f"Exactly {effective_count} visible human subject(s), expressed only as anonymous hands if present."]
    if mode in {"pure_black", "loading_wheel", "title_card", "environment_transition", "object_macro"}:
        return ["Exactly 0 visible human subject(s)."]
    return [f"Exactly {effective_count} visible subject(s)."]


def _continuity_lines(packet, *, mode: str = "standard") -> list[str]:
    lines = []
    if packet.previous_beat:
        lines.append(f"Previous beat: {packet.previous_beat.narrative_beat or packet.previous_beat.action_summary or packet.previous_beat.frame_id}")
    lines.append(f"Current beat: {packet.current_beat}")
    if packet.next_beat:
        lines.append(f"Next beat: {packet.next_beat.narrative_beat or packet.next_beat.action_summary or packet.next_beat.frame_id}")
    continuity_deltas = list(packet.continuity_deltas)
    if mode == "screen_presence":
        continuity_deltas = [
            line for line in continuity_deltas
            if not line.startswith("Cast leaving frame:")
        ]
    elif mode == "title_card":
        continuity_deltas = [
            line for line in continuity_deltas
            if not (line.startswith("Cast leaving frame:") or line.startswith("Cast entering frame:"))
        ]
    lines.extend(continuity_deltas)
    return lines


def _image_continuity_lines(
    packet,
    *,
    mode: str,
    single_subject_focus: bool = False,
    large_group_frame: bool = False,
) -> list[str]:
    lines = _continuity_lines(packet, mode=mode)
    if single_subject_focus:
        lines = [line for line in lines if not line.startswith("Cast leaving frame:")]
    if large_group_frame:
        lines = [
            line for line in lines
            if not (line.startswith("Cast entering frame:") or line.startswith("Cast leaving frame:"))
        ]
    return lines


def _negative_constraints(
    packet,
    *,
    dialogue_present: bool,
    guidance_only: bool = False,
    mode: str = "standard",
    subject_count_override: int | None = None,
) -> list[str]:
    lines = [
        "Do not add or remove cast, props, wardrobe, architecture, or light sources.",
        "No subtitles, captions, speech bubbles, lyric text, labels, watermarks, or UI overlays."
        if mode != "title_card"
        else "Do not add captions, subtitles, browser chrome, logos, or any extra UI beyond the authored title text.",
        "Keep anatomy, hands, faces, prop scale, and object physics coherent.",
    ]
    if guidance_only:
        lines.append("This is a storyboard guidance panel, not a final polished hero frame.")
    if dialogue_present:
        lines.append("Dialogue is conveyed through performance and native audio only; do not render spoken words visually.")
    else:
        lines.append("No spoken dialogue should be implied visually through text.")
    effective_subject_count = subject_count_override if subject_count_override is not None else _effective_subject_count(packet, mode)
    if mode == "pure_black":
        lines.append("Render pure black only. No imagery, no silhouettes, no texture, and no visible subject.")
    elif mode == "loading_wheel":
        lines.append("Render only a minimal white loading spinner on black. No extra UI, no browser chrome, and no additional symbols.")
    elif mode == "screen_presence":
        lines.append("Treat the on-screen caller or listener as the visible subject; do not reduce the frame to a faceless interface.")
    elif mode == "title_card":
        lines.append("Render only the intended title-card wording from the beat. Do not add extra copy, slogans, labels, or logos.")
    elif mode == "hand_object_action":
        lines.append("Do not invent a face, torso, or additional people around the hand-driven action.")
    elif mode == "object_macro":
        lines.append("Do not introduce people, faces, or extra room geography into this macro object beat.")
    elif mode == "environment_transition":
        lines.append("Do not introduce people, faces, or hands into this transition beat.")
    if effective_subject_count:
        lines.append(f"Do not exceed {effective_subject_count} visible subject(s).")
    else:
        lines.append("Keep visible human subject count at zero.")
    return lines


def _span_frame_ids(graph: NarrativeGraph, start_frame: str, end_frame: str) -> list[str]:
    if not start_frame:
        return []
    if not end_frame or start_frame == end_frame:
        return [start_frame]
    try:
        si = graph.frame_order.index(start_frame)
        ei = graph.frame_order.index(end_frame)
    except ValueError:
        return [start_frame]
    if ei < si:
        return [start_frame]
    return graph.frame_order[si:ei + 1]


def _split_span_text_chunk(text: str, chunk_index: int, total_chunks: int) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text or "") if s.strip()]
    if len(sentences) >= total_chunks > 0:
        per_chunk = len(sentences) / total_chunks
        start = round(chunk_index * per_chunk)
        end = round((chunk_index + 1) * per_chunk)
        return " ".join(sentences[start:end])

    words = (text or "").split()
    if not words or total_chunks <= 0:
        return ""
    per_chunk = len(words) / total_chunks
    start = round(chunk_index * per_chunk)
    end = max(round((chunk_index + 1) * per_chunk), start + 1)
    if start >= len(words):
        return ""
    return " ".join(words[start:end])


def _dialogue_role_for_frame(dialogue_node: DialogueNode, frame_id: str, span_frames: list[str]) -> str:
    if frame_id == dialogue_node.primary_visual_frame:
        return "speaker_sync"
    if frame_id in (dialogue_node.reaction_frame_ids or []):
        return "listener_reaction"
    if span_frames:
        if frame_id == span_frames[0]:
            return "prelap_entry"
        if frame_id == span_frames[-1]:
            return "carryover_tail"
    return "bridge_coverage"


def _dialogue_scene_stitch_lines() -> list[str]:
    return [
        "Hold cast positions, eyelines, and prop geography stable across the dialogue run.",
        "Between adjacent dialogue clips, change only one visual axis at a time: angle, crop, or camera distance.",
        "Prefer readable coverage progression such as speaker close coverage, listener reaction, then a wider re-anchor shot instead of restaging the room.",
    ]


def _build_dialogue_coverage(
    graph: NarrativeGraph,
    frame_id: str,
    ctx: dict,
    packet,
) -> list[dict[str, object]]:
    if not packet.audio.dialogue_present:
        return []

    visible_cast_ids = set(packet.visible_cast_ids)
    cast_name_by_id = {cast.cast_id: cast.name for cast in graph.cast.values()}

    coverage: list[dict[str, object]] = []
    for turn in packet.audio.turns:
        dnode = graph.dialogue.get(turn.dialogue_id)
        if dnode is None:
            continue

        span_frames = _span_frame_ids(graph, dnode.start_frame, dnode.end_frame)
        if not span_frames:
            span_frames = [frame_id]
        try:
            span_index = span_frames.index(frame_id) + 1
        except ValueError:
            span_index = 1
        role = _dialogue_role_for_frame(dnode, frame_id, span_frames)
        role_policy = DIALOGUE_SHOT_COMPOSITION_LIBRARY[role]
        listener_ids = [cast_id for cast_id in sorted(visible_cast_ids) if cast_id != dnode.cast_id]
        listener_names = [cast_name_by_id.get(cast_id, cast_id) for cast_id in listener_ids]

        coverage.append({
            "dialogue_id": dnode.dialogue_id,
            "speaker": dnode.speaker,
            "speaker_cast_id": dnode.cast_id,
            "speaker_visible": dnode.cast_id in visible_cast_ids,
            "listener_names": listener_names,
            "span_frames": span_frames,
            "span_index": span_index,
            "span_length": len(span_frames),
            "role": role,
            "role_label": role_policy["label"],
            "image_rule": role_policy["image_rule"],
            "video_rule": role_policy["video_rule"],
            "duration_weight": role_policy["duration_weight"],
            "padding_weight": role_policy["padding_weight"],
            "line_chunk": turn.line or "",
            "env_intensity": turn.env_intensity or "",
        })

    coverage.sort(key=lambda item: next(
        (idx for idx, turn in enumerate(packet.audio.turns) if turn.dialogue_id == item["dialogue_id"]),
        0,
    ))
    return coverage


def _dialogue_coverage_lines(coverage: list[dict[str, object]], *, for_video: bool) -> list[str]:
    if not coverage:
        return []

    lines = _dialogue_scene_stitch_lines()
    seen_dialogues: set[str] = set()
    for item in coverage:
        dialogue_id = str(item["dialogue_id"])
        if dialogue_id in seen_dialogues:
            continue
        seen_dialogues.add(dialogue_id)
        listener_names = item.get("listener_names") or []
        listener_text = ""
        if listener_names:
            listener_text = " | listener coverage: " + ", ".join(str(name) for name in listener_names[:2])
        lines.append(
            f'{item["speaker"]} | {item["role_label"]} | span {item["span_index"]}/{item["span_length"]}{listener_text}'
        )
        policy_line = item["video_rule"] if for_video else item["image_rule"]
        if policy_line:
            lines.append(str(policy_line))
    return lines


def _motion_continuity_lines(frame: dict, packet) -> list[str]:
    lines: list[str] = []
    composition = frame.get("composition") if isinstance(frame, dict) else getattr(frame, "composition", None)
    composition = composition or {}
    transition = _compact_text(composition.get("transition") if isinstance(composition, dict) else getattr(composition, "transition", None))
    if transition:
        lines.append(f"Transition from previous frame: {transition}")
    visual_flow = frame.get("visual_flow_element") if isinstance(frame, dict) else getattr(frame, "visual_flow_element", None)
    if visual_flow:
        lines.append(f'Visual flow emphasis: {visual_flow}')
    next_beat = packet.next_beat.narrative_beat if packet.next_beat else ""
    if next_beat:
        lines.append(f"End the clip ready to cut into: {next_beat}")
    return lines


def _require_video_shot_payload(frame_id: str, packet) -> None:
    missing = []
    if not _compact_text(packet.shot_intent.shot):
        missing.append("shot")
    if not _compact_text(packet.shot_intent.angle):
        missing.append("angle")
    if not _compact_text(packet.shot_intent.movement):
        missing.append("movement")
    if missing:
        raise ValueError(
            f"{frame_id}: incomplete shot packet for video prompt assembly; missing {', '.join(missing)}"
        )


def _assemble_prompt_sections(
    packet,
    lead_lines: list[str],
    *,
    audio_title: str,
    audio_lines: list[str],
    dialogue_coverage_lines: list[str] | None = None,
    motion_lines: list[str] | None = None,
) -> list[str]:
    sections = [
        "\n".join(_compact_text(line) for line in lead_lines if _compact_text(line)),
        _format_section("SHOT INTENT", _shot_intent_lines(packet)),
        _format_section("CONTINUITY", _continuity_lines(packet)),
        _format_section("SUBJECT COUNT", [f"Exactly {packet.subject_count} visible subject(s)."]),
        _format_section("CAST INVARIANTS", packet.cast_invariants),
        _format_section("PROP INVARIANTS", packet.prop_invariants),
        _format_section("LOCATION INVARIANTS", packet.location_invariants),
        _format_section("DIALOGUE COVERAGE", dialogue_coverage_lines or []),
        _format_section("BLOCKING", packet.blocking),
        _format_section("BACKGROUND", packet.background),
        _format_section("MOTION CONTINUITY", motion_lines or []),
        _format_section(audio_title, audio_lines),
        _format_section(
            "NEGATIVE CONSTRAINTS",
            _negative_constraints(packet, dialogue_present=packet.audio.dialogue_present),
        ),
    ]
    return [section for section in sections if section]


def _load_cast_bible_snapshot(
    graph: NarrativeGraph,
    *,
    frame_id: str,
    project_dir: str | Path | None = None,
    cast_ids: list[str] | None = None,
):
    if project_dir is None:
        return None

    base = Path(project_dir)
    store = GraphStore(base)
    run_id = current_run_id("")
    sequence_id = getattr(graph.project, "project_id", "") or ""
    cast_bible = store.load_latest_cast_bible(run_id=run_id, sequence_id=sequence_id)
    if cast_bible is None:
        collector = ReferenceImageCollector(graph, base)
        cast_bible = collector.build_cast_bible(run_id=run_id, sequence_id=sequence_id)

    return cast_bible_snapshot_for_frame(cast_bible, graph, frame_id, cast_ids=cast_ids)


def _pose_lock_lines(snapshot: dict | None, *, include_history: bool = True) -> list[str]:
    if not snapshot:
        return []

    lines: list[str] = []
    for character in snapshot.get("characters", []):
        pose = character.get("pose") or {}
        pose_name = _compact_text(str(pose.get("pose") or ""))
        if not pose_name:
            continue

        name = _compact_text(str(character.get("name") or character.get("character_id") or "Character"))
        modifiers = [
            _compact_text(str(item))
            for item in pose.get("modifiers", [])
            if _compact_text(str(item))
        ]
        line = f"{name}: exactly match locked pose {pose_name}."
        if modifiers:
            line += " Locked modifiers: " + ", ".join(modifiers) + "."

        recent_history = character.get("recent_pose_history") or []
        if include_history and recent_history:
            trail = [
                _compact_text(str(item.get("pose") or ""))
                for item in recent_history
                if _compact_text(str(item.get("pose") or ""))
            ]
            if trail:
                line += " Recent transition trail: " + " -> ".join(trail + [pose_name]) + "."

        line += " Do not change posture unless the blocking explicitly calls for a transition."
        lines.append(line)

    return lines


def _build_core_sections(
    packet,
    frame_node,
    graph: "NarrativeGraph",
    refs: dict | None = None,
    pose_snapshot: dict | None = None,
    include_pose_history: bool = True,
) -> dict[str, str]:
    """Build the shared prompt sections consumed by both image and video assembly.

    Returns a dict with keys:
        shot_intent    — SHOT INTENT section string
        continuity     — CONTINUITY section string
        visual_anchors — VISUAL ANCHORS section string (empty string when refs is None)
        audio_context  — AUDIO CONTEXT section string (dialogue presence + performance
                         direction; no raw dialogue text, safe for image prompts)

    Args:
        packet:      ShotPacket for the frame.
        frame_node:  Frame dict or FrameNode object (reserved for future per-frame hints).
        graph:       NarrativeGraph (reserved for entity lookups).
        refs:        Optional dict from ReferenceImageCollector.get_frame_references(),
                     mapping role label strings to image path(s). Each entry becomes a
                     VISUAL ANCHORS bullet so the generator knows each ref's purpose.
                     Pass None (default) to skip the section — fully backward compatible.
    """
    # SHOT INTENT — cinematic tag, directing, composition
    shot_intent = _format_section("SHOT INTENT", _shot_intent_lines(packet))

    # CONTINUITY — beat context (cast/prop/location invariants remain separate sections
    # built by each caller so their section keys are preserved for video compression)
    continuity = _format_section("CONTINUITY", _continuity_lines(packet))

    pose_lock = _format_section("POSE LOCK", _pose_lock_lines(pose_snapshot, include_history=include_pose_history))

    # VISUAL ANCHORS — reference images listed with their role labels
    visual_anchors = ""
    if refs:
        anchor_lines: list[str] = []
        for role, path in refs.items():
            if isinstance(path, (list, tuple)):
                for p in path:
                    if p:
                        anchor_lines.append(f"{role}: {p}")
            elif path:
                anchor_lines.append(f"{role}: {path}")
        visual_anchors = _format_section("VISUAL ANCHORS", anchor_lines)

    # AUDIO CONTEXT — dialogue presence + performance direction (no raw line text;
    # raw dialogue text is intentionally excluded so this section is safe for image
    # prompts where visible dialogue words must not be rendered)
    audio_ctx_lines: list[str] = []
    if packet.audio.dialogue_present:
        audio_ctx_lines.append(
            "Dialogue is happening in this frame; preserve the speaking beat through expression and body language only."
        )
        for turn in packet.audio.turns:
            if turn.performance_direction:
                audio_ctx_lines.append(f"Performance: {turn.performance_direction}")
        audio_ctx_lines.append(
            "Do not render subtitles, captions, lyric text, speech bubbles, or visible dialogue words."
        )
    else:
        audio_ctx_lines.append("No spoken dialogue in this frame.")
    audio_context = _format_section("AUDIO CONTEXT", audio_ctx_lines)

    return {
        "shot_intent": shot_intent,
        "continuity": continuity,
        "pose_lock": pose_lock,
        "visual_anchors": visual_anchors,
        "audio_context": audio_context,
    }


_VIDEO_PROMPT_LEAD_KEY = "__LEAD__"
_VIDEO_PROMPT_TIER1_KEYS = {"AUDIO:", "MOTION CONTINUITY:"}
_VIDEO_PROMPT_TIER3_DROP_ORDER = (
    "VISUAL ANCHORS:",
    "BACKGROUND:",
    "LOCATION INVARIANTS:",
    "PROP INVARIANTS:",
)
_VIDEO_PROMPT_TIER2_SHRINK_ORDER = (
    _VIDEO_PROMPT_LEAD_KEY,
    "BLOCKING:",
    "DIALOGUE COVERAGE:",
    "CONTINUITY:",
    "SHOT INTENT:",
    "CAST INVARIANTS:",
    "SUBJECT COUNT:",
    "NEGATIVE CONSTRAINTS:",
)
_VIDEO_PROMPT_TIER2_DROP_ORDER = (
    "NEGATIVE CONSTRAINTS:",
    "DIALOGUE COVERAGE:",
    "CAST INVARIANTS:",
)


def _parse_prompt_section(section: str) -> dict[str, object]:
    lines = [_compact_text(line) for line in section.splitlines() if _compact_text(line)]
    if not lines:
        return {"key": _VIDEO_PROMPT_LEAD_KEY, "heading": "", "lines": []}
    heading = lines[0] if lines[0].endswith(":") else ""
    return {
        "key": heading or _VIDEO_PROMPT_LEAD_KEY,
        "heading": heading,
        "lines": lines[1:] if heading else lines,
    }


def _render_prompt_section(section_info: dict[str, object]) -> str:
    lines = [str(line) for line in section_info.get("lines", []) if _compact_text(str(line))]
    heading = str(section_info.get("heading", "") or "")
    if heading:
        if not lines:
            return ""
        return "\n".join([heading, *lines])
    return "\n".join(lines)


def _serialize_prompt_sections(section_infos: list[dict[str, object]]) -> str:
    return "\n\n".join(
        rendered for rendered in (_render_prompt_section(info) for info in section_infos) if rendered
    ).strip()


def _drop_section(section_infos: list[dict[str, object]], key: str) -> bool:
    for idx, info in enumerate(section_infos):
        if info.get("key") == key:
            section_infos.pop(idx)
            return True
    return False


def _shrink_section(section_infos: list[dict[str, object]], key: str) -> bool:
    for info in section_infos:
        if info.get("key") != key:
            continue
        lines = info.get("lines", [])
        if isinstance(lines, list) and len(lines) > 1:
            lines.pop()
            return True
    return False


def _serialize_video_prompt_sections(sections: list[str]) -> str:
    """Serialize the assembled video prompt sections without length capping."""
    return "\n\n".join(section for section in sections if section).strip()


def _tempo_units_per_second(tempo: str = "", env_intensity: str = "") -> float:
    tempo_lower = (tempo or "").lower()
    env_lower = (env_intensity or "").lower()
    units_per_second = 2.15

    if "fast" in tempo_lower:
        units_per_second = 2.75
    elif any(token in tempo_lower for token in ("slow", "measured", "deliberate", "careful")):
        units_per_second = 1.8

    if any(token in env_lower for token in ("whisper", "quiet", "soft", "hushed")):
        units_per_second = min(units_per_second, 1.75)
    elif any(token in env_lower for token in ("loud", "shouting", "shout", "yelling", "urgent")):
        units_per_second = max(units_per_second, 2.45)

    return units_per_second


def _allocate_dialogue_span_duration(
    graph: NarrativeGraph,
    dialogue_node: DialogueNode,
    *,
    frame_id: str,
    tempo: str = "",
    env_intensity: str = "",
) -> float:
    span_frames = _span_frame_ids(graph, dialogue_node.start_frame, dialogue_node.end_frame)
    if not span_frames:
        span_frames = [frame_id]

    full_line = _normalize_ws(dialogue_node.raw_line or dialogue_node.line)
    full_timing = _estimate_dialogue_timing(
        [full_line] if full_line else [],
        tempo=tempo,
        env_intensity=env_intensity or dialogue_node.env_intensity or "",
    )

    extra_budget = max(
        float(full_timing["recommended_duration"]) - (MIN_VIDEO_DURATION_SECONDS * len(span_frames)),
        0.0,
    )

    weighted_frames: list[tuple[str, float]] = []
    for idx, span_frame_id in enumerate(span_frames):
        role = _dialogue_role_for_frame(dialogue_node, span_frame_id, span_frames)
        role_policy = DIALOGUE_SHOT_COMPOSITION_LIBRARY[role]
        chunk_text = _split_span_text_chunk(full_line, idx, len(span_frames)) if full_line else ""
        chunk_units = max(_count_dialogue_units(chunk_text), 1)
        weight = (
            chunk_units * float(role_policy["duration_weight"]) +
            float(role_policy["padding_weight"])
        )
        weighted_frames.append((span_frame_id, weight))

    total_weight = sum(weight for _, weight in weighted_frames) or 1.0
    allocation = next(
        (
            MIN_VIDEO_DURATION_SECONDS + (extra_budget * weight / total_weight)
            for span_frame_id, weight in weighted_frames
            if span_frame_id == frame_id
        ),
        float(MIN_VIDEO_DURATION_SECONDS),
    )
    return allocation


def _formula_default_duration(frame: dict, tag: str) -> int:
    ct = frame.get("cinematic_tag") if isinstance(frame, dict) else getattr(frame, "cinematic_tag", {})
    ct = ct or {}
    family = _ct_field(ct, "family")
    duration = DURATION_BY_CINEMATIC_FAMILY.get(family, 4)
    visual_flow = frame.get("visual_flow_element") if isinstance(frame, dict) else getattr(frame, "visual_flow_element", "")
    visual_flow = (visual_flow or "").lower()
    if visual_flow in {"establishment", "transition", "weight"}:
        duration += 1
    elif visual_flow in {"reaction", "dialogue"}:
        duration = max(duration, 3)

    composition = frame.get("composition") if isinstance(frame, dict) else getattr(frame, "composition", {})
    composition = composition or {}
    transition = _compact_text(composition.get("transition") if isinstance(composition, dict) else getattr(composition, "transition", "")).lower()
    if transition and any(token in transition for token in ("match", "carry", "linger", "hold")):
        duration += 1
    return min(max(duration, MIN_VIDEO_DURATION_SECONDS), MAX_VIDEO_DURATION_SECONDS)


def _resolve_video_duration(
    graph: NarrativeGraph,
    frame_id: str,
    frame: dict,
    packet,
    dialogue_coverage: list[dict[str, object]],
    primary_voice_tempo: str,
) -> dict[str, object]:
    tag = frame.get("cinematic_tag", {}) if isinstance(frame, dict) else getattr(frame, "cinematic_tag", {})
    formula_duration = _formula_default_duration(frame, "")
    authored_duration = frame.get("suggested_duration") if isinstance(frame, dict) else getattr(frame, "suggested_duration", None)
    dialogue_lines = [turn.line for turn in packet.audio.turns if _normalize_ws(turn.line)]
    dialogue_turns = packet.audio.turns
    env_intensity = dialogue_turns[0].env_intensity if dialogue_turns else ""
    multi_frame_dialogue = any(int(item.get("span_length", 1)) > 1 for item in dialogue_coverage)
    if dialogue_lines:
        dialogue_floor = 3 if multi_frame_dialogue else max(MIN_VIDEO_DURATION_SECONDS, min(formula_duration, 4))
        duration_candidates: list[tuple[str, float]] = [("dialogue_floor", float(dialogue_floor))]
    else:
        duration_candidates = [("formula_default", float(formula_duration))]
    if authored_duration and MIN_VIDEO_DURATION_SECONDS <= authored_duration <= 30:
        duration_candidates.append(("authored_duration", float(authored_duration)))

    dialogue_timing: dict[str, float | int | bool] | None = None
    duration_allocation_details: list[dict[str, object]] = []

    if dialogue_lines:
        if multi_frame_dialogue:
            pass
        else:
            dialogue_timing = _estimate_dialogue_timing(
                dialogue_lines,
                tempo=primary_voice_tempo,
                env_intensity=env_intensity or "",
            )
            duration_candidates.append(("dialogue_timing", float(dialogue_timing["recommended_duration"])))

        seen_dialogues: set[str] = set()
        for item in dialogue_coverage:
            dialogue_id = str(item["dialogue_id"])
            if dialogue_id in seen_dialogues:
                continue
            seen_dialogues.add(dialogue_id)
            dnode = graph.dialogue.get(dialogue_id)
            if dnode is None:
                continue
            allocated = _allocate_dialogue_span_duration(
                graph,
                dnode,
                frame_id=frame_id,
                tempo=primary_voice_tempo,
                env_intensity=str(item.get("env_intensity") or env_intensity or ""),
            )
            duration_candidates.append(("dialogue_span_allocation", allocated))
            duration_allocation_details.append({
                "dialogue_id": dialogue_id,
                "role": item["role"],
                "span_index": item["span_index"],
                "span_length": item["span_length"],
                "allocated_seconds": round(allocated, 2),
            })

        if dialogue_timing is None:
            dialogue_timing = _estimate_dialogue_timing(
                dialogue_lines,
                tempo=primary_voice_tempo,
                env_intensity=env_intensity or "",
            )

    reason, recommended_duration = max(duration_candidates, key=lambda item: item[1])
    recommended_duration = max(recommended_duration, float(MIN_VIDEO_DURATION_SECONDS))
    actual_duration = int(min(MAX_VIDEO_DURATION_SECONDS, math.ceil(recommended_duration)))

    if dialogue_lines:
        if recommended_duration > MAX_VIDEO_DURATION_SECONDS:
            dialogue_fit_status = "capped_to_model_max"
        elif multi_frame_dialogue:
            dialogue_fit_status = "span_allocated"
        else:
            dialogue_fit_status = "fits"
    else:
        dialogue_fit_status = "no_dialogue"

    return {
        "duration": actual_duration,
        "recommended_duration": round(recommended_duration, 2),
        "duration_reason": reason,
        "dialogue_fit_status": dialogue_fit_status,
        "dialogue_timing": dialogue_timing,
        "duration_allocation_details": duration_allocation_details,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE PROMPT ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════════


def assemble_image_prompt(graph: NarrativeGraph, frame_id: str,
                          project_dir: str | Path | None = None,
                          refs: dict | None = None) -> dict:
    """Build the final single-frame prompt for a frame.

    Storyboards act only as continuity guidance through `ref_images`. The
    prompt itself is assembled from the canonical shot packet.

    Args:
        refs: Optional dict from ReferenceImageCollector.get_frame_references(),
              mapping role label strings to image paths. When provided, a VISUAL
              ANCHORS section is injected so the generator knows each ref's purpose.
    """
    ctx = get_frame_context(graph, frame_id)
    frame = ctx["frame"]
    scene = ctx["scene"] or {}
    packet = build_shot_packet(graph, frame_id)

    style_prefix = _resolve_style_prefix(graph)
    collector = ReferenceImageCollector(graph, Path(project_dir)) if project_dir else None
    storyboard_image = None
    reference_images: list[str] = []
    reference_manifest = None
    cast_bible_snapshot = _load_cast_bible_snapshot(
        graph,
        frame_id=frame_id,
        project_dir=project_dir,
        cast_ids=packet.visible_cast_ids,
    )
    if collector is not None:
        collected = collector.get_frame_references(frame_id)
        if collected.storyboard_cell and collected.storyboard_cell.exists():
            storyboard_image = str(collected.storyboard_cell.relative_to(Path(project_dir)).as_posix())
        reference_images = [
            str(path.relative_to(Path(project_dir)).as_posix())
            for path in collector.get_flat_reference_list(frame_id)
            if path.exists()
        ]
        reference_manifest = collector.build_reference_manifest_entry(frame_id)

    ref_images = (
        ([storyboard_image] if storyboard_image else []) + reference_images
        if collector is not None
        else resolve_ref_images(graph, frame_id, project_dir=project_dir)
    )
    size = SIZE_PRESET_MAP.get(graph.project.aspect_ratio, "landscape_16_9")
    dialogue_coverage = _build_dialogue_coverage(graph, frame_id, ctx, packet)
    prompt_mode = _classify_prompt_mode(packet, frame)
    group_cast_frame = _is_group_cast_frame(packet, prompt_mode)
    large_group_frame = _is_large_group_cast_frame(packet, prompt_mode)
    special_handling = _format_section("SPECIAL HANDLING", _special_handling_lines(prompt_mode))

    if prompt_mode == "pure_black":
        lead_lines = [
            "Generate one final finished frame.",
            "Output clean pure black only.",
        ]
    elif prompt_mode == "loading_wheel":
        lead_lines = [
            "Generate one final finished frame.",
            "Output a simple white loading spinner centered on pure black.",
        ]
    elif prompt_mode == "title_card":
        lead_lines = [
            "Generate one final finished frame.",
            "Render a bold authored title-card insert using only the wording described in this beat.",
            (frame.get("action_summary") if isinstance(frame, dict) else getattr(frame, "action_summary", None)) or packet.current_beat,
        ]
    else:
        lead_lines = [
            f"{style_prefix}Generate one final cinematic frame.",
            "This is a single finished image, not a storyboard grid or contact sheet.",
            (frame.get("action_summary") if isinstance(frame, dict) else getattr(frame, "action_summary", None)) or packet.current_beat,
        ]
    if scene.get("mood_keywords"):
        lead_lines.append("Mood: " + ", ".join(scene["mood_keywords"]))

    # Cinematic tag composition directive — inject before audio section
    shot_desc = _resolve_shot_description(frame)
    if shot_desc and prompt_mode not in {"pure_black", "loading_wheel", "title_card"}:
        lead_lines.append(shot_desc)
    _ct_img = frame.get("cinematic_tag") if isinstance(frame, dict) else getattr(frame, "cinematic_tag", {})
    _ct_img = _ct_img or {}
    _ct_img_definition = _ct_field(_ct_img, "definition")
    if _ct_img_definition and prompt_mode not in {"pure_black", "loading_wheel", "title_card"}:
        lead_lines.append(_ct_img_definition)
    _ct_img_dof = _ct_field(_ct_img, "dof_guidance")
    if _ct_img_dof and prompt_mode not in {"pure_black", "loading_wheel", "title_card"}:
        lead_lines.append(_ct_img_dof)
    _ct_img_lens = _ct_field(_ct_img, "lens_guidance")
    if _ct_img_lens and prompt_mode not in {"pure_black", "loading_wheel", "title_card"}:
        lead_lines.append(_ct_img_lens)

    raw_blocking_lines = list(packet.blocking)
    profile_two_shot = _is_profile_two_shot(packet, prompt_mode, frame, raw_blocking_lines)
    image_blocking_lines = _image_blocking_lines(
        packet,
        mode=prompt_mode,
        profile_two_shot=profile_two_shot,
    )
    single_subject_focus = _is_single_subject_focus(packet, prompt_mode, frame, image_blocking_lines)
    single_subject_dialogue = single_subject_focus or _is_single_subject_dialogue_coverage(packet, prompt_mode)
    subject_count_override = 1 if single_subject_focus else None
    pose_snapshot = cast_bible_snapshot
    if single_subject_focus:
        pose_snapshot = _filter_pose_snapshot(cast_bible_snapshot, _blocking_names(image_blocking_lines))

    # Shared core: SHOT INTENT, CONTINUITY, VISUAL ANCHORS, AUDIO CONTEXT
    core = _build_core_sections(
        packet,
        frame,
        graph,
        refs=refs,
        pose_snapshot=pose_snapshot,
        include_pose_history=False,
    )

    image_location_lines = _image_location_lines(
        packet,
        mode=prompt_mode,
        single_subject_dialogue=single_subject_dialogue,
    )
    image_background_lines = _image_background_lines(
        graph,
        packet,
        mode=prompt_mode,
        single_subject_dialogue=single_subject_dialogue,
    )
    image_cast_lines = _image_cast_lines(packet, mode=prompt_mode)

    sections = [
        "\n".join(_compact_text(line) for line in lead_lines if _compact_text(line)),
        special_handling,
        core["shot_intent"],
        _format_section(
            "CONTINUITY",
            _image_continuity_lines(
                packet,
                mode=prompt_mode,
                single_subject_focus=single_subject_focus,
                large_group_frame=large_group_frame,
            ),
        ),
        core["pose_lock"] if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "environment_transition", "title_card", "hand_object_action"} and not group_cast_frame and not profile_two_shot else "",
        core["visual_anchors"] if prompt_mode not in {"pure_black", "loading_wheel", "title_card"} else "",
        _format_section("SUBJECT COUNT", _subject_count_lines(packet, prompt_mode, override=subject_count_override)),
        _format_section("CAST INVARIANTS", image_cast_lines) if prompt_mode not in {"pure_black", "loading_wheel", "title_card"} else "",
        _format_section("PROP INVARIANTS", packet.prop_invariants) if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "object_macro", "environment_transition", "title_card"} else "",
        _format_section("LOCATION INVARIANTS", image_location_lines) if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "object_macro", "environment_transition", "title_card", "hand_object_action"} else "",
        _format_section("DIALOGUE COVERAGE", _dialogue_coverage_lines(dialogue_coverage, for_video=False)) if prompt_mode not in {"screen_presence", "title_card"} else "",
        _format_section("BLOCKING", image_blocking_lines) if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "object_macro", "environment_transition", "title_card", "hand_object_action"} else "",
        _format_section("BACKGROUND", image_background_lines) if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "object_macro", "hand_object_action"} else "",
        core["audio_context"],
        _format_section(
            "NEGATIVE CONSTRAINTS",
            _negative_constraints(
                packet,
                dialogue_present=packet.audio.dialogue_present,
                mode=prompt_mode,
                subject_count_override=subject_count_override,
            ),
        ),
    ]
    full_prompt = "\n\n".join(section for section in sections if section).strip()

    return {
        "frame_id": frame_id,
        "scene_id": frame.get("scene_id", "") if isinstance(frame, dict) else getattr(frame, "scene_id", ""),
        "sequence_index": frame.get("sequence_index", 0) if isinstance(frame, dict) else getattr(frame, "sequence_index", 0),
        "prompt": full_prompt,
        "ref_images": ref_images,
        "storyboard_image": storyboard_image,
        "reference_images": reference_images,
        "reference_manifest": reference_manifest,
        "cast_bible_snapshot": cast_bible_snapshot,
        "size": size,  # canonical schema key — do not use legacy 'image_size'
        "out_path": f"frames/composed/{frame_id}_gen.png",
        "cinematic_tag": _ct_field(_ct_img, "tag"),
        "style_prefix_used": style_prefix,
        "shot_packet_path": f"frames/shot_packets/{frame_id}.json",
        "dialogue_present": packet.audio.dialogue_present,
        "dialogue_coverage_roles": [str(item["role"]) for item in dialogue_coverage],
        "directing": frame.get("directing", {}) if isinstance(frame, dict) else getattr(frame, "directing", {}),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO PROMPT ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════════


def assemble_video_prompt(graph: NarrativeGraph, frame_id: str,
                          project_dir: str | Path | None = None,
                          refs: dict | None = None) -> dict:
    """Build a Grok native-audio video prompt for a frame.

    The only behavioral branch is whether dialogue exists on the frame.
    There are no alternate video providers or external audio inputs.

    Args:
        refs: Optional dict from ReferenceImageCollector.get_frame_references(),
              mapping role label strings to image paths. When provided, a VISUAL
              ANCHORS section is injected listing each reference by role.

    Note on action_summary: it should still carry the most important
    environmental context (lighting, atmosphere, location atmosphere) because
    it leads the prompt and anchors downstream refinement.
    """
    ctx = get_frame_context(graph, frame_id)
    frame = ctx["frame"]
    scene = ctx["scene"] or {}
    packet = build_shot_packet(graph, frame_id)
    dialogue_coverage = _build_dialogue_coverage(graph, frame_id, ctx, packet)
    cast_bible_snapshot = _load_cast_bible_snapshot(
        graph,
        frame_id=frame_id,
        project_dir=project_dir,
        cast_ids=packet.visible_cast_ids,
    )
    _require_video_shot_payload(frame_id, packet)

    _ct_vid = frame.get("cinematic_tag") if isinstance(frame, dict) else getattr(frame, "cinematic_tag", {})
    _ct_vid = _ct_vid or {}
    shot_type = _ct_field(_ct_vid, "ai_prompt_language")
    camera_movement = (
        _modifier_to_camera_movement(_ct_field(_ct_vid, "modifier"))
        or packet.shot_intent.movement
    )
    tag = _ct_field(_ct_vid, "tag")

    # Kinetic style hint — preserves motion physics constraint even without style_prefix.
    # style_prefix is omitted from video lead lines to save tokens; KINETIC_STYLE_HINT
    # injects a condensed 2-3 word substitute so the model keeps the right physics.
    media_style = graph.project.media_style or "live_clear"
    kinetic_hint = KINETIC_STYLE_HINT.get(media_style, "")

    frame_video_block = (
        frame.get("video_optimized_prompt_block", "")
        if isinstance(frame, dict)
        else getattr(frame, "video_optimized_prompt_block", "")
    )
    frame_action_summary = (
        frame.get("action_summary", "")
        if isinstance(frame, dict)
        else getattr(frame, "action_summary", "")
    )
    compressed_lead = packet.video_optimized_prompt_block or frame_video_block or frame_action_summary
    prompt_mode = _classify_prompt_mode(packet, frame)
    special_handling = _format_section("SPECIAL HANDLING", _special_handling_lines(prompt_mode))

    if prompt_mode == "pure_black":
        lead_lines = [
            "Generate a cinematic motion clip with native audio.",
            "Hold on full-frame pure black with no visible imagery.",
        ]
    elif prompt_mode == "loading_wheel":
        lead_lines = [
            "Generate a cinematic motion clip with native audio.",
            "Hold on a simple white loading spinner centered on pure black.",
        ]
    elif prompt_mode == "title_card":
        lead_lines = [
            "Generate a cinematic motion clip with native audio.",
            "Render a bold authored title-card insert using only the wording described in this beat.",
            compressed_lead or packet.current_beat,
        ]
    else:
        lead_lines = ["Generate a cinematic motion clip with native audio."]
    if kinetic_hint and prompt_mode not in {"pure_black", "loading_wheel", "title_card"}:
        lead_lines.append(kinetic_hint)
    if prompt_mode not in {"pure_black", "loading_wheel", "title_card"}:
        lead_lines.extend([
            compressed_lead or packet.current_beat,
            f"Shot type: {packet.shot_intent.shot or shot_type}.",
            f"Camera motion: {camera_movement}.",
        ])
    if scene.get("mood_keywords"):
        lead_lines.append("Mood: " + ", ".join(scene["mood_keywords"]))
    _ct_vid_dof = _ct_field(_ct_vid, "dof_guidance")
    if _ct_vid_dof and prompt_mode not in {"pure_black", "loading_wheel", "title_card"}:
        lead_lines.append(_ct_vid_dof)

    dialogue_turn_lines: list[str] = []
    dialogue_line_all: list[str] = []
    primary_voice_tempo = ""
    dialogue_delivery = ""
    for turn in packet.audio.turns:
        speaker_name = _get_cast_name(ctx["cast"], turn.cast_id) or turn.speaker or turn.cast_id or "Character"
        speaker_appearance = _get_cast_appearance(ctx["cast"], {"cast_id": turn.cast_id})
        speaker_voice = _get_cast_voice_profile(ctx["cast"], turn.cast_id)
        delivery, tempo = _build_dialogue_delivery({
            "performance_direction": turn.performance_direction,
            "env_intensity": turn.env_intensity,
            "env_distance": turn.env_distance,
            "env_medium": turn.env_medium,
            "env_atmosphere": turn.env_atmosphere,
        }, speaker_voice)
        if not primary_voice_tempo:
            primary_voice_tempo = tempo
        if not dialogue_delivery:
            dialogue_delivery = delivery
        if turn.line:
            dialogue_line_all.append(turn.line)
            label = f"{speaker_name} ({speaker_appearance})" if speaker_appearance else speaker_name
            detail = f'{label}: "{turn.line}"'
            if delivery:
                detail += f" | delivery {delivery}"
            dialogue_turn_lines.append(detail)

    audio_lines = []
    if packet.audio.dialogue_present:
        audio_lines.append("Native spoken dialogue is required in this clip.")
        audio_lines.extend(dialogue_turn_lines)
    else:
        audio_lines.append("No spoken dialogue. Native audio should come only from ambience.")
    if packet.audio.ambient_layers:
        audio_lines.append("Ambient layers: " + ", ".join(packet.audio.ambient_layers))
    if packet.audio.background_music:
        audio_lines.append("Background music: " + packet.audio.background_music)

    duration_data = _resolve_video_duration(
        graph,
        frame_id,
        frame,
        packet,
        dialogue_coverage,
        primary_voice_tempo,
    )
    actual_duration = int(duration_data["duration"])
    recommended_duration = duration_data["recommended_duration"]
    duration_reason = str(duration_data["duration_reason"])
    dialogue_fit_status = str(duration_data["dialogue_fit_status"])
    dialogue_timing = duration_data["dialogue_timing"]
    duration_allocation_details = duration_data["duration_allocation_details"]

    dialogue_pacing = ""
    if dialogue_line_all:
        combined_dialogue = " ".join(_normalize_ws(line) for line in dialogue_line_all if _normalize_ws(line))
        words_per_sec = len(combined_dialogue.split()) / max(actual_duration - 0.5, 1)
        if words_per_sec > 3.5:
            dialogue_pacing = "brisk"
        elif words_per_sec > 2.5:
            dialogue_pacing = "natural"
        elif words_per_sec > 1.5:
            dialogue_pacing = "measured"
        else:
            dialogue_pacing = "slow"

    # Shared core: SHOT INTENT, CONTINUITY, VISUAL ANCHORS.
    # Video uses its own detailed AUDIO section rather than core["audio_context"]
    # to include full dialogue turn lines with speaker identity and delivery.
    core = _build_core_sections(packet, frame, graph, refs=refs, pose_snapshot=cast_bible_snapshot)

    sections = [
        "\n".join(_compact_text(line) for line in lead_lines if _compact_text(line)),
        special_handling,
        core["shot_intent"],
        _format_section("CONTINUITY", _continuity_lines(packet, mode=prompt_mode)),
        core["pose_lock"] if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "environment_transition", "title_card", "hand_object_action"} else "",
        core["visual_anchors"] if prompt_mode not in {"pure_black", "loading_wheel", "title_card"} else "",
        _format_section("SUBJECT COUNT", _subject_count_lines(packet, prompt_mode)),
        _format_section("CAST INVARIANTS", packet.cast_invariants) if prompt_mode not in {"pure_black", "loading_wheel", "title_card"} else "",
        _format_section("PROP INVARIANTS", packet.prop_invariants) if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "object_macro", "environment_transition", "title_card"} else "",
        _format_section("LOCATION INVARIANTS", packet.location_invariants) if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "object_macro", "environment_transition", "title_card", "hand_object_action"} else "",
        _format_section("DIALOGUE COVERAGE", _dialogue_coverage_lines(dialogue_coverage, for_video=True)) if prompt_mode != "title_card" else "",
        _format_section("BLOCKING", packet.blocking) if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "object_macro", "environment_transition", "title_card", "hand_object_action"} else "",
        _format_section("BACKGROUND", packet.background) if prompt_mode not in {"pure_black", "loading_wheel", "screen_presence", "object_macro", "hand_object_action"} else "",
        _format_section("MOTION CONTINUITY", _motion_continuity_lines(frame, packet)),
        _format_section("AUDIO", audio_lines),
        _format_section(
            "NEGATIVE CONSTRAINTS",
            _negative_constraints(packet, dialogue_present=packet.audio.dialogue_present, mode=prompt_mode),
        ),
    ]
    sections = [s for s in sections if s]
    full_prompt = _serialize_video_prompt_sections(sections)

    return {
        "frame_id": frame_id,
        "scene_id": frame.get("scene_id", "") if isinstance(frame, dict) else getattr(frame, "scene_id", ""),
        "sequence_index": frame.get("sequence_index", 0) if isinstance(frame, dict) else getattr(frame, "sequence_index", 0),
        "prompt": full_prompt,
        "duration": actual_duration,
        "recommended_duration": recommended_duration,
        "duration_reason": duration_reason,
        "dialogue_fit_status": dialogue_fit_status,
        "target_api": "grok-video",
        "input_image_path": (frame.get("composed_image_path") if isinstance(frame, dict) else getattr(frame, "composed_image_path", None)) or f"frames/composed/{frame_id}_gen.png",
        "dialogue_line": " ".join(dialogue_line_all) if dialogue_line_all else None,
        "voice_delivery": dialogue_delivery,
        "dialogue_pacing": dialogue_pacing,
        "voice_tempo": primary_voice_tempo,
        "estimated_speech_seconds": dialogue_timing["speech_seconds"] if dialogue_timing else None,
        "estimated_pause_seconds": dialogue_timing["pause_seconds"] if dialogue_timing else None,
        "dialogue_word_count": dialogue_timing["word_count"] if dialogue_timing else 0,
        "dialogue_turn_count": dialogue_timing["turn_count"] if dialogue_timing else 0,
        "dialogue_exceeds_model_max": bool(dialogue_timing["exceeds_model_max"]) if dialogue_timing else False,
        "action_summary": frame.get("action_summary", "") if isinstance(frame, dict) else getattr(frame, "action_summary", ""),
        "video_optimized_prompt_block": packet.video_optimized_prompt_block,
        "cast_bible_snapshot": cast_bible_snapshot,
        "cinematic_tag": tag,
        "shot_type": packet.shot_intent.shot or shot_type,
        "camera_motion": camera_movement,
        "shot_packet_path": f"frames/shot_packets/{frame_id}.json",
        "dialogue_present": packet.audio.dialogue_present,
        "dialogue_coverage_roles": [str(item["role"]) for item in dialogue_coverage],
        "duration_allocation_details": duration_allocation_details,
        "duration_model_min_seconds": MIN_VIDEO_DURATION_SECONDS,
        "duration_model_max_seconds": MAX_VIDEO_DURATION_SECONDS,
        "directing": frame.get("directing", {}) if isinstance(frame, dict) else getattr(frame, "directing", {}),
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
        "size": "portrait_9_16",  # canonical schema key — cast composites are always portrait_9_16
        "out_path": f"cast/composites/{cast_id}_ref.png",
    }


def assemble_location_prompt(graph: NarrativeGraph, location_id: str) -> dict:
    """Build a single primary location reference prompt."""
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
    parts.append(
        "Single coherent reference image only. No split panels, no labels, no collage, "
        "no character presence. Environmental focus with production-design clarity."
    )

    # Direction descriptions remain textual spatial anchors for downstream frame prompts.
    if loc.directions:
        direction_summaries = []
        for direction in ("north", "south", "east", "west"):
            view = getattr(loc.directions, direction, None)
            if view and view.description:
                direction_summaries.append(f"{direction}: {view.description}")
        if direction_summaries:
            parts.append(
                "Spatial orientation anchors for later camera-facing continuity: "
                + "; ".join(direction_summaries)
                + "."
            )

    ar = graph.project.aspect_ratio
    size_map = {"16:9": "landscape_16_9", "9:16": "portrait_9_16", "4:3": "landscape_4_3", "1:1": "square_hd"}

    # Map location_type → template_type for the grid handler.
    # Valid handler templates: "exterior", "interior".  Default to "exterior".
    raw_type = (loc.location_type or "exterior").strip().lower()
    template_type = raw_type if raw_type in ("interior", "exterior") else "exterior"

    return {
        "location_id": location_id,
        "prompt": " ".join(parts),
        "template_type": template_type,
        "size": size_map.get(ar, "landscape_16_9"),  # canonical schema key — do not use legacy 'image_size'
        "out_path": f"locations/primary/{location_id}.png",
    }
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
        "size": size_map.get(ar, "landscape_16_9"),  # canonical schema key — do not use legacy 'image_size'
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
    if frame_id not in graph.frames:
        return []
    if project_dir is None:
        return []

    base = Path(project_dir)
    collector = ReferenceImageCollector(graph, base)
    frame_refs = collector.get_frame_references(frame_id)
    refs: list[str] = []
    if ENABLE_STORYBOARD_GUIDANCE and frame_refs.storyboard_cell and frame_refs.storyboard_cell.exists():
        refs.append(frame_refs.storyboard_cell.relative_to(base).as_posix())
    refs.extend(
        path.relative_to(base).as_posix()
        for path in collector.get_flat_reference_list(frame_id)
        if path.exists()
    )
    return refs


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH ASSEMBLY — Write all prompts to disk
# ═══════════════════════════════════════════════════════════════════════════════


MAX_STORYBOARD_PANELS = 6  # Guidance-only storyboard grids cap at 6 panels

TAG_LABELS = {
    "F01": "Character Focus", "F02": "Two-Shot", "F03": "Group",
    "F04": "Close-Up Dialogue", "F05": "Over-Shoulder", "F06": "Wide Dialogue",
    "F07": "Establishing", "F08": "Detail", "F09": "Transition",
    "F10": "Motion", "F11": "Prop Interaction", "F12": "Time Passage",
    "F13": "Flashback", "F17": "Scene Bridge", "F18": "Dramatic Emphasis",
}

# Priority cinematic families for storyboard sampling — prefer narrative-driving shots
_KEY_FAMILIES = {"E", "R", "A", "T"}


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
        ct = getattr(frame, "cinematic_tag", None)
        family = (ct.family if ct else "") or ""
        priority = 2 if family in _KEY_FAMILIES else 1
        cast_states = get_frame_cast_state_models(graph, fid)
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


def _build_cell_prompt(graph: NarrativeGraph, frame_id: str) -> str:
    """Build a compact guidance-only prompt for one storyboard cell."""
    packet = build_shot_packet(graph, frame_id)
    frame = graph.frames.get(frame_id)

    segments = [
        packet.current_beat,
        f"Subjects: {packet.subject_count}.",
        "Shot: " + " | ".join(part for part in [
            packet.shot_intent.shot,
            packet.shot_intent.angle,
        ] if part),
        "Blocking: " + "; ".join(packet.blocking[:2]) if packet.blocking else "",
        "Background: " + "; ".join(packet.background[:2]) if packet.background else "",
        "Continuity: " + "; ".join(packet.continuity_deltas[:2]) if packet.continuity_deltas else "",
        (
            "Dialogue moment only through performance, never visible text."
            if packet.audio.dialogue_present else
            "No spoken dialogue."
        ),
    ]
    if frame and frame.action_summary and frame.action_summary != packet.current_beat:
        segments.insert(1, frame.action_summary)
    return " ".join(_compact_text(segment) for segment in segments if _compact_text(segment))


def assemble_grid_storyboard_prompt(graph: NarrativeGraph, grid_id: str,
                                     project_dir: str | Path | None = None) -> dict:
    """Build a storyboard guidance prompt for a storyboard grid.

    Returns dict with:
        grid_id: str
        grid: str — layout string such as "2x2" or "3x2"
        cell_prompts: list[str]
        refs: list[str]
        output_dir: str
    """
    grid = graph.storyboard_grids.get(grid_id)
    if not grid:
        raise KeyError(f"StoryboardGrid {grid_id} not found")

    # All valid frames in the grid, in order
    all_frame_ids = [fid for fid in grid.frame_ids if graph.frames.get(fid)]

    # Build compact guidance prompts from each frame's shot packet
    cell_prompts: list[str] = []
    for fid in all_frame_ids:
        cell_prompts.append(_build_cell_prompt(graph, fid))

    # Grid layout string
    grid_layout = f"{grid.cols}x{grid.rows}"

    # Gather reference images
    # Previous grid composite → cascading continuity
    refs: list[str] = []
    if grid.previous_grid_id:
        prev_grid = graph.storyboard_grids.get(grid.previous_grid_id)
        if prev_grid and prev_grid.composite_image_path:
            refs.append(prev_grid.composite_image_path)

    # Cast composites
    seen_refs: set[str] = set(refs)
    for cid in grid.cast_present:
        cast = graph.cast.get(cid)
        if cast and cast.composite_path and cast.composite_path not in seen_refs:
            seen_refs.add(cast.composite_path)
            refs.append(cast.composite_path)

    # Location images
    for fid in all_frame_ids:
        frame = graph.frames[fid]
        if frame.location_id:
            loc = graph.locations.get(frame.location_id)
            if loc and loc.primary_image_path and loc.primary_image_path not in seen_refs:
                seen_refs.add(loc.primary_image_path)
                refs.append(loc.primary_image_path)
        for prop_state in get_frame_prop_state_models(graph, fid)[:2]:
            prop = graph.props.get(prop_state.prop_id)
            if prop and prop.image_path and prop.image_path not in seen_refs:
                seen_refs.add(prop.image_path)
                refs.append(prop.image_path)

    output_dir = f"frames/storyboards/{grid_id}"

    return {
        "grid_id": grid_id,
        "grid": grid_layout,
        "style_prefix": _resolve_style_prefix(graph),
        "cell_prompts": cell_prompts,
        "scene": "\n".join(f"[Cell {i+1}] {cp}" for i, cp in enumerate(cell_prompts)),
        "refs": refs,
        "guidance_only": True,
        "output_dir": output_dir,
        "cell_map": grid.cell_map,
        "frame_ids": all_frame_ids,
    }


def assemble_all_prompts(graph: NarrativeGraph, project_dir: str | Path) -> dict:
    """Assemble all image + video prompts and write to disk.

    Writes:
        frames/prompts/{frame_id}_image.json
        frames/shot_packets/{frame_id}.json
        video/prompts/{frame_id}_video.json
        cast/prompts/{cast_id}_composite.json
        locations/prompts/{location_id}_location.json
        props/prompts/{prop_id}_prop.json

    Returns summary dict.
    """
    project_dir = Path(project_dir)
    frame_prompt_dir = project_dir / "frames" / "prompts"
    shot_packet_dir = project_dir / "frames" / "shot_packets"
    video_prompt_dir = project_dir / "video" / "prompts"
    cast_prompt_dir = project_dir / "cast" / "prompts"
    loc_prompt_dir = project_dir / "locations" / "prompts"
    prop_prompt_dir = project_dir / "props" / "prompts"
    storyboard_prompt_dir = project_dir / "frames" / "storyboard_prompts"

    for d in [frame_prompt_dir, shot_packet_dir, video_prompt_dir, cast_prompt_dir, loc_prompt_dir, prop_prompt_dir, storyboard_prompt_dir]:
        d.mkdir(parents=True, exist_ok=True)

    stale_prompt_patterns = {
        frame_prompt_dir: "*_image.json",
        shot_packet_dir: "*.json",
        video_prompt_dir: "*_video.json",
        cast_prompt_dir: "*_composite.json",
        loc_prompt_dir: "*_location.json",
        prop_prompt_dir: "*_prop.json",
        storyboard_prompt_dir: "*_grid.json",
    }
    for directory, pattern in stale_prompt_patterns.items():
        for existing in directory.glob(pattern):
            existing.unlink()

    counts = {"image_prompts": 0, "shot_packets": 0, "video_prompts": 0, "composite_prompts": 0,
              "location_prompts": 0, "prop_prompts": 0, "storyboard_prompts": 0}

    try:
        ReferenceImageCollector(graph, project_dir).sync_cast_bible(
            run_id=current_run_id(""),
            sequence_id=getattr(graph.project, "project_id", ""),
        )
    except Exception as exc:
        print(f"WARNING: Failed to sync cast bible before prompt assembly: {exc}")

    # Frame prompts
    for frame_id in graph.frame_order:
        try:
            packet = build_shot_packet(graph, frame_id)
            (shot_packet_dir / f"{frame_id}.json").write_text(
                json.dumps(packet.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["shot_packets"] += 1

            img = assemble_image_prompt(graph, frame_id, project_dir=project_dir)
            (frame_prompt_dir / f"{frame_id}_image.json").write_text(
                json.dumps(img, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            counts["image_prompts"] += 1

            vid = assemble_video_prompt(graph, frame_id, project_dir=project_dir)
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

    # Location prompts — single primary references
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

    # Grid storyboard prompts — optional guidance layer
    if ENABLE_STORYBOARD_GUIDANCE:
        for grid_id, grid in graph.storyboard_grids.items():
            try:
                sb = assemble_grid_storyboard_prompt(graph, grid_id, project_dir=project_dir)
                (storyboard_prompt_dir / f"{grid_id}_grid.json").write_text(
                    json.dumps(sb, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                # Update the grid's prompt path on the graph
                grid.storyboard_prompt_path = f"frames/storyboard_prompts/{grid_id}_grid.json"
                counts["storyboard_prompts"] += 1
            except Exception as e:
                print(f"WARNING: Failed to assemble storyboard for {grid_id}: {e}")

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
    # Voice description provides holistic speech-style context
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


def _estimate_dialogue_timing(lines: list[str], tempo: str = "", env_intensity: str = "") -> dict[str, float | int | bool]:
    """Estimate full-line dialogue timing for a single clip or span budget."""
    cleaned_lines = [_normalize_ws(line) for line in lines if _normalize_ws(line)]
    if not cleaned_lines:
        return {
            "recommended_duration": MIN_VIDEO_DURATION_SECONDS,
            "speech_seconds": 0.0,
            "pause_seconds": 0.0,
            "word_count": 0,
            "turn_count": 0,
            "exceeds_model_max": False,
        }

    combined = " ".join(cleaned_lines)
    word_count = len(re.findall(r"\b[\w']+\b", combined))
    units = max(_count_dialogue_units(combined), 1)
    sentence_breaks = len(re.findall(r"[.!?]+", combined))
    clause_breaks = len(re.findall(r"[,;:—-]", combined))
    speaker_turns = max(len(cleaned_lines) - 1, 0)

    units_per_second = _tempo_units_per_second(tempo=tempo, env_intensity=env_intensity)
    speech_seconds = units / units_per_second
    pause_seconds = (
        sentence_breaks * 0.55 +
        clause_breaks * 0.2 +
        speaker_turns * 0.75
    )
    lead_in_seconds = 0.9 if speaker_turns == 0 else 1.35
    recommended_duration = max(
        MIN_VIDEO_DURATION_SECONDS,
        math.ceil(speech_seconds + pause_seconds + lead_in_seconds),
    )

    return {
        "recommended_duration": recommended_duration,
        "speech_seconds": round(speech_seconds, 2),
        "pause_seconds": round(pause_seconds + lead_in_seconds, 2),
        "word_count": word_count,
        "turn_count": speaker_turns + 1,
        "exceeds_model_max": recommended_duration > MAX_VIDEO_DURATION_SECONDS,
    }


def _estimate_dialogue_duration(lines: list[str], tempo: str = "", env_intensity: str = "") -> int:
    """Estimate clip duration for native-audio video generation.

    Conservative wrapper retained for callers that only need the integer.
    """
    return int(_estimate_dialogue_timing(
        lines,
        tempo=tempo,
        env_intensity=env_intensity,
    )["recommended_duration"])



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

"""
Frame Enricher — Parallel per-frame enrichment via Grok reasoning
=================================================================

Step 2b of the CC-First pipeline. Receives the base NarrativeGraph
(already seeded by cc_parser.py) and dispatches parallel frame-enricher
API calls to fill in:

  - CastFrameState: screen_position, looking_at, emotion, posture, ...
  - FrameComposition: shot, angle, movement, focus
  - FrameEnvironment: lighting, atmosphere, materials
  - FrameBackground: background_action, depth_layers
  - FrameDirecting: dramatic_purpose, beat_turn, pov_owner, ...
  - FrameNode: action_summary, emotional_arc, visual_flow_element

Usage:
    python3 graph/frame_enricher.py --project-dir ./projects/test
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from llm.xai_client import XAIClient, build_prompt_cache_key

from .schema import (
    CastFrameState,
    EmotionalArc,
    FrameAtmosphere,
    FrameBackground,
    FrameComposition,
    FrameDirecting,
    FrameEnvironment,
    FrameLighting,
    NarrativeGraph,
)
from .store import GraphStore

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

FRAME_ENRICHER_MODEL = "grok-4-1-fast-reasoning"
FRAME_ENRICHER_TEMPERATURE = 0.3
FRAME_ENRICHER_MAX_TOKENS = 1500


def _coerce_text_scalar(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (list, tuple, set)):
        parts: list[str] = []
        for item in value:
            normalized = _coerce_text_scalar(item)
            if normalized:
                parts.append(normalized)
        if not parts:
            return None
        return ", ".join(dict.fromkeys(parts))
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _coerce_enum_token(value: object) -> Optional[str]:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            normalized = _coerce_enum_token(item)
            if normalized:
                return normalized
        return None
    return _coerce_text_scalar(value)


def _coerce_float_scalar(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            normalized = _coerce_float_scalar(item)
            if normalized is not None:
                return normalized
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None

# ─── System Prompt ───────────────────────────────────────────────────────────

FRAME_ENRICHER_SYSTEM_PROMPT = """You are a cinematic enrichment worker for a screenplay-to-visual pipeline.

You receive a single frame's context and must fill out a structured enrichment form. Your output
drives image generation, video direction, and continuity tracking. Be precise and visual — every
field must be directly useful to an image/video generation model.

## YOUR TASK

Fill the structured enrichment form for the given frame. Return ONLY valid JSON — no markdown,
no explanation, no commentary outside the JSON object.

## CAST STATE ENRICHMENT

For each character in `cast_in_frame`, fill their state in `cast_states`:

- `screen_position` (MANDATORY): Where they appear in the frame.
  Values: frame_left | frame_center | frame_right | frame_left_third | frame_right_third
  BASE ON: the `staging_anchor` provided for this frame. Use it as your default unless
  the action explicitly places the character elsewhere. Do not deviate without cause.

- `looking_at` (MANDATORY): What they are looking at.
  Values: another cast_id, a prop_id, "distance", "camera", or a location feature name.
  BASE ON: staging_anchor.looking_at. Override only for motivated character action.

- `facing_direction` (MANDATORY): Their body orientation relative to the camera.
  Values: toward_camera | away | profile_left | profile_right | three_quarter
  BASE ON: staging_anchor.facing_direction.

- `emotion`: Single compound term (e.g. restrained_anger, quiet_determination, bitter_amusement).
  Match to the emotional content of the source_text.

- `emotion_intensity`: Float 0.0–1.0. Low = subtle, High = breaking point.

- `posture`: standing | sitting | crouching | kneeling | lying | walking | running | leaning | hunched

- `action`: Verb-first physical action (e.g. crosses_to_window, grips_door_frame, adjusts_dials).

- `frame_role`: subject | object | background | partial | referenced
  The primary character(s) in the shot = subject. Others = object/background.

- `delta_fields`: list of field names that changed from previous_frame_state.
  Empty list if this is the first frame or nothing changed.

Optional fields (include only if relevant):
- `props_held`: list of prop_ids currently in hand
- `props_interacted`: list of prop_ids touched/used this frame
- `clothing_state`: base | damaged | wet | changed | removed (only if changed from identity)
- `hair_state`: disheveled | wet | tied_back (only if changed from identity)
- `injury`: description (only if new or changed)
- `eye_direction`: downward | at_other_character | distant

## COMPOSITION

Fill `composition` based on the prose mood and staging:
- `shot`: medium_shot | close_up | wide | extreme_close_up | medium_close_up | two_shot | over_shoulder
- `angle`: eye_level | low | high | dutch | birds_eye | worms_eye
- `movement`: static | push | pull | pan_left | pan_right | tracking | dolly | crane | drift | subtle_drift
- `focus`: deep | shallow | rack
- `placement`: Rule of thirds (e.g. "subject_left_third")
- `grouping`: Multi-character arrangement (e.g. "triangle_composition")
- `blocking`: Stage direction note
- `transition`: Transition from previous frame (e.g. "cut", "match_cut")
- `rule`: Composition rule (e.g. "rule_of_thirds", "leading_lines")

## ENVIRONMENT

Fill `environment` with lighting and atmosphere:
- `lighting.direction`: front | side_left | side_right | back | overhead | under | ambient
- `lighting.quality`: hard | soft | harsh | diffused | golden | flat | dappled | silhouette
- `lighting.color_temp`: warm string (e.g. "cool_blue", "warm_amber", "green_fluorescent")
- `lighting.motivated_source`: What is producing the light (e.g. "window_left", "candle", "monitor_glow")
- `lighting.shadow_behavior`: How shadows behave (e.g. "deep_pools", "striped_parallel", "soft_diffused")
- `atmosphere.particles`: dust_motes | smoke | rain | snow | pollen | fog (if present)
- `atmosphere.weather`: rain | snow | fog | clear | overcast (exterior only)
- `atmosphere.ambient_motion`: curtain_sway | candle_flicker | leaves_rustling | screen_flicker
- `atmosphere.temperature_feel`: humid | cold | stifling | dry | freezing
- `materials_present`: list of texture-rich materials visible (e.g. ["weathered_wood", "cracked_concrete"])
- `foreground_objects`: list of objects in the foreground plane
- `midground_detail`: Description of midground plane
- `background_depth`: What is visible in the far background

## BACKGROUND

Fill `background`:
- `background_action`: Activity happening in the background (e.g. "servants clearing dishes in distance")
- `background_sound`: Ambient sound (e.g. "distant market chatter")
- `background_music`: Diegetic music only (e.g. "faint erhu melody from radio")
- `depth_layers`: list of layer descriptions from foreground to background

## DIRECTING

Fill `directing` with narrative intent:
- `dramatic_purpose` (MANDATORY): reveal | reaction | intimidation | intimacy | concealment | introduction | transition
- `beat_turn` (MANDATORY): One sentence — what changes by the end of this frame
- `pov_owner` (MANDATORY): cast_id, or "audience"
- `camera_motivation` (MANDATORY): Why this framing or movement serves the story beat
- `viewer_knowledge_delta`: New information the viewer learns
- `power_dynamic`: Who holds advantage and how
- `tension_source`: What creates pressure in this moment
- `movement_motivation`: Why the scene feels active or kinetic
- `movement_path`: Start-to-end blocking path
- `reaction_target`: The line or action this frame responds to
- `background_life`: What supporting life exists behind the subject

## FRAME-LEVEL FIELDS

- `action_summary` (MANDATORY): Concise verb-first physical action for video prompt.
  E.g. "Watanabe hunches over oscilloscope, fingers adjusting dials"
  This feeds directly into video generation. Be concrete and visual.

- `video_optimized_prompt_block` (MANDATORY): One dense cinematic sentence under 500
  characters that preserves action, blocking, and environmental context together.
  It should read like a compressed final video prompt lead, not a field label dump.
  This survives downstream Grok prompt compression, so include the most important
  lighting, atmosphere, and staging context here.

- `emotional_arc` (MANDATORY): Emotional direction relative to previous frame.
  Values: rising | falling | static | peak | release

- `visual_flow_element` (MANDATORY): The dominant visual driver of this frame.
  Values: motion | dialogue | reaction | action | weight | establishment

## OUTPUT FORMAT

Return exactly this JSON structure (add only the fields you are populating):

{
  "frame_id": "<frame_id>",
  "action_summary": "<verb-first physical action>",
  "video_optimized_prompt_block": "<dense cinematic sentence under 500 chars>",
  "emotional_arc": "<rising|falling|static|peak|release>",
  "visual_flow_element": "<motion|dialogue|reaction|action|weight|establishment>",
  "composition": {
    "shot": "<shot type>",
    "angle": "<angle>",
    "movement": "<movement>",
    "focus": "<focus>"
  },
  "environment": {
    "lighting": {
      "direction": "<direction>",
      "quality": "<quality>",
      "color_temp": "<optional>",
      "motivated_source": "<optional>",
      "shadow_behavior": "<optional>"
    },
    "atmosphere": {
      "particles": "<optional>",
      "weather": "<optional>",
      "ambient_motion": "<optional>",
      "temperature_feel": "<optional>"
    },
    "materials_present": [],
    "foreground_objects": [],
    "midground_detail": "<optional>",
    "background_depth": "<optional>"
  },
  "background": {
    "camera_facing": "<inherited from input>",
    "background_action": "<optional>",
    "background_sound": "<optional>",
    "depth_layers": []
  },
  "directing": {
    "dramatic_purpose": "<mandatory>",
    "beat_turn": "<mandatory>",
    "pov_owner": "<mandatory>",
    "camera_motivation": "<mandatory>",
    "viewer_knowledge_delta": "<optional>",
    "power_dynamic": "<optional>",
    "tension_source": "<optional>",
    "movement_motivation": "<optional>",
    "reaction_target": "<optional>",
    "background_life": "<optional>"
  },
  "cast_states": [
    {
      "cast_id": "<cast_id>",
      "screen_position": "<mandatory>",
      "looking_at": "<mandatory>",
      "facing_direction": "<mandatory>",
      "emotion": "<emotion>",
      "emotion_intensity": 0.5,
      "posture": "<posture>",
      "action": "<verb-first action>",
      "frame_role": "<role>",
      "props_held": [],
      "props_interacted": [],
      "delta_fields": []
    }
  ]
}
"""


def _nullable_string_schema() -> dict:
    return {"type": ["string", "null"]}


def _nullable_number_schema() -> dict:
    return {"type": ["number", "null"]}


_STRING_LIST_SCHEMA = {"type": "array", "items": {"type": "string"}}

FRAME_ENRICHER_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "frame_id": {"type": "string"},
        "action_summary": _nullable_string_schema(),
        "video_optimized_prompt_block": _nullable_string_schema(),
        "emotional_arc": _nullable_string_schema(),
        "visual_flow_element": _nullable_string_schema(),
        "composition": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "shot": _nullable_string_schema(),
                "angle": _nullable_string_schema(),
                "movement": _nullable_string_schema(),
                "focus": _nullable_string_schema(),
                "placement": _nullable_string_schema(),
                "grouping": _nullable_string_schema(),
                "blocking": _nullable_string_schema(),
                "transition": _nullable_string_schema(),
                "rule": _nullable_string_schema(),
            },
        },
        "environment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "lighting": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "direction": _nullable_string_schema(),
                        "quality": _nullable_string_schema(),
                        "color_temp": _nullable_string_schema(),
                        "motivated_source": _nullable_string_schema(),
                        "shadow_behavior": _nullable_string_schema(),
                    },
                },
                "atmosphere": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "particles": _nullable_string_schema(),
                        "weather": _nullable_string_schema(),
                        "ambient_motion": _nullable_string_schema(),
                        "temperature_feel": _nullable_string_schema(),
                    },
                },
                "materials_present": _STRING_LIST_SCHEMA,
                "foreground_objects": _STRING_LIST_SCHEMA,
                "midground_detail": _nullable_string_schema(),
                "background_depth": _nullable_string_schema(),
            },
        },
        "background": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "camera_facing": _nullable_string_schema(),
                "background_action": _nullable_string_schema(),
                "background_sound": _nullable_string_schema(),
                "background_music": _nullable_string_schema(),
                "depth_layers": _STRING_LIST_SCHEMA,
            },
        },
        "directing": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "dramatic_purpose": _nullable_string_schema(),
                "beat_turn": _nullable_string_schema(),
                "pov_owner": _nullable_string_schema(),
                "camera_motivation": _nullable_string_schema(),
                "viewer_knowledge_delta": _nullable_string_schema(),
                "power_dynamic": _nullable_string_schema(),
                "tension_source": _nullable_string_schema(),
                "movement_motivation": _nullable_string_schema(),
                "movement_path": _nullable_string_schema(),
                "reaction_target": _nullable_string_schema(),
                "background_life": _nullable_string_schema(),
            },
        },
        "cast_states": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "cast_id": {"type": "string"},
                    "screen_position": _nullable_string_schema(),
                    "looking_at": _nullable_string_schema(),
                    "facing_direction": _nullable_string_schema(),
                    "emotion": _nullable_string_schema(),
                    "emotion_intensity": _nullable_number_schema(),
                    "posture": _nullable_string_schema(),
                    "action": _nullable_string_schema(),
                    "frame_role": _nullable_string_schema(),
                    "props_held": _STRING_LIST_SCHEMA,
                    "props_interacted": _STRING_LIST_SCHEMA,
                    "delta_fields": _STRING_LIST_SCHEMA,
                    "clothing_state": _nullable_string_schema(),
                    "hair_state": _nullable_string_schema(),
                    "injury": _nullable_string_schema(),
                    "eye_direction": _nullable_string_schema(),
                },
                "required": ["cast_id"],
            },
        },
    },
    "required": ["frame_id"],
}

FRAME_ENRICHER_CACHE_KEY = build_prompt_cache_key(
    "frame-enricher",
    FRAME_ENRICHER_SYSTEM_PROMPT,
)


# ─── Input Builder ────────────────────────────────────────────────────────────


def _staging_beat_key(frame_ids: list[str], frame_id: str) -> str:
    """Return 'start', 'mid', or 'end' based on frame position in scene."""
    if not frame_ids:
        return "start"
    try:
        idx = frame_ids.index(frame_id)
    except ValueError:
        return "start"
    ratio = idx / max(len(frame_ids) - 1, 1)
    if ratio < 1 / 3:
        return "start"
    if ratio >= 2 / 3:
        return "end"
    return "mid"


def _build_identity_summary(cast_node) -> str:
    """Compose a compact identity description from CastNode.identity."""
    identity = cast_node.identity
    parts = []
    if identity.age_descriptor:
        parts.append(identity.age_descriptor)
    if identity.gender:
        parts.append(identity.gender)
    if identity.build:
        parts.append(f"{identity.build} build")
    if identity.skin:
        parts.append(f"{identity.skin} skin")
    hair_parts = [p for p in [identity.hair_color, identity.hair_length, identity.hair_style] if p]
    if hair_parts:
        parts.append(" ".join(hair_parts) + " hair")
    if identity.physical_description:
        return identity.physical_description
    return ", ".join(parts) if parts else cast_node.name


def _resolve_staging_anchor(scene, frame_id: str) -> dict:
    """Resolve staging anchor from scene.staging_plan for the given frame."""
    beat_key = _staging_beat_key(scene.frame_ids, frame_id)
    staging_plan = scene.staging_plan or {}
    beat = staging_plan.get(beat_key)
    if beat is None:
        # Try adjacent beats
        for fallback in ("start", "mid", "end"):
            beat = staging_plan.get(fallback)
            if beat is not None:
                break
    if beat is None:
        return {}

    # StagingBeat has per-cast dicts; build a frame-level anchor
    # by merging all cast positions (first cast's values used as baseline)
    anchor: dict = {}
    if beat.cast_positions:
        # Take the first entry as representative anchor
        first_cast_id = next(iter(beat.cast_positions))
        anchor["screen_position"] = beat.cast_positions[first_cast_id]
    if beat.cast_looking_at:
        first_cast_id = next(iter(beat.cast_looking_at))
        anchor["looking_at"] = beat.cast_looking_at[first_cast_id]
    if beat.cast_facing:
        first_cast_id = next(iter(beat.cast_facing))
        anchor["facing_direction"] = beat.cast_facing[first_cast_id]
    return anchor


def _resolve_location_directions(graph: NarrativeGraph, location_id: Optional[str]) -> dict:
    """Extract cardinal direction descriptions from the location node."""
    if not location_id or location_id not in graph.locations:
        return {}
    loc = graph.locations[location_id]
    dirs = loc.directions
    result = {}
    for compass in ("north", "south", "east", "west", "exterior"):
        view = getattr(dirs, compass, None)
        if view and view.description:
            result[compass] = view.description
    return result


def _previous_frame_context(graph: NarrativeGraph, prev_frame_id: Optional[str]) -> Optional[dict]:
    """Build a compact previous-frame context dict."""
    if not prev_frame_id or prev_frame_id not in graph.frames:
        return None
    prev = graph.frames[prev_frame_id]
    # Summarise cast states for previous frame
    cast_summaries = []
    for state_key, state in graph.cast_frame_states.items():
        if state.frame_id == prev_frame_id:
            parts = [state.cast_id]
            if state.emotion:
                parts.append(state.emotion)
            if state.posture:
                posture_val = state.posture.value if hasattr(state.posture, "value") else str(state.posture)
                parts.append(posture_val)
            cast_summaries.append(", ".join(parts))
    return {
        "frame_id": prev_frame_id,
        "narrative_beat": prev.narrative_beat or "",
        "cast_states_summary": "; ".join(cast_summaries) if cast_summaries else "",
    }


def build_frame_enricher_inputs(graph: NarrativeGraph) -> list[dict]:
    """Build one frame-enricher input dict per frame, following Section 3.1 format.

    Returns a list ordered by frame_order.
    """
    inputs: list[dict] = []

    for frame_id in graph.frame_order:
        frame = graph.frames.get(frame_id)
        if frame is None:
            logger.warning("frame_order references unknown frame %s — skipping", frame_id)
            continue

        scene = graph.scenes.get(frame.scene_id) if frame.scene_id else None

        # ── Scene context ──────────────────────────────────────────────────
        scene_context: dict = {}
        if scene:
            location_name = ""
            location_type = ""
            if scene.location_id and scene.location_id in graph.locations:
                loc = graph.locations[scene.location_id]
                location_name = loc.name
                location_type = loc.location_type or ""
            time_val = None
            if frame.time_of_day:
                time_val = frame.time_of_day.value if hasattr(frame.time_of_day, "value") else str(frame.time_of_day)
            elif scene.time_of_day:
                time_val = scene.time_of_day.value if hasattr(scene.time_of_day, "value") else str(scene.time_of_day)
            scene_context = {
                "scene_id": scene.scene_id,
                "title": scene.title or "",
                "location": location_name,
                "location_type": location_type,
                "time_of_day": time_val or "",
                "mood_keywords": scene.mood_keywords or [],
                "pacing": scene.pacing or "",
            }

        # ── Cast in frame ──────────────────────────────────────────────────
        cast_in_frame: list[dict] = []
        # Collect cast_ids visible in this frame from CastFrameState
        cast_ids_in_frame: list[str] = []
        for state_key, state in graph.cast_frame_states.items():
            if state.frame_id == frame_id:
                cast_ids_in_frame.append(state.cast_id)

        for cast_id in cast_ids_in_frame:
            cast_node = graph.cast.get(cast_id)
            if cast_node is None:
                continue
            state_key = f"{cast_id}@{frame_id}"
            current_state = graph.cast_frame_states.get(state_key)

            # Previous frame state for this cast member
            prev_state_summary = None
            if frame.previous_frame_id:
                prev_state_key = f"{cast_id}@{frame.previous_frame_id}"
                prev_state = graph.cast_frame_states.get(prev_state_key)
                if prev_state:
                    prev_state_summary = {
                        "emotion": prev_state.emotion,
                        "posture": (
                            prev_state.posture.value
                            if prev_state.posture and hasattr(prev_state.posture, "value")
                            else str(prev_state.posture) if prev_state.posture else None
                        ),
                        "screen_position": prev_state.screen_position,
                        "looking_at": prev_state.looking_at,
                        "facing_direction": prev_state.facing_direction,
                        "action": prev_state.action,
                        "props_held": prev_state.props_held or [],
                        "clothing_state": prev_state.clothing_state,
                        "injury": prev_state.injury,
                    }

            active_tag = current_state.active_state_tag if current_state else "base"
            cast_in_frame.append(
                {
                    "cast_id": cast_id,
                    "name": cast_node.name,
                    "identity_summary": _build_identity_summary(cast_node),
                    "active_state_tag": active_tag,
                    "previous_frame_state": prev_state_summary,
                }
            )

        # ── Staging anchor ─────────────────────────────────────────────────
        staging_anchor = {}
        if scene:
            staging_anchor = _resolve_staging_anchor(scene, frame_id)

        # ── Location directions ────────────────────────────────────────────
        location_directions = _resolve_location_directions(
            graph, scene.location_id if scene else None
        )

        # ── Props in scene ─────────────────────────────────────────────────
        props_in_scene: list[dict] = []
        if scene:
            for prop_id in scene.props_present:
                prop = graph.props.get(prop_id)
                if prop:
                    props_in_scene.append(
                        {
                            "prop_id": prop_id,
                            "name": prop.name,
                            "description": prop.description or "",
                        }
                    )

        # ── Dialogue ──────────────────────────────────────────────────────
        dialogue_text = None
        if frame.is_dialogue and frame.dialogue_ids:
            lines = []
            for did in frame.dialogue_ids:
                dnode = graph.dialogue.get(did)
                if dnode and dnode.raw_line:
                    lines.append(f"{dnode.speaker}: {dnode.raw_line}")
            dialogue_text = "\n".join(lines) if lines else None

        # ── Build input dict ───────────────────────────────────────────────
        frame_input = {
            "frame_id": frame_id,
            "sequence_index": frame.sequence_index,
            "source_text": frame.source_text or frame.narrative_beat or "",
            "scene_context": scene_context,
            "cast_in_frame": cast_in_frame,
            "staging_anchor": staging_anchor,
            "location_directions": location_directions,
            "props_in_scene": props_in_scene,
            "previous_frame": _previous_frame_context(graph, frame.previous_frame_id),
            "is_dialogue": frame.is_dialogue,
            "dialogue_text": dialogue_text,
        }
        inputs.append(frame_input)

    return inputs


# ─── Frame Enricher API Calls ────────────────────────────────────────────────


@lru_cache(maxsize=4)
def _get_xai_client(api_key: str) -> XAIClient:
    return XAIClient(api_key=api_key)


async def enrich_single_frame(input_dict: dict, api_key: str) -> dict:
    """Send one frame to the frame enricher and parse the JSON response.

    On API failure: logs error and returns a minimal dict with the
    frame_id and an 'error' field so the caller can continue.
    """
    frame_id = input_dict.get("frame_id", "unknown")
    client = _get_xai_client(api_key or os.environ.get("XAI_API_KEY", ""))

    user_message = json.dumps(input_dict, indent=2, ensure_ascii=False)

    try:
        result = await client.generate_json(
            system_prompt=FRAME_ENRICHER_SYSTEM_PROMPT,
            prompt=user_message,
            schema=FRAME_ENRICHER_RESPONSE_SCHEMA,
            model=FRAME_ENRICHER_MODEL,
            temperature=FRAME_ENRICHER_TEMPERATURE,
            max_tokens=FRAME_ENRICHER_MAX_TOKENS,
            cache_key=FRAME_ENRICHER_CACHE_KEY,
            task_hint="frame_enrichment",
            schema_name="frame_enrichment",
        )
        result["frame_id"] = frame_id  # Ensure frame_id is always present
        return result

    except json.JSONDecodeError as e:
        logger.error("Frame %s: frame enricher returned invalid JSON — %s", frame_id, e)
        return {"frame_id": frame_id, "error": f"json_parse_error: {e}"}
    except Exception as e:
        logger.error("Frame %s: frame enricher API error — %s", frame_id, e)
        return {"frame_id": frame_id, "error": f"api_error: {e}"}


async def frame_enricher_batch_enrich(
    inputs: list[dict],
    api_key: str,
    max_concurrent: int = 20,
) -> list[dict]:
    """Run all frame enrichment calls with a concurrency semaphore.

    Failures are captured per-frame and do not abort the batch.
    Returns results in the same order as inputs.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded_enrich(inp: dict) -> dict:
        async with semaphore:
            return await enrich_single_frame(inp, api_key)

    tasks = [bounded_enrich(inp) for inp in inputs]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return list(results)


# ─── Apply Enrichment ─────────────────────────────────────────────────────────


def apply_frame_enrichment(graph: NarrativeGraph, result: dict) -> None:
    """Apply one frame enricher worker result to the graph in-place.

    Updates:
      - FrameNode: composition, environment, background, directing,
                   action_summary, emotional_arc, visual_flow_element
      - CastFrameState: all enriched fields
    """
    frame_id = result.get("frame_id")
    if not frame_id:
        logger.warning("Frame enricher result missing frame_id — skipping")
        return

    if "error" in result:
        logger.warning("Skipping frame %s due to enrichment error: %s", frame_id, result["error"])
        return

    frame = graph.frames.get(frame_id)
    if frame is None:
        logger.warning("Frame enricher result references unknown frame %s — skipping", frame_id)
        return

    # ── Frame-level fields ─────────────────────────────────────────────────
    action_summary = _coerce_text_scalar(result.get("action_summary"))
    if action_summary:
        frame.action_summary = action_summary
    prompt_block = _coerce_text_scalar(result.get("video_optimized_prompt_block"))
    if prompt_block:
        frame.video_optimized_prompt_block = prompt_block

    emotional_arc = _coerce_enum_token(result.get("emotional_arc"))
    if emotional_arc:
        try:
            frame.emotional_arc = EmotionalArc(emotional_arc)
        except ValueError:
            logger.warning("Frame %s: invalid emotional_arc value '%s'", frame_id, emotional_arc)

    visual_flow_element = _coerce_text_scalar(result.get("visual_flow_element"))
    if visual_flow_element:
        frame.visual_flow_element = visual_flow_element

    # ── Composition ────────────────────────────────────────────────────────
    comp_data = result.get("composition")
    if comp_data and isinstance(comp_data, dict):
        comp = frame.composition
        for field in ("shot", "angle", "movement", "focus", "placement",
                      "grouping", "blocking", "transition", "rule"):
            val = _coerce_text_scalar(comp_data.get(field))
            if val:
                setattr(comp, field, val)

    # ── Environment ────────────────────────────────────────────────────────
    env_data = result.get("environment")
    if env_data and isinstance(env_data, dict):
        env = frame.environment

        lighting_data = env_data.get("lighting")
        if lighting_data and isinstance(lighting_data, dict):
            from .schema import LightingDirection, LightingQuality
            lt = env.lighting
            direction = _coerce_enum_token(lighting_data.get("direction"))
            if direction:
                try:
                    lt.direction = LightingDirection(direction)
                except ValueError:
                    logger.debug("Frame %s: unknown lighting direction '%s'", frame_id, direction)
            quality = _coerce_enum_token(lighting_data.get("quality"))
            if quality:
                try:
                    lt.quality = LightingQuality(quality)
                except ValueError:
                    logger.debug("Frame %s: unknown lighting quality '%s'", frame_id, quality)
            for field in ("color_temp", "motivated_source", "shadow_behavior"):
                val = _coerce_text_scalar(lighting_data.get(field))
                if val:
                    setattr(lt, field, val)

        atmo_data = env_data.get("atmosphere")
        if atmo_data and isinstance(atmo_data, dict):
            atmo = env.atmosphere
            for field in ("particles", "weather", "ambient_motion", "temperature_feel"):
                val = _coerce_text_scalar(atmo_data.get(field))
                if val:
                    setattr(atmo, field, val)

        materials = env_data.get("materials_present")
        if materials and isinstance(materials, list):
            env.materials_present = materials

        fg_objects = env_data.get("foreground_objects")
        if fg_objects and isinstance(fg_objects, list):
            env.foreground_objects = fg_objects

        midground_detail = _coerce_text_scalar(env_data.get("midground_detail"))
        if midground_detail:
            env.midground_detail = midground_detail

        background_depth = _coerce_text_scalar(env_data.get("background_depth"))
        if background_depth:
            env.background_depth = background_depth

    # ── Background ─────────────────────────────────────────────────────────
    bg_data = result.get("background")
    if bg_data and isinstance(bg_data, dict):
        bg = frame.background
        for field in ("background_action", "background_sound", "background_music"):
            val = _coerce_text_scalar(bg_data.get(field))
            if val:
                setattr(bg, field, val)
        depth_layers = bg_data.get("depth_layers")
        if depth_layers and isinstance(depth_layers, list):
            bg.depth_layers = depth_layers

    # ── Directing ──────────────────────────────────────────────────────────
    dir_data = result.get("directing")
    if dir_data and isinstance(dir_data, dict):
        dr = frame.directing
        for field in (
            "dramatic_purpose", "beat_turn", "pov_owner", "camera_motivation",
            "viewer_knowledge_delta", "power_dynamic", "tension_source",
            "movement_motivation", "movement_path", "reaction_target", "background_life",
        ):
            val = _coerce_text_scalar(dir_data.get(field))
            if val:
                setattr(dr, field, val)

    # ── Cast states ────────────────────────────────────────────────────────
    cast_states_data = result.get("cast_states", [])
    for cs_data in cast_states_data:
        if not isinstance(cs_data, dict):
            continue
        cast_id = cs_data.get("cast_id")
        if not cast_id:
            continue
        state_key = f"{cast_id}@{frame_id}"
        state = graph.cast_frame_states.get(state_key)
        if state is None:
            logger.debug("Frame %s: no CastFrameState for %s — skipping cast enrichment", frame_id, cast_id)
            continue

        # Scalar fields
        for field in (
            "screen_position", "looking_at", "emotion",
            "facing_direction", "action", "eye_direction",
            "clothing_state", "hair_state", "injury",
        ):
            val = _coerce_text_scalar(cs_data.get(field))
            if val is not None:
                setattr(state, field, val)

        emotion_intensity = _coerce_float_scalar(cs_data.get("emotion_intensity"))
        if emotion_intensity is not None:
            state.emotion_intensity = emotion_intensity

        # Posture (enum)
        posture = _coerce_enum_token(cs_data.get("posture"))
        if posture:
            from .schema import Posture
            try:
                state.posture = Posture(posture)
            except ValueError:
                logger.debug("Frame %s / %s: unknown posture '%s'", frame_id, cast_id, posture)

        # CastFrameRole (enum)
        frame_role = _coerce_enum_token(cs_data.get("frame_role"))
        if frame_role:
            from .schema import CastFrameRole
            try:
                state.frame_role = CastFrameRole(frame_role)
            except ValueError:
                logger.debug("Frame %s / %s: unknown frame_role '%s'", frame_id, cast_id, frame_role)

        # List fields
        if isinstance(cs_data.get("props_held"), list):
            state.props_held = cs_data["props_held"]
        if isinstance(cs_data.get("props_interacted"), list):
            state.props_interacted = cs_data["props_interacted"]
        if isinstance(cs_data.get("delta_fields"), list):
            state.delta_fields = cs_data["delta_fields"]


# ─── Correction Re-Enrichment ────────────────────────────────────────────────

_CORRECTION_SYSTEM_PROMPT_SUFFIX = """

## CORRECTION MODE

The following correction(s) MUST be applied for this frame. Previous enrichment
produced values that violate continuity constraints. Fix ONLY the fields listed
below — do not change anything else.

{corrections}

Return the same JSON structure as normal, but with the corrected values applied.
"""


def _build_correction_block(frame_issues: list[dict]) -> str:
    """Format a list of per-frame issues into a correction instruction block."""
    lines = []
    for issue in frame_issues:
        what = issue.get("what") or issue.get("message", issue.get("check_name", "unknown"))
        lines.append(f"- CORRECTION REQUIRED: {what}")
    return "\n".join(lines)


async def re_enrich_single_frame(
    input_dict: dict,
    corrections: list[dict],
    api_key: str,
) -> dict:
    """Send one frame to the frame enricher with correction context injected into the system prompt."""
    frame_id = input_dict.get("frame_id", "unknown")
    client = _get_xai_client(api_key or os.environ.get("XAI_API_KEY", ""))

    correction_block = _build_correction_block(corrections)
    correction_system = FRAME_ENRICHER_SYSTEM_PROMPT + _CORRECTION_SYSTEM_PROMPT_SUFFIX.format(
        corrections=correction_block
    )
    correction_cache_key = build_prompt_cache_key(
        "frame-enricher-correction",
        correction_system,
    )

    user_message = json.dumps(input_dict, indent=2, ensure_ascii=False)

    try:
        result = await client.generate_json(
            system_prompt=correction_system,
            prompt=user_message,
            schema=FRAME_ENRICHER_RESPONSE_SCHEMA,
            model=FRAME_ENRICHER_MODEL,
            temperature=FRAME_ENRICHER_TEMPERATURE,
            max_tokens=FRAME_ENRICHER_MAX_TOKENS,
            cache_key=correction_cache_key,
            task_hint="frame_enricher_correction",
            schema_name="frame_enrichment_correction",
        )
        result["frame_id"] = frame_id
        return result
    except json.JSONDecodeError as e:
        logger.error("Re-enrich frame %s: frame enricher returned invalid JSON — %s", frame_id, e)
        return {"frame_id": frame_id, "error": f"json_parse_error: {e}"}
    except Exception as e:
        logger.error("Re-enrich frame %s: frame enricher API error — %s", frame_id, e)
        return {"frame_id": frame_id, "error": f"api_error: {e}"}


async def re_enrich_frames(
    graph: NarrativeGraph,
    frame_issues: list[dict],
    api_key: str = "",
    max_concurrent: int = 10,
) -> list[dict]:
    """Re-enrich specific frames with correction context.

    For each frame_issue, builds a frame-enricher input that INCLUDES the issue
    description so the enricher knows what to correct:

        'CORRECTION REQUIRED: Previous enrichment placed cast_rafe at
         frame_right but staging anchor requires frame_left. Fix
         screen_position to match staging plan.'

    Args:
        graph:        The NarrativeGraph (used to build per-frame inputs).
        frame_issues: List of issue dicts from validate_continuity() that have
                      needs_re_enrichment=True. Each dict must have at minimum:
                      {frame_id, check_name, what} (or 'message' as fallback).
        api_key:      XAI API key. Falls back to XAI_API_KEY env var.
        max_concurrent: Max parallel frame enricher calls.

    Returns:
        List of enrichment result dicts (same format as frame_enricher_batch_enrich).
    """
    if not api_key:
        api_key = os.environ.get("XAI_API_KEY", "")

    # Group issues by frame_id
    issues_by_frame: dict[str, list[dict]] = {}
    for issue in frame_issues:
        fid = issue.get("frame_id")
        if fid:
            issues_by_frame.setdefault(fid, []).append(issue)

    if not issues_by_frame:
        return []

    # Build full inputs for all frames, then filter to only frames needing correction
    all_inputs = build_frame_enricher_inputs(graph)
    inputs_by_frame = {inp["frame_id"]: inp for inp in all_inputs}

    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded_re_enrich(frame_id: str, corrections: list[dict]) -> dict:
        inp = inputs_by_frame.get(frame_id)
        if inp is None:
            logger.warning("re_enrich_frames: no input found for frame %s — skipping", frame_id)
            return {"frame_id": frame_id, "error": "frame_not_in_graph"}
        async with semaphore:
            return await re_enrich_single_frame(inp, corrections, api_key)

    tasks = [
        bounded_re_enrich(fid, corrections)
        for fid, corrections in issues_by_frame.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return list(results)


# ─── Phase Runner ─────────────────────────────────────────────────────────────


def run_phase_2b(graph: NarrativeGraph, project_dir: Path, api_key: str) -> NarrativeGraph:
    """Dispatch parallel frame enricher workers for per-frame enrichment and save."""
    inputs = build_frame_enricher_inputs(graph)
    logger.info("Dispatching %d frame enricher workers (max_concurrent=20)...", len(inputs))

    results = asyncio.run(frame_enricher_batch_enrich(inputs, api_key, max_concurrent=20))

    successes = 0
    failures = 0
    for result in results:
        if "error" in result:
            failures += 1
        else:
            apply_frame_enrichment(graph, result)
            successes += 1

    logger.info("Frame enrichment complete: %d succeeded, %d failed", successes, failures)

    store = GraphStore(project_dir)
    store.save(graph)
    return graph


# ─── CLI Entrypoint ───────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Run frame-enricher per-frame enrichment (Step 2b) on an existing NarrativeGraph."
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        help="Path to the project directory (must contain graph/narrative_graph.json)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="xAI API key. Defaults to XAI_API_KEY env var.",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=20,
        help="Max concurrent frame enricher API calls (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build inputs and print them without calling the API",
    )
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    api_key = args.api_key or os.environ.get("XAI_API_KEY", "")

    if not api_key and not args.dry_run:
        logger.error("No API key provided. Set XAI_API_KEY or pass --api-key")
        sys.exit(1)

    store = GraphStore(project_dir)
    if not store.exists():
        logger.error("No graph found at %s", store.graph_path)
        sys.exit(1)

    graph = store.load()
    logger.info("Loaded graph: %d frames, %d cast members", len(graph.frames), len(graph.cast))

    inputs = build_frame_enricher_inputs(graph)
    logger.info("Built %d frame inputs", len(inputs))

    if args.dry_run:
        print(json.dumps(inputs, indent=2, ensure_ascii=False))
        return

    results = asyncio.run(
        frame_enricher_batch_enrich(inputs, api_key, max_concurrent=args.max_concurrent)
    )

    successes = 0
    failures = 0
    for result in results:
        if "error" in result:
            logger.warning("Frame %s failed: %s", result.get("frame_id"), result["error"])
            failures += 1
        else:
            apply_frame_enrichment(graph, result)
            successes += 1

    logger.info("Enrichment complete: %d/%d frames succeeded", successes, len(inputs))

    saved_path = store.save(graph)
    logger.info("Graph saved → %s", saved_path)

    if failures:
        sys.exit(1)

if __name__ == "__main__":
    main()

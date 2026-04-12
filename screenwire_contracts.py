"""Shared ScreenWire onboarding/runtime contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CREATIVE_FREEDOM_CONTRACTS: dict[str, dict[str, str]] = {
    "strict": {
        "philosophy": "Change as little as possible to make it work.",
        "fidelity": "98-100%",
        "permission": (
            "Only minimal technical fixes are allowed. Preserve source dialogue, blocking, props, "
            "intent, and scene progression exactly."
        ),
        "failure_modes": (
            "Helpful additions, paraphrase, or invented connective tissue can drift the work away "
            "from the source. Prevent this by blocking any new text, new beats, or interpretive rewrite."
        ),
        "dialogue_policy": "Never add or alter dialogue. Word-for-word only. Zero improvisation.",
    },
    "balanced": {
        "philosophy": "Follow the source closely with room for natural flow.",
        "fidelity": "85-95%",
        "permission": (
            "Minor organic moments, natural pauses, slight framing tweaks, and delivery smoothing are "
            "allowed, but the source meaning and intent must stay intact."
        ),
        "failure_modes": (
            "Dialogue can drift into sounding more natural and quietly change meaning. Prevent this by "
            "allowing only light delivery smoothing and forbidding new lines or new plot material."
        ),
        "dialogue_policy": (
            "Minor re-phrasing for natural delivery only. No new lines. No added reactions. "
            "Changes must preserve exact meaning and intent."
        ),
    },
    "creative": {
        "philosophy": "Keep the core story while allowing artistic reframes.",
        "fidelity": "70-85%",
        "permission": (
            "Alternative angles, lighting, color, visual metaphor, and subtext emphasis are allowed. "
            "Short reaction lines may be added when they reinforce existing subtext."
        ),
        "failure_modes": (
            "Invented dialogue or new entities can quietly alter tone, voice, or plot direction. "
            "Prevent this by limiting additions to short reaction lines and keeping all changes aligned "
            "with existing subtext, character voice, and motivation."
        ),
        "dialogue_policy": (
            "Short reaction lines and moderate re-phrasing are allowed only when they preserve meaning, "
            "voice, and motivation. No new plot-advancing lines."
        ),
    },
    "unbounded": {
        "philosophy": "Start from a seed idea and fully expand into a complete story.",
        "fidelity": "40-70%",
        "permission": (
            "Freely invent new information, characters, subplots, pacing, and connective tissue while "
            "preserving the core emotional arc and final outcome."
        ),
        "failure_modes": (
            "The story can balloon into a different arc or ending. Prevent this by locking the core "
            "emotional arc and final outcome even while everything else can expand."
        ),
        "dialogue_policy": (
            "Freely add, alter, or invent dialogue as long as it serves the core emotional arc and ending."
        ),
    },
}

DEFAULT_CREATIVE_FREEDOM = "balanced"
DEFAULT_FRAME_BUDGET: int | None = None

FRAME_BUDGET_PRESETS: dict[str, int] = {
    "short": 20,
    "short_film": 125,
    "televised": 300,
    "feature": 1250,
}

DEFAULT_DIALOGUE_WORKFLOW: dict = {
    "enabled": True,
    "version": "grok-4.2-recovery-universal",
    "agents": [
        {
            "name": "extraction_recovery",
            "promptFile": "agent_prompts/dialogue_extraction_recovery.md",
            "runsOn": "skeleton_load",
            "alwaysRun": True,
        },
        {
            "name": "mapping_assignment",
            "promptFile": "agent_prompts/dialogue_mapping_assignment.md",
            "runsOn": "after_extraction",
            "usesCreativeFreedomTier": True,
        },
        {
            "name": "confirmation_validation",
            "promptFile": "agent_prompts/dialogue_confirmation_validation.md",
            "runsOn": "before_prompt_generation",
            "enforcesCreativeFreedomTier": True,
        },
    ],
    "recoveryPass": {
        "forceUniversal": True,
        "fallbackToContext": True,
    },
    "tierEnforcement": {
        "strict": {
            "addDialogue": False,
            "alterDialogue": "none",
            "reactionLines": False,
            "mustPreserveMeaningAndVoice": True,
        },
        "balanced": {
            "addDialogue": False,
            "alterDialogue": "light",
            "reactionLines": False,
            "mustPreserveMeaningAndVoice": True,
        },
        "creative": {
            "addDialogue": "short_reactions_only",
            "alterDialogue": "moderate",
            "reactionLines": True,
            "mustPreserveMeaningAndVoice": True,
        },
        "unbounded": {
            "addDialogue": "full_freedom",
            "alterDialogue": "full_freedom",
            "reactionLines": True,
            "mustPreserveMeaningAndVoice": False,
        },
    },
}


def creative_freedom_contract(tier: str) -> dict[str, str]:
    normalized = (tier or "").strip().lower()
    return deepcopy(
        CREATIVE_FREEDOM_CONTRACTS.get(normalized, CREATIVE_FREEDOM_CONTRACTS[DEFAULT_CREATIVE_FREEDOM])
    )


def default_dialogue_workflow() -> dict:
    return deepcopy(DEFAULT_DIALOGUE_WORKFLOW)


def normalize_frame_budget(value: Any) -> int | None:
    """Normalize onboarding/runtime frame budget values.

    Returns None for ``auto`` or empty values.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("frameBudget cannot be boolean")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("frameBudget must be a positive integer or 'auto'")
        return value

    raw = str(value).strip().lower()
    if not raw or raw == "auto":
        return None
    if raw in FRAME_BUDGET_PRESETS:
        return FRAME_BUDGET_PRESETS[raw]
    if raw.isdigit():
        budget = int(raw)
        if budget <= 0:
            raise ValueError("frameBudget must be a positive integer or 'auto'")
        return budget
    raise ValueError(f"Invalid frameBudget value: {value!r}")


def derive_output_size_from_frame_budget(value: Any) -> str:
    """Map a normalized frame budget to an internal size label."""
    budget = normalize_frame_budget(value)
    if budget is None:
        return "auto"
    if budget <= 20:
        return "short"
    if budget <= 125:
        return "short_film"
    if budget <= 300:
        return "televised"
    return "feature"


def derive_output_size_label_from_frame_budget(value: Any) -> str:
    budget = normalize_frame_budget(value)
    if budget is None:
        return "Auto"
    return f"{budget} Frames"


def derive_frame_range_from_budget(value: Any) -> list[int]:
    budget = normalize_frame_budget(value)
    if budget is None:
        return []
    return [budget, budget]


def minimum_scene_count_for_frame_budget(value: Any) -> int:
    """Heuristic-only guardrail for Phase 1 quality checks.

    This is intentionally loose. Scene count should never be a user-authored
    threshold anymore; it is only a sanity check against trivially incomplete
    outputs.
    """
    budget = normalize_frame_budget(value)
    if budget is None:
        return 1
    if budget <= 20:
        return 1
    if budget <= 60:
        return 2
    if budget <= 150:
        return 4
    if budget <= 300:
        return 6
    return 10

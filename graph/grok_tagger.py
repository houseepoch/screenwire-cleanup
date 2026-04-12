"""
Grok Cinematic Frame Tagger
============================

Assigns a cinematic tag from the Cinematic Frame Tag Taxonomy to every
FrameNode in the NarrativeGraph.

Runs post-Haiku-enrichment (Step 2b.5) so the complete frame data —
cast states, composition, environment, directing, action_summary — is
available for informed tag selection.

Flow:
  1. Load NarrativeGraph from disk (graph/narrative_graph.json)
  2. For each FrameNode, build a structured text context payload
  3. Send payload to Grok with the full taxonomy as system prompt
  4. Parse the single-tag response (e.g. 'D01.a +push')
  5. Look up tag definition in TAG_DEFINITIONS
  6. Populate FrameNode.cinematic_tag with tag + definition + prompt language
  7. Save updated graph to disk

Usage:
  python3 graph/grok_tagger.py --project-dir ./projects/test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from .api import get_frame_cast_state_models
from .schema import CinematicTag, NarrativeGraph
from .store import GraphStore

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE_URL = "https://api.x.ai/v1"
GROK_TAGGER_MODEL = "grok-4-1-fast-non-reasoning"
TAGGER_MAX_TOKENS = 50  # Response is just a tag string


def _log(tag: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] [GrokTagger:{tag}] {msg}")


# ---------------------------------------------------------------------------
# Taxonomy text — loaded from canonical path, used as Grok system prompt
# ---------------------------------------------------------------------------

_TAXONOMY_PATH = Path("/home/nikoles16/Downloads/cinematic-frame-tag-taxonomy.md")


def _load_taxonomy_text() -> str:
    if _TAXONOMY_PATH.exists():
        return _TAXONOMY_PATH.read_text(encoding="utf-8")
    # Fallback: compact inline version so module is self-contained
    return (
        "You are a cinematic crew sheet caller. "
        "Assign the single most relevant cinematic frame tag from the taxonomy "
        "(families D/E/R/A/C/T/S/M) to the frame context provided. "
        "Reply with ONLY the tag string, e.g. 'D01.a +push'. Nothing else."
    )


TAXONOMY_TEXT: str = _load_taxonomy_text()


# ---------------------------------------------------------------------------
# TAG_DEFINITIONS lookup table — every tag from every family
# ---------------------------------------------------------------------------

TAG_DEFINITIONS: dict[str, dict] = {
    # ── D — Dialogue ──────────────────────────────────────────────────────
    "D01.a": {
        "name": "Clean Single — Eye Level",
        "composition": "CU or MCU, eye-level, shallow DOF, subject centered or rule-of-thirds",
        "editorial_function": "Standard emotional coverage. Neutral power dynamic. The baseline.",
        "ai_prompt_language": "Close-up of single person speaking, isolated framing, no other people visible, shallow depth of field, eye-level",
        "lens": "50-85mm",
        "dof": "Shallow (f/1.8-2.8)",
        "family": "D",
    },
    "D01.b": {
        "name": "Clean Single — High Angle",
        "composition": "CU, camera looks slightly down at subject, shallow DOF",
        "editorial_function": "Subject feels vulnerable, diminished, pleading. Power taken from them.",
        "ai_prompt_language": "Close-up of single person speaking, isolated framing, no other people visible, shallow depth of field, camera looking down",
        "lens": "50-85mm",
        "dof": "Shallow (f/1.8-2.8)",
        "family": "D",
    },
    "D01.c": {
        "name": "Clean Single — Low Angle",
        "composition": "CU, camera looks slightly up at subject, shallow DOF",
        "editorial_function": "Subject feels dominant, threatening, confident. Power given to them.",
        "ai_prompt_language": "Close-up of single person speaking, isolated framing, no other people visible, shallow depth of field, camera looking up",
        "lens": "50-85mm",
        "dof": "Shallow (f/1.8-2.8)",
        "family": "D",
    },
    "D02.a": {
        "name": "Standard OTS",
        "composition": "Shoulder/nape in foreground, speaker sharp, medium depth",
        "editorial_function": "Workhorse dialogue coverage. Spatial relationship maintained. Conversational intimacy.",
        "ai_prompt_language": "Over-the-shoulder shot, blurred shoulder/head in foreground, focused on character speaking",
        "lens": "50-75mm",
        "dof": "Medium-shallow (f/2.8-4)",
        "family": "D",
    },
    "D02.b": {
        "name": "Tight OTS",
        "composition": "Foreground shoulder larger, speaker tighter, more compression",
        "editorial_function": "Increased tension. The listener's physical presence crowds the speaker.",
        "ai_prompt_language": "Over-the-shoulder shot, large blurred shoulder in foreground crowding speaker, tight compression",
        "lens": "50-75mm",
        "dof": "Medium-shallow (f/2.8-4)",
        "family": "D",
    },
    "D02.c": {
        "name": "Wide OTS",
        "composition": "Foreground shoulder minimal, speaker in wider context, deeper focus",
        "editorial_function": "Relaxed, environmental. The conversation has room to breathe.",
        "ai_prompt_language": "Over-the-shoulder shot, minimal foreground shoulder, speaker in wider context, deeper focus",
        "lens": "50-75mm",
        "dof": "Medium-shallow (f/2.8-4)",
        "family": "D",
    },
    "D03.a": {
        "name": "Dirty Single — Soft Edge",
        "composition": "Minimal intrusion, just a blur of color at frame edge",
        "editorial_function": "Maintains spatial awareness without breaking speaker isolation. Warmer than clean.",
        "ai_prompt_language": "Close-up on character speaking, slight out-of-focus shoulder or blurred edge of another person at frame border",
        "lens": "65-85mm",
        "dof": "Shallow (f/2-2.8)",
        "family": "D",
    },
    "D03.b": {
        "name": "Dirty Single — Profile Sliver",
        "composition": "Listener's profile or jawline partially visible, very soft",
        "editorial_function": "Stronger sense of physical proximity. Intimate, conspiratorial.",
        "ai_prompt_language": "Close-up on character speaking, listener's out-of-focus profile or jawline partially visible at frame edge",
        "lens": "65-85mm",
        "dof": "Shallow (f/2-2.8)",
        "family": "D",
    },
    "D04.a": {
        "name": "Profile 50/50",
        "composition": "Both in profile, facing each other, frame split down the middle",
        "editorial_function": "Confrontation, negotiation, balance of power. Neither side favored.",
        "ai_prompt_language": "Two people facing each other in equal framing, both visible, profile view, frame split evenly",
        "lens": "40-50mm",
        "dof": "Medium (f/4-5.6)",
        "family": "D",
    },
    "D04.b": {
        "name": "Three-Quarter 50/50",
        "composition": "Both in ¾ view, slightly angled to camera, shared frame",
        "editorial_function": "Warmer than pure profile. Collaboration, shared decision, dinner table.",
        "ai_prompt_language": "Two people facing each other in equal framing, both visible, three-quarter view, slightly angled to camera",
        "lens": "40-50mm",
        "dof": "Medium (f/4-5.6)",
        "family": "D",
    },
    "D04.c": {
        "name": "Stacked / Foreground-Background",
        "composition": "One speaker foreground (soft), one background (sharp), then swap",
        "editorial_function": "Shifting allegiance. Whoever is in focus holds the audience's attention.",
        "ai_prompt_language": "Two people in conversation, one in foreground out-of-focus, one in background sharp, stacked depth framing",
        "lens": "40-50mm",
        "dof": "Medium (f/4-5.6)",
        "family": "D",
    },
    "D05.a": {
        "name": "Held Reaction",
        "composition": "CU or MCU, static, extended duration, speaker's voice off-screen",
        "editorial_function": "The impact of words landing. Often the most powerful shot in the scene. Held longer than expected.",
        "ai_prompt_language": "Close-up of person listening, not speaking, emotional reaction on face, attentive expression, speaker implied off-screen",
        "lens": "50-85mm",
        "dof": "Shallow",
        "family": "D",
    },
    "D05.b": {
        "name": "Shifting Reaction",
        "composition": "MCU, subtle drift or reframe as the listener's expression changes",
        "editorial_function": "Tracking an evolving internal response — confusion becoming anger, hope becoming defeat.",
        "ai_prompt_language": "Medium close-up of person listening, expression visibly shifting, emotional transformation on face, speaker off-screen",
        "lens": "50-85mm",
        "dof": "Shallow",
        "family": "D",
    },
    "D05.c": {
        "name": "Peripheral Reaction",
        "composition": "MCU on a third party observing the conversation, not a direct participant",
        "editorial_function": "The witness. How does this conversation affect someone outside it?",
        "ai_prompt_language": "Close-up of person observing conversation as witness, not participating directly, peripheral reaction visible",
        "lens": "50-85mm",
        "dof": "Shallow",
        "family": "D",
    },
    "D06.a": {
        "name": "Static Master",
        "composition": "Medium-wide, locked, both speakers and environment visible, deep focus",
        "editorial_function": "The reset. Editors cut back here to re-establish where everyone is. Breathing room.",
        "ai_prompt_language": "Medium-wide shot of characters in conversation, full environment visible, deeper focus, static locked camera",
        "lens": "28-35mm",
        "dof": "Deep (f/4-8)",
        "family": "D",
    },
    "D06.b": {
        "name": "Tracking Master",
        "composition": "Medium-wide, gentle track alongside walking/moving talent",
        "editorial_function": "Walk-and-talk. The conversation moves through space. Location is an active participant.",
        "ai_prompt_language": "Medium-wide shot of characters in conversation walking, full environment visible, tracking camera following movement",
        "lens": "28-35mm",
        "dof": "Deep (f/4-8)",
        "family": "D",
    },
    "D06.c": {
        "name": "Environmental Master",
        "composition": "Wide, location-dominant, speakers relatively small in frame, deep focus",
        "editorial_function": "The setting overwhelms the conversation. Isolation, vastness, the world indifferent to their words.",
        "ai_prompt_language": "Wide shot of characters in conversation, environment dominant, speakers small in frame, deep focus",
        "lens": "28-35mm",
        "dof": "Deep (f/4-8)",
        "family": "D",
    },

    # ── E — Establishment & Environment ──────────────────────────────────
    "E01.a": {
        "name": "Grand Wide",
        "composition": "Extreme wide, deep focus, slow pan or static, no cast or cast as specks",
        "editorial_function": "Scale and context. Often opens a film or a new act. Holds long enough for the audience to build a mental map.",
        "ai_prompt_language": "Wide establishing shot of location, no people or people very small, deep focus, time of day, weather/atmosphere",
        "lens": "16-35mm",
        "dof": "Deep (f/5.6-11)",
        "family": "E",
    },
    "E01.b": {
        "name": "Approach",
        "composition": "Wide, tracking toward or into the location, cast may enter frame edge",
        "editorial_function": "The audience arrives with the character. Discovery in real time. Builds anticipation.",
        "ai_prompt_language": "Wide establishing shot, tracking approach toward location, cast entering frame edge, arrival in real time",
        "lens": "16-35mm",
        "dof": "Deep (f/5.6-11)",
        "family": "E",
    },
    "E01.c": {
        "name": "Detail-First Establish",
        "composition": "Starts on a tight environmental detail (a sign, a texture, an object), then widens or cuts to the wider context",
        "editorial_function": "Grounds the audience in specificity before scale. This isn't just any location — look at this detail.",
        "ai_prompt_language": "Close-up of environmental detail establishing shot, sign/texture/object, before widening to reveal location context",
        "lens": "16-35mm",
        "dof": "Deep (f/5.6-11)",
        "family": "E",
    },
    "E02.a": {
        "name": "Abbreviated Wide",
        "composition": "Similar angle to E01 but held shorter, possibly tighter framing",
        "editorial_function": "You know where we are. Quick orientation before cutting inside.",
        "ai_prompt_language": "Brief establishing shot of familiar location, abbreviated framing, quick orientation",
        "lens": "24-50mm",
        "dof": "Medium-deep",
        "family": "E",
    },
    "E02.b": {
        "name": "Signature Detail",
        "composition": "A recognizable detail of the location — a specific window, neon sign, doorway",
        "editorial_function": "Efficient shorthand. The audience's memory fills in the rest. Faster pacing.",
        "ai_prompt_language": "Recognizable location detail as establishing shorthand, specific window/sign/doorway, familiar visual anchor",
        "lens": "24-50mm",
        "dof": "Medium-deep",
        "family": "E",
    },
    "E02.c": {
        "name": "Time/Weather Shift",
        "composition": "Same angle as original E01 but different time of day, season, or weather",
        "editorial_function": "The location is familiar but time has passed. The change is the information.",
        "ai_prompt_language": "Establishing shot of familiar location with changed time or weather, same framing as before, condition shift visible",
        "lens": "24-50mm",
        "dof": "Medium-deep",
        "family": "E",
    },
    "E03.a": {
        "name": "Before/After Match",
        "composition": "Same angle, same composition as E01 or E02, but the location is now altered",
        "editorial_function": "The visual rhyme forces comparison. The matched framing IS the storytelling.",
        "ai_prompt_language": "Same location as before but now changed/damaged/transformed, matching previous camera angle exactly, transformation visible",
        "lens": "Match original",
        "dof": "Deep",
        "family": "E",
    },
    "E03.b": {
        "name": "Slow Reveal of Change",
        "composition": "Starts on an unchanged detail, then pans or widens to reveal the transformation",
        "editorial_function": "Delayed shock. The audience thinks they know where they are, then the change unfolds.",
        "ai_prompt_language": "Starts on unchanged location detail, pans or widens to reveal transformation, delayed discovery of change",
        "lens": "Match original",
        "dof": "Deep",
        "family": "E",
    },
    "E03.c": {
        "name": "Aftermath Pan",
        "composition": "Slow lateral movement across the transformed space, surveying damage or change",
        "editorial_function": "Cataloguing consequences. Each new detail the pan discovers adds weight.",
        "ai_prompt_language": "Slow lateral pan across transformed space surveying aftermath, each detail adding to total weight",
        "lens": "Match original",
        "dof": "Deep",
        "family": "E",
    },
    "E04.a": {
        "name": "Environmental Detail",
        "composition": "ECU or CU on surface, texture, weather — rust, rain on glass, steam, peeling wallpaper",
        "editorial_function": "Sensory grounding. The audience feels the world's material reality.",
        "ai_prompt_language": "Cinematic b-roll of environmental texture or surface detail, no people, tactile mood, close-up of material world",
        "lens": "Variable",
        "dof": "Variable",
        "family": "E",
    },
    "E04.b": {
        "name": "Ambient Life",
        "composition": "Medium or wide, background activity — traffic, crowds, birds, machinery — no featured cast",
        "editorial_function": "The world has a pulse independent of the story. Establishes normalcy or unease.",
        "ai_prompt_language": "Cinematic b-roll of background activity, no featured people, ambient world pulse, traffic/crowds/machinery",
        "lens": "Variable",
        "dof": "Variable",
        "family": "E",
    },
    "E04.c": {
        "name": "Atmospheric Mood",
        "composition": "Any size, but composed for emotional tone — fog, light shafts, empty corridors, wind in grass",
        "editorial_function": "Tonal coloring. These shots don't advance plot but establish how the world feels.",
        "ai_prompt_language": "Cinematic b-roll of atmospheric mood, no people, fog/light shafts/empty corridor/wind, emotional tone",
        "lens": "Variable",
        "dof": "Variable",
        "family": "E",
    },
    "E05.a": {
        "name": "High Overhead / Map View",
        "composition": "Top-down or near-top-down, deep focus, slow drift or static",
        "editorial_function": "Objective, omniscient perspective. The audience sees the whole board.",
        "ai_prompt_language": "Aerial shot of landscape/city, top-down or near-top-down, deep focus, slow drift or static, omniscient view",
        "lens": "Wide (14-35mm equivalent)",
        "dof": "Deep",
        "family": "E",
    },
    "E05.b": {
        "name": "Sweeping Approach",
        "composition": "Elevated, forward-moving, descending toward a location",
        "editorial_function": "Arrival with grandeur. The camera brings the audience down into the world from above.",
        "ai_prompt_language": "Aerial shot sweeping approach, elevated forward-moving descending toward location, grand arrival from above",
        "lens": "Wide (14-35mm equivalent)",
        "dof": "Deep",
        "family": "E",
    },
    "E05.c": {
        "name": "Elevated Tracking",
        "composition": "High angle, following a vehicle, person, or convoy from above",
        "editorial_function": "Pursuit, journey, showing the path through landscape. Scale of terrain vs. traveler.",
        "ai_prompt_language": "Aerial tracking shot following vehicle/person/convoy from above, elevated angle, scale of terrain visible",
        "lens": "Wide (14-35mm equivalent)",
        "dof": "Deep",
        "family": "E",
    },

    # ── R — Revealer ──────────────────────────────────────────────────────
    "R01.a": {
        "name": "Pan-to-Discover",
        "composition": "Camera pans across environment, discovers a figure previously outside the frame",
        "editorial_function": "Surprise presence. They were here the whole time. Recontextualizes the space.",
        "ai_prompt_language": "Camera pans across environment to discover figure previously outside frame, surprise presence, recontextualizing",
        "lens": "Variable",
        "dof": "Shifts during shot",
        "family": "R",
    },
    "R01.b": {
        "name": "Focus Pull Reveal",
        "composition": "Foreground or background starts soft, rack focus reveals a person standing there",
        "editorial_function": "The figure materializes through optics. Ghostly, unsettling, or dramatic.",
        "ai_prompt_language": "Rack focus reveal, soft background or foreground sharpens to reveal person standing there, ghostly or dramatic",
        "lens": "Variable (longer for focus pulls)",
        "dof": "Shifts during shot",
        "family": "R",
    },
    "R01.c": {
        "name": "Depth Reveal",
        "composition": "Wide frame, a figure is present but visually lost in the composition, then moves or is lit",
        "editorial_function": "Hidden in plain sight. Rewards attentive viewers. The environment was concealing them.",
        "ai_prompt_language": "Wide frame with figure visually lost in composition, hidden in plain sight, then movement or lighting reveals presence",
        "lens": "Variable",
        "dof": "Shifts during shot",
        "family": "R",
    },
    "R01.d": {
        "name": "Entrance Reveal",
        "composition": "Frame holds on empty or occupied space, character enters from off-screen or through a door",
        "editorial_function": "Arrival as event. The frame waits for them. Anticipation built through the empty space.",
        "ai_prompt_language": "Frame holds on empty space, character enters from off-screen or through doorway, arrival as dramatic event",
        "lens": "Variable",
        "dof": "Shifts during shot",
        "family": "R",
    },
    "R02.a": {
        "name": "The Planted Discover",
        "composition": "Camera movement or reframe discovers an object placed earlier — a gun, a letter, a clue",
        "editorial_function": "Payoff of a setup. The audience recognizes the object and its implications.",
        "ai_prompt_language": "Camera movement discovers previously planted object, gun/letter/clue, narrative payoff of earlier setup",
        "lens": "Often longer (50-100mm)",
        "dof": "Shallow on the object",
        "family": "R",
    },
    "R02.b": {
        "name": "The New Detail",
        "composition": "Push-in or cut to a detail not previously shown — a wound, an insignia, a label",
        "editorial_function": "New information introduced. Changes the audience's understanding of the scene.",
        "ai_prompt_language": "Push-in or cut to previously unseen detail, wound/insignia/label, new information changes understanding",
        "lens": "Often longer (50-100mm)",
        "dof": "Shallow on the object",
        "family": "R",
    },
    "R02.c": {
        "name": "The Absence Reveal",
        "composition": "Frame shows where something should be but isn't — an empty holster, a missing photo, a vacated chair",
        "editorial_function": "What's missing tells the story. The negative space is the reveal.",
        "ai_prompt_language": "Frame shows where something should be but is absent, empty holster/missing photo/vacated chair, absence as reveal",
        "lens": "Often longer (50-100mm)",
        "dof": "Shallow",
        "family": "R",
    },
    "R03.a": {
        "name": "Pull-Back Reveal",
        "composition": "Starts tight, pulls back to reveal unexpected scale — a person on a cliff edge, an army behind the hill",
        "editorial_function": "Oh. It's much bigger than we thought. Awe, dread, or comedy depending on context.",
        "ai_prompt_language": "Camera pulls back to reveal unexpected scale or scope, person on cliff/army behind hill, awe or dread",
        "lens": "Starts tight, ends wide",
        "dof": "Deepens as scale revealed",
        "family": "R",
    },
    "R03.b": {
        "name": "Crane-Up Reveal",
        "composition": "Camera lifts vertically to reveal what was hidden by the horizon line or foreground obstruction",
        "editorial_function": "Theatrical revelation. The rise itself carries dramatic weight.",
        "ai_prompt_language": "Camera lifts vertically to reveal what was hidden by horizon or obstruction, theatrical crane-up reveal",
        "lens": "Starts tight, ends wide",
        "dof": "Deepens as scale revealed",
        "family": "R",
    },
    "R03.c": {
        "name": "Corner / Threshold Reveal",
        "composition": "Camera moves through a doorway, around a corner, past an obstruction to reveal what's beyond",
        "editorial_function": "Spatial discovery. The architecture participates in the storytelling.",
        "ai_prompt_language": "Camera moves through doorway or around corner past obstruction to reveal what's beyond, spatial architectural reveal",
        "lens": "Starts tight, ends wide",
        "dof": "Deepens as scale revealed",
        "family": "R",
    },

    # ── A — Action ────────────────────────────────────────────────────────
    "A01.a": {
        "name": "Wide Coverage",
        "composition": "Wide, static or minimal movement, full space visible, deep focus",
        "editorial_function": "The master. Shows all combatants/movers and their spatial relationships. The safety shot.",
        "ai_prompt_language": "Action scene wide master shot, confined space, all combatants/movers visible, deep focus, spatial relationships clear",
        "lens": "24-35mm",
        "dof": "Deep",
        "family": "A",
    },
    "A01.b": {
        "name": "Medium Engagement",
        "composition": "Medium, tracking with primary subject, background actors visible",
        "editorial_function": "In the fight but still geographically oriented. The audience is ringside.",
        "ai_prompt_language": "Action scene medium tracking shot with primary subject, background fighters visible, geographically oriented",
        "lens": "24-35mm",
        "dof": "Deep",
        "family": "A",
    },
    "A01.c": {
        "name": "Tight Impact",
        "composition": "MCU or CU, fast, on the point of contact — a punch, a grab, a collision",
        "editorial_function": "Visceral. Felt, not just seen. Cut in briefly between wider coverage for punctuation.",
        "ai_prompt_language": "Action close-up on point of contact, punch/grab/collision MCU or CU, visceral impact, fast cut",
        "lens": "50mm",
        "dof": "Shallower for impact",
        "family": "A",
    },
    "A02.a": {
        "name": "Parallel Tracking",
        "composition": "Medium, camera runs alongside subject at matching speed, background scrolls",
        "editorial_function": "Classic chase coverage. The audience moves with the character. Exhausting and immersive.",
        "ai_prompt_language": "Tracking shot running alongside character at matching speed, background scrolling, classic chase coverage",
        "lens": "24-35mm",
        "dof": "Deep (f/4-8)",
        "family": "A",
    },
    "A02.b": {
        "name": "Leading",
        "composition": "Camera ahead of the subject, they run toward us, we see what's behind them",
        "editorial_function": "What's chasing them? Threat from behind. The audience sees more than the character.",
        "ai_prompt_language": "Camera ahead of running character, they run toward camera, we see what pursues them from behind",
        "lens": "24-35mm",
        "dof": "Deep",
        "family": "A",
    },
    "A02.c": {
        "name": "Following",
        "composition": "Camera behind the subject, we see what they're running toward",
        "editorial_function": "Into the unknown. Shared perspective with the character. Discovery as they discover.",
        "ai_prompt_language": "Camera behind running character, following POV, we see what lies ahead, into the unknown",
        "lens": "24-35mm",
        "dof": "Deep",
        "family": "A",
    },
    "A02.d": {
        "name": "Overhead Pursuit",
        "composition": "Elevated tracking, following the chase from above, environment mapped",
        "editorial_function": "Tactical clarity. The audience sees the whole path — shortcuts, dead ends, converging threats.",
        "ai_prompt_language": "Elevated aerial tracking following chase from above, tactical clarity, full path visible, converging threats",
        "lens": "24-35mm",
        "dof": "Deep",
        "family": "A",
    },
    "A03.a": {
        "name": "Walk-With",
        "composition": "Medium, tracking alongside, eye-level, steady pace",
        "editorial_function": "Character in transit. Contemplative or determined. The journey is the scene.",
        "ai_prompt_language": "Medium tracking shot alongside walking character, eye-level, steady pace, contemplative or determined transit",
        "lens": "28-50mm",
        "dof": "Medium",
        "family": "A",
    },
    "A03.b": {
        "name": "Entrance / Arrival",
        "composition": "Wider, character enters frame or crosses threshold into new space",
        "editorial_function": "Transition from one world to another. The doorway/boundary is compositionally significant.",
        "ai_prompt_language": "Character entering frame or crossing threshold into new space, arrival framing, boundary compositionally significant",
        "lens": "28-50mm",
        "dof": "Medium",
        "family": "A",
    },
    "A03.c": {
        "name": "Departure / Exit",
        "composition": "Character moves away from camera or exits frame, held on empty space after",
        "editorial_function": "Leaving. The held empty frame after they go creates weight. What's left behind matters.",
        "ai_prompt_language": "Character moving away or exiting frame, camera holds on empty space after departure, weight of leaving",
        "lens": "28-50mm",
        "dof": "Medium",
        "family": "A",
    },
    "A04.a": {
        "name": "Hands and Object",
        "composition": "MCU on hands interacting with object, face excluded, shallow DOF",
        "editorial_function": "The action is manual. What the hands do tells the story — opening, loading, writing, building.",
        "ai_prompt_language": "MCU on hands interacting with significant object, face excluded, shallow focus, manual action tells the story",
        "lens": "50-100mm",
        "dof": "Shallow",
        "family": "A",
    },
    "A04.b": {
        "name": "Face and Object",
        "composition": "MCU including character's face and the object, reaction visible",
        "editorial_function": "The emotional relationship to the object. Recognition, revulsion, tenderness, fear.",
        "ai_prompt_language": "MCU of character's face and significant object together, emotional reaction to object visible",
        "lens": "50-100mm",
        "dof": "Shallow",
        "family": "A",
    },
    "A04.c": {
        "name": "Full Interaction",
        "composition": "Medium, character's body and the object in context, environment visible",
        "editorial_function": "How the object fits into the larger scene. Picking up the weapon from the table — we see the room.",
        "ai_prompt_language": "Medium shot of character with significant object in full environmental context, how object fits the scene",
        "lens": "35-50mm",
        "dof": "Shallow on object",
        "family": "A",
    },

    # ── C — Cast / Portrait ───────────────────────────────────────────────
    "C01.a": {
        "name": "Still Portrait",
        "composition": "MCU, static, extended hold, shallow DOF, Rembrandt or sculpted lighting",
        "editorial_function": "Pure observation. The audience sits with the character in their emotion. Duration creates weight.",
        "ai_prompt_language": "Medium close-up portrait of character, deep emotion, shallow depth of field, sculpted dramatic lighting, static extended hold",
        "lens": "50-85mm",
        "dof": "Shallow (f/1.8-2.8)",
        "family": "C",
    },
    "C01.b": {
        "name": "Slow Push",
        "composition": "MCU to CU, very slow push-in, intensity gradually increases",
        "editorial_function": "The camera closes distance as the emotion deepens. Approaching intimacy.",
        "ai_prompt_language": "Medium close-up portrait of character with emotion, very slow push-in toward CU, approaching intimacy",
        "lens": "50-85mm",
        "dof": "Shallow",
        "family": "C",
    },
    "C01.c": {
        "name": "Pull Away",
        "composition": "CU to MCU or wider, slow pull-back, the character recedes",
        "editorial_function": "Emotional withdrawal. The audience is separated from the character. Isolation, abandonment.",
        "ai_prompt_language": "Close-up portrait of character, slow pull-back widening, emotional withdrawal, isolation and abandonment",
        "lens": "50-85mm",
        "dof": "Shallow",
        "family": "C",
    },
    "C02.a": {
        "name": "Environment-First Intro",
        "composition": "Wide, character visible but not dominant, camera or focus narrows to them",
        "editorial_function": "Discovered within context. The world defines them before we see their face.",
        "ai_prompt_language": "Character seen for first time, wide frame, emerging from environment, world defines them before face revealed",
        "lens": "Wide",
        "dof": "Deep",
        "family": "C",
    },
    "C02.b": {
        "name": "Detail-First Intro",
        "composition": "Starts on a characteristic detail — boots, hands, a silhouette — then reveals the whole",
        "editorial_function": "Mystery first, identity second. The detail becomes their visual signature.",
        "ai_prompt_language": "Character first introduction through characteristic detail — boots/hands/silhouette — then full reveal, mystery first",
        "lens": "Long",
        "dof": "Shallow",
        "family": "C",
    },
    "C02.c": {
        "name": "Direct Intro",
        "composition": "MCU or medium, character looks into or near lens, immediate presence",
        "editorial_function": "Confrontational, charismatic, or iconic. No hiding. They announce themselves.",
        "ai_prompt_language": "Character first introduction, MCU or medium, looks directly at or near camera lens, confrontational immediate presence",
        "lens": "Medium",
        "dof": "Medium",
        "family": "C",
    },
    "C03.a": {
        "name": "Full Silhouette",
        "composition": "Subject entirely backlit, face unreadable, body outlined",
        "editorial_function": "Mystery, anonymity, dramatic weight without identity.",
        "ai_prompt_language": "Full silhouette of figure against light source, backlit, face unreadable, body outlined, anonymous dramatic presence",
        "lens": "Any",
        "dof": "Variable",
        "family": "C",
    },
    "C03.b": {
        "name": "Partial Rim",
        "composition": "Subject mostly in shadow, rim light traces edges, fragments of face visible",
        "editorial_function": "Emerging from darkness. Halfway between hidden and known.",
        "ai_prompt_language": "Figure mostly in shadow with rim light tracing edges, fragments of face partially visible, emerging from darkness",
        "lens": "Any",
        "dof": "Variable",
        "family": "C",
    },
    "C04.a": {
        "name": "Cowboy Shot",
        "composition": "Mid-thigh up, eye-level, stance and bearing visible",
        "editorial_function": "Classic character framing. Physicality and attitude without environmental dominance.",
        "ai_prompt_language": "Full body shot of character from mid-thigh up, eye-level, stance and bearing visible, physicality and attitude",
        "lens": "35-50mm",
        "dof": "Medium (f/4)",
        "family": "C",
    },
    "C04.b": {
        "name": "Full Length",
        "composition": "Head to toe, environmental context visible",
        "editorial_function": "Costume reveal, physical comparison between characters, showing full body language.",
        "ai_prompt_language": "Full body shot of character head to toe, environmental context visible, costume and full body language",
        "lens": "35-50mm",
        "dof": "Medium",
        "family": "C",
    },
    "C05.a": {
        "name": "Eyes",
        "composition": "Just the eyes, or a single eye, filling the frame",
        "editorial_function": "The window. Maximum intimacy. The audience is closer than anyone should be.",
        "ai_prompt_language": "Extreme close-up of eyes or single eye filling the frame, maximum intimacy, very shallow focus",
        "lens": "85-100mm+",
        "dof": "Extremely shallow",
        "family": "C",
    },
    "C05.b": {
        "name": "Hands",
        "composition": "Hands in detail — trembling, gripping, relaxing, reaching",
        "editorial_function": "The body betraying what the face conceals. Nervous hands under a calm voice.",
        "ai_prompt_language": "Extreme close-up of hands, trembling/gripping/relaxing/reaching, body language betraying hidden emotion",
        "lens": "85-100mm+",
        "dof": "Extremely shallow",
        "family": "C",
    },
    "C05.c": {
        "name": "Sensory Detail",
        "composition": "Lips, an ear, the nape of a neck, skin texture",
        "editorial_function": "Intimacy, vulnerability, or menace depending on context. Hyper-physical.",
        "ai_prompt_language": "Extreme close-up of sensory body detail, lips/ear/neck/skin texture, hyper-physical intimacy or menace",
        "lens": "85-100mm+",
        "dof": "Extremely shallow",
        "family": "C",
    },

    # ── T — Transitional ──────────────────────────────────────────────────
    "T01.a": {
        "name": "Empty Passage",
        "composition": "Medium-wide, a corridor or path, no cast, still or slow drift",
        "editorial_function": "Meanwhile. The world between scenes. Creates temporal breathing room.",
        "ai_prompt_language": "Empty hallway/stairwell/road, liminal transitional space, no people, atmospheric lighting, still or drifting",
        "lens": "28-50mm",
        "dof": "Medium-deep",
        "family": "T",
    },
    "T01.b": {
        "name": "Threshold",
        "composition": "A doorway, window, or boundary, possibly with light spilling through",
        "editorial_function": "Between one state and another. The audience is about to cross into a new scene.",
        "ai_prompt_language": "Doorway/window/boundary with light spilling through, liminal threshold, between states, no people",
        "lens": "28-50mm",
        "dof": "Medium-deep",
        "family": "T",
    },
    "T02.a": {
        "name": "Time-Lapse Suggestion",
        "composition": "Static wide, implied long exposure or dissolve, light/shadow shifts",
        "editorial_function": "Hours or days compressed. Clouds race, shadows sweep, candles burn down.",
        "ai_prompt_language": "Time-lapse or long exposure suggestion, light/shadow shifting, hours or days compressed in single frame",
        "lens": "Variable",
        "dof": "Variable",
        "family": "T",
    },
    "T02.b": {
        "name": "Montage Unit",
        "composition": "Variable, rhythmic, one beat in a sequence of many",
        "editorial_function": "A single cell in a montage. Training, building, healing, traveling. Quick and purposeful.",
        "ai_prompt_language": "Single rhythmic montage beat, one quick purposeful unit in sequence, training/building/healing/traveling",
        "lens": "Variable",
        "dof": "Variable",
        "family": "T",
    },
    "T02.c": {
        "name": "Seasonal / Clock Detail",
        "composition": "CU on a clock face, calendar, seasonal marker, aging detail",
        "editorial_function": "Explicit time indicator. Leaves falling, frost forming, a clock reading 3 AM then 7 AM.",
        "ai_prompt_language": "Close-up of clock face/calendar/seasonal marker/aging detail, explicit time passage indicator",
        "lens": "Variable",
        "dof": "Variable",
        "family": "T",
    },
    "T03.a": {
        "name": "Vacated Space",
        "composition": "A chair someone was sitting in. A bed still warm. A door left open. Static, held.",
        "editorial_function": "Presence through absence. The character's ghost is in the empty space.",
        "ai_prompt_language": "Empty space after someone left, vacated chair/warm bed/open door, static held, presence through absence",
        "lens": "Variable",
        "dof": "Variable",
        "family": "T",
    },
    "T03.b": {
        "name": "Consequence Survey",
        "composition": "Slow pan across the results — wreckage, mess, aftermath of violence or celebration",
        "editorial_function": "Cataloguing what happened. Each detail the camera finds adds to the total weight.",
        "ai_prompt_language": "Slow pan across aftermath results, wreckage/mess/consequence, cataloguing what happened, each detail adds weight",
        "lens": "Variable",
        "dof": "Variable",
        "family": "T",
    },
    "T03.c": {
        "name": "The Still Object",
        "composition": "CU on an object left behind — a forgotten coffee cup, a discarded weapon, a letter",
        "editorial_function": "Synecdoche. The object stands for the whole event.",
        "ai_prompt_language": "Close-up of object left behind, forgotten cup/discarded weapon/letter, object as synecdoche for entire event",
        "lens": "Variable",
        "dof": "Variable",
        "family": "T",
    },

    # ── S — Stylistic / Psychological ────────────────────────────────────
    "S01.a": {
        "name": "Clean POV",
        "composition": "Matched eyeline, smooth, what the character literally sees",
        "editorial_function": "Identification. The audience IS the character. Often preceded by a CU of the character looking.",
        "ai_prompt_language": "Point-of-view shot through character's eyes, matched eyeline, smooth, clean subjective camera",
        "lens": "Matches character's perspective",
        "dof": "Variable",
        "family": "S",
    },
    "S01.b": {
        "name": "Impaired POV",
        "composition": "Blurred edges, doubled vision, tunnel vision, rack-focus instability",
        "editorial_function": "Drunk, drugged, injured, or panicked. The optics communicate the character's altered state.",
        "ai_prompt_language": "Point-of-view shot through character's eyes, impaired/blurred/doubled/tunnel vision, altered state perception",
        "lens": "Matches perspective",
        "dof": "Variable",
        "family": "S",
    },
    "S01.c": {
        "name": "Predatory / Surveillance POV",
        "composition": "Through binoculars, a scope, a window, a keyhole — framed by the viewing device",
        "editorial_function": "Someone is watching. The device border reminds the audience this is voyeuristic. Threat implied.",
        "ai_prompt_language": "Point-of-view through binoculars/scope/window/keyhole, device border frames image, voyeuristic surveillance threat",
        "lens": "Matches viewing device",
        "dof": "Variable",
        "family": "S",
    },
    "S02.a": {
        "name": "High Angle / God's Eye",
        "composition": "Looking down at subject, wide or tight",
        "editorial_function": "Vulnerability. The subject is diminished, trapped, observed from above.",
        "ai_prompt_language": "High angle looking down at character/scene, vulnerability and diminishment, subject trapped observed from above",
        "lens": "Variable",
        "dof": "Variable",
        "family": "S",
    },
    "S02.b": {
        "name": "Low Angle / Heroic",
        "composition": "Looking up at subject, often wider lens",
        "editorial_function": "Power, authority, menace, or aspiration. The subject looms.",
        "ai_prompt_language": "Low angle looking up at character, power and authority or menace, subject looms heroically or threateningly",
        "lens": "Variable",
        "dof": "Variable",
        "family": "S",
    },
    "S02.c": {
        "name": "Dutch Angle (Canted)",
        "composition": "Horizon tilted 15–45 degrees",
        "editorial_function": "Psychological unease, madness, instability. The world is literally off-balance.",
        "ai_prompt_language": "Dutch angle shot with tilted horizon 15-45 degrees, psychological unease, world literally off-balance",
        "lens": "Variable",
        "dof": "Variable",
        "family": "S",
    },
    "S03.a": {
        "name": "Soft Focus / Diffusion",
        "composition": "CU or medium, edges softened, highlight bloom, gauzy quality",
        "editorial_function": "Memory, dream, idealization. The past seen through emotional rather than optical truth.",
        "ai_prompt_language": "Dreamlike soft focus shot, edges softened, highlight bloom, gauzy diffusion, memory or dream quality",
        "lens": "Variable, often vintage glass or filters",
        "dof": "Often very shallow",
        "family": "S",
    },
    "S03.b": {
        "name": "Distorted / Uncanny",
        "composition": "Unusual lens, warped perspective, wrong proportions, unsettling framing",
        "editorial_function": "Hallucination, psychosis, fever dream. Reality is unreliable.",
        "ai_prompt_language": "Surreal distorted shot, unusual lens, warped perspective, wrong proportions, hallucination or psychosis",
        "lens": "Unusual — lensbaby, prisms, distortion",
        "dof": "Often shallow",
        "family": "S",
    },
    "S03.c": {
        "name": "Slow Motion / Temporal Stretch",
        "composition": "Normal composition but at reduced speed, sound may be absent or distorted",
        "editorial_function": "Heightened awareness, trauma, beauty, or dread. Time bends around significance.",
        "ai_prompt_language": "Slow motion temporal stretch shot, heightened awareness, trauma/beauty/dread, time bends around significance",
        "lens": "Variable",
        "dof": "Variable",
        "family": "S",
    },
    "S04.a": {
        "name": "The Hero Shot",
        "composition": "Low angle, slow push or crane, subject enters or stands in power framing",
        "editorial_function": "Arrival, triumph, defiance. The character earns a frame that makes them monumental.",
        "ai_prompt_language": "Low angle hero shot, slow push or crane, character in power framing, arrival/triumph/defiance, monumental",
        "lens": "Variable",
        "dof": "Variable",
        "family": "S",
    },
    "S04.b": {
        "name": "The Vertigo / Zolly",
        "composition": "Dolly in while zooming out (or reverse), background warps relative to subject",
        "editorial_function": "Sudden realization, horror, disorientation. The world shifts around a fixed point.",
        "ai_prompt_language": "Dolly zoom vertigo effect, background warping while subject stays fixed, sudden realization or horror",
        "lens": "Variable",
        "dof": "Variable",
        "family": "S",
    },
    "S04.c": {
        "name": "Overhead Press",
        "composition": "Directly overhead, looking straight down at subject, often lying down or fallen",
        "editorial_function": "Surrender, collapse, isolation. The character is pinned beneath the audience's gaze.",
        "ai_prompt_language": "Directly overhead shot looking straight down at character lying/fallen, surrender/collapse/isolation, pinned beneath gaze",
        "lens": "Variable",
        "dof": "Variable",
        "family": "S",
    },

    # ── M — Music Video ───────────────────────────────────────────────────
    "M01.a": {
        "name": "Hero Performance",
        "composition": "MCU or medium, artist centered, high production lighting, direct-to-camera or profile",
        "editorial_function": "The marquee shot. The artist at their most visually compelling. Cuts on beat.",
        "ai_prompt_language": "Music video hero performance shot, MCU or medium, artist centered, high production lighting, direct to camera",
        "lens": "Variable",
        "dof": "Variable",
        "family": "M",
    },
    "M01.b": {
        "name": "Instrumental / Band",
        "composition": "Medium or wide, featuring musicians, instruments visible, coordinated staging",
        "editorial_function": "The ensemble. Communicates musical energy and group dynamic.",
        "ai_prompt_language": "Music video band/instrumental shot, musicians and instruments visible, coordinated staging, ensemble energy",
        "lens": "Variable",
        "dof": "Variable",
        "family": "M",
    },
    "M01.c": {
        "name": "Kinetic Performance",
        "composition": "Wide or medium, full-body dance or movement, dynamic camera",
        "editorial_function": "Physical expression of the music. The body translates sound into visual rhythm.",
        "ai_prompt_language": "Music video kinetic performance, full-body dance or movement, dynamic camera, body translating sound to rhythm",
        "lens": "Variable",
        "dof": "Variable",
        "family": "M",
    },
    "M02.a": {
        "name": "Cut-on-Beat",
        "composition": "Variable framing, edit points land precisely on rhythmic hits",
        "editorial_function": "The visual pulse. Every cut is a beat. Creates hypnotic synchronization.",
        "ai_prompt_language": "Music video cut-on-beat visual, edit point aligned to rhythmic hit, visual pulse synchronized to music",
        "lens": "Variable",
        "dof": "Variable",
        "family": "M",
    },
    "M02.b": {
        "name": "Movement-on-Beat",
        "composition": "Camera pushes, whips, or shifts on musical accents",
        "editorial_function": "The camera dances. Movement punctuates rhythm rather than story.",
        "ai_prompt_language": "Music video camera movement on musical beat, push/whip/shift on accent, camera dances to rhythm",
        "lens": "Variable",
        "dof": "Variable",
        "family": "M",
    },
    "M03.a": {
        "name": "Literal Illustration",
        "composition": "Framing directly depicts what lyrics describe",
        "editorial_function": "The audience sees the words. Can be playful, ironic, or emotionally direct.",
        "ai_prompt_language": "Music video literal lyric illustration, framing directly depicts what lyrics describe, can be playful or ironic",
        "lens": "Variable",
        "dof": "Variable",
        "family": "M",
    },
    "M03.b": {
        "name": "Thematic / Abstract",
        "composition": "Visuals evoke the mood or theme of lyrics without literal depiction",
        "editorial_function": "Emotional resonance over narrative clarity. Texture and feeling.",
        "ai_prompt_language": "Music video thematic abstract visualization, mood/theme of lyrics without literal depiction, emotional resonance",
        "lens": "Variable",
        "dof": "Variable",
        "family": "M",
    },
}


# ---------------------------------------------------------------------------
# Movement modifiers
# ---------------------------------------------------------------------------

MOVEMENT_MODIFIERS: dict[str, str] = {
    "+static": "Locked Off — Tripod, no movement. Maximum stability and formality.",
    "+push": "Push In — Slow forward dolly toward subject. Builds intensity or intimacy.",
    "+pull": "Pull Back — Slow reverse dolly away from subject. Creates revelation or isolation.",
    "+track": "Tracking / Dolly — Camera moves laterally, parallel to subject movement.",
    "+pan": "Pan — Camera rotates horizontally on fixed axis. Surveys or follows.",
    "+tilt": "Tilt — Camera rotates vertically on fixed axis. Reveals height or scale.",
    "+crane": "Crane / Jib — Vertical lift or descent. Adds grandeur or shifts perspective.",
    "+handheld": "Handheld — Organic instability. Urgency, realism, rawness.",
    "+steadicam": "Steadicam / Gimbal — Smooth floating movement through space. Dreamlike or following.",
    "+whip": "Whip Pan — Rapid horizontal blur. Energetic transition or comedic snap.",
    "+dolly-zoom": "Dolly Zoom (Zolly) — Push in while zooming out (or reverse). Vertigo, sudden realization.",
    "+drone": "Aerial / Drone — Elevated sweeping or top-down. Scale, geography, pursuit.",
    "+drift": "Subtle Drift — Near-imperceptible lateral or rotational float. Unease or life.",
}


# ---------------------------------------------------------------------------
# Frame context builder
# ---------------------------------------------------------------------------

def _build_frame_context(graph: NarrativeGraph, frame_id: str) -> str:
    """Build the structured text context payload sent to Grok for one frame."""
    frame = graph.frames[frame_id]

    # Scene context
    scene = graph.scenes.get(frame.scene_id)
    scene_mood = ", ".join(scene.mood_keywords) if scene else ""
    scene_pacing = scene.pacing or "" if scene else ""

    # Cast info
    cast_names: list[str] = []
    cast_emotions: list[str] = []
    for cs in get_frame_cast_state_models(graph, frame.frame_id):
        cn = graph.cast.get(cs.cast_id)
        name = cn.name if cn else cs.cast_id
        cast_names.append(name)
        if cs.emotion:
            cast_emotions.append(f"{name}: {cs.emotion}" +
                                 (f" (intensity {cs.emotion_intensity:.1f})" if cs.emotion_intensity else ""))

    cast_count = len(cast_names)

    # Dialogue lines
    dialogue_lines: list[str] = []
    for dlg_id in frame.dialogue_ids:
        dlg = graph.dialogue.get(dlg_id)
        if dlg and dlg.raw_line:
            speaker_node = graph.cast.get(dlg.cast_id)
            speaker_name = speaker_node.name if speaker_node else dlg.speaker
            dialogue_lines.append(f"  {speaker_name}: {dlg.raw_line.strip()}")

    # Composition from Haiku enrichment
    comp = frame.composition
    composition_hint = ""
    if comp.shot or comp.angle or comp.movement:
        parts = [p for p in [comp.shot, comp.angle, comp.movement, comp.focus] if p]
        composition_hint = ", ".join(parts)

    # Previous / next frame beats
    prev_beat = ""
    if frame.previous_frame_id:
        prev_frame = graph.frames.get(frame.previous_frame_id)
        if prev_frame:
            prev_beat = (prev_frame.action_summary or prev_frame.narrative_beat or "")[:150]

    next_beat = ""
    if frame.next_frame_id:
        next_frame = graph.frames.get(frame.next_frame_id)
        if next_frame:
            next_beat = (next_frame.action_summary or next_frame.narrative_beat or "")[:150]

    # Directing intent
    directing_purpose = ""
    if frame.directing.dramatic_purpose:
        directing_purpose = frame.directing.dramatic_purpose

    # Build context block
    lines = [
        f"FRAME: {frame_id}",
        f"CAST COUNT: {cast_count}",
    ]
    if cast_names:
        lines.append(f"CAST: {', '.join(cast_names)}")
    lines.append(f"IS DIALOGUE: {'yes' if frame.is_dialogue else 'no'}")
    if dialogue_lines:
        lines.append("DIALOGUE:")
        lines.extend(dialogue_lines)
    if frame.action_summary:
        lines.append(f"ACTION: {frame.action_summary}")
    if scene_mood:
        lines.append(f"SCENE MOOD: {scene_mood}")
    if scene_pacing:
        lines.append(f"PACING: {scene_pacing}")
    if cast_emotions:
        lines.append("EMOTIONS: " + "; ".join(cast_emotions))
    if composition_hint:
        lines.append(f"HAIKU COMPOSITION HINT: {composition_hint}")
    if directing_purpose:
        lines.append(f"DRAMATIC PURPOSE: {directing_purpose}")
    if frame.visual_flow_element:
        lines.append(f"VISUAL FLOW: {frame.visual_flow_element}")
    if prev_beat:
        lines.append(f"PREV FRAME: {prev_beat}")
    if next_beat:
        lines.append(f"NEXT FRAME: {next_beat}")
    if frame.source_text:
        lines.append(f"SOURCE PROSE: {frame.source_text[:300]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tag response parser
# ---------------------------------------------------------------------------

def _parse_tag_response(response: str) -> tuple[str, str, str]:
    """Parse Grok tag response into (tag, modifier, full_tag).

    Expected format: 'D01.a +push' or 'D01.a' (no modifier).
    Returns ('D01.a', '+push', 'D01.a +push') or ('D01.a', '', 'D01.a').
    """
    parts = response.strip().split()
    tag = parts[0] if parts else ""
    modifier = parts[1] if len(parts) > 1 else ""
    full_tag = f"{tag} {modifier}".strip() if modifier else tag
    return tag, modifier, full_tag


def _lookup_tag_definition(tag: str, modifier: str, full_tag: str) -> CinematicTag:
    """Look up tag in TAG_DEFINITIONS and return a populated CinematicTag."""
    definition = TAG_DEFINITIONS.get(tag, {})

    # Build textual definition combining name and composition
    definition_text = ""
    if definition:
        name = definition.get("name", "")
        composition = definition.get("composition", "")
        definition_text = f"{name}. {composition}".strip(". ")

    # Append modifier description if present
    if modifier and modifier in MOVEMENT_MODIFIERS:
        definition_text = f"{definition_text}. Movement: {MOVEMENT_MODIFIERS[modifier]}"

    return CinematicTag(
        tag=tag,
        modifier=modifier,
        full_tag=full_tag,
        definition=definition_text,
        family=definition.get("family", tag[0] if tag else ""),
        editorial_function=definition.get("editorial_function", ""),
        ai_prompt_language=definition.get("ai_prompt_language", ""),
        lens_guidance=definition.get("lens", ""),
        dof_guidance=definition.get("dof", ""),
    )


# ---------------------------------------------------------------------------
# Single-frame Grok API call
# ---------------------------------------------------------------------------

async def tag_single_frame(
    frame_id: str,
    frame_context: str,
    *,
    api_key: str = "",
    client: Optional[httpx.AsyncClient] = None,
) -> CinematicTag:
    """Send one frame's context to Grok and return a populated CinematicTag."""
    key = api_key or XAI_API_KEY
    if not key:
        raise RuntimeError(
            "XAI_API_KEY not set — required for Grok cinematic frame tagging"
        )

    messages = [
        {"role": "system", "content": TAXONOMY_TEXT},
        {
            "role": "user",
            "content": (
                "Assign the single best cinematic tag for this frame.\n"
                "Reply with ONLY the tag string (e.g. 'D01.a +push'). "
                "Nothing else — no explanation, no punctuation, no markdown.\n\n"
                f"{frame_context}"
            ),
        },
    ]

    payload = {
        "model": GROK_TAGGER_MODEL,
        "messages": messages,
        "max_tokens": TAGGER_MAX_TOKENS,
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30.0)

    try:
        resp = await client.post(
            f"{XAI_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own_client:
            await client.aclose()

    raw = data["choices"][0]["message"]["content"].strip()
    tag, modifier, full_tag = _parse_tag_response(raw)

    if tag not in TAG_DEFINITIONS:
        _log(frame_id, f"Unknown tag '{tag}' in response '{raw}' — storing raw")

    return _lookup_tag_definition(tag, modifier, full_tag)


# ---------------------------------------------------------------------------
# Batch tagging — all frames
# ---------------------------------------------------------------------------

async def tag_all_frames(
    project_dir: Path | str,
    *,
    api_key: str = "",
    concurrency: int = 10,
) -> dict:
    """Tag all frames in the NarrativeGraph using Grok.

    Loads the graph from project_dir/graph/narrative_graph.json,
    assigns a CinematicTag to every FrameNode, saves the updated graph.

    Returns summary dict:
      {
        "tagged": N,
        "failed": N,
        "skipped": N,
        "tag_distribution": {"D": N, "E": N, ...},
      }
    """
    project_dir = Path(project_dir)
    store = GraphStore(project_dir)

    if not store.exists():
        _log("Batch", f"No graph at {store.graph_path}")
        return {"tagged": 0, "failed": 0, "skipped": 0, "error": "no_graph"}

    graph = store.load()
    frame_ids = sorted(graph.frames.keys())

    if not frame_ids:
        _log("Batch", "Graph has no frames")
        return {"tagged": 0, "failed": 0, "skipped": 0}

    _log("Batch", f"Tagging {len(frame_ids)} frames (concurrency={concurrency})")

    sem = asyncio.Semaphore(concurrency)
    results: dict = {"tagged": 0, "failed": 0, "skipped": 0, "tag_distribution": {}}

    key = api_key or XAI_API_KEY

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def _tag_one(frame_id: str) -> None:
            frame = graph.frames[frame_id]

            # Skip already-tagged frames (tag is non-empty)
            if frame.cinematic_tag.tag:
                results["skipped"] += 1
                return

            async with sem:
                t0 = time.monotonic()
                try:
                    context = _build_frame_context(graph, frame_id)
                    cinematic_tag = await tag_single_frame(
                        frame_id, context, api_key=key, client=client
                    )
                    frame.cinematic_tag = cinematic_tag
                    results["tagged"] += 1

                    family = cinematic_tag.family or "?"
                    results["tag_distribution"][family] = (
                        results["tag_distribution"].get(family, 0) + 1
                    )

                    elapsed = round(time.monotonic() - t0, 2)
                    _log(frame_id, f"→ {cinematic_tag.full_tag} ({elapsed}s)")

                except Exception as exc:
                    results["failed"] += 1
                    _log(frame_id, f"Failed: {exc}")

        await asyncio.gather(*[_tag_one(fid) for fid in frame_ids])

    store.save(graph)

    _log(
        "Batch",
        f"Done: {results['tagged']} tagged, "
        f"{results['skipped']} skipped, "
        f"{results['failed']} failed",
    )
    dist = results["tag_distribution"]
    if dist:
        _log("Batch", "Distribution: " + ", ".join(f"{k}:{v}" for k, v in sorted(dist.items())))

    return results


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assign cinematic frame tags to all frames in a ScreenWire project graph."
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        help="Path to the project directory (must contain graph/narrative_graph.json)",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="XAI API key (defaults to XAI_API_KEY env var)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Max parallel Grok calls (default: 10)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-tag frames that already have a cinematic_tag assigned",
    )
    args = parser.parse_args()

    project_dir = Path(args.project_dir)
    if not project_dir.exists():
        print(f"ERROR: Project directory not found: {project_dir}")
        raise SystemExit(1)

    if args.force:
        # Clear existing tags so they get re-tagged
        store = GraphStore(project_dir)
        if store.exists():
            graph = store.load()
            for frame in graph.frames.values():
                frame.cinematic_tag = CinematicTag()
            store.save(graph)
            print(f"Cleared existing tags on {len(graph.frames)} frames.")

    summary = asyncio.run(
        tag_all_frames(
            project_dir,
            api_key=args.api_key,
            concurrency=args.concurrency,
        )
    )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

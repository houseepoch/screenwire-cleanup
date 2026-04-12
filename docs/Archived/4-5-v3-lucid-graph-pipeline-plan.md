# Lucid Graph Pipeline v3 — Implementation Plan
## 04-05 Specification: Frame Density, Blocking, Background, Scene Board & Phase Architecture

**Status:** Active — Single Source of Truth  
**Created:** 2026-04-05  
**Last Updated:** 2026-04-05  

---

## Overview

This document captures all planned changes from the 04-05 specification transcript. Each change has full implementation instructions, downstream impact analysis, and collateral considerations. Changes are organized by execution priority.

**Guiding Principle:** Agent instructions are additive only — do not modify existing instructions unless they block desired processes. All changes communicated here with goal, what changes, and collateral.

---

## Completed Changes (Implemented)

### DONE-2: Character Appearance Description in Video Prompts

**Goal:** Video model (grok-video) cannot identify characters by name. Physical appearance must be injected into every video prompt's character performance block.

**What Changed:**
- `graph/prompt_assembler.py` — Added `_get_cast_appearance()` helper function that builds a brief physical descriptor from CastNode identity fields (age, gender, ethnicity, build, hair, skin, clothing)
- `assemble_video_prompt()` character performance loop now wraps each character name with appearance: `"Mei (early 20s, Chinese, female, slender build, black long hair, pale skin, wearing blue silk kimono)"`
- Uses CastFrameState `clothing_current` when available (wardrobe changed), falls back to CastIdentity `wardrobe_description`

**Downstream Impact:** Video prompts are longer but more descriptive. No schema changes. No instruction changes needed.

**Collateral:** None — purely additive to prompt output.

---

### DONE-4a: Time of Day Per Frame (Schema + Instructions)

**Goal:** Every frame must have explicit `time_of_day` tracked directly on the FrameNode, not just inherited from scene. Ensures lighting consistency across continuous frames.

**What Changed:**
- `graph/schema.py` — Added `time_of_day: Optional[TimeOfDay] = None` field to `FrameNode`
- `agent_prompts/morpheus.md` — Added MANDATORY instruction for Morpheus to set `time_of_day` on every frame (inherit from scene, override for time-passage)
- Added to Stage D audit completeness checklist

**Downstream Impact:**
- `graph/prompt_assembler.py` — NEEDS UPDATE: `assemble_image_prompt()` currently reads `scene.get("time_of_day")` on line 183. Should be updated to prefer `frame.get("time_of_day")` with scene as fallback. (Tracked as PLAN-4b below)
- Continuity checks could validate time_of_day consistency within continuous chains

**Collateral:** Minor — existing graphs without per-frame time_of_day still work (field is Optional, scene fallback preserved).

---

### DONE-9: Media Type Style Prefix in Image Prompts

**Goal:** The media type (live_action, anime, 3d, etc.) must prefix EVERY outgoing image and video prompt.

**What Changed:**
- `graph/prompt_assembler.py` — `assemble_image_prompt()` was resolving `style_prefix` but never inserting it into the final prompt. Fixed: style_prefix is now prepended to the full image prompt.

**Downstream Impact:** All image prompts now start with the appropriate media type prefix. Video prompts already had this.

**Collateral:** None — existing prompts just gain the prefix they were always supposed to have.

---

### DONE-10: Continuity Verification Audit

**Goal:** Verify Morpheus correctly links continuous frames via `previous_frame_id`, `next_frame_id`, `continuity_chain`, and FOLLOWS edges.

**What Changed:**
- `graph/api.py` — `check_continuity()` enhanced with:
  - Bidirectional link validation (if A.next=B, B.prev must=A)
  - Continuity chain verification (if chain=True, same scene+location as prev)
  - Sequence index monotonicity check along chains

**Downstream Impact:** Continuity checks now catch frame linking errors that were previously silent. Morpheus instructions already cover linking — no instruction changes needed.

**Collateral:** Existing graphs may surface new warnings if links were inconsistent. All new warnings are non-blocking (severity: "warning").

---

## Planned Changes — Priority 0 (Execute Next)

### PLAN-1: Frame Extraction Density — Every Verb/Noun = A Frame

**Goal:** Morpheus currently under-extracts frames. Reinforce that every verb-to-noun, verb-to-verb, and noun-to-noun transition produces a separate frame. Non-cast environment frames must also be generated.

**What Changes:**
- `agent_prompts/morpheus.md` — ADD to the ATOMIC BEAT EXTRACTION section (after line 251, before the Frame Formula Directory). This is a pure instruction addition.

**New Instructions to Add:**

```markdown
#### FRAME DENSITY RULES (MANDATORY)

The following rules OVERRIDE any compression instinct. More frames is always better than fewer frames.

**Every verb = a frame.** Every noun = a frame. Every transition between verbs or nouns = a frame.

Concrete rules:
1. **Verb→Noun:** If a time a action meets an object, that is its own frame.
   Example: "She picked up the letter" = one frame (F11 prop interaction)
2. **Verb→Verb:** If two actions happen in sequence, even in the same sentence, each action is its own frame.
   Example: "She turned (noun verb = framed) and walked to the door (verb to noun)" = two frames (F01 turn + F10 walking)
3. **Noun→Noun:** If focus shifts between two objects or characters, each gets its own frame.
   Example: "The candle flickered (noun verbed), as the ink dried (noun verb) on the page" = two frames (F08 candle + F08 ink)
4. **Environment-only frames:** Shots without cast are VALID and REQUIRED. A room settling after someone leaves, rain on a windowpane, a door closing — these are frames.
5. **Close-up companion frames:** When a character interacts with a prop or performs a significant physical action, generate BOTH:
   - The wider shot showing the action in context (F01/F02/F10)
   - A close-up detail shot of the interaction itself (F08/F11)
   This ensures the audience sees both the character doing it and what is being done.
6. **Reaction frames:** After every significant action or dialogue line, include at least one reaction frame showing the listener/observer's response.

**Minimum frame density at stickiness 3:**
- Dialogue sequences: ceil(dialogue_lines * 1.5) frames minimum (speaker + listener reactions + environment cuts)
- Action sequences: 2-3 frames per described action (setup + execution + aftermath)
- Scene transitions: minimum 2 frames (leaving shot + establishing shot of new location)
- Total: expect 20-30 frames per scene for a 3-scene short (~60-90 total)

**Anti-compression validation:** After segmenting all frames for a scene, count them. If you have fewer frames than (number_of_verbs_in_prose + number_of_distinct_nouns_focused_on) * 0.7, you have compressed too aggressively. Re-read and split.
```

**Downstream Impact:**
- More frames = more CastFrameState/PropFrameState snapshots (linear increase, storage trivial)
- More image prompt files generated in Phase 2D
- More API calls in Phase 4 (composition) and Phase 5 (video) — wall clock time increases
- Quality gate `quality_gate_phase_2()` has a minimum frame count check — may need threshold raised

**Collateral:**
- `run_pipeline.py` line 589-593: Phase 2 quality gate checks `len(manifest.get("frames", []))`. Current minimum is likely low. After this change, a 3-scene short should produce 60-90 frames. Consider updating the minimum threshold.
- Phase 4/5 batch sizes (10 concurrent) remain appropriate — just more batches.

**Files Modified:** `agent_prompts/morpheus.md` only  
**Risk:** Low — purely additive instructions

---

### PLAN-4b: Prompt Assembler Uses Frame-Level Time of Day

**Goal:** Now that FrameNode has `time_of_day`, the prompt assembler should prefer frame-level over scene-level.

**What Changes:**
- `graph/prompt_assembler.py` — In `assemble_image_prompt()`, change line 183 from:
  ```python
  time_cn = TIME_CHINESE.get(scene.get("time_of_day", ""), "")
  ```
  to:
  ```python
  # Prefer frame-level time_of_day, fall back to scene
  frame_tod = frame.get("time_of_day") or scene.get("time_of_day", "")
  time_cn = TIME_CHINESE.get(frame_tod, "")
  ```
- Same pattern in `assemble_video_prompt()` if time_of_day is used there (currently not directly, but should be for lighting context).

**Downstream Impact:** Image prompts now reflect per-frame time shifts within a scene.  
**Collateral:** None — fallback preserves existing behavior for graphs without per-frame time_of_day.  
**Files Modified:** `graph/prompt_assembler.py`

---

## Planned Changes — Priority 1

### PLAN-3: Canonical Character State Tagging System

**Goal:** Character states (wardrobe changes, injuries, environmental effects) need a canonical tagging system that:
1. Morpheus generates per frame
2. Image generation handlers recognize for selecting/generating variant reference images
3. Maps to the scene_coordinator's existing `sw_edit_image` state variant workflow

**Current State (Scene Coordinator Convention):**
- Base composite: `cast/composites/{castId}_ref.png`
- State variants: `cast/composites/{castId}/{state_tag}_ref.png`
- State tags are snake_case descriptive: `full_kit_face_paint_sweating`, `wounded_left_arm`, `night_ops`
- Variants are generated via `sw_edit_image` with base composite as `--input`
- Manifest tracks via `stateImages` dict: `{"sweating": {"path": "...", "derivedFrom": "..."}}`

**Proposed Canonical Tag System:**

Add to `graph/schema.py` — new enum and field:

```python
class CastStateTag(str, Enum):
    """Canonical state tags for character appearance variants.
    Each tag maps to a reference image variant generated via sw_edit_image."""
    BASE = "base"                    # Default appearance from CastIdentity
    WET = "wet"                      # Rained on, splashed, sweating heavily
    SWEATING = "sweating"            # Light-moderate perspiration
    WOUNDED = "wounded"              # Visible injury
    BLOODIED = "bloodied"            # Blood visible on face/clothing
    DIRTY = "dirty"                  # Mud, soot, grime
    TORN_CLOTHING = "torn_clothing"  # Wardrobe damage
    FORMAL = "formal"                # Changed into formal attire
    CASUAL = "casual"                # Changed into casual attire
    DISGUISED = "disguised"          # Intentional appearance change
    NIGHT = "night"                  # Night-adapted appearance (darker clothing, gear)
    EXHAUSTED = "exhausted"          # Visible fatigue, dark circles, disheveled

class CastStateVariant(BaseModel):
    """A named visual state variant for a cast member."""
    state_tag: str                              # Canonical tag or custom compound
    description: str = ""                       # What changed visually
    derived_from: str = "base"                  # Which state this derives from
    image_path: Optional[str] = None            # Generated variant image path
    trigger_frame: Optional[str] = None         # Frame where this state first appears
    active_through: Optional[str] = None        # Last frame where this state is active
```

Add to `CastNode`:
```python
    # State variant tracking
    state_variants: dict[str, CastStateVariant] = Field(default_factory=dict)
    # Key = state_tag, value = variant metadata
```

Add to `CastFrameState`:
```python
    # Active state tag for this frame (determines which reference image to use)
    active_state_tag: str = "base"
    # Compound tag for multiple simultaneous states: "wet_wounded" 
    # Convention: alphabetical, underscore-joined
```

**Morpheus Instructions Addition** (to `agent_prompts/morpheus.md`, C2 Cast Frame States section):

```markdown
#### STATE TAG ASSIGNMENT (MANDATORY)

Every CastFrameState must include `active_state_tag`. This determines which reference image variant downstream agents use for this character in this frame.

**Tag assignment rules:**
1. Start with "base" — the character's default appearance
2. When prose describes a significant visual change, assign the matching canonical tag
3. For multiple simultaneous states, join alphabetically: "bloodied_torn_clothing"
4. Once assigned, a state tag PERSISTS forward until prose indicates recovery/change
5. State tags trigger variant image generation — flag in events.jsonl:
   ```json
   {"level": "INFO", "code": "STATE_VARIANT_NEEDED", "entity_id": "cast_001_mei", 
    "state_tag": "wet", "trigger_frame": "f_025",
    "description": "Caught in rain, kimono clinging, hair plastered"}
   ```

**Tag → Visual Change Mapping:**
| Tag | What to describe in clothing_current/hair_state/injury |
|---|---|
| wet | Clothing clinging, hair plastered, water droplets visible |
| sweating | Sheen on skin, damp collar/hairline |
| wounded | Specific injury location and severity in `injury` field |
| bloodied | Blood location + source (lip, forehead, hands) |
| dirty | Soot/mud location, contrast with original colors |
| torn_clothing | Which garment, where torn, what's exposed |
| exhausted | Posture change, eye state (heavy-lidded), skin pallor |
```

**Location State Variant Generation Architecture:**

For location states (time of day variants, damage states, directional views), the generation follows the same pattern:
1. Base location image generated via `sw_fresh_generation` (NanoBanana 2)
2. Each state variant generated via `sw_edit_image` with base image as `--input`
3. Time of day variants: `sw_edit_image --input loc_base.png --prompt "Same location, shift to dusk lighting, warm amber tones, long shadows"`
4. Directional variants: `sw_edit_image --input loc_base.png --prompt "View from the north entrance, looking south through the main hall"`

This means:
- Scene Coordinator generates base images first
- Then derives state variants from base using NanoBanana 2 edit
- Each variant is a separate reference image that can be injected into frame prompts

**Downstream Impact:**
- `graph/prompt_assembler.py` — `resolve_ref_images()` needs to check `active_state_tag` and use the variant image path instead of base composite when available
- Scene Coordinator / Image Verifier — needs to read STATE_VARIANT_NEEDED events and generate variants
- `graph/materializer.py` — `materialize_cast_profiles()` should include state variant paths
- `run_pipeline.py` Phase 3 — state variant generation happens after base composites

**Collateral:**
- Schema addition (new models + new fields on CastNode/CastFrameState) — non-breaking, all fields have defaults
- Existing graphs work unchanged (active_state_tag defaults to "base")
- Image verifier instructions may need addition to check state variant coverage

**Files Modified:** `graph/schema.py`, `agent_prompts/morpheus.md`, `graph/prompt_assembler.py`, `graph/materializer.py`

---

### PLAN-6: Character Blocking Per Frame (Current→Ending in Video Prompts)

**Goal:** Every frame needs explicit blocking data. Video prompts must describe character state transitions from current position/facing to ending position/facing for the clip.

**Current State:**
- `CastFrameState` already has: `spatial_position`, `facing_direction`, `eye_direction`, `posture`
- Missing: `screen_position` (left/center/right of frame), `looking_at` (what/who they're focused on)
- Video prompt currently uses these but doesn't express the transition from current→next frame state

**Schema Changes** (`graph/schema.py`):

Add to `CastFrameState`:
```python
    # Screen blocking (where in the frame)
    screen_position: Optional[str] = None       # "frame_left", "frame_center", "frame_right", "frame_left_third", "frame_right_third"
    looking_at: Optional[str] = None            # "cast_002_lin", "prop_001_letter", "window", "camera", "distance"
```

**Morpheus Instructions Addition** — ADD to C2 Cast Frame States:

```markdown
#### CHARACTER BLOCKING (MANDATORY for every non-referenced CastFrameState)

You MUST populate these blocking fields for every character that is visible in a frame:

- `screen_position` — where in the camera frame: "frame_left", "frame_center", "frame_right", "frame_left_third", "frame_right_third"
- `looking_at` — what they're looking at: another cast_id, a prop_id, a location feature ("window", "door"), "camera" (breaking fourth wall), or "distance" (gazing away)
- `facing_direction` — toward_camera, away, profile_left, profile_right, three_quarter_left, three_quarter_right
- `spatial_position` — physical position in the scene (not screen): "foreground_left", "center_frame", "background_right", "doorway", "seated_at_table"

These are critical for:
1. Image composition — the prompt builder uses screen_position and facing_direction to describe character placement
2. Video continuity — blocking must flow logically between consecutive frames
3. Video motion — the video prompt describes the transition from this frame's blocking to the next frame's blocking

**Blocking continuity rules:**
- Characters don't teleport — if someone is frame_left in F_010, they must walk/move to reach frame_right in F_012. The intermediate frame shows the movement.
- Facing changes require motivation — a character turns because they hear something, see someone enter, or need to address a different person.
- If two characters are in dialogue, one should be frame_left facing right, the other frame_right facing left (standard two-shot blocking).
```

**Video Prompt Assembler Changes** (`graph/prompt_assembler.py`):

In `assemble_video_prompt()`, after building the character performance section, add blocking transition data. This requires reading the NEXT frame's CastFrameState to describe the transition:

```python
# ── Blocking transition (current frame → next frame)
blocking_parts = []
if frame.get("next_frame_id"):
    next_ctx = get_frame_context(graph, frame["next_frame_id"])
    for cs in ctx.get("cast_states", []):
        if cs.get("frame_role") in ("referenced", None):
            continue
        name = _get_cast_name(ctx["cast"], cs["cast_id"])
        # Find this cast member's state in the next frame
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

if blocking_parts:
    blocking_section = "Character blocking: " + ". ".join(blocking_parts) + "."
    # Insert into prompt parts before beat_section
```

**Video prompt output format change:**
```
Current: "Mei sits on cushion, expression showing restrained anger..."
New:     "Mei sits on cushion, expression showing restrained anger... Character blocking: Mei: turns from profile_left to face toward_camera; shifts from sitting to standing."
```

**Downstream Impact:**
- Video prompts are richer with blocking transitions — video model gets directorial motion cues
- `get_frame_context()` may be called twice per frame during video prompt assembly (current + next) — minor perf impact
- No Phase 3/4 impact — blocking is only used in video prompts (Phase 5)

**Collateral:**
- Schema addition is non-breaking (Optional fields with None defaults)
- Existing video prompts just won't have blocking section (graceful degradation)
- `get_frame_context()` for next frame is a dict lookup — no perf concern

**Files Modified:** `graph/schema.py`, `agent_prompts/morpheus.md`, `graph/prompt_assembler.py`

Favor framing for cast not looking at the camera instead live in their environment. 

---

### PLAN-7: Close-Up Companion Frames for Key Interactions

**Goal:** When a character interacts with something significant, automatically generate a close-up detail frame alongside the wider context shot.

**What Changes:**
- `agent_prompts/morpheus.md` — Included in PLAN-1 frame density rules above (rule #5: close-up companion frames)
- No schema changes — FormulaTag F08 (detail) and F11 (prop interaction) already exist

**Morpheus Instructions (included in PLAN-1 addition):**
Already covered by rule #5 in PLAN-1's frame density section. The instruction states:
> When a character interacts with a prop or performs a significant physical action, generate BOTH the wider shot AND a close-up detail shot (F08/F11).

**Downstream Impact:** More frames (compounds with PLAN-1). This is intentional.  
**Collateral:** None beyond what PLAN-1 already covers.  
**Files Modified:** `agent_prompts/morpheus.md` (bundled with PLAN-1)

---

## Planned Changes — Priority 2

### PLAN-5: Background Data Per Frame + Cardinal Direction System

**Goal:** Every frame needs structured background information. Locations need cardinal direction descriptions (N/S/E/W) for what's visible from each direction. A Morpheus helper agent enriches each frame's background after initial extraction.

**Schema Changes** (`graph/schema.py`):

#### LocationNode additions:

```python
class LocationDirections(BaseModel):
    """Cardinal direction views from within a location.
    Only populated for directions that are used in frames."""
    north: Optional[str] = None     # "Courtyard with stone well, plum tree"
    south: Optional[str] = None     # "Main hall entrance, red columns"
    east: Optional[str] = None      # "Garden wall, bamboo grove beyond"
    west: Optional[str] = None      # "Mountain vista, terraced rice paddies"
    exterior: Optional[str] = None  # "From outside: two-story wooden building, tiled roof, lanterns"
```

Add to `LocationNode`:
```python
    # Cardinal direction views (what you see facing each direction from inside)
    directions: LocationDirections = Field(default_factory=LocationDirections)
```

#### FrameNode additions (FrameBackground model):

```python
class FrameBackground(BaseModel):
    """Structured background information for a frame.
    What's happening behind the foreground action."""
    visible_description: str = ""               # What's visible in the background of this shot
    camera_facing: Optional[str] = None         # "north", "south", "east", "west" — which direction camera points
    background_action: Optional[str] = None     # "Servants clearing dishes in the distance"
    background_sound: Optional[str] = None      # "Distant market chatter, horse hooves on cobblestone"
    background_music: Optional[str] = None      # "Faint erhu melody from the performance hall"
    depth_layers: list[str] = Field(default_factory=list)  # ["midground: silk screens", "far: mountain silhouette"]
```

Add to `FrameNode`:
```python
    # Background data (structured, beyond what FrameEnvironment covers)
    background: FrameBackground = Field(default_factory=FrameBackground)
```

#### Implementation as Morpheus Helper Agent:

This runs as a helper within Morpheus's swarm, not a separate pipeline phase. After Morpheus completes initial frame extraction (Stage C), a background enrichment pass runs as part of Stage C or as a post-processing step before Stage D audit.

**Morpheus Instructions Addition:**

```markdown
#### STAGE C-POST: BACKGROUND ENRICHMENT

After extracting all frames, run a background enrichment pass. For each frame:

1. **Camera facing:** Determine which cardinal direction the camera faces based on:
   - The frame's `composition.shot` and staging
   - Where characters are positioned relative to the location layout
   - Set `background.camera_facing` to "north", "south", "east", "west"

2. **Background description:** Using the location's `directions` data for the camera-facing direction, write what's visible behind the foreground action:
   - Pull from `LocationNode.directions.{camera_facing}`
   - Add frame-specific context (other characters in background, environmental changes)
   - Set `background.visible_description`

3. **Background action:** If anything moves or happens in the background:
   - Other characters moving
   - Environmental events (door opening, animal passing, weather)
   - Set `background.background_action`

4. **Background audio:** For video prompting:
   - Location-appropriate ambient sounds
   - Distant activities that would produce sound
   - Set `background.background_sound`

5. **Background music:** Only if diegetic music exists in the scene:
   - A musician performing in the story
   - Music playing from a source within the scene
   - Set `background.background_music`

**Location Direction Seeding:**
During Stage B (skeleton pre-seed), when creating LocationNodes, populate `directions` based on the prose description of the location. Only fill directions that are actually used or implied by the narrative. Don't fabricate directions not supported by the prose.

Example:
```json
{
  "directions": {
    "north": "Main entrance, heavy wooden doors, stone steps descending to street",
    "south": "Private garden, koi pond, weeping willows",
    "east": "Adjoining tea room, paper screens partially open",
    "west": "Balcony overlooking the river, wooden railing, distant bridge"
  }
}
```
```

**Prompt Assembler Changes:**

In `assemble_image_prompt()`, enhance Segment 3 (Environmental details) with background data:
```python
# Background enrichment (if available)
bg_data = frame.get("background", {})
if bg_data.get("visible_description"):
    env_parts.append(f"远景：{bg_data['visible_description']}")
```

In `assemble_video_prompt()`, add background section with action + audio:
```python
# ── Background (enriched)
bg_data = frame.get("background", {})
bg_parts = []
if bg_data.get("visible_description"):
    bg_parts.append(bg_data["visible_description"])
if bg_data.get("background_action"):
    bg_parts.append(bg_data["background_action"])
bg_section = f"Background: {'. '.join(bg_parts)}." if bg_parts else ""

# Background audio goes into AUDIO section
if bg_data.get("background_sound"):
    audio_layers.append(bg_data["background_sound"])
```

**Downstream Impact:**
- Image prompts get richer background descriptions
- Video prompts get background action and audio layers
- Location profiles gain cardinal direction data
- Morpheus processing time increases slightly (background enrichment pass)

**Collateral:**
- Schema additions are all Optional with defaults — non-breaking
- Existing prompts continue working without background data (empty strings)
- `graph/materializer.py` — `materialize_location_profiles()` should include directions data

**Files Modified:** `graph/schema.py`, `agent_prompts/morpheus.md`, `graph/prompt_assembler.py`, `graph/materializer.py`

---

### PLAN-8: Scene Board (Storyboard Reference Images) + Phase Architecture Renaming

**Goal:** For every scene or continuous frame sequence, generate a single multi-panel storyboard image (NanoBanana 4K). This becomes an input reference for all frames in that sequence, improving visual consistency and linear coherency.

**Additionally:** With all these additions, the pipeline phases need renaming to accurately reflect their expanded roles.

#### Scene Board Implementation:

**New Skill:** `sw_generate_sceneboard`

This skill:
1. Takes a scene_id (or frame range)
2. Reads all frame prompt data for that scene from the graph
3. Assembles a multi-panel storyboard prompt with:
   - Media type prefix (MANDATORY — from onboarding config)
   - All reference images as input (cast composites, location refs, prop refs)
   - Each frame's narrative_beat labeled as a panel
   - Instruction prefix: "Generate a multi-panel cinematic storyboard depicting the following sequential scenes. Each panel should flow naturally into the next."
4. Calls NanoBanana 2 at 4K resolution with all reference images
5. Saves to `frames/storyboards/{scene_id}_storyboard.png`

**New Function in `graph/prompt_assembler.py`:**

```python
def assemble_sceneboard_prompt(graph: NarrativeGraph, scene_id: str) -> dict:
    """Build a multi-panel storyboard prompt for a scene.
    
    Returns dict with:
        prompt: str — multi-panel storyboard instruction
        ref_images: list[str] — all cast/location/prop references
        size: str — always "landscape_16_9" at max resolution
        out_path: str — storyboard output path
        frame_ids: list[str] — frames included in this storyboard
    """
```

The prompt format:
```
{media_style_prefix}Multi-panel cinematic storyboard, sequential scene panels flowing left to right, top to bottom. Professional storyboard art, clear panel borders, consistent character appearance across panels.

Panel 1 (F07 Establishing): {frame_1_narrative_beat}
Panel 2 (F01 Character Focus): {frame_2_narrative_beat}
Panel 3 (F04 Dialogue): {frame_3_narrative_beat}
...

Characters: {cast descriptions with appearance}
Location: {location description}
Mood: {scene mood keywords}
```

**Pipeline Placement:**

Scene boards must be generated AFTER:
- Phase 3 (cast composites, location refs, prop refs are all generated)
- All reference images exist as inputs

Scene boards must be generated BEFORE:
- Phase 4 (individual frame composition) — frames use storyboard as reference

This means scene board generation happens at the START of Phase 4, before individual frame generation.

**Reference Image Injection:**

`resolve_ref_images()` in `prompt_assembler.py` needs a scene board reference:
```python
# 0. Scene storyboard (if exists) — highest priority reference
scene = graph.scenes.get(frame.scene_id)
if scene:
    storyboard_path = f"frames/storyboards/{frame.scene_id}_storyboard.png"
    # Check existence at generation time
    refs.insert(0, storyboard_path)  # First reference = strongest influence
```

#### Phase Architecture Renaming

With the additions (scene boards, background enrichment, state variant generation, blocking), the phases have grown beyond their original names. Proposed new naming:

| Old Phase | Old Name | New Phase | New Name | What Changed |
|-----------|----------|-----------|----------|-------------|
| Phase 0 | Scaffold Verification | Phase 0 | **Project Scaffold** | No change |
| Phase 1 | Narrative (Creative Writing) | Phase 1 | **Narrative Writing** | No change |
| Phase 2 | Morpheus (Graph Build + Prompt Assembly) | Phase 2 | **Graph Construction** | Now includes: frame density rules, time_of_day per frame, blocking, background enrichment, state tag assignment, canonical tagging |
| Phase 3 | Image Verification + Voice | Phase 3 | **Asset Generation & Verification** | Now includes: base composites, state variant generation (via sw_edit_image from base), location directional variants, location time-of-day variants, prop refs, voice profiling |
| — | — | Phase 3.5 (new sub-phase) | **Scene Board Generation** | NEW: Generate multi-panel storyboard per scene using NanoBanana 4K with all reference images. Runs after Phase 3, before Phase 4. |
| Phase 4 | Composition Verification | Phase 4 | **Frame Composition** | Now receives: scene board references, enriched blocking data, background descriptions, state variant refs. Scene boards generated at start of this phase. |
| Phase 5 | Video Verification | Phase 5 | **Video Generation** | Now receives: appearance descriptions, blocking transitions (current→ending), background actions, background audio layers |
| Phase 6 | Export (ffmpeg) | Phase 6 | **Final Export** | No change |

**Implementation in `run_pipeline.py`:**

Update `PHASE_NAMES` dict:
```python
PHASE_NAMES = {
    0: "Project Scaffold",
    1: "Narrative Writing",
    2: "Graph Construction",
    3: "Asset Generation & Verification",
    4: "Frame Composition",
    5: "Video Generation",
    6: "Final Export",
}
```

Scene board generation integrates into the START of `phase_4_production()`:
```python
def phase_4_production(dry_run, phase_timers, skip_tts=False):
    # Step 1: Generate scene boards (NEW)
    log("Generating scene storyboards...")
    # For each scene, call sw_generate_sceneboard
    # This uses assembled prompts from Phase 2 + reference images from Phase 3
    
    # Step 2: Re-assemble image prompts with storyboard references (NEW)
    # Run graph_assemble_prompts again so ref_images includes storyboards
    
    # Step 3: Run composition verifier (existing)
    result = run_agent("composition_verifier", ...)
```

**Downstream Impact:**
- Scene board generation adds ~30-60s per scene (one NanoBanana 4K call each)
- Individual frame generation gets stronger consistency from storyboard reference
- Phase names update in all logs, manifest, and quality gate reports

**Collateral:**
- `run_pipeline.py` — PHASE_NAMES dict update, phase_4 gets scene board step
- `project_manifest.json` — phase names in phases dict need to match new names (or keep numeric keys which is fine)
- Quality gate functions — no changes needed (they check outputs, not names)
- New skill file: `skills/sw_generate_sceneboard` needs to be created
- `graph/prompt_assembler.py` — new `assemble_sceneboard_prompt()` function + `resolve_ref_images()` update

**Files Modified:** `run_pipeline.py`, `graph/prompt_assembler.py`, new `skills/sw_generate_sceneboard`, possibly `server.py` for scene board queue handling

---

## Execution Order

```
ALL COMPLETE (2026-04-05):
  [x] DONE-2:  Character appearance in video prompts
  [x] DONE-4a: time_of_day field on FrameNode + Morpheus instructions
  [x] DONE-9:  Media type prefix in image prompts  
  [x] DONE-10: Continuity check enhancements (+ 4 new checks in api.py)
  [x] PLAN-1:  Frame density rules + close-up companion frames (morpheus.md)
  [x] PLAN-4b: Prompt assembler uses frame-level time_of_day
  [x] PLAN-3:  Canonical state tagging system (schema + morpheus + assembler + materializer)
  [x] PLAN-6:  Character blocking fields + video prompt transitions (schema + morpheus + assembler)
  [x] PLAN-7:  Close-up companion frames (bundled with PLAN-1)
  [x] PLAN-5:  Background data + cardinal directions (schema + morpheus + assembler + materializer)
  [x] PLAN-8:  Scene board generation + phase renaming (new skill + assembler + pipeline)
```

**Dependency Chain:**
```
PLAN-1 ──→ PLAN-7 (companion frames are part of density rules)
PLAN-4b ──→ standalone (no dependencies)
PLAN-3 ──→ PLAN-5 (background enrichment benefits from state tags for location variants)
PLAN-6 ──→ PLAN-8 (blocking data enriches storyboard panels)
PLAN-5 ──→ PLAN-8 (background data enriches storyboard prompts)
PLAN-8 depends on: PLAN-1, PLAN-3, PLAN-5, PLAN-6 all complete
```

---

## Testing Strategy

### Unit Testing (per change):
- **Schema changes:** Verify Pydantic models accept new fields, defaults work, existing data loads without error
- **Prompt assembler:** Generate prompts from test_project graph, verify new data appears in output
- **Continuity checks:** Run `graph_continuity --check-all` on test_project, verify new checks fire

### Integration Testing (full pipeline):
- Run `python3 run_pipeline.py --phase 2` on test_project after PLAN-1 implementation
- Verify frame count increased (expect 60-90 for 3-scene short)
- Verify all new fields populated (time_of_day, blocking, background)
- Run prompt assembly and inspect output JSON for completeness

### End-to-End Testing:
- Full pipeline run after all changes
- Verify Phase 4 frames use storyboard references
- Verify Phase 5 video prompts include blocking transitions and appearance descriptions
- Compare output quality against pre-change baseline

---

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Increased frame count overwhelms API budget | Medium | Frame count is controlled by stickiness level. Stickiness 3 targets 60-90 frames, not unbounded. |
| Morpheus context window exceeded by new instructions | Low | Instructions are additive, ~500 tokens added. Morpheus already handles 900+ lines of instructions. |
| Scene board generation fails (NanoBanana timeout) | Low | Scene board is a reference aid, not blocking. Frames can generate without storyboard ref. |
| Blocking transitions in video prompts confuse model | Low | Transitions are natural language, tested format. Graceful fallback: no blocking section if next frame lacks data. |
| State tag proliferation creates too many variant images | Low | Canonical tags are bounded (12 values). Compound tags are rare. Budget: ~2-5 variants per character max. |

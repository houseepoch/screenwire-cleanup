# CC-First Deterministic Graph Construction — Implementation Specification

> **Status:** APPROVED — Implementation ACTIVE
> **Date:** 2026-04-11
> **Supersedes:** Morpheus Agents 1-4 (Entity Seeder, Frame Parser, Dialogue Wirer, Compositor)
> **Depends on:** `graph/schema.py`, `graph/prompt_assembler.py`, `graph/api.py`, `agent_prompts/creative_coordinator.md`
> **Runtime note:** The active runtime uses Grok reasoning models for the Creative Coordinator, prose workers, and frame enrichment. Older references below to Opus or Haiku are historical wording, not the current provider path.

---

## 0. Architecture Summary

```
CC (Grok reasoning) → outline_skeleton.md  (structured ///TAG blocks)
                   → creative_output.md   (///frame markers + dialogue)
                ↓
Python parser (graph/cc_parser.py) — deterministic, seconds
  → CastNodes, LocationNodes, PropNodes, SceneNodes
  → FrameNodes with sequence linking
  → DialogueNodes with temporal span + ENV parsing
  → All edges (FOLLOWS, APPEARS_IN, AT_LOCATION, etc.)
  → Base CastFrameState per character per frame
                ↓
Parallel Grok frame enricher workers — per-frame enrichment
  → CastFrameState enrichment (screen_position, looking_at, emotion, posture, ...)
  → FrameComposition (shot, angle, movement, focus)
  → FrameEnvironment, FrameBackground, FrameDirecting
                ↓
Grok frame tagging — post-enrichment cinematic tag assignment
  → Per-frame tag from Cinematic Frame Tag Taxonomy (D/E/R/A/C/T/S/M families)
  → Tag definition + ai_prompt_language injected into generation prompts
                ↓
Continuity validator (legacy Morpheus Agent 5 role, reduced to audit-only)
  → Spatial consistency check
  → Cast state delta continuity
  → Dialogue coverage verification
                ↓
Prompt assembly + materialization (existing deterministic code)
```

**What this eliminates:**
- Morpheus Agent 1 (Entity Seeder) — replaced by Python parser
- Morpheus Agent 2 (Frame Parser) — replaced by Python parser
- Morpheus Agent 3 (Dialogue Wirer) — replaced by Python parser
- Morpheus Agent 4 (Compositor) — replaced by frame enricher workers

**What remains:**
- Morpheus Agent 5 (Continuity Wirer) — reduced to validation-only

---

## 1. CC Skeleton Output Format

The Creative Coordinator outputs `creative_output/outline_skeleton.md` with parsable `///TAG` blocks. All entity IDs use `snake_case` slugs derived from the entity name (lowercase, spaces → underscores, strip non-alphanumeric).

### 1.1 Cast Roster Tags

One tag per character. All fields after `id` and `name` are pipe-separated `key=value` pairs.

```
///CAST: id=cast_{slug} | name={Name} | role={NarrativeRole} | gender={gender} | age={age_descriptor} | build={build} | hair={length,style,color} | skin={tone} | clothing={item1,item2,...} | clothing_style={style} | clothing_fabric={fabric} | footwear={footwear} | accessories={acc1,acc2,...} | personality={trait1,trait2,...} | wardrobe={full_wardrobe_description} | arc={start_state -> end_state} | state_tags={base,tag2,tag3,...}
```

**Field mapping to schema:**

| Tag field | Schema target | Required | Notes |
|-----------|--------------|----------|-------|
| `id` | `CastNode.cast_id` | YES | Format: `cast_{slug}` |
| `name` | `CastNode.name` | YES | Display name |
| `role` | `CastNode.role` → `NarrativeRole` | YES | One of: protagonist, antagonist, mentor, ally, catalyst, supporting, background |
| `gender` | `CastIdentity.gender` | YES | |
| `age` | `CastIdentity.age_descriptor` | YES | e.g. "30s", "early 20s", "50-year-old" |
| `build` | `CastIdentity.build` | YES | tall, slender, athletic, heavy, petite, etc. |
| `hair` | `CastIdentity.hair_length`, `.hair_style`, `.hair_color` | YES | Comma-separated triple: `short,cropped,black` |
| `skin` | `CastIdentity.skin` | YES | pale, light, medium, dark, weathered, etc. |
| `clothing` | `CastIdentity.clothing` | YES | Comma-separated garment list |
| `clothing_style` | `CastIdentity.clothing_style` | NO | e.g. "military", "bohemian" |
| `clothing_fabric` | `CastIdentity.clothing_fabric` | NO | e.g. "linen", "leather" |
| `footwear` | `CastIdentity.footwear` | NO | |
| `accessories` | `CastIdentity.accessories` | NO | Comma-separated |
| `personality` | `CastNode.personality` | YES | Comma-separated traits → joined with ", " |
| `wardrobe` | `CastIdentity.wardrobe_description` | YES | Full prose wardrobe description |
| `arc` | `CastNode.arc_summary` | NO | e.g. "broken soldier -> found purpose" |
| `state_tags` | `CastNode.state_variants` keys | NO | Comma-separated `CastStateTag` values. "base" always implied. Each listed tag gets a `CastStateVariant` entry with `state_tag` and `derived_from="base"` |

**Parser builds `CastIdentity.physical_description`** by concatenating: `{age} {gender}, {build} build, {skin} skin, {hair_color} {hair_length} {hair_style} hair`.

### 1.2 Location Roster Tags

```
///LOCATION: id=loc_{slug} | name={Name} | type={interior|exterior} | atmosphere={description} | material_palette={mat1,mat2,...} | architecture={kw1,kw2,...} | flora={description} | description={base_description}
///LOCATION_DIR: id=loc_{slug} | direction={north|south|east|west|exterior} | description={what_is_visible} | features={feature1,feature2,...} | depth={fg_to_bg_layers}
```

Location directions are separate tags — one `///LOCATION_DIR` per cardinal direction used. This keeps line lengths manageable.

**Field mapping to schema:**

| Tag field | Schema target | Required | Notes |
|-----------|--------------|----------|-------|
| `id` | `LocationNode.location_id` | YES | Format: `loc_{slug}` |
| `name` | `LocationNode.name` | YES | |
| `type` | `LocationNode.location_type` | YES | "interior" or "exterior" |
| `atmosphere` | `LocationNode.atmosphere` | YES | |
| `material_palette` | `LocationNode.material_palette` | NO | Comma-separated |
| `architecture` | `LocationNode.architecture_keywords` | NO | Comma-separated |
| `flora` | `LocationNode.flora` | NO | |
| `description` | `LocationNode.description` | YES | Base physical description |

**Direction field mapping:**

| Tag field | Schema target | Required | Notes |
|-----------|--------------|----------|-------|
| `id` | Links to parent `LocationNode` | YES | Must match a `///LOCATION` id |
| `direction` | `LocationDirections.{north\|south\|east\|west\|exterior}` | YES | |
| `description` | `LocationDirectionView.description` | YES | |
| `features` | `LocationDirectionView.key_features` | NO | Comma-separated |
| `depth` | `LocationDirectionView.depth_description` | NO | |

### 1.3 Prop Roster Tags

```
///PROP: id=prop_{slug} | name={Name} | description={physical_description} | significance={narrative_significance} | associated_cast={cast_id1,cast_id2,...} | materials={mat1,mat2,...}
```

| Tag field | Schema target | Required | Notes |
|-----------|--------------|----------|-------|
| `id` | `PropNode.prop_id` | YES | Format: `prop_{slug}` |
| `name` | `PropNode.name` | YES | |
| `description` | `PropNode.description` | YES | Physical description, intact state |
| `significance` | `PropNode.narrative_significance` | YES | |
| `associated_cast` | `PropNode.associated_cast` | NO | Comma-separated cast_ids |
| `materials` | `PropNode.material_context` | NO | Comma-separated |

### 1.4 Scene Header Tags

One per scene, placed at the start of each scene block in the skeleton and in `creative_output.md`.

```
///SCENE: id=scene_{NN} | title={Title} | location=loc_{slug} | time_of_day={TimeOfDay} | int_ext={INT|EXT|INT/EXT} | cast={cast_id1,cast_id2,...} | mood={kw1,kw2,...} | pacing={pacing} | cast_states={cast_id:state_tag,cast_id:state_tag,...} | props={prop_id1,prop_id2,...}
```

| Tag field | Schema target | Required | Notes |
|-----------|--------------|----------|-------|
| `id` | `SceneNode.scene_id` | YES | Format: `scene_{NN}` (zero-padded 2-digit) |
| `title` | `SceneNode.title` | YES | |
| `location` | `SceneNode.location_id` | YES | Must reference a `///LOCATION` id |
| `time_of_day` | `SceneNode.time_of_day` → `TimeOfDay` | YES | One of: dawn, morning, midday, afternoon, dusk, night |
| `int_ext` | `SceneNode.int_ext` | YES | "INT", "EXT", or "INT/EXT" |
| `cast` | `SceneNode.cast_present` | YES | Comma-separated cast_ids |
| `mood` | `SceneNode.mood_keywords` | YES | Comma-separated |
| `pacing` | `SceneNode.pacing` | NO | slow-burn, tense, frenetic, measured |
| `cast_states` | Initial `CastFrameState.active_state_tag` per cast in scene | NO | Format: `cast_id:state_tag,...`. Default: "base" for unlisted cast |
| `props` | `SceneNode.props_present` | NO | Comma-separated prop_ids |

**Parser derives `SceneNode.scene_number`** from the `NN` in `scene_{NN}`.
**Parser builds `SceneNode.scene_heading`** from: `{int_ext}. {LOCATION_NAME} — {TIME_OF_DAY}`.

### 1.5 Per-Frame Tags

In `creative_output.md`, every visual paragraph is preceded by a `///` frame marker:

```
/// cast:{name1,name2} | cam:{direction} | dlg | cast_states:{name1=state_tag,name2=state_tag}
```

| Tag field | Schema target | Required | Notes |
|-----------|--------------|----------|-------|
| `cast:{names}` | `FrameNode.cast_states[].cast_id` (resolved via name→id lookup) | NO | Omit for environment-only. Comma-separated display names |
| `cam:{direction}` | `FrameNode.background.camera_facing` | YES | north, south, east, west, exterior |
| `dlg` | `FrameNode.is_dialogue` | NO | Flag (present = true). Frames with this flag reference one or more `///DLG` tags |
| `cast_states:{name=tag,...}` | `CastFrameState.active_state_tag` per cast | NO | Override scene-default state for specific cast in this frame |

**NO `dur:` field.** Duration is computed downstream by `prompt_assembler.py` using dialogue timing.

**Note:** Frame composition tags are assigned POST-GRAPH by a dedicated Grok tagging pass (see Section 6). The CC does not assign frame tags.

**Dialogue frames and `///DLG` tags:** When `dlg` flag is present on a `///` frame marker, the frame contains dialogue. The actual dialogue text is NOT in the frame's `source_text` — it is referenced via `///DLG` tags defined separately in the skeleton. The parser resolves these tags to extract the verbatim dialogue from `creative_output.md`.

**Parser assigns:**
- `frame_id`: `f_{NNN}` (zero-padded 3-digit, globally sequential across all scenes)
- `sequence_index`: 0-based global sequence number
- `scene_id`: inherited from the most recent `///SCENE` header
- `source_text`: the full paragraph text following the `///` marker
- `narrative_beat`: same as `source_text` (enriched later by the frame enricher)
- `is_dialogue`: true if `dlg` flag present
- `location_id`: inherited from the scene's `location` field
- `time_of_day`: inherited from the scene's `time_of_day`

### 1.6 Dialogue Excerpt Tags

Dialogue is NOT copied into the skeleton. Instead, CC outputs `///DLG` excerpt pointer tags that reference the source text in `creative_output.md`:

```
///DLG: speaker={name} | cast_id={cast_id} | src_start="{first_5_words}" | src_end="{last_3_words}" | src_lines={start}-{end}
| perf={direction_tags} | env={location,distance,intensity}
```

**Field mapping to schema:**

| Tag field | Schema target | Required | Notes |
|-----------|--------------|----------|-------|
| `speaker` | `DialogueNode.speaker` | YES | Display name (resolved to cast_id via name→id lookup) |
| `cast_id` | `DialogueNode.cast_id` | YES | Entity ID (e.g. `cast_watanabe`) |
| `src_start` | Validation anchor | YES | First 5 words of dialogue line (fuzzy match ±5 lines) |
| `src_end` | Validation anchor | YES | Last 3 words of dialogue line (fuzzy match ±5 lines) |
| `src_lines` | Source lookup | YES | Line range in creative_output.md (e.g. `142-144`) — primary anchor |
| `perf` | `DialogueNode.performance_direction` | NO | Direction tags, comma-separated |
| `env` | `DialogueNode.env_tags` (parsed) | NO | Location, distance, intensity (see below) |

**ENV tag parsing** (from `env=` field):

| ENV element | Schema target | Position | Values |
|------------|--------------|----------|--------|
| env_location | `DialogueNode.env_location` | 1st CSV | indoor, outdoor, vehicle, etc. |
| env_distance | `DialogueNode.env_distance` | 2nd CSV | intimate, close, medium, far |
| env_intensity | `DialogueNode.env_intensity` | 3rd CSV | whisper, quiet, normal, loud, shouting |
| env_medium | `DialogueNode.env_medium` | 4th CSV (optional) | radio, comms, phone, muffled |
| env_atmosphere | `DialogueNode.env_atmosphere` | Remaining CSV (optional) | Additional context |

**Temporal span assignment:**
- `start_frame`: the frame_id of the `///` marker containing this dialogue (the `dlg` frame)
- `end_frame`: same as `start_frame` (single-frame span by default; frame enricher workers can extend for J/L cuts)
- `primary_visual_frame`: same as `start_frame`
- `dialogue_id`: `dlg_{NNN}` (globally sequential)
- `order`: 0-based global sequence
- `scene_id`: inherited from the enclosing scene
- `raw_line`: extracted verbatim from creative_output.md at `src_lines` (no rewriting)

### 1.7 Scene Staging Tags

The CC declares a spatial staging plan per scene with three beats (start, mid, end) defining character positions, eyelines, and facing directions. frame enricher workers use these as anchors — they interpolate between beats based on frame position within the scene.

Format:
```
///SCENE_STAGING: id=scene_{NN} | location=loc_{slug}
| start: {cast_id}={screen_position},{looking_at},{facing_direction} | {cast_id}={screen_position},{looking_at},{facing_direction}
| mid: {cast_id}={screen_position},{looking_at},{facing_direction} | {cast_id}={screen_position},{looking_at},{facing_direction}
| end: {cast_id}={screen_position},{looking_at},{facing_direction} | {cast_id}={screen_position},{looking_at},{facing_direction}
```

Field definitions per cast entry within a beat:
- screen_position (MANDATORY): frame_left | frame_center | frame_right | frame_left_third | frame_right_third
- looking_at (MANDATORY): another cast_id | prop_id | distance | camera | a location feature
- facing_direction (MANDATORY): toward_camera | away | profile_left | profile_right | three_quarter

Example:
```
///SCENE_STAGING: id=scene_01 | location=loc_lab
| start: cast_watanabe=frame_center,prop_oscilloscope,three_quarter | cast_chen=frame_right,distance,profile_left
| mid: cast_watanabe=frame_left,cast_chen,toward_camera | cast_chen=frame_right,cast_watanabe,toward_camera
| end: cast_watanabe=frame_left,distance,profile_right | cast_chen=frame_center,prop_notebook,three_quarter
```

Parser behavior:
- Extract per-scene staging plans and attach to SceneNode.staging_plan
- Each beat (start/mid/end) maps cast_id → {screen_position, looking_at, facing_direction}
- frame enricher workers receive the staging_plan in their input and use the appropriate beat as baseline

---

## 2. Python Parser Specification

**Module:** `graph/cc_parser.py`

### 2.1 Public Interface

```python
def parse_cc_output(
    project_dir: Path,
    project_node: ProjectNode,
) -> NarrativeGraph:
    """Parse CC output files and build the complete narrative graph.

    Reads:
      - {project_dir}/creative_output/outline_skeleton.md
      - {project_dir}/creative_output/creative_output.md

    Returns a fully populated NarrativeGraph with all entities,
    frames, dialogue, states, and edges. Ready for frame enrichment.
    """
```

### 2.2 Parsing Pipeline

```
Step 1: parse_skeleton(skeleton_text) → entities dict
  ├── extract_cast_tags()      → list[CastNode]
  ├── extract_location_tags()  → list[LocationNode]  (with directions)
  ├── extract_prop_tags()      → list[PropNode]
  └── build_name_to_id_map()   → dict[str, str]  (display name → entity id)

Step 2: parse_creative_output(creative_text, name_map) → frames + dialogue
  ├── extract_scene_tags()           → list[SceneNode]
  ├── extract_scene_staging_tags()   → attach staging_plan to SceneNodes
  ├── extract_frame_markers()        → list[FrameNode]  (with sequence linking)
  ├── extract_dialogue()             → list[DialogueNode]
  └── build_base_cast_states()       → list[CastFrameState]

Step 3: wire_edges(graph) → list[GraphEdge]
  ├── FOLLOWS edges            (frame → frame sequential)
  ├── BELONGS_TO_SCENE edges   (frame → scene)
  ├── APPEARS_IN edges         (cast → frame)
  ├── AT_LOCATION edges        (frame → location)
  ├── DIALOGUE_SPANS edges     (dialogue → frame)
  ├── SPOKEN_BY edges          (dialogue → cast)
  ├── USES_PROP edges          (frame → prop, when props in scene)
  └── CONTINUITY_CHAIN edges   (frame → frame within same scene+location)

Step 4: validate(graph) → list[str]  (warnings/errors)
```

### 2.3 Regex Patterns

```python
# Tag line patterns
RE_CAST_TAG       = re.compile(r'^///CAST:\s*(.+)$', re.MULTILINE)
RE_LOCATION_TAG   = re.compile(r'^///LOCATION:\s*(.+)$', re.MULTILINE)
RE_LOCATION_DIR   = re.compile(r'^///LOCATION_DIR:\s*(.+)$', re.MULTILINE)
RE_PROP_TAG       = re.compile(r'^///PROP:\s*(.+)$', re.MULTILINE)
RE_SCENE_TAG      = re.compile(r'^///SCENE:\s*(.+)$', re.MULTILINE)
RE_SCENE_STAGING  = re.compile(r'^///SCENE_STAGING:\s*(.+)$', re.MULTILINE)
RE_DIALOGUE_TAG   = re.compile(r'^///DLG:\s*(.+)$', re.MULTILINE)

# Frame marker — lines starting with /// but NOT ///CAST, ///LOCATION, ///DLG, etc.
RE_FRAME_MARKER   = re.compile(
    r'^///\s+(?!CAST:|LOCATION:|LOCATION_DIR:|PROP:|SCENE:|SCENE_STAGING:|DLG:|ADDITION_JUSTIFICATION:)(.+)$',
    re.MULTILINE,
)

# Key=value pairs within a tag line (pipe-separated)
RE_KV_PAIR        = re.compile(r'(\w+)=([^|]+)')

# ENV tag parser — used when parsing env field from ///DLG tags
RE_ENV_FIELD      = re.compile(r'(\w+)=([^|,]+)')
```

### 2.4 Field Extraction Logic

**`_parse_tag_fields(tag_line: str) -> dict[str, str]`**

1. Split on ` | ` (pipe with surrounding spaces)
2. For each segment, match `key=value`
3. Strip whitespace from keys and values
4. Return dict of key → value

**`_parse_csv(value: str) -> list[str]`**

1. Split on `,`
2. Strip whitespace from each element
3. Filter empty strings

**`_parse_hair(value: str) -> tuple[str, str, str]`**

1. Split CSV: expects `length,style,color`
2. Return (hair_length, hair_style, hair_color)
3. Missing elements default to `""`

**`_resolve_cast_id(name: str, name_map: dict) -> str`**

1. Normalize: lowercase, strip whitespace
2. Lookup in name_map (case-insensitive)
3. If not found: generate `cast_{slugify(name)}` and warn

### 2.5 Frame Extraction Algorithm

```python
def extract_frame_markers(creative_text: str, scenes: list[SceneNode],
                          name_map: dict) -> tuple[list[FrameNode], list[CastFrameState]]:
    """
    Walk creative_output.md line by line.
    Track current_scene (updated on ///SCENE lines).
    On each /// frame marker:
      1. Parse marker fields (cast, cam, dlg, cast_states)
      2. Capture paragraph text until next /// or ///SCENE or EOF
      3. Build FrameNode with incremented sequence_index
      4. Build base CastFrameState for each cast member:
         - cast_id from name lookup
         - frame_id from current frame
         - active_state_tag from cast_states field or scene default
         - frame_role = SUBJECT if only 1 cast, BACKGROUND otherwise
         - All other fields: None/defaults (frame enricher fills them)
      5. Link previous_frame_id / next_frame_id
    """
```

### 2.6 Dialogue Extraction Algorithm

```python
def extract_dialogue(skeleton_text: str, creative_text: str, frames: list[FrameNode],
                     name_map: dict, creative_lines: list[str]) -> list[DialogueNode]:
    """
    Extract dialogue from ///DLG tags in the skeleton, resolving them to verbatim
    source text in creative_output.md.

    For each ///DLG tag:
      1. Parse tag fields: speaker, cast_id, src_lines, src_start, src_end, perf, env
      2. Use src_lines as primary anchor (exact line range)
      3. Fuzzy-match src_start and src_end within ±5 lines of src_lines as validation
      4. Extract verbatim text between anchors → raw_line
      5. Build DialogueNode:
         - dialogue_id: dlg_{NNN} (global counter)
         - speaker: from tag's speaker field
         - cast_id: from tag's cast_id field (or name_map lookup as fallback)
         - raw_line: verbatim source text extracted from creative_output.md
         - scene_id: resolved from frame marked 'dlg' containing this dialogue
         - start_frame / end_frame / primary_visual_frame: frame_id of enclosing ///dlg frame
         - performance_direction: parsed from tag's perf field (comma-separated)
         - ENV tag fields: parsed from tag's env field (CSV: location,distance,intensity,...)
      6. Log WARNING if src_lines anchor fails; continue pipeline (don't halt)
      7. Add dialogue_id to FrameNode.dialogue_ids
    """

    # Anchor extraction logic:
    def extract_by_src_lines(creative_lines, start_line, end_line):
        """Extract text from line range (1-indexed in tag, convert to 0-indexed)."""
        return '\n'.join(creative_lines[start_line-1:end_line])

    def validate_with_fuzzy_anchors(text, src_start, src_end, start_line, end_line, creative_lines):
        """Fuzzy-match src_start and src_end within ±5 lines of src_lines."""
        search_start = max(0, start_line - 6)
        search_end = min(len(creative_lines), end_line + 5)
        search_text = '\n'.join(creative_lines[search_start:search_end])
        # Check if src_start and src_end appear in search window (loose match)
        return src_start in search_text and src_end in search_text
```

### 2.7 Edge Wiring

All edges use `canonical_edge_id()` from `graph/schema.py`.

| Edge Type | Source → Target | Wiring Rule |
|-----------|----------------|-------------|
| `FOLLOWS` | `frame[N]` → `frame[N+1]` | Sequential frame order |
| `BELONGS_TO_SCENE` | `frame` → `scene` | Frame's `scene_id` |
| `APPEARS_IN` | `cast_id` → `frame_id` | Per CastFrameState in frame |
| `AT_LOCATION` | `frame_id` → `location_id` | Frame's `location_id` |
| `DIALOGUE_SPANS` | `dialogue_id` → `frame_id` | Per dialogue's start_frame..end_frame range |
| `SPOKEN_BY` | `dialogue_id` → `cast_id` | Dialogue's `cast_id` |
| `USES_PROP` | `frame_id` → `prop_id` | Props listed in the scene's `props_present` for frames in that scene |
| `CONTINUITY_CHAIN` | `frame[N]` → `frame[N+1]` | Same scene_id AND same location_id |

### 2.8 Validation Rules

Run after graph construction. Return list of warnings/errors.

| Check | Severity | Rule |
|-------|----------|------|
| Orphan cast | ERROR | Every `cast_id` in a frame's cast_states must exist in `graph.cast` |
| Orphan location | ERROR | Every frame's `location_id` must exist in `graph.locations` |
| Orphan prop | WARN | Every prop in `scene.props_present` should exist in `graph.props` |
| Missing cam | ERROR | Every FrameNode must have `background.camera_facing` set |
| Dialogue without dlg | WARN | Frame contains dialogue text but `is_dialogue` is False |
| dlg without dialogue | WARN | Frame has `is_dialogue=True` but no DialogueNode references it |
| Sequential gaps | ERROR | frame_order must be contiguous with no duplicate frame_ids |
| Scene continuity | WARN | Scene N exit should logically precede scene N+1 entry (logged, not enforced) |
| Empty frame | ERROR | Frame has no source_text |
| Direction reference | WARN | Frame's `camera_facing` direction should exist on the location's `LocationDirections` |

### 2.9 Provenance

All parser-created nodes get:
```python
Provenance(
    source_prose_chunk="",  # filled with source text where applicable
    generated_by="cc_parser",
    confidence=1.0,
    created_at=datetime.now(timezone.utc).isoformat(),
)
```

---

## 3. Frame Enricher Worker Form Specification

Each frame enricher worker receives a single frame and fills out a structured enrichment form. Workers run in parallel (one per frame). The active pipeline dispatches them through the local Grok reasoning runner.

### 3.1 Worker Input

```json
{
  "frame_id": "f_001",
  "sequence_index": 0,
  "source_text": "Rain streaks the glass in silver threads...",
  "scene_context": {
    "scene_id": "scene_01",
    "title": "The Arrival",
    "location_name": "Abandoned Greenhouse",
    "location_type": "interior",
    "time_of_day": "morning",
    "mood_keywords": ["tense", "isolated"],
    "pacing": "slow-burn"
  },
  "cast_in_frame": [
    {
      "cast_id": "cast_watanabe",
      "name": "Dr. Watanabe",
      "identity_summary": "50s male, slender build, medium skin, grey short cropped hair, wearing rumpled lab coat, wire-rimmed glasses",
      "active_state_tag": "base",
      "previous_frame_state": null
    }
  ],
  "location_directions": {
    "north": "Main entrance, heavy wooden doors",
    "east": "Reinforced windows facing antenna array",
    "south": "Equipment racks, oscilloscopes",
    "west": "Corridor to storage wing"
  },
  "props_in_scene": [
    {"prop_id": "prop_signal_pager", "name": "Signal Pager", "description": "..."}
  ],
  "previous_frame": {
    "frame_id": "f_000",
    "narrative_beat": "...",
    "cast_states_summary": "..."
  },
  "staging_anchor": {
    "screen_position": "frame_center",
    "looking_at": "prop_oscilloscope",
    "facing_direction": "three_quarter"
  },
  "is_dialogue": false,
  "dialogue_text": null
}
```

**Staging Anchor:** The `staging_anchor` is resolved from the scene's staging_plan (from the `///SCENE_STAGING` tag) based on the frame's position within the scene. Frames in the first third of the scene use the `start` beat, middle third use `mid`, final third use `end`. Workers MUST use this anchor as the baseline for `screen_position`, `looking_at`, and `facing_direction`. Override only with explicit justification for motivated character movement or action.

### 3.2 Worker Output — CastFrameState Enrichment

Per visible character in the frame:

| Field | Type | Required | Values / Rules |
|-------|------|----------|---------------|
| `screen_position` | str | **MANDATORY** | `frame_left`, `frame_center`, `frame_right`, `frame_left_third`, `frame_right_third` |
| `looking_at` | str | **MANDATORY** | Another `cast_id`, `prop_id`, `"distance"`, `"camera"`, or a location feature |
| `emotion` | str | YES | Compound term from `EMOTION_EXPRESSION_MAP` keys or novel compounds (e.g. `restrained_anger`, `quiet_determination`, `bitter_amusement`) |
| `emotion_intensity` | float | YES | 0.0–1.0. Drives expression resolution bands in prompt_assembler |
| `posture` | Posture | YES | One of: standing, sitting, crouching, kneeling, lying, walking, running, leaning, hunched |
| `facing_direction` | str | YES | `toward_camera`, `away`, `profile_left`, `profile_right`, `three_quarter` |
| `action` | str | YES | Verb-first description (e.g. `crosses_to_window`, `grips_door_frame`, `adjusts_dials`) |
| `frame_role` | CastFrameRole | YES | `subject`, `object`, `background`, `partial`, `referenced` |
| `props_held` | list[str] | NO | prop_ids currently in hand |
| `props_interacted` | list[str] | NO | prop_ids touched/used this frame |
| `clothing_state` | str | NO | `base`, `damaged`, `wet`, `changed`, `removed`. Only if changed from identity |
| `hair_state` | str | NO | Only if changed from identity (`disheveled`, `wet`, `tied_back`) |
| `injury` | str | NO | Only if new or changed (`wounded_left_arm`, `bruised_face`) |
| `spatial_position` | str | NO | `center_frame`, `foreground_left`, `background`, etc. Legacy field — `screen_position` is authoritative |
| `eye_direction` | str | NO | `downward`, `at_other_character`, `distant` |

**Delta tracking:** Worker reports which fields changed from the previous frame's state as `delta_fields: list[str]`.

### 3.3 Worker Output — FrameComposition

| Field | Type | Required | Values |
|-------|------|----------|--------|
| `shot` | str | YES | Based on prose context and scene mood. e.g. `medium_shot`, `close_up`, `wide`, `extreme_close_up`, `medium_close_up` |
| `angle` | str | YES | `eye_level`, `low`, `high`, `dutch`, `birds_eye`, `worms_eye` |
| `movement` | str | YES | Based on prose context and scene mood. `static`, `push`, `pull`, `pan_left`, `pan_right`, `tracking`, `dolly`, `crane`, `drift`, `subtle_drift` |
| `focus` | str | YES | `deep`, `shallow`, `rack` |
| `placement` | str | NO | Rule of thirds position |
| `grouping` | str | NO | Multi-character arrangement |
| `blocking` | str | NO | Stage direction |
| `transition` | str | NO | Transition from previous frame |
| `rule` | str | NO | Composition rule applied |

### 3.5 Worker Output — FrameEnvironment

| Field | Type | Required |
|-------|------|----------|
| `lighting.direction` | LightingDirection | YES |
| `lighting.quality` | LightingQuality | YES |
| `lighting.color_temp` | str | NO |
| `lighting.motivated_source` | str | NO |
| `lighting.shadow_behavior` | str | NO |
| `atmosphere.particles` | str | NO |
| `atmosphere.weather` | str | NO |
| `atmosphere.ambient_motion` | str | NO |
| `atmosphere.temperature_feel` | str | NO |
| `materials_present` | list[str] | NO |
| `foreground_objects` | list[str] | NO |
| `midground_detail` | str | NO |
| `background_depth` | str | NO |

### 3.6 Worker Output — FrameBackground

| Field | Type | Required |
|-------|------|----------|
| `visible_description` | str | REMOVED |
| `camera_facing` | str | INHERITED | Already set by parser from `cam:` tag |
| `background_action` | str | NO |
| `background_sound` | str | NO |
| `background_music` | str | NO |
| `depth_layers` | list[str] | NO |

**Note on `visible_description` removal:** This field was redundant with `location.directions[camera_facing].description`. `api.py` already falls back to location direction descriptions when this field is empty (api.py:489-499). Removing it eliminates duplicate work in worker prompts and reduces ambiguity between location-sourced and worker-authored descriptions.

### 3.7 Worker Output — FrameDirecting

| Field | Type | Required |
|-------|------|----------|
| `dramatic_purpose` | str | YES |
| `beat_turn` | str | YES |
| `pov_owner` | str | YES |
| `camera_motivation` | str | YES |
| `viewer_knowledge_delta` | str | NO |
| `power_dynamic` | str | NO |
| `tension_source` | str | NO |
| `movement_motivation` | str | NO |
| `movement_path` | str | NO |
| `reaction_target` | str | NO |
| `background_life` | str | NO |

### 3.8 Worker Output — Additional Frame Fields

| Field | Type | Required |
|-------|------|----------|
| `action_summary` | str | YES | Concise verb-first physical action for video prompt |
| `emotional_arc` | EmotionalArc | YES | rising, falling, static, peak, release (relative to previous frame) |
| `visual_flow_element` | str | YES | One of: motion, dialogue, reaction, action, weight, establishment |

### 3.9 Worker Output Format

Workers return structured JSON. The `composition` fields (shot, angle, movement, focus) are filled based on prose context and scene mood. The Grok tagger may later override these with tag-appropriate values (see Section 6).

```json
{
  "frame_id": "f_001",
  "action_summary": "Watanabe hunches over oscilloscope, fingers adjusting dials",
  "emotional_arc": "static",
  "visual_flow_element": "establishment",
  "composition": { "shot": "medium_close_up", "angle": "eye_level", "movement": "static", "focus": "shallow" },
  "environment": {
    "lighting": { "direction": "side_left", "quality": "harsh", "color_temp": "cool_green", "motivated_source": "oscilloscope_screens" },
    "atmosphere": { "particles": "dust_motes", "ambient_motion": "screen_flicker" }
  },
  "background": {
    "visible_description": "Equipment racks with blinking LEDs, oscilloscope traces in green",
    "background_action": "Data streams scrolling on secondary monitors"
  },
  "directing": {
    "dramatic_purpose": "introduction",
    "beat_turn": "Character established in environment of obsession",
    "pov_owner": "audience",
    "camera_motivation": "Close framing emphasizes isolation and focus"
  },
  "cast_states": [
    {
      "cast_id": "cast_watanabe",
      "screen_position": "frame_center",
      "looking_at": "prop_oscilloscope",
      "emotion": "contemplative",
      "emotion_intensity": 0.6,
      "posture": "hunched",
      "facing_direction": "three_quarter",
      "action": "adjusts_oscilloscope_dials",
      "frame_role": "subject",
      "delta_fields": []
    }
  ]
}
```

---

## 4. Morpheus Reduction

### 4.1 Removed Agents

| Agent | Reason | Replacement |
|-------|--------|-------------|
| Agent 1 — Entity Seeder | CC skeleton already defines all entities in parsable format | `cc_parser.py` |
| Agent 2 — Frame Parser | CC `///` markers already define frame boundaries, cast, location | `cc_parser.py` |
| Agent 3 — Dialogue Wirer | Dialogue is embedded in CC prose with ENV tags | `cc_parser.py` |
| Agent 4 — Compositor | Composition, environment, directing, cast state enrichment | frame enricher workers |

### 4.2 Retained Agent: Continuity Validator (Agent 5 — Reduced)

**Role:** Validation-only pass. Does NOT build new data. Audits and flags issues.

**Input:** Complete graph after frame enrichment.

**Checks performed:**

1. **Spatial consistency across cuts:**
   - If frame N has cast_A at `frame_left` and frame N+1 (same scene) has cast_A at `frame_right` without an intervening action, flag as `SPATIAL_JUMP`.
   - Characters should not teleport between screen positions without motivation.

2. **Cast state delta continuity:**
   - `clothing_state` cannot change from `base` to `damaged` without an intervening frame showing the cause.
   - `injury` cannot appear without a preceding action frame.
   - `props_held` items must be picked up before held, put down before gone.
   - `active_state_tag` changes must align with narrative beats (e.g., `base` → `wet` requires a rain/water event).

3. **Dialogue coverage verification:**
   - Every frame with `is_dialogue=True` must have at least one `DialogueNode` referencing it.
   - Every `DialogueNode` must have its `primary_visual_frame` exist in `graph.frames`.
   - `SPOKEN_BY` edges must exist for every dialogue node.
   - `DIALOGUE_SPANS` edges must cover the full start_frame..end_frame range.

4. **Edge completeness:**
   - Every frame must have a `BELONGS_TO_SCENE` edge.
   - Every frame (except first) must have a `FOLLOWS` edge.
   - Every cast member visible in a frame must have an `APPEARS_IN` edge.
   - Every frame must have an `AT_LOCATION` edge.

5. **Graph data completeness:**
   - Every FrameNode must have: `composition.shot`, `composition.angle`, `composition.movement`, `action_summary`, `environment.lighting.direction`.
   - Every CastFrameState must have: `screen_position`, `looking_at`, `emotion`, `posture`, `facing_direction`.

6. **Staging plan compliance:**
   - For each frame, resolve which staging beat applies (start/mid/end based on frame position in scene)
   - If CastFrameState.screen_position contradicts the staging anchor, flag as STAGING_VIOLATION (WARN)
   - If CastFrameState.looking_at contradicts the staging anchor, flag as STAGING_VIOLATION (WARN)
   - Tolerance: workers may drift from anchors for motivated action (e.g. character crossing room)

**Output:** Validation report with `{severity, frame_id, check_name, message}` entries. Severities: ERROR (blocks pipeline), WARN (logged, pipeline continues).

**Implementation:** Can be Python-only (deterministic rule checks) or paired with a targeted reasoning audit for nuanced spatial or narrative consistency that rule checks miss. Recommended: Python rule checks first, then a targeted audit only if rule checks find 0 ERRORs and the project is `televised` or `feature` size.

---

## 5. Pipeline Phase Changes

### 5.1 Retired Pipeline (Historical Reference)

```
Phase 0: Server startup
Phase 1: CC writes skeleton + prose (legacy provider wording)
Phase 2: Morpheus swarm (retired multi-agent path)
  Agent 1: Entity Seeder
  Agent 2: Frame Parser
  Agent 3: Dialogue Wirer
  Agent 4: Compositor
  Agent 5: Continuity Wirer
Phase 3: Asset generation (images, locations, storyboards)
Phase 4: Video direction validation
Phase 5: Video clip generation
Phase 6: Export (ffmpeg assembly)
```

### 5.2 New Pipeline

```
Phase 0: Server startup (unchanged)
Phase 1: CC writes skeleton + prose (unchanged)
Phase 2: Deterministic graph + frame enrichment
  Step 2a: Python parser (cc_parser.py)             — deterministic, <5 seconds
  Step 2b: Parallel frame enricher workers            — per-frame enrichment, ~2-10 seconds per frame
  Step 2b.5: Grok frame tagging (grok_tagger.py)    — per-frame tag assignment, ~10 concurrent
  Step 2c: Continuity validation                     — deterministic Python checks + optional graph auditor
  Step 2d: Prompt assembly + materialization         — existing deterministic code (prompt_assembler.py + materializer.py)
Phase 3: Asset generation (unchanged)
Phase 4: Video direction validation (unchanged)
Phase 5: Video clip generation (unchanged)
Phase 6: Export (unchanged)
```

### 5.3 Step 2a — Python Parser

```python
# In run_pipeline.py, replaces the Morpheus Agent 1/2/3 spawns

from graph.cc_parser import parse_cc_output
from graph.schema import ProjectNode
from graph.store import GraphStore

def run_phase_2a(project_dir: Path, project_node: ProjectNode) -> NarrativeGraph:
    graph = parse_cc_output(project_dir, project_node)
    store = GraphStore(project_dir / "graph")
    store.save(graph)
    warnings = graph.build_log  # parser appends warnings here
    for w in warnings:
        log(f"  PARSER WARNING: {w}")
    return graph
```

**Timing:** Deterministic string parsing. Expected <5 seconds for any project size.

### 5.4 Step 2b — Parallel Frame Enricher Workers

```python
def run_phase_2b(graph: NarrativeGraph, project_dir: Path) -> NarrativeGraph:
    """Dispatch parallel frame enricher workers for per-frame enrichment."""
    worker_inputs = build_frame_enricher_inputs(graph)  # one per frame

    # Parallel Grok reasoning calls — all frames in parallel
    results = frame_enricher_batch_enrich(worker_inputs, max_concurrent=20)

    # Apply enrichments to graph
    for result in results:
        apply_frame_enrichment(graph, result)

    store = GraphStore(project_dir / "graph")
    store.save(graph)
    return graph
```

**frame enricher worker dispatch options:**
1. **Concurrent local Grok calls** — `asyncio.gather()` with semaphore. This is the active runtime path.
2. **Targeted re-enrichment** — only frames flagged by continuity or contract checks are re-run.

**Cost estimate:** depends on the active Grok reasoning model and project size; budget should be measured from live usage rather than the retired Haiku estimate.

### 5.4.5 Step 2b.5 — Grok Frame Tagging

```python
def run_phase_2b5(graph: NarrativeGraph, project_dir: Path) -> NarrativeGraph:
    '''Grok frame tagging — assign cinematic tags post-enrichment.'''
    from graph.grok_tagger import tag_all_frames
    import asyncio
    results = asyncio.run(tag_all_frames(project_dir))
    log(f'Tagged {results["tagged"]} frames, {results["failed"]} failed')
    for family, count in results.get('tag_distribution', {}).items():
        log(f'  {family}: {count}')
    return graph
```

**Timing:** Grok calls are fast (~0.5s each). With 10 concurrent, a 100-frame project completes in ~5 seconds.

### 5.5 Step 2c — Continuity Validation

```python
def run_phase_2c(graph: NarrativeGraph, project_dir: Path) -> list[dict]:
    """Run continuity validation. Returns list of issues."""
    from graph.continuity_validator import validate_continuity
    issues = validate_continuity(graph)

    errors = [i for i in issues if i["severity"] == "ERROR"]
    if errors:
        log(f"  CONTINUITY: {len(errors)} errors found")
        # Attempt auto-fix for known patterns, or flag for manual review
    return issues
```

### 5.5.5 Step 2c — Self-Recovering Validation Loop

Phase 2c runs a **validate → auto-fix → re-enrich → re-validate** loop (max 2 passes):

1. **Pass 1 — auto-fix:** `validate_continuity(graph, fix=True, project_dir=...)` runs all checks
   and immediately attempts deterministic fixes for:
   - `MISSING_FOLLOWS_EDGE` — create FOLLOWS edge from previous frame in sequence
   - `MISSING_BELONGS_TO_SCENE` — infer scene from adjacent frames; create BELONGS_TO_SCENE edge
   - `MISSING_AT_LOCATION` — infer location from scene; create AT_LOCATION edge
   - `MISSING_APPEARS_IN` — create APPEARS_IN edge from cast_id to frame
   - `MISSING_SPOKEN_BY_EDGE` — create SPOKEN_BY edge from dialogue node to cast_id
   - `MISSING_DIALOGUE_SPANS_EDGE` — create DIALOGUE_SPANS edges for all missing frames

   Fixed issues are tagged `auto_fixed=True`. Graph is saved to disk after all fixes.

2. **Issues that cannot be auto-fixed** are tagged `needs_re_enrichment=True` with a `what`
   field describing the specific correction required:
   - `INCOMPLETE_FRAME_DATA` / `INCOMPLETE_CAST_STATE` — missing required field (need frame-enricher fill-in)
   - `SPATIAL_JUMP` / `STAGING_VIOLATION` — positional inconsistency (need frame-enricher correction)

3. **Re-enrichment:** `re_enrich_frames(graph, issues)` dispatches targeted reasoning calls for
   each frame with `needs_re_enrichment=True`. A `CORRECTION REQUIRED` block is appended to
   the system prompt so the frame enricher knows exactly what to fix. Results are applied with
   `apply_frame_enrichment()` and the graph is saved.

4. **Pass 2 — re-validate:** The loop runs `validate_continuity(fix=True)` again to confirm
   fixes landed.

5. **Never halt permanently.** After `MAX_FIX_PASSES = 2`, any remaining unresolved errors are
   logged as warnings and the pipeline proceeds to Step 2d. Downstream quality gates
   (Phase 3/4/5) catch visual problems from unresolved graph issues.

```
validate_continuity(fix=True)
  ├── auto_fixed=True        → edge created, saved to disk
  └── needs_re_enrichment=True
        ↓
      re_enrich_frames()     → frame enricher corrects specific frames
        ↓
      apply_frame_enrichment() → graph updated
        ↓
      validate_continuity(fix=True)   ← pass 2
        └── still failing → log_warn, proceed (never halt)
```

**Implementation:** `run_pipeline.py:phase_2_morpheus()` Step 2c section.
**Validator:** `graph/continuity_validator.py:validate_continuity(fix, project_dir)`
**Re-enricher:** `graph/frame_enricher.py:re_enrich_frames()`

---

### 5.6 Step 2d — Prompt Assembly + Materialization

Unchanged. Uses existing `prompt_assembler.py` and `materializer.py`:
- `assemble_composite_prompt()` for cast reference images
- `assemble_location_prompt()` for location reference images
- `assemble_image_prompt()` for per-frame image prompts
- `assemble_video_prompt()` for per-frame video prompts

---

## 6. Cinematic Frame Tag System (Grok Tagger)

### 6.1 Overview

Frame composition tags are assigned AFTER the graph is fully built (entities seeded, frames parsed, frame enrichment complete). A dedicated Grok agent reads each frame's complete node data and assigns the single most relevant tag from the Cinematic Frame Tag Taxonomy.

The tag's textual definition from the taxonomy becomes part of the frame's generation prompt — the definition IS the composition directive sent to image and video generation.

**Taxonomy source:** `/home/nikoles16/Downloads/cinematic-frame-tag-taxonomy.md` (deployed as Grok system prompt)

### 6.2 Tag Format

```
[Family Prefix][Number].[Variation Letter] +[movement_modifier]
```

Families:
- **D** — Dialogue (subject-dominant, editorial pairing)
- **E** — Establishment & Environment (environment-dominant, spatial)
- **R** — Revealer (information-controlled, discovery-driven)
- **A** — Action (kinetic, body-in-motion)
- **C** — Cast / Portrait (subject-focused, non-dialogue, emotional)
- **T** — Transitional (pacing, breathing room, scene bridges)
- **S** — Stylistic / Psychological (angle/lens/technique serving mood)
- **M** — Music Video (performance and rhythm-driven)

Movement modifiers: +static, +push, +pull, +track, +pan, +tilt, +crane, +handheld, +steadicam, +whip, +dolly-zoom, +drone, +drift

Examples: D01.a +push, E01.c +tilt, R01.b +static, A02.a +steadicam, C01.b +push

### 6.3 Schema Changes

Replace the FormulaTag enum in graph/schema.py:

```python
# OLD — DELETE
class FormulaTag(str, Enum):
    F01 = 'F01'
    ...
    F18 = 'F18'

# NEW — REPLACE WITH
class CinematicTag(BaseModel):
    tag: str = ''           # e.g. 'D01.a'
    modifier: str = ''      # e.g. '+push'
    full_tag: str = ''      # e.g. 'D01.a +push'
    definition: str = ''    # The textual composition definition from taxonomy
    family: str = ''        # D, E, R, A, C, T, S, M
    editorial_function: str = ''  # From taxonomy: 'Standard emotional coverage. Neutral power dynamic.'
    ai_prompt_language: str = ''  # From taxonomy: 'Close-up of single person speaking...'
    lens_guidance: str = ''       # From taxonomy: '50-85mm'
    dof_guidance: str = ''        # From taxonomy: 'Shallow (f/1.8-2.8)'
```

On FrameNode, replace:
```python
# OLD
formula_tag: Optional[FormulaTag] = None

# NEW
cinematic_tag: CinematicTag = Field(default_factory=CinematicTag)
```

### 6.4 Grok Tagger Implementation

New module: `graph/grok_tagger.py`

```python
async def tag_all_frames(
    project_dir: Path,
    *,
    api_key: str = '',
    concurrency: int = 10,
) -> dict:
    '''Tag all frames in the graph using Grok with the cinematic taxonomy.

    For each frame:
      1. Build a context payload from the frame node (cast states, dialogue,
         environment, action_summary, scene mood, previous/next frame beats)
      2. Send to Grok with the taxonomy as system prompt
      3. Parse the single-tag response (e.g. 'D01.a +push')
      4. Look up the tag definition from the taxonomy
      5. Populate FrameNode.cinematic_tag with tag + definition + ai_prompt_language
      6. Save to graph

    Returns summary: {tagged: N, failed: N, tag_distribution: {family: count}}
    '''
```

Grok model: grok-4-1-fast-non-reasoning (same as frame refiner)
System prompt: The full cinematic-frame-tag-taxonomy.md
User message per frame: Structured frame node data (cast count, dialogue flag, action summary, emotion, scene mood, previous/next context)
Expected output: Single tag string only (e.g. 'D01.a +push')
Temperature: 0.1 (deterministic selection)

### 6.5 Tag Definition Lookup

The tagger must parse the taxonomy document and build a lookup table:

```python
TAG_DEFINITIONS: dict[str, dict] = {
    'D01.a': {
        'name': 'Clean Single — Eye Level',
        'composition': 'CU or MCU, eye-level, shallow DOF, subject centered or rule-of-thirds',
        'editorial_function': 'Standard emotional coverage. Neutral power dynamic. The baseline.',
        'ai_prompt_language': 'Close-up of single person speaking, isolated framing, no other people visible, shallow depth of field, eye-level',
        'lens': '50-85mm',
        'dof': 'Shallow (f/1.8-2.8)',
        'family': 'D',
    },
    # ... all tags parsed from taxonomy
}
```

When the Grok response is parsed, look up the tag in TAG_DEFINITIONS and populate the CinematicTag model fields.

### 6.6 Prompt Assembler Integration

In graph/prompt_assembler.py, replace the old FORMULA_SHOT and FORMULA_VIDEO lookup tables. Instead:

1. Read frame.cinematic_tag.ai_prompt_language — inject directly into image prompt as the shot/composition directive
2. Read frame.cinematic_tag.definition — use as the composition intent block
3. Read frame.cinematic_tag.modifier — translate to camera movement directive for video prompts
4. Read frame.cinematic_tag.dof_guidance — inform depth of field language
5. Read frame.cinematic_tag.lens_guidance — inform focal length language (for style, not literal camera specs)

The tag's textual definition REPLACES the old formula-based shot description blocks.

### 6.7 Pipeline Integration

New Phase 2 step between frame enrichment (2b) and continuity validation (2c):

```
Phase 2:
  Step 2a: Python parser (cc_parser.py)
  Step 2b: Parallel frame enricher workers (frame_enricher.py)
  Step 2b.5: Grok frame tagging (grok_tagger.py)  ← NEW
  Step 2c: Continuity validation
  Step 2d: Prompt assembly + materialization
```

The tagger runs after frame enrichment so it has the complete frame data (cast states, composition, environment, directing) to make informed tag selections.

---

## 7. CC Prompt Changes Required

The Creative Coordinator prompt (`agent_prompts/creative_coordinator.md`) must be updated to:

### 7.1 Phase 1 — Skeleton Output Changes

Add to the skeleton output spec:

1. **Entity rosters use `///TAG` format** — replace current free-text character/location/prop rosters with the parsable `///CAST`, `///LOCATION`, `///LOCATION_DIR`, `///PROP` tags defined in Section 1.
2. **All entities referenced by ID** — every scene spec, beat, and dialogue gist must reference entities by their tag-assigned `id` field.
3. **Scene specs use `///SCENE` tags** — replace current free-text scene headers with parsable `///SCENE` tags.
4. **`cast_states` field on scene tags** — CC must declare which state variant each character enters the scene with.
5. **Dialogue excerpt pointers use `///DLG` tags** — for each dialogue block, output a `///DLG` tag with source anchors (src_lines, src_start, src_end) instead of copying the dialogue text into the skeleton. See Section 1.6 for format and Section 2.6 for parser behavior.

### 7.2 Phase 2 — Prose Output Changes

1. **Remove `dur:{seconds}` from `///` frame markers.** Duration is computed downstream.
2. **Frame markers no longer include tag, shot, angle, or movement fields.** These are assigned post-graph by the Grok tagger (Section 6).
3. **Add `cast_states:{name=tag,...}` to frame markers** when a character's state changes mid-scene.
4. **All other frame marker fields unchanged** — `cast:`, `cam:`, `dlg` remain as-is.

### 7.3 What Does NOT Change

- Dialogue format (screenplay-style with ENV tags) — unchanged
- Prose style and quality requirements — unchanged
- Frame marking rules (one `///` = one paragraph = one frame) — unchanged
- Stickiness level enforcement — unchanged
- Output size constraints — unchanged
- Phase 3 assembly pass — unchanged

---

## 8. File Manifest

| File | Status | Description |
|------|--------|-------------|
| `graph/cc_parser.py` | NEW | Python parser — skeleton + prose → graph |
| `graph/frame_enricher.py` | NEW | frame enricher worker dispatch + result application |
| `graph/grok_tagger.py` | NEW | Grok-based cinematic frame tagger |
| `graph/continuity_validator.py` | NEW | Deterministic continuity rule checks |
| `graph/schema.py` | MODIFIED | Add StagingBeat model + SceneNode.staging_plan field. Replace FormulaTag enum with CinematicTag model |
| `graph/prompt_assembler.py` | MODIFIED | Replace FORMULA_SHOT/FORMULA_VIDEO lookups with CinematicTag-based prompt injection |
| `cinematic-frame-tag-taxonomy.md` | REFERENCE | Grok system prompt for frame tagging (source: /home/nikoles16/Downloads/) |
| `graph/api.py` | UNCHANGED | Graph query/mutation API unchanged |
| `graph/store.py` | UNCHANGED | Persistence unchanged |
| `agent_prompts/creative_coordinator.md` | MODIFIED | Add `///TAG` format spec to skeleton output |
| `agent_prompts/morpheus_1_entity_seeder.md` | REMOVED | Replaced by cc_parser.py |
| `agent_prompts/morpheus_2_frame_parser.md` | REMOVED | Replaced by cc_parser.py |
| `agent_prompts/morpheus_3_dialogue_wirer.md` | REMOVED | Replaced by cc_parser.py |
| `agent_prompts/morpheus_4_compositor.md` | REMOVED | Replaced by frame_enricher.py |
| `agent_prompts/morpheus_5_continuity_wirer.md` | MODIFIED | Reduced to validation-only |
| `run_pipeline.py` | MODIFIED | Phase 2 rewritten as Steps 2a-2d |

---

## 9. Migration Strategy

1. **Implement cc_parser.py** — write parser, test against existing CC output from previous pipeline runs
2. **Implement frame_enricher.py** — build worker input/output, test with mock Grok responses
3. **Implement continuity_validator.py** — port relevant checks from Agent 5 prompt to Python
4. **Update CC prompt** — add `///TAG` format to skeleton spec, remove `dur:` from frame markers
5. **Update run_pipeline.py** — replace Morpheus agent spawns with Steps 2a-2d
6. **Validate** — run full pipeline on test project, compare graph output quality to Morpheus-built graph
7. **Remove dead code** — delete Morpheus Agent 1-4 prompts and related skills

**Rollback plan:** Keep Morpheus prompts in `agent_prompts/archived_intents/` until 3 successful pipeline runs confirm the new path produces equivalent or better graph quality.

---

## 10. Data Source Map

| Field | Source File | Parser Step | Graph Target | Downstream Consumer |
|-------|------------|-------------|--------------|-------------------|
| Entity rosters (cast, location, prop) | outline_skeleton.md | parse_skeleton | CastNode, LocationNode, PropNode | prompt_assembler |
| Scene headers + staging | outline_skeleton.md | parse_skeleton | SceneNode + staging_plan | frame_enricher, continuity_validator |
| ///DLG excerpt pointers | outline_skeleton.md (tags) + creative_output.md (text) | parse_skeleton + resolve | DialogueNode.raw_line | prompt_assembler |
| Frame markers (cast, cam, dlg, cast_states) | creative_output.md | parse_creative_output | FrameNode + base CastFrameState | frame_enricher, prompt_assembler |
| Frame prose (source_text) | creative_output.md | parse_creative_output | FrameNode.source_text | frame_enricher |
| Enriched cast states (screen_position, looking_at, emotion, etc.) | frame enricher workers | frame_enricher | CastFrameState | prompt_assembler |
| Composition, environment, directing | frame enricher workers | frame_enricher | FrameNode sub-objects | prompt_assembler |
| Location directions (background description) | outline_skeleton.md | parse_skeleton | LocationNode.directions | api.py fallback -> prompt_assembler |
| Cinematic frame tag + definition | Grok tagger | grok_tagger.py (Step 2b.5) | FrameNode.cinematic_tag | prompt_assembler |

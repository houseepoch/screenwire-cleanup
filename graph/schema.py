"""
ScreenWire Lucid Graph — Schema Definition
============================================

The complete data contract for the narrative graph.
Every node type, edge type, and field that Morpheus produces
and downstream agents consume.

Organized by node domain. Each model is a graph node type.
Edges are defined separately with typed source/target constraints.

This schema is the union of:
  - Lucid Lines context engine (entity fields, world synthesis,
    composition engine, glyph mappings, context chains, tension scoring)
  - ScreenWire pipeline (cinematic tags, cast/location/prop profiles,
    dialogue format, visual analysis, composition prompts, video prompts)

Design decisions:
  - CastFrameState / PropFrameState / LocationFrameState are ABSOLUTE
    SNAPSHOTS, not deltas. Morpheus copies previous frame state, mutates
    changed fields, saves full snapshot. Storage is cheap; backward walks
    through a delta chain are not.
  - DialogueNode has temporal span (start_frame / end_frame) to support
    J-cuts, L-cuts, and dialogue that spans multiple visual frames.
  - Every node and edge carries provenance (source_prose_chunk,
    generated_by, confidence) for error tracing and targeted re-runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS — Controlled vocabularies
# ═══════════════════════════════════════════════════════════════════════════════


class EntityType(str, Enum):
    CAST = "cast"
    LOCATION = "location"
    PROP = "prop"
    FACTION = "faction"


class NarrativeRole(str, Enum):
    PROTAGONIST = "protagonist"
    ANTAGONIST = "antagonist"
    MENTOR = "mentor"
    ALLY = "ally"
    CATALYST = "catalyst"
    SUPPORTING = "supporting"
    BACKGROUND = "background"


class CastFrameRole(str, Enum):
    """How a cast member participates in a specific frame."""
    SUBJECT = "subject"          # Camera's focus, grammatical agent
    OBJECT = "object"            # Action directed at them
    BACKGROUND = "background"    # Visible but not focal
    PARTIAL = "partial"          # Partially visible (OTS back of head, hand)
    REFERENCED = "referenced"    # Mentioned in dialogue but not physically present


class PropFrameRole(str, Enum):
    """How a prop participates in a specific frame."""
    ACTIVE_HELD = "active_held"      # Character gripping/using it
    ACTIVE_FOCAL = "active_focal"    # F11 — the frame is about this object
    PASSIVE_PRESENT = "passive_present"  # Visible but not interacted with
    TRANSFERRED = "transferred"      # Changes hands in this frame
    INTRODUCED = "introduced"        # First appearance in the story


class EdgeType(str, Enum):
    # Frame linkage
    APPEARS_IN = "appears_in"            # entity → frame
    AT_LOCATION = "at_location"          # frame → location variant
    USES_PROP = "uses_prop"              # frame → prop (with role)
    DIALOGUE_SPANS = "dialogue_spans"    # dialogue → frame (temporal span)
    SPOKEN_BY = "spoken_by"              # dialogue → cast
    FOLLOWS = "follows"                  # frame → frame (sequence)
    CONTINUITY_CHAIN = "continuity_chain"  # frame → frame (same scene+loc)

    # Entity relationships
    CO_OCCURRENCE = "co_occurrence"
    DIALOGUE_EXCHANGE = "dialogue_exchange"
    POSSESSION = "possession"            # temporal: cast → prop
    CONTAINMENT = "containment"          # location → entity
    AUTHORITY = "authority"
    CONFLICT = "conflict"
    KINSHIP = "kinship"
    ALLIANCE = "alliance"
    AVERSION = "aversion"

    # Hierarchy
    CHILD_OF = "child_of"                # location variant → parent
    BELONGS_TO_SCENE = "belongs_to_scene"  # frame → scene
    SCENE_IN_ACT = "scene_in_act"        # scene → act


class LightingDirection(str, Enum):
    TOP = "top"
    SIDE_LEFT = "side_left"
    SIDE_RIGHT = "side_right"
    SIDE_RAKING = "side_raking"
    BACK = "back"
    RIM = "rim"
    UNDER = "under"
    AMBIENT = "ambient"
    SPLIT = "split"


class LightingQuality(str, Enum):
    HARSH = "harsh"
    SOFT = "soft"
    DIFFUSED = "diffused"
    DAPPLED = "dappled"
    VOLUMETRIC = "volumetric"
    FLICKERING = "flickering"


class TimeOfDay(str, Enum):
    DAWN = "dawn"
    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    DUSK = "dusk"
    NIGHT = "night"


class Posture(str, Enum):
    STANDING = "standing"
    SITTING = "sitting"
    CROUCHING = "crouching"
    KNEELING = "kneeling"
    LYING = "lying"
    WALKING = "walking"
    RUNNING = "running"
    LEANING = "leaning"
    HUNCHED = "hunched"


class EmotionalArc(str, Enum):
    """Direction of tension/emotion relative to previous frame."""
    RISING = "rising"
    FALLING = "falling"
    STATIC = "static"
    PEAK = "peak"
    RELEASE = "release"


class CastStateTag(str, Enum):
    """Canonical state tags for character appearance variants.
    Each tag maps to a reference image variant generated via sw_edit_image."""
    BASE = "base"
    WET = "wet"
    SWEATING = "sweating"
    WOUNDED = "wounded"
    BLOODIED = "bloodied"
    DIRTY = "dirty"
    TORN_CLOTHING = "torn_clothing"
    FORMAL = "formal"
    CASUAL = "casual"
    DISGUISED = "disguised"
    NIGHT = "night"
    EXHAUSTED = "exhausted"


# ═══════════════════════════════════════════════════════════════════════════════
# PROVENANCE — Attached to every node and edge for error tracing
# ═══════════════════════════════════════════════════════════════════════════════


class Provenance(BaseModel):
    """Tracks where a piece of graph data came from.
    Enables targeted re-runs when Morpheus detects errors without
    burning down the graph."""

    source_prose_chunk: str = ""            # Exact text that generated this fact
    chunk_index: Optional[int] = None       # Which processing chunk produced this
    generated_by: str = ""                  # Agent/worker that seeded it ("entity_discovery", "beat_wiring", etc.)
    confidence: float = 1.0                 # 0.0-1.0 — how sure the agent was
    created_at: Optional[str] = None        # ISO-8601 timestamp
    last_modified_at: Optional[str] = None  # Updated on mutation
    last_modified_by: Optional[str] = None  # Which agent last touched this
    supersedes: Optional[str] = None        # ID of the node/edge this replaced (for corrections)
    run_id: Optional[str] = None            # Pipeline run that last touched this datum


class CastStateVariant(BaseModel):
    """A named visual state variant for a cast member."""
    state_tag: str = ""
    description: str = ""
    derived_from: str = "base"
    image_path: Optional[str] = None
    trigger_frame: Optional[str] = None
    active_through: Optional[str] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_pose_state() -> "PoseState":
    return PoseState(pose="standing_neutral")


def _coerce_optional_text(value: Any) -> Any:
    """Collapse list-like textual values into a single optional string."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (list, tuple, set)):
        parts: list[str] = []
        for item in value:
            normalized = _coerce_optional_text(item)
            if isinstance(normalized, str) and normalized:
                parts.append(normalized)
        if not parts:
            return None
        return ", ".join(dict.fromkeys(parts))
    if isinstance(value, (int, float, bool)):
        return str(value)
    return value


class PoseState(BaseModel):
    """Canonical pose lock for a character at a specific frame boundary."""

    pose: str = Field(
        ...,
        description=(
            "Canonical pose name, e.g. 'sitting_straight_chair', "
            "'lying_supine_ground', 'crouching_ready', 'standing_profile_left'"
        ),
    )
    modifiers: list[str] = Field(
        default_factory=list,
        description="Pose-specific modifiers such as hands, eyeline, or screen position locks.",
    )
    frame_id: Optional[str] = None
    last_seen_frame: Optional[int] = None
    confidence: float = Field(
        default=1.0,
        description="0.0-1.0 how confidently the validator resolved this pose.",
    )


class CharacterSheet(BaseModel):
    """Versioned cast-bible sheet for one character."""

    character_id: str
    name: str
    description: str = ""
    current_pose: PoseState = Field(default_factory=_default_pose_state)
    pose_history: list[PoseState] = Field(default_factory=list)
    frame_poses: dict[str, PoseState] = Field(default_factory=dict)
    appearance_notes: dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def _frame_sequence(frame_id: str | None) -> int:
        try:
            return int(str(frame_id or "").split("_")[-1])
        except (TypeError, ValueError):
            return -1

    def pose_for_frame(self, frame_id: str) -> Optional[PoseState]:
        exact = self.frame_poses.get(frame_id)
        if exact is not None:
            return exact

        target_seq = self._frame_sequence(frame_id)
        if target_seq < 0:
            return self.current_pose

        candidates: list[PoseState] = []
        for pose in self.frame_poses.values():
            if self._frame_sequence(pose.frame_id) <= target_seq:
                candidates.append(pose)
        for pose in self.pose_history:
            if self._frame_sequence(pose.frame_id) <= target_seq:
                candidates.append(pose)
        current_seq = self._frame_sequence(self.current_pose.frame_id)
        if current_seq >= 0 and current_seq <= target_seq:
            candidates.append(self.current_pose)

        if not candidates:
            return None
        return max(candidates, key=lambda pose: self._frame_sequence(pose.frame_id))


class CastBible(BaseModel):
    """Versioned sidecar store for cast pose locks and reusable appearance anchors."""

    characters: dict[str, CharacterSheet] = Field(default_factory=dict)
    locations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    version: str = Field(default_factory=_utc_now_iso)
    run_id: Optional[str] = None
    sequence_id: Optional[str] = None

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, data: str) -> "CastBible":
        return cls.model_validate_json(data)


# ═══════════════════════════════════════════════════════════════════════════════
# NODE TYPES — Graph node models
# ═══════════════════════════════════════════════════════════════════════════════


# ─── Project / World ─────────────────────────────────────────────────────────

class WorldContext(BaseModel):
    """Global world parameters. One per project.
    Sourced from Lucid Lines 25-field world synthesis."""

    # Calendar / Era
    era: Optional[str] = None
    current_period: Optional[str] = None
    calendar_system: Optional[str] = None

    # Culture / Customs
    culture: Optional[str] = None
    clothing_norms: Optional[str] = None
    traditions: Optional[str] = None
    social_norms: Optional[str] = None

    # Civics
    governance: Optional[str] = None
    laws: Optional[str] = None
    social_structure: Optional[str] = None

    # Geography
    biome: Optional[str] = None
    regions: Optional[str] = None
    climate: Optional[str] = None

    # Technology
    technology_level: Optional[str] = None
    technology_items: Optional[str] = None
    infrastructure: Optional[str] = None

    # Conflict
    central_conflict: Optional[str] = None
    conflict_factions: Optional[str] = None
    conflict_stakes: Optional[str] = None

    # Tone
    genre: Optional[str] = None
    mood: Optional[str] = None
    themes: Optional[str] = None

    # Physiology
    species: Optional[str] = None
    species_traits: Optional[str] = None

    # Architecture / Visual
    architecture_style: Optional[str] = None
    color_palette: Optional[str] = None
    lighting_style: Optional[str] = None
    flora_desc: Optional[str] = None

    # Principles
    principles: Optional[str] = None

    provenance: Provenance = Field(default_factory=Provenance)


class VisualDirection(BaseModel):
    """Project-level visual direction.
    Sourced from ScreenWire scene_coordinator visual_analysis."""

    media_style: str = "live_clear"
    style_direction: list[str] = Field(default_factory=list)
    genre_influence: list[str] = Field(default_factory=list)
    mood_palette: list[str] = Field(default_factory=list)
    visual_tone_per_act: dict[str, str] = Field(default_factory=dict)
    style_prefix: str = ""  # Resolved media style prompt prefix

    provenance: Provenance = Field(default_factory=Provenance)


class ProjectNode(BaseModel):
    """Root node of the graph. One per project.
    Contains ONLY onboarding-supplied details. World context and visual
    direction live as separate top-level graph nodes."""

    project_id: str
    title: str = ""
    pipeline: str = "story_upload"  # story_upload | pitch_idea | music_video
    creative_freedom: str = "balanced"
    creative_freedom_permission: str = ""
    creative_freedom_failure_modes: str = ""
    dialogue_policy: str = ""
    frame_budget: Optional[int] = None
    output_size: str = "auto"
    output_size_label: str = "Auto"
    frame_range: list[int] = Field(default_factory=list)
    scene_range: list[int] = Field(default_factory=list)
    media_style: str = "live_clear"
    media_style_prefix: str = ""
    aspect_ratio: str = "16:9"
    style: list[str] = Field(default_factory=list)
    genre: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    extra_details: str = ""
    source_files: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)


# ─── Cast ────────────────────────────────────────────────────────────────────

class CastIdentity(BaseModel):
    """Static identity fields — physical appearance, baseline wardrobe.
    Sourced from Lucid Lines EntityFields + ScreenWire cast profiles."""

    # Physical (from Lucid Lines EntityFields)
    age_descriptor: Optional[str] = None    # "30s", "early 20s", "50-year-old"
    gender: Optional[str] = None
    ethnicity: Optional[str] = None
    build: Optional[str] = None             # tall, slender, athletic, heavy, petite
    skin: Optional[str] = None              # pale, light, medium, dark, weathered
    hair_length: Optional[str] = None
    hair_style: Optional[str] = None
    hair_color: Optional[str] = None

    # Wardrobe baseline
    clothing: list[str] = Field(default_factory=list)
    clothing_style: Optional[str] = None
    clothing_fabric: Optional[str] = None
    clothing_fit: Optional[str] = None
    footwear: Optional[str] = None
    accessories: list[str] = Field(default_factory=list)

    # Full text description (for image generation prompts)
    physical_description: str = ""
    wardrobe_description: str = ""


class CastVoice(BaseModel):
    """Voice and delivery profile for generated dialogue.
    Used as a performance hint for native-audio video generation."""

    voice_description: str = ""
    quality_prefix: str = ""
    tone: Optional[str] = None
    pitch: Optional[str] = None
    accent: Optional[str] = None
    delivery_style: Optional[str] = None
    tempo: Optional[str] = None
    emotional_range: Optional[str] = None
    vocal_style: Optional[str] = None       # from Lucid Lines EntityFields


class CastNode(BaseModel):
    """A character in the story."""

    cast_id: str
    name: str
    display_name: str = ""
    source_name: str = ""
    entity_type: EntityType = EntityType.CAST

    identity: CastIdentity = Field(default_factory=CastIdentity)
    voice: CastVoice = Field(default_factory=CastVoice)

    # Narrative
    personality: str = ""
    role: NarrativeRole = NarrativeRole.SUPPORTING
    arc_summary: str = ""
    voice_notes: str = ""                   # From creative output — prose description of voice
    relationships: list[dict] = Field(default_factory=list)

    # Graph metrics (accumulated as graph builds)
    importance_score: float = 0.0
    dialogue_line_count: int = 0
    scene_count: int = 0
    first_appearance: Optional[str] = None  # scene_id
    scenes_present: list[str] = Field(default_factory=list)

    # Generation tracking
    composite_path: Optional[str] = None
    composite_status: Optional[str] = None

    # State variant tracking
    state_variants: dict[str, CastStateVariant] = Field(default_factory=dict)

    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def _sync_names(self) -> "CastNode":
        if not self.display_name:
            self.display_name = self.name or self.source_name
        if not self.source_name:
            self.source_name = self.name or self.display_name
        if not self.name:
            self.name = self.display_name or self.source_name
        return self


# ─── Cast State (per-frame ABSOLUTE SNAPSHOT) ────────────────────────────────

class CastFrameState(BaseModel):
    """ABSOLUTE SNAPSHOT of a cast member at a specific frame.

    NOT a delta. Morpheus copies the previous frame's state for this
    cast member, mutates only the fields that changed in the new prose
    chunk, and saves the complete snapshot. This means querying the
    state at any frame is a single read — no backward traversal.

    Storage cost: ~200 bytes per cast member per frame. For a 300-frame
    project with 5 cast members = ~300KB. Trivial."""

    cast_id: str
    frame_id: str

    # Role in this frame
    frame_role: CastFrameRole = CastFrameRole.BACKGROUND

    # Physical state at this moment
    action: Optional[str] = None            # "crosses_to_window", "grips_door_frame"
    posture: Optional[Posture] = None
    facing_direction: Optional[str] = None  # "toward_camera", "away", "profile_left"
    spatial_position: Optional[str] = None  # "center_frame", "foreground_left", "background"
    eye_direction: Optional[str] = None     # "downward", "at_other_character", "distant"
    screen_position: Optional[str] = None       # "frame_left", "frame_center", "frame_right", "frame_left_third", "frame_right_third"
    looking_at: Optional[str] = None            # cast_id, prop_id, location feature, "camera", or "distance"

    # Emotional state at this moment
    emotion: Optional[str] = None           # "contemplative", "fearful", "controlled_fury"
    emotion_intensity: Optional[float] = None  # 0.0 - 1.0

    # Clothing/wardrobe state (absolute — what they're wearing NOW)
    clothing_state: str = "base"            # "base", "damaged", "wet", "changed", "removed"
    clothing_current: list[str] = Field(default_factory=list)  # What they're actually wearing
    # If "base", clothing_current can be empty (inherit from CastIdentity).
    # If anything else, clothing_current is the authoritative wardrobe.
    injury: Optional[str] = None            # "wounded_left_arm", "bruised_face", None = uninjured
    hair_state: Optional[str] = None        # "disheveled", "wet", "tied_back" — if changed from identity

    # Active state tag — determines which reference image variant to use
    active_state_tag: str = "base"

    # Props (absolute — what they hold RIGHT NOW)
    props_held: list[str] = Field(default_factory=list)         # prop_ids currently in hand
    props_interacted: list[str] = Field(default_factory=list)   # prop_ids touched/used this frame

    # What changed from previous frame (for Morpheus's diff log — NOT for querying state)
    delta_fields: list[str] = Field(default_factory=list)
    # e.g. ["emotion", "props_held"] — which fields Morpheus mutated from previous snapshot

    provenance: Provenance = Field(default_factory=Provenance)


# ─── Prop State (per-frame ABSOLUTE SNAPSHOT) ────────────────────────────────

class PropFrameState(BaseModel):
    """ABSOLUTE SNAPSHOT of a prop at a specific frame.

    Tracks prop mutations: a sword that shatters in F_020 shows
    condition='shattered' from F_020 onward. The prompt builder for
    F_021 pulls 'shattered sword', not 'sword'.

    Only created for frames where the prop is present or referenced.
    If a prop doesn't appear in a frame, no PropFrameState exists
    for it at that frame — query the most recent one."""

    prop_id: str
    frame_id: str

    # Current physical state
    condition: str = "intact"               # "intact", "damaged", "broken", "shattered",
                                            # "burning", "bloodied", "wet", "opened", "empty"
    condition_detail: Optional[str] = None  # Freeform: "blade snapped at hilt, only handle remains"

    # Spatial
    holder_cast_id: Optional[str] = None    # Who's holding it (None = on surface/ground)
    spatial_position: Optional[str] = None  # "on_table", "in_hand", "on_ground", "mounted_on_wall"
    visibility: str = "visible"             # "visible", "partially_hidden", "concealed", "off_frame"

    # Role in this frame
    frame_role: PropFrameRole = PropFrameRole.PASSIVE_PRESENT

    # What changed
    delta_fields: list[str] = Field(default_factory=list)

    provenance: Provenance = Field(default_factory=Provenance)


# ─── Location State (per-frame ABSOLUTE SNAPSHOT) ────────────────────────────

class LocationFrameState(BaseModel):
    """ABSOLUTE SNAPSHOT of a location at a specific frame.

    Tracks location mutations: a room that catches fire in F_030,
    a window that shatters in F_015, blood on the floor from F_022.
    The prompt builder pulls the current state, not the pristine
    description from LocationNode.

    Only created when the location state diverges from its base
    description. If no LocationFrameState exists for a frame,
    the location is in its default state per LocationNode."""

    location_id: str
    frame_id: str

    # Condition overrides (layered on top of LocationNode.description)
    condition_modifiers: list[str] = Field(default_factory=list)
    # e.g. ["fire_spreading_east_wall", "broken_window_north", "blood_on_floor"]
    # These are ADDITIVE — the base description still applies, plus these modifiers.

    # Atmosphere overrides (replaces LocationNode atmosphere for this frame onward)
    atmosphere_override: Optional[str] = None  # "smoke-filled, visibility dropping, orange glow"

    # Lighting shift (if the location's lighting changed — fire, power outage, dawn breaking)
    lighting_override: Optional[str] = None

    # Destruction/damage level
    damage_level: str = "none"              # "none", "minor", "moderate", "severe", "destroyed"

    # What changed
    delta_fields: list[str] = Field(default_factory=list)

    provenance: Provenance = Field(default_factory=Provenance)


# ─── Location ───────────────────────────────────────────────────────────────

class LocationDirectionView(BaseModel):
    """A single cardinal direction view from within a location."""
    description: str = ""           # What is visible facing this direction
    key_features: list[str] = Field(default_factory=list)  # Doors, windows, furniture, etc.
    depth_description: str = ""     # Foreground → midground → background layers


class LocationDirections(BaseModel):
    """Cardinal direction views from within a location.
    Each direction is a textual spatial anchor used by downstream frame prompts."""
    north: Optional[LocationDirectionView] = None
    south: Optional[LocationDirectionView] = None
    east: Optional[LocationDirectionView] = None
    west: Optional[LocationDirectionView] = None
    exterior: Optional[LocationDirectionView] = None

    @model_validator(mode="before")
    @classmethod
    def _upgrade_string_directions(cls, data: Any) -> Any:
        """Backward compat: auto-upgrade plain string directions to LocationDirectionView."""
        if isinstance(data, dict):
            for key in ("north", "south", "east", "west", "exterior"):
                val = data.get(key)
                if isinstance(val, str):
                    data[key] = {"description": val} if val else None
        return data


class LocationNode(BaseModel):
    """A location in the story. May have child variants (INT/EXT, sub-areas).
    This is the BASE description — pristine, as first established.
    Per-frame mutations are tracked in LocationFrameState."""

    location_id: str
    name: str
    entity_type: EntityType = EntityType.LOCATION
    parent_location_id: Optional[str] = None  # For variants: child → parent

    # Description (base state)
    description: str = ""
    atmosphere: str = ""
    narrative_purpose: str = ""

    # Visual
    material_palette: list[str] = Field(default_factory=list)  # wood, stone, metal — texture anchors
    architecture_keywords: list[str] = Field(default_factory=list)
    flora: Optional[str] = None
    location_type: Optional[str] = "exterior"  # "interior" or "exterior" — drives location grid template selection

    # Cardinal direction views
    directions: LocationDirections = Field(default_factory=LocationDirections)

    # Per-scene mood
    mood_per_scene: dict[str, str] = Field(default_factory=dict)
    time_of_day_variants: list[str] = Field(default_factory=list)

    # Usage tracking
    scenes_used: list[str] = Field(default_factory=list)

    # Generation tracking
    primary_image_path: Optional[str] = None
    image_status: Optional[str] = None

    provenance: Provenance = Field(default_factory=Provenance)


# ─── Prop ────────────────────────────────────────────────────────────────────

class PropNode(BaseModel):
    """A narratively significant object. BASE description — pristine state.
    Per-frame mutations tracked in PropFrameState."""

    prop_id: str
    name: str
    entity_type: EntityType = EntityType.PROP

    description: str = ""                   # Base physical description (intact state)
    narrative_significance: str = ""
    material_context: list[str] = Field(default_factory=list)  # from Lucid Lines EntityFields

    # Ownership tracking (temporal)
    associated_cast: list[str] = Field(default_factory=list)   # cast_ids
    scenes_used: list[str] = Field(default_factory=list)
    introduction_frame: Optional[str] = None  # First appearance

    # Generation tracking
    image_path: Optional[str] = None

    provenance: Provenance = Field(default_factory=Provenance)


# ─── Scene Staging ───────────────────────────────────────────────────────────

class StagingBeat(BaseModel):
    """Spatial staging plan for a single beat (start/mid/end) within a scene.
    Populated by the CC parser from ///SCENE_STAGING tags.
    Haiku workers interpolate between beats based on frame position."""

    cast_positions: dict[str, str] = {}     # cast_id -> screen_position
    cast_looking_at: dict[str, str] = {}    # cast_id -> looking_at target
    cast_facing: dict[str, str] = {}        # cast_id -> facing_direction


# ─── Scene ───────────────────────────────────────────────────────────────────

class SceneNode(BaseModel):
    """A scene — a contiguous sequence of frames at one location/time."""

    scene_id: str
    scene_number: int
    title: str = ""
    location_id: Optional[str] = None
    time_of_day: Optional[TimeOfDay] = None
    explicit_time: Optional[str] = None     # "3:42 AM"
    int_ext: str = ""                       # "INT", "EXT", "INT/EXT"

    # Narrative
    scene_heading: str = ""
    mood_keywords: list[str] = Field(default_factory=list)
    pacing: Optional[str] = None            # "slow-burn", "tense", "frenetic", "measured"
    emotional_arc: Optional[str] = None     # Overall arc of the scene

    # Cast / entity presence
    cast_present: list[str] = Field(default_factory=list)      # cast_ids
    props_present: list[str] = Field(default_factory=list)     # prop_ids

    # Continuity
    entry_conditions: Optional[str] = None  # From CC skeleton — character states entering
    exit_conditions: Optional[str] = None   # Character states exiting
    continuity_notes: list[str] = Field(default_factory=list)

    # Tension (from Lucid Lines tension scoring)
    tension_score: Optional[float] = None
    tension_signals: Optional[dict] = None

    # Frame range
    frame_ids: list[str] = Field(default_factory=list)
    frame_count: int = 0

    # Scene staging (from CC ///SCENE_STAGING tags)
    staging_plan: dict[str, StagingBeat] = Field(default_factory=dict)  # 'start' | 'mid' | 'end' -> StagingBeat

    provenance: Provenance = Field(default_factory=Provenance)


# ─── Frame (Beat) ───────────────────────────────────────────────────────────

class FrameLighting(BaseModel):
    """Lighting state for a specific frame."""

    direction: Optional[LightingDirection] = None
    quality: Optional[LightingQuality] = None
    color_temp: Optional[str] = None        # "warm_golden", "cool_blue", "neutral"
    motivated_source: Optional[str] = None  # "window_lattice", "candle", "overhead_fluorescent"
    shadow_behavior: Optional[str] = None   # "striped_parallel", "deep_pools", "soft_diffused"


class FrameAtmosphere(BaseModel):
    """Atmospheric state for a specific frame."""

    particles: Optional[str] = None         # "dust_motes", "pollen", "smoke", "rain"
    weather: Optional[str] = None           # "rain", "snow", "fog", "clear"
    ambient_motion: Optional[str] = None    # "curtain_sway", "candle_flicker", "leaves_rustling"
    temperature_feel: Optional[str] = None  # "humid", "cold", "stifling"

    @field_validator("particles", "weather", "ambient_motion", "temperature_feel", mode="before")
    @classmethod
    def _normalize_text_fields(cls, value: Any) -> Any:
        return _coerce_optional_text(value)


class FrameEnvironment(BaseModel):
    """Complete environmental state for a frame."""

    lighting: FrameLighting = Field(default_factory=FrameLighting)
    atmosphere: FrameAtmosphere = Field(default_factory=FrameAtmosphere)
    materials_present: list[str] = Field(default_factory=list)  # Texture detail source
    foreground_objects: list[str] = Field(default_factory=list)
    midground_detail: Optional[str] = None
    background_depth: Optional[str] = None  # "courtyard_through_window", "jungle_canopy"


class FrameBackground(BaseModel):
    """Structured background information for a frame."""
    visible_description: str = ""
    camera_facing: Optional[str] = None         # "north", "south", "east", "west"
    background_action: Optional[str] = None     # "Servants clearing dishes in the distance"
    background_sound: Optional[str] = None      # "Distant market chatter"
    background_music: Optional[str] = None      # "Faint erhu melody" (diegetic only)
    depth_layers: list[str] = Field(default_factory=list)


class FrameDirecting(BaseModel):
    """Narrative and directorial intent for a frame.

    These fields explain why the shot exists, whose experience it aligns
    with, and how camera language should support the beat.
    """

    dramatic_purpose: Optional[str] = None      # reveal, reaction, intimidation, intimacy, concealment
    beat_turn: Optional[str] = None             # What changes by the end of the frame
    pov_owner: Optional[str] = None             # cast_id, prop_id, location feature, or "audience"
    viewer_knowledge_delta: Optional[str] = None  # New information the viewer learns here
    power_dynamic: Optional[str] = None         # Who holds advantage and how
    tension_source: Optional[str] = None        # What is creating pressure in the moment
    camera_motivation: Optional[str] = None     # Why this framing or movement is being used
    movement_motivation: Optional[str] = None   # Why the subject/background/camera should feel active
    movement_path: Optional[str] = None         # Start-to-end movement or blocking path
    reaction_target: Optional[str] = None       # The line/action this frame answers
    background_life: Optional[str] = None       # Supporting environmental life behind the subject


class FrameComposition(BaseModel):
    """Camera and composition data for a frame.
    Sourced from Lucid Lines CompositionEngine + ScreenWire formula-to-lens mapping."""

    shot: Optional[str] = None              # "medium_shot", "close_up", "wide"
    angle: Optional[str] = None             # "eye_level", "high", "low", "dutch"
    placement: Optional[str] = None         # Rule of thirds position
    grouping: Optional[str] = None          # How multiple characters are arranged
    blocking: Optional[str] = None          # Stage direction
    movement: Optional[str] = None          # "static", "slow_push", "tracking"
    focus: Optional[str] = None             # "character", "environment", "prop"
    transition: Optional[str] = None        # Transition from previous frame
    rule: Optional[str] = None              # Composition rule applied


class CinematicTag(BaseModel):
    """Cinematic frame tag from the Grok tagging pass.
    Assigned post-graph from the Cinematic Frame Tag Taxonomy (D/E/R/A/C/T/S/M families)."""

    tag: str = ''                   # e.g. 'D01.a'
    modifier: str = ''              # e.g. '+push'
    full_tag: str = ''              # e.g. 'D01.a +push'
    definition: str = ''            # Textual composition definition from taxonomy
    family: str = ''                # D, E, R, A, C, T, S, M
    editorial_function: str = ''
    ai_prompt_language: str = ''
    lens_guidance: str = ''
    dof_guidance: str = ''


class FrameNode(BaseModel):
    """A single visual beat — one shot, one composition, one image.
    The atomic unit of the visual pipeline."""

    frame_id: str
    scene_id: str
    sequence_index: int                     # Global ordering across all scenes
    cinematic_tag: CinematicTag = Field(default_factory=CinematicTag)

    # Prose origin (preserved from decomposition)
    narrative_beat: str = ""                 # Environment-first visual description
    source_text: str = ""                   # Original prose excerpt this frame comes from

    # Entity wiring — keyed registries on NarrativeGraph are canonical
    location_id: Optional[str] = None       # Specific location variant for this frame

    # Time of day — explicit per frame for consistency
    # Inherited from scene by default, but overridable for time-passage sequences
    time_of_day: Optional[TimeOfDay] = None

    # Dialogue — which dialogue nodes are AUDIBLE during this frame
    # (not 1:1 — a dialogue line may span multiple frames via J/L cuts)
    is_dialogue: bool = False
    dialogue_ids: list[str] = Field(default_factory=list)

    # Environment at this frame
    environment: FrameEnvironment = Field(default_factory=FrameEnvironment)

    # Composition
    composition: FrameComposition = Field(default_factory=FrameComposition)

    # Background data (structured, beyond FrameEnvironment)
    background: FrameBackground = Field(default_factory=FrameBackground)

    # Directorial intent
    directing: FrameDirecting = Field(default_factory=FrameDirecting)

    # Narrative flow
    emotional_arc: Optional[EmotionalArc] = None  # Relative to previous frame
    tension_delta: Optional[float] = None
    visual_flow_element: Optional[str] = None  # motion|dialogue|reaction|action|weight|establishment

    # Video direction — Morpheus sets these for downstream video generation
    suggested_duration: Optional[int] = None  # Clip duration in seconds (min 2, scales with content). None = use formula heuristic
    action_summary: str = ""                  # Concise physical action for video prompt (e.g., "Mei turns from railing, kimono catching wind")
    video_optimized_prompt_block: str = ""   # Dense cinematic summary that preserves action+blocking+environment within Grok limits

    # Continuity
    continuity_chain: bool = False          # Same scene+location as previous frame
    previous_frame_id: Optional[str] = None
    next_frame_id: Optional[str] = None

    # Generation tracking
    composed_image_path: Optional[str] = None
    video_path: Optional[str] = None
    status: str = "pending"
    refs_used: Optional[list[str]] = None  # Ref paths sent during frame generation (storyboard first)

    provenance: Provenance = Field(default_factory=Provenance)


# ─── Dialogue ────────────────────────────────────────────────────────────────

class DialogueNode(BaseModel):
    """A single dialogue line with full performance metadata.

    TEMPORAL SPAN: A dialogue line may span multiple visual frames.
    The graph tracks where the spoken line begins, remains audible, and
    becomes the primary on-camera sync frame so prompt assembly can size
    native-audio video clips correctly.

    - start_frame: first frame where this audio is heard (may be a J-cut —
      audio starts before we see the speaker)
    - end_frame: last frame where this audio is heard (may be an L-cut —
      audio continues over a reaction shot)
    - primary_visual_frame: the frame where the speaker is the camera's
      subject (the 'sync' frame for lip movement)
    - reaction_frame_ids: frames showing other characters' reactions while
      this dialogue plays underneath"""

    dialogue_id: str
    scene_id: str
    order: int                              # Global sequential order

    # Speaker
    speaker: str                            # Character name
    cast_id: str

    # Temporal span (which visual frames this audio plays over)
    start_frame: str                        # Frame where audio begins
    end_frame: str                          # Frame where audio ends
    primary_visual_frame: str               # Frame where speaker is on-camera (lip sync target)
    reaction_frame_ids: list[str] = Field(default_factory=list)  # Frames showing listener reactions

    # Content
    line: str = ""                          # Full line WITH bracket directions
    raw_line: str = ""                      # Clean text, no brackets
    performance_direction: str = ""         # Extracted from brackets (before |)
    env_tags: str = ""                      # Extracted from brackets (after ENV:)

    # ENV tag breakdown (parsed from env_tags for direct graph queries)
    env_location: Optional[str] = None      # outdoor, indoor, jungle, etc.
    env_distance: Optional[str] = None      # intimate, close, medium, far
    env_medium: Optional[str] = None        # radio, comms, phone, muffled
    env_intensity: Optional[str] = None     # whisper, quiet, normal, loud, shouting
    env_atmosphere: list[str] = Field(default_factory=list)  # wind, rain, static

    provenance: Provenance = Field(default_factory=Provenance)


# ─── Shot Packet ────────────────────────────────────────────────────────────

class ShotNeighborBeat(BaseModel):
    """Previous/next frame snapshot used when assembling continuity-aware prompts."""

    frame_id: Optional[str] = None
    narrative_beat: str = ""
    action_summary: str = ""
    formula_tag: Optional[str] = None
    scene_id: Optional[str] = None
    is_dialogue: bool = False


class ShotAudioTurn(BaseModel):
    """A single audible dialogue turn during the frame."""

    dialogue_id: str = ""
    cast_id: str = ""
    speaker: str = ""
    line: str = ""
    performance_direction: str = ""
    env_intensity: Optional[str] = None
    env_distance: Optional[str] = None
    env_medium: Optional[str] = None
    env_atmosphere: list[str] = Field(default_factory=list)


class ShotAudioBeat(BaseModel):
    """Audio payload for prompt assembly.

    This does not imply external TTS or lip-sync assets; it is the native-audio
    context passed into the video prompt.
    """

    dialogue_present: bool = False
    turns: list[ShotAudioTurn] = Field(default_factory=list)
    ambient_layers: list[str] = Field(default_factory=list)
    background_music: Optional[str] = None


class ShotIntent(BaseModel):
    """The directorial intent the downstream generators should preserve."""

    formula_tag: Optional[str] = None
    shot: Optional[str] = None
    angle: Optional[str] = None
    movement: Optional[str] = None
    focus: Optional[str] = None
    dramatic_purpose: Optional[str] = None
    beat_turn: Optional[str] = None
    pov_owner: Optional[str] = None
    viewer_knowledge_delta: Optional[str] = None
    power_dynamic: Optional[str] = None
    tension_source: Optional[str] = None
    camera_motivation: Optional[str] = None
    movement_motivation: Optional[str] = None
    movement_path: Optional[str] = None
    reaction_target: Optional[str] = None


class ShotPacket(BaseModel):
    """Canonical deterministic prompt packet for a frame.

    Downstream prompt assembly reads this packet instead of re-deriving intent
    ad hoc from scattered graph fields.
    """

    frame_id: str
    scene_id: str
    sequence_index: int
    location_id: Optional[str] = None
    subject_count: int = 0
    visible_cast_ids: list[str] = Field(default_factory=list)
    visible_prop_ids: list[str] = Field(default_factory=list)
    previous_beat: Optional[ShotNeighborBeat] = None
    current_beat: str = ""
    video_optimized_prompt_block: str = ""
    next_beat: Optional[ShotNeighborBeat] = None
    continuity_deltas: list[str] = Field(default_factory=list)
    cast_invariants: list[str] = Field(default_factory=list)
    prop_invariants: list[str] = Field(default_factory=list)
    location_invariants: list[str] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    background: list[str] = Field(default_factory=list)
    shot_intent: ShotIntent = Field(default_factory=ShotIntent)
    audio: ShotAudioBeat = Field(default_factory=ShotAudioBeat)


# ─── Storyboard Grid ─────────────────────────────────────────────────────

class ShotMatchGroup(BaseModel):
    """Frames within a storyboard grid sharing the same shot setup.
    Populated post-generation for visual consistency enforcement."""
    group_id: str                            # "smg_grid_01_00"
    frame_ids: list[str] = Field(default_factory=list)
    match_basis: str = ""                    # e.g. "F01_medium_eye_level"
    confidence: float = 0.0


class StoryboardGrid(BaseModel):
    """Sequential guidance batch of up to 6 frames.

    Used for continuity planning only. Final frames are rendered separately.
    """
    grid_id: str                             # "grid_01"
    frame_ids: list[str] = Field(default_factory=list)  # ordered, max 6
    frame_count: int = 0
    rows: int = 3
    cols: int = 3

    # Context (informational, not grouping keys)
    scene_ids: list[str] = Field(default_factory=list)  # scenes touched by this grid
    break_reason: Optional[str] = None       # "full" | "scene_break" | "large_shift" | "end"

    # Entity presence
    cast_present: list[str] = Field(default_factory=list)
    props_present: list[str] = Field(default_factory=list)

    # Cascading chain
    previous_grid_id: Optional[str] = None
    next_grid_id: Optional[str] = None

    # Storyboard generation
    storyboard_prompt_path: Optional[str] = None
    composite_image_path: Optional[str] = None    # full grid composite
    cell_image_dir: Optional[str] = None          # dir with {frame_id}.png cells
    storyboard_status: str = "pending"             # pending | generated | archived
    storyboard_history: list[str] = Field(default_factory=list)

    # Cell-to-frame mapping (cell index -> frame_id)
    cell_map: dict[int, str] = Field(default_factory=dict)  # {0: "f_001", 1: "f_002", ...}

    # Shot matching (post-generation)
    shot_match_groups: list[ShotMatchGroup] = Field(default_factory=list)
    shot_matching_status: str = "pending"    # pending | matched

    provenance: Provenance = Field(default_factory=Provenance)


# ═══════════════════════════════════════════════════════════════════════════════
# EDGES — Typed relationships between nodes
# ═══════════════════════════════════════════════════════════════════════════════


def canonical_edge_id(
    source_id: str,
    edge_type: "EdgeType | str",
    target_id: str,
) -> str:
    """Return the single canonical identifier for an edge."""
    edge_type_value = edge_type.value if isinstance(edge_type, EdgeType) else str(edge_type)
    return f"{source_id}__{edge_type_value}__{target_id}"


class GraphEdge(BaseModel):
    """A typed, weighted edge between two nodes."""

    edge_id: Optional[str] = None           # For targeted correction/replacement
    source_id: str
    target_id: str
    edge_type: EdgeType
    weight: float = 1.0
    evidence: list[str] = Field(default_factory=list)

    # Temporal scope (for edges that change over time)
    start_frame: Optional[str] = None       # When this edge becomes active
    end_frame: Optional[str] = None         # When this edge ends (None = still active)

    # Metadata
    metadata: dict = Field(default_factory=dict)

    provenance: Provenance = Field(default_factory=Provenance)


# ═══════════════════════════════════════════════════════════════════════════════
# THE GRAPH — Top-level container
# ═══════════════════════════════════════════════════════════════════════════════


class NarrativeGraph(BaseModel):
    """The complete narrative graph for a project.
    Morpheus builds this incrementally. Downstream agents query it."""

    # Root — onboarding details only
    project: ProjectNode

    # World & Visual — standalone top-level nodes (seeded before cast)
    world: WorldContext = Field(default_factory=WorldContext)
    visual: VisualDirection = Field(default_factory=VisualDirection)

    # Entity registries (base identity — static or slowly evolving)
    cast: dict[str, CastNode] = Field(default_factory=dict)
    locations: dict[str, LocationNode] = Field(default_factory=dict)
    props: dict[str, PropNode] = Field(default_factory=dict)

    # Story structure
    scenes: dict[str, SceneNode] = Field(default_factory=dict)
    frames: dict[str, FrameNode] = Field(default_factory=dict)
    dialogue: dict[str, DialogueNode] = Field(default_factory=dict)

    # Storyboard grids (guidance-only sequential batches of up to 6 frames)
    storyboard_grids: dict[str, StoryboardGrid] = Field(default_factory=dict)

    # Per-frame state snapshots (keyed by "{entity_id}@{frame_id}")
    cast_frame_states: dict[str, CastFrameState] = Field(default_factory=dict)
    prop_frame_states: dict[str, PropFrameState] = Field(default_factory=dict)
    location_frame_states: dict[str, LocationFrameState] = Field(default_factory=dict)

    # Relationships
    edges: list[GraphEdge] = Field(default_factory=list)

    # Ordered sequences (for linear traversal)
    frame_order: list[str] = Field(default_factory=list)       # frame_ids in sequence
    scene_order: list[str] = Field(default_factory=list)       # scene_ids in sequence
    dialogue_order: list[str] = Field(default_factory=list)    # dialogue_ids in sequence

    # ─── Graph metadata ──────────────────────────────────────────────────

    # Completeness tracking — Morpheus uses this to know what's seeded
    seeded_domains: dict[str, bool] = Field(default_factory=lambda: {
        "world_context": False,
        "visual_direction": False,
        "cast_identity": False,
        "cast_voice": False,
        "locations": False,
        "props": False,
        "scenes": False,
        "frames": False,
        "dialogue": False,
        "storyboard_grids": False,
        "cast_frame_states": False,
        "prop_frame_states": False,
        "location_frame_states": False,
        "frame_environments": False,
        "frame_compositions": False,
        "edges_relationships": False,
        "edges_continuity": False,
    })

    # Per-frame completeness (Morpheus tracks which frames are fully wired)
    frame_completeness: dict[str, dict[str, bool]] = Field(default_factory=dict)
    # Example: {"f_014": {"cast_wired": True, "location_resolved": True,
    #           "props_tracked": True, "environment": True, "composition": True,
    #           "dialogue_spans": True}}

    # Build stats
    total_tokens_used: int = 0
    build_log: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_chained_frame_groups(cls, data: Any) -> Any:
        """Migrate legacy chained_frame_groups data to storyboard_grids."""
        if not isinstance(data, dict):
            return data
        cfg = data.get("chained_frame_groups")
        if cfg and "storyboard_grids" not in data:
            grids: dict[str, dict] = {}
            for chain_id, chain_data in cfg.items():
                if isinstance(chain_data, dict):
                    cd = chain_data
                else:
                    cd = chain_data.model_dump() if hasattr(chain_data, "model_dump") else dict(chain_data)
                frame_ids = cd.get("frame_ids", [])
                n = len(frame_ids)
                if n <= 1:
                    rows, cols = 1, 1
                elif n <= 2:
                    rows, cols = 1, 2
                elif n <= 4:
                    rows, cols = 2, 2
                elif n <= 6:
                    rows, cols = 2, 3
                else:
                    rows, cols = 2, 3
                grid_id = chain_id.replace("chain_", "grid_")
                grids[grid_id] = {
                    "grid_id": grid_id,
                    "frame_ids": frame_ids,
                    "frame_count": cd.get("frame_count", n),
                    "rows": rows,
                    "cols": cols,
                    "scene_ids": [cd["scene_id"]] if cd.get("scene_id") else [],
                    "cast_present": cd.get("cast_present", []),
                    "props_present": cd.get("props_present", []),
                    "storyboard_prompt_path": cd.get("storyboard_prompt_path"),
                    "composite_image_path": cd.get("storyboard_image_path"),
                    "storyboard_status": cd.get("storyboard_status", "pending"),
                    "storyboard_history": cd.get("storyboard_history", []),
                    "cell_map": {i: fid for i, fid in enumerate(frame_ids)},
                    "provenance": cd.get("provenance", {}),
                }
            data["storyboard_grids"] = grids
            del data["chained_frame_groups"]
            # Migrate seeded_domains key
            sd = data.get("seeded_domains", {})
            if "chained_frame_groups" in sd:
                sd["storyboard_grids"] = sd.pop("chained_frame_groups")
        return data

    @model_validator(mode="after")
    def _validate_contracts(self) -> "NarrativeGraph":
        """Enforce canonical graph contracts on persisted data."""
        errors: list[str] = []

        node_registries = [
            ("cast", self.cast),
            ("location", self.locations),
            ("prop", self.props),
            ("scene", self.scenes),
            ("frame", self.frames),
            ("dialogue", self.dialogue),
            ("storyboard_grid", self.storyboard_grids),
            ("cast_frame_state", self.cast_frame_states),
            ("prop_frame_state", self.prop_frame_states),
            ("location_frame_state", self.location_frame_states),
        ]
        for registry_name, registry in node_registries:
            for node_id, node in registry.items():
                provenance = getattr(node, "provenance", None)
                source_prose_chunk = getattr(provenance, "source_prose_chunk", "")
                if not source_prose_chunk or not source_prose_chunk.strip():
                    errors.append(f"{registry_name}:{node_id} missing provenance.source_prose_chunk")

        for key, state in self.cast_frame_states.items():
            expected_key = f"{state.cast_id}@{state.frame_id}"
            if key != expected_key:
                errors.append(f"cast_frame_state key mismatch: expected {expected_key}, found {key}")
        for key, state in self.prop_frame_states.items():
            expected_key = f"{state.prop_id}@{state.frame_id}"
            if key != expected_key:
                errors.append(f"prop_frame_state key mismatch: expected {expected_key}, found {key}")
        for key, state in self.location_frame_states.items():
            expected_key = f"{state.location_id}@{state.frame_id}"
            if key != expected_key:
                errors.append(f"location_frame_state key mismatch: expected {expected_key}, found {key}")

        seen_edge_ids: set[str] = set()
        for edge in self.edges:
            expected_edge_id = canonical_edge_id(edge.source_id, edge.edge_type, edge.target_id)
            if not edge.edge_id or not edge.edge_id.strip():
                errors.append(
                    f"edge missing edge_id: {edge.source_id} --{edge.edge_type.value}--> {edge.target_id}"
                )
                continue
            if edge.edge_id != expected_edge_id:
                errors.append(
                    f"edge_id mismatch: expected {expected_edge_id}, found {edge.edge_id}"
                )
            if edge.edge_id in seen_edge_ids:
                errors.append(f"duplicate edge_id: {edge.edge_id}")
            seen_edge_ids.add(edge.edge_id)

        if errors:
            preview = "; ".join(errors[:20])
            remaining = len(errors) - min(len(errors), 20)
            if remaining > 0:
                preview += f"; ... {remaining} more"
            raise ValueError(f"Graph contract validation failed: {preview}")

        return self

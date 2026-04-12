"""
Reference Image Collector — Single source of truth for frame reference images.
===============================================================================

Centralizes resolution and validation of reference images needed for frame
generation. Replaces ad-hoc path construction scattered across prompt_assembler
and other modules.

Key design decision: the storyboard cell is the PRIMARY COMPOSITION INPUT and
is always routed separately (to FrameInput.storyboard_image), never mixed into
reference_images. get_flat_reference_list() enforces this split.

Reference priority order within FrameReferences:
  storyboard_cell(1) → previous_frame(1) → cast(≤5) → location(1) → props(≤3)
  Total cap: 11 images
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from .api import (
    build_shot_packet,
    get_frame_cast_state_models,
    get_frame_cell_image,
    get_frame_prop_state_models,
)
from .feature_flags import ENABLE_STORYBOARD_GUIDANCE
from .schema import CastBible, CastFrameRole, CastFrameState, CharacterSheet, NarrativeGraph, PoseState
from .store import GraphStore
from telemetry import current_run_id

logger = logging.getLogger("graph.reference_collector")

_CAST_MAX = 5
_PROP_MAX = 3
_CAST_STITCH_MIN = 3
_CAST_STITCH_CELL = (512, 768)
_CAST_STITCH_GAP = 24


def _normalize_pose_token(value: str) -> str:
    return (
        (value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _sequence_number(frame_id: str) -> Optional[int]:
    try:
        return int(str(frame_id).split("_")[-1])
    except (TypeError, ValueError):
        return None


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


def pose_state_from_cast_state(
    cast_state: CastFrameState,
    *,
    frame_id: str = "",
    sequence_index: Optional[int] = None,
    frame_text: str = "",
) -> PoseState:
    """Canonicalize a cast frame state into a prompt-safe pose lock."""
    frame_text_norm = _normalize_pose_token(frame_text)
    inferred_posture = ""
    if any(token in frame_text_norm for token in ("sitting", "sits", "seated")):
        inferred_posture = "sitting"
    elif any(token in frame_text_norm for token in ("crouching", "crouches", "crouched")):
        inferred_posture = "crouching"
    elif any(token in frame_text_norm for token in ("kneeling", "kneels", "kneel")):
        inferred_posture = "kneeling"
    elif any(token in frame_text_norm for token in ("lying", "lies", "laying")):
        inferred_posture = "lying"
    elif any(token in frame_text_norm for token in ("running", "runs", "sprinting", "sprints")):
        inferred_posture = "running"
    elif any(token in frame_text_norm for token in ("walking", "walks", "strides", "mid_stride")):
        inferred_posture = "walking"
    elif any(token in frame_text_norm for token in ("leaning", "leans", "leaned")):
        inferred_posture = "leaning"

    posture = getattr(cast_state.posture, "value", cast_state.posture) or inferred_posture or "standing"
    posture_token = _normalize_pose_token(str(posture)) or "standing"
    facing = _normalize_pose_token(cast_state.facing_direction or "")
    if not facing:
        if "profile_left" in frame_text_norm:
            facing = "profile_left"
        elif "profile_right" in frame_text_norm:
            facing = "profile_right"
    action = _normalize_pose_token(cast_state.action or "")
    if not action:
        if "supine" in frame_text_norm:
            action = "lying_supine"
        elif "prone" in frame_text_norm:
            action = "lying_prone"

    pose_parts = [posture_token]
    if posture_token == "lying":
        if "supine" in action:
            pose_parts.append("supine")
        elif "prone" in action:
            pose_parts.append("prone")
    if facing:
        pose_parts.append(facing)
    elif posture_token == "standing":
        pose_parts.append("neutral")

    modifiers: list[str] = []
    for key, value in (
        ("action", cast_state.action),
        ("screen_position", cast_state.screen_position),
        ("spatial_position", cast_state.spatial_position),
        ("facing_direction", cast_state.facing_direction),
        ("eye_direction", cast_state.eye_direction),
        ("looking_at", cast_state.looking_at),
        ("emotion", cast_state.emotion),
        ("state_tag", cast_state.active_state_tag if cast_state.active_state_tag != "base" else ""),
        ("clothing_state", cast_state.clothing_state if cast_state.clothing_state != "base" else ""),
    ):
        if value:
            modifiers.append(f"{key}:{value}")

    confidence = 0.55
    if cast_state.posture:
        confidence += 0.15
    if cast_state.facing_direction:
        confidence += 0.1
    if cast_state.action:
        confidence += 0.1
    if cast_state.screen_position or cast_state.spatial_position:
        confidence += 0.05
    if cast_state.looking_at or cast_state.eye_direction:
        confidence += 0.05

    resolved_frame_id = frame_id or cast_state.frame_id
    return PoseState(
        pose="_".join(part for part in pose_parts if part),
        modifiers=modifiers,
        frame_id=resolved_frame_id or None,
        last_seen_frame=sequence_index if sequence_index is not None else _sequence_number(resolved_frame_id),
        confidence=min(confidence, 1.0),
    )


def cast_bible_snapshot_for_frame(
    bible: Optional[CastBible],
    graph: NarrativeGraph,
    frame_id: str,
    cast_ids: list[str] | None = None,
) -> Optional[dict]:
    """Return a minimal per-frame snapshot for prompt assembly and manifests."""
    if bible is None:
        return None

    visible_cast_ids = cast_ids or [
        state.cast_id
        for state in get_frame_cast_state_models(graph, frame_id)
        if getattr(getattr(state, "frame_role", None), "value", getattr(state, "frame_role", None)) != "referenced"
    ]

    characters: list[dict] = []
    for cast_id in visible_cast_ids:
        sheet = bible.characters.get(cast_id)
        if sheet is None:
            continue
        pose = sheet.pose_for_frame(frame_id)
        if pose is None:
            continue
        target_sequence = _sequence_number(frame_id) or -1
        recent_candidates = [
            state
            for state in chain(sheet.pose_history, sheet.frame_poses.values())
            if (
                state.frame_id
                and (seq := _sequence_number(state.frame_id)) is not None
                and seq <= target_sequence
                and state.frame_id != frame_id
            )
        ]
        recent_candidates.sort(
            key=lambda state: _sequence_number(state.frame_id) or -1
        )
        recent_history = [
            state.model_dump()
            for state in recent_candidates[-3:]
        ]
        characters.append(
            {
                "character_id": sheet.character_id,
                "name": sheet.name,
                "description": sheet.description,
                "pose": pose.model_dump(),
                "recent_pose_history": recent_history,
                "appearance_notes": dict(sheet.appearance_notes),
            }
        )

    if not characters:
        return None

    return {
        "version": bible.version,
        "run_id": bible.run_id,
        "sequence_id": bible.sequence_id,
        "frame_id": frame_id,
        "characters": characters,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FrameReferences:
    """Typed reference image set for a single frame.

    Consumers must route these correctly:
    - storyboard_cell → FrameInput.storyboard_image
    - everything else → FrameInput.reference_images (via get_flat_reference_list)

    All paths are absolute. A field may be None/empty if the asset does not
    exist or is not applicable for this frame. Use validate_references() to
    surface missing-file warnings explicitly.
    """

    storyboard_cell: Optional[Path] = None
    """Storyboard grid cell — PRIMARY COMPOSITION INPUT.
    Routes to FrameInput.storyboard_image, not reference_images."""

    previous_frame: Optional[Path] = None
    """Composed output of the immediately preceding frame (FOLLOWS chain).
    Provides temporal continuity for image generation."""

    cast_composites: list[Path] = field(default_factory=list)
    """Cast reference images for all visible cast members, up to 5.
    State-variant-aware — non-base active_state_tag uses variant image."""

    location_primary: Optional[Path] = None
    """Primary location reference image for this frame's location."""

    props: list[Path] = field(default_factory=list)
    """Prop reference images for props active in this frame, up to 3."""


# ═══════════════════════════════════════════════════════════════════════════════
# COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════════


class ReferenceImageCollector:
    """Resolves, validates, and hashes reference images for frame generation.

    Args:
        graph: The narrative graph for this project.
        project_dir: Absolute path to the project root directory.
    """

    def __init__(self, graph: NarrativeGraph, project_dir: Path) -> None:
        self.graph = graph
        self.project_dir = Path(project_dir)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_frame_references(self, frame_id: str) -> FrameReferences:
        """Return typed reference set for a single frame.

        Resolves all reference paths to absolute paths via project_dir.
        Missing files are logged as warnings but do not raise — call
        validate_references() for explicit pre-flight auditing.

        Args:
            frame_id: The frame to resolve references for.

        Returns:
            FrameReferences with all resolvable paths populated.
        """
        if frame_id not in self.graph.frames:
            logger.warning(
                "get_frame_references: frame '%s' not found in graph", frame_id
            )
            return FrameReferences()

        return FrameReferences(
            storyboard_cell=(
                self._resolve_storyboard_cell(frame_id)
                if ENABLE_STORYBOARD_GUIDANCE else None
            ),
            previous_frame=self._resolve_previous_frame(frame_id),
            cast_composites=self._resolve_cast_composites(frame_id),
            location_primary=self._resolve_location(frame_id),
            props=self._resolve_props(frame_id),
        )

    def get_flat_reference_list(self, frame_id: str) -> list[Path]:
        """Return ordered flat list for FrameInput.reference_images.

        Excludes storyboard_cell — that goes to FrameInput.storyboard_image.
        Only includes paths that exist on disk at call time.

        Order: previous_frame → cast_composites → location_primary → props

        Args:
            frame_id: The frame to resolve references for.

        Returns:
            Ordered list of existing absolute paths (storyboard cell excluded).
        """
        refs = self.get_frame_references(frame_id)
        flat: list[Path] = []

        if refs.previous_frame and refs.previous_frame.exists():
            flat.append(refs.previous_frame)

        stitched_cast = self._resolve_stitched_cast_reference(frame_id)
        if stitched_cast is not None and stitched_cast.exists():
            flat.append(stitched_cast)
        else:
            for p in refs.cast_composites:
                if p.exists():
                    flat.append(p)

        if refs.location_primary and refs.location_primary.exists():
            flat.append(refs.location_primary)

        for p in refs.props:
            if p.exists():
                flat.append(p)

        return flat

    def validate_references(self, frame_id: str) -> list[str]:
        """Check all expected reference paths. Return warnings for missing files.

        Does not raise. Use before generation calls to confirm assets are
        present before spending on API calls.

        Args:
            frame_id: The frame to validate references for.

        Returns:
            List of human-readable warning strings. Empty list if all present.
        """
        refs = self.get_frame_references(frame_id)
        warnings: list[str] = []

        def _check(label: str, path: Optional[Path]) -> None:
            if path is not None and not path.exists():
                warnings.append(f"[{frame_id}] {label}: {path} not found")

        _check("storyboard_cell", refs.storyboard_cell)
        _check("previous_frame", refs.previous_frame)
        for i, p in enumerate(refs.cast_composites):
            _check(f"cast_composite[{i}]", p)
        _check("location_primary", refs.location_primary)
        for i, p in enumerate(refs.props):
            _check(f"prop[{i}]", p)

        for w in warnings:
            logger.warning(w)

        return warnings

    def build_reference_manifest_entry(self, frame_id: str) -> dict:
        """Build a manifest dict with relative paths and SHA-256 hashes.

        Used for manifest tracking to detect reference changes across pipeline
        runs. Only hashes files that exist — missing files are recorded with
        sha256: null.

        Args:
            frame_id: The frame to build a manifest entry for.

        Returns:
            dict with keys matching FrameReferences fields, each containing
            {"path": str (relative to project_dir) | None, "sha256": str | None}.
        """
        refs = self.get_frame_references(frame_id)

        def _entry(path: Optional[Path]) -> dict:
            if path is None:
                return {"path": None, "sha256": None}
            sha = _sha256(path) if path.exists() else None
            return {"path": str(self._to_relative(path)), "sha256": sha}

        return {
            "frame_id": frame_id,
            "storyboard_cell": _entry(refs.storyboard_cell),
            "previous_frame": _entry(refs.previous_frame),
            "cast_composites": [_entry(p) for p in refs.cast_composites],
            "location_primary": _entry(refs.location_primary),
            "props": [_entry(p) for p in refs.props],
        }

    def build_cast_bible(
        self,
        *,
        run_id: str = "",
        sequence_id: str = "",
    ) -> CastBible:
        """Build a full cast bible from the current graph state."""
        resolved_run_id = run_id or current_run_id("")
        resolved_sequence_id = sequence_id or getattr(self.graph.project, "project_id", "") or None
        bible = CastBible(
            run_id=resolved_run_id or None,
            sequence_id=resolved_sequence_id,
        )

        for location in self.graph.locations.values():
            bible.locations[location.location_id] = {
                "name": location.name,
                "description": location.description,
                "atmosphere": location.atmosphere,
                "primary_image_path": location.primary_image_path,
            }

        for cast in self.graph.cast.values():
            bible.characters[cast.cast_id] = CharacterSheet(
                character_id=cast.cast_id,
                name=cast.name,
                description=(
                    cast.identity.physical_description
                    or cast.identity.wardrobe_description
                    or cast.personality
                    or cast.name
                ),
                appearance_notes=self._appearance_notes(cast.cast_id),
            )

        ordered_frame_ids = self.graph.frame_order or sorted(
            self.graph.frames,
            key=lambda item: getattr(self.graph.frames[item], "sequence_index", 0),
        )
        for frame_id in ordered_frame_ids:
            self.update_from_frame_description(
                frame_id,
                bible=bible,
                persist=False,
            )

        return bible

    def sync_cast_bible(
        self,
        *,
        store: GraphStore | None = None,
        run_id: str = "",
        sequence_id: str = "",
    ) -> CastBible:
        """Rebuild and persist the cast bible from the graph in one pass."""
        graph_store = store or GraphStore(self.project_dir)
        bible = self.build_cast_bible(run_id=run_id, sequence_id=sequence_id)
        graph_store.save_cast_bible(
            bible,
            run_id=run_id or current_run_id(""),
            sequence_id=sequence_id or getattr(self.graph.project, "project_id", ""),
        )
        return bible

    def update_from_frame_description(
        self,
        frame_id: str,
        *,
        bible: CastBible | None = None,
        store: GraphStore | None = None,
        persist: bool = True,
        run_id: str = "",
        sequence_id: str = "",
    ) -> CastBible:
        """Update cast-bible pose locks from the canonical graph state for one frame."""
        frame = self.graph.frames.get(frame_id)
        if frame is None:
            raise KeyError(f"Frame {frame_id} not found in graph")

        graph_store = store or GraphStore(self.project_dir)
        resolved_run_id = run_id or current_run_id("")
        resolved_sequence_id = sequence_id or getattr(self.graph.project, "project_id", "")
        working = bible or graph_store.load_latest_cast_bible(
            run_id=resolved_run_id,
            sequence_id=resolved_sequence_id,
        ) or CastBible(
            run_id=resolved_run_id or None,
            sequence_id=resolved_sequence_id or None,
        )

        if frame.location_id and frame.location_id in self.graph.locations:
            location = self.graph.locations[frame.location_id]
            working.locations[frame.location_id] = {
                "name": location.name,
                "description": location.description,
                "atmosphere": location.atmosphere,
                "primary_image_path": location.primary_image_path,
            }

        for cast_state in get_frame_cast_state_models(self.graph, frame_id):
            role = getattr(getattr(cast_state, "frame_role", None), "value", getattr(cast_state, "frame_role", None))
            if role == "referenced":
                continue

            cast = self.graph.cast.get(cast_state.cast_id)
            sheet = working.characters.get(cast_state.cast_id)
            if sheet is None:
                sheet = CharacterSheet(
                    character_id=cast_state.cast_id,
                    name=cast.name if cast else cast_state.cast_id,
                    description=(
                        cast.identity.physical_description
                        if cast
                        else cast_state.cast_id
                    ),
                    appearance_notes=self._appearance_notes(cast_state.cast_id),
                )
                working.characters[cast_state.cast_id] = sheet

            next_pose = pose_state_from_cast_state(
                cast_state,
                frame_id=frame_id,
                sequence_index=getattr(frame, "sequence_index", None),
                frame_text=" ".join(
                    part
                    for part in (
                        getattr(frame, "narrative_beat", ""),
                        getattr(frame, "source_text", ""),
                    )
                    if part
                ),
            )
            previous_pose = sheet.current_pose.model_copy(deep=True)
            if (
                previous_pose.pose != next_pose.pose
                or previous_pose.modifiers != next_pose.modifiers
                or previous_pose.frame_id != next_pose.frame_id
            ):
                if previous_pose.frame_id or previous_pose.pose != "standing_neutral":
                    sheet.pose_history.append(previous_pose)
                    sheet.pose_history = sheet.pose_history[-5:]

            sheet.current_pose = next_pose
            sheet.frame_poses[frame_id] = next_pose
            sheet.appearance_notes.update(self._appearance_notes(cast_state.cast_id))

        if persist:
            graph_store.save_cast_bible(
                working,
                run_id=resolved_run_id,
                sequence_id=resolved_sequence_id,
            )
        return working

    # ── Private resolution helpers ────────────────────────────────────────────

    def _resolve_storyboard_cell(self, frame_id: str) -> Optional[Path]:
        """Resolve the storyboard cell image for this frame.

        Resolution order:
        1. graph.storyboard_grids cell_image_dir via get_frame_cell_image()
        2. Canonical convention: frames/storyboards/{grid_id}/frames/{frame_id}.png
        3. Storyboard handler output convention: frames/storyboards/cells/{grid_id}/{frame_id}_cell.png
        """
        # Primary: api helper reads cell_image_dir from graph
        cell_rel = get_frame_cell_image(self.graph, frame_id)
        if cell_rel:
            p = self._to_absolute(cell_rel)
            if p.exists():
                return p

            legacy_name = p.with_name(f"{frame_id}_cell.png")
            if legacy_name.exists():
                return legacy_name

            logger.warning(
                "Storyboard cell for '%s' expected at %s — not found on disk",
                frame_id, p,
            )

        # Fallback: scan grids and try known conventions
        for grid in self.graph.storyboard_grids.values():
            if frame_id not in grid.frame_ids:
                continue

            # Convention 1 (task spec): frames/storyboards/{grid_id}/frames/{frame_id}.png
            p1 = (
                self.project_dir
                / "frames"
                / "storyboards"
                / grid.grid_id
                / "frames"
                / f"{frame_id}.png"
            )
            if p1.exists():
                return p1

            # Convention 2 (storyboard handler output): frames/storyboards/cells/{grid_id}/{frame_id}_cell.png
            p2 = (
                self.project_dir
                / "frames"
                / "storyboards"
                / "cells"
                / grid.grid_id
                / f"{frame_id}_cell.png"
            )
            if p2.exists():
                return p2

            logger.warning(
                "No storyboard cell found for frame '%s' (grid '%s')",
                frame_id,
                grid.grid_id,
            )
            return None  # Frame is in this grid but no cell found

        return None  # Frame not in any grid

    def _resolve_previous_frame(self, frame_id: str) -> Optional[Path]:
        """Resolve the composed output of the preceding frame (FOLLOWS chain).

        Uses FrameNode.previous_frame_id (set by the FOLLOWS edge wiring).
        Checks composed_image_path from the graph first, then falls back to
        the convention: frames/composed/{prev_id}_gen.png.
        """
        frame = self.graph.frames.get(frame_id)
        if not frame or not frame.previous_frame_id:
            return None

        prev = self.graph.frames.get(frame.previous_frame_id)
        if not prev:
            return None

        # Graph-tracked composed output
        if prev.composed_image_path:
            p = self._to_absolute(prev.composed_image_path)
            if p.exists():
                return p

        # Convention fallback
        convention = (
            self.project_dir
            / "frames"
            / "composed"
            / f"{frame.previous_frame_id}_gen.png"
        )
        if convention.exists():
            return convention

        return None

    def _resolve_cast_reference_entries(self, frame_id: str) -> list[tuple[CastFrameState, Path]]:
        """Resolve visible cast states paired with their reference images.

        Respects active_state_tag — a non-base state tag uses the variant image
        (e.g. wounded, formal) if available. Falls back to base composite, then
        to the convention path cast/composites/{cast_id}_ref.png.

        Excludes cast with frame_role "referenced" (off-screen/mentioned only).
        Cap: _CAST_MAX (5).
        """
        cast_states = get_frame_cast_state_models(self.graph, frame_id)
        visible = [cs for cs in cast_states if cs.frame_role != "referenced"]
        if not visible:
            try:
                packet = build_shot_packet(self.graph, frame_id)
            except Exception:
                packet = None
            inferred_cast_ids = list(getattr(packet, "visible_cast_ids", []) or [])
            visible = [
                CastFrameState(
                    cast_id=cast_id,
                    frame_id=frame_id,
                    frame_role=CastFrameRole.SUBJECT,
                    active_state_tag="base",
                )
                for cast_id in inferred_cast_ids[:_CAST_MAX]
            ]

        entries: list[tuple[CastFrameState, Path]] = []
        for cs in visible[:_CAST_MAX]:
            cast = self.graph.cast.get(cs.cast_id)
            if not cast:
                logger.warning(
                    "Cast '%s' in frame '%s' not found in graph",
                    cs.cast_id,
                    frame_id,
                )
                continue

            # State variant image (e.g. wet, wounded, formal)
            if cs.active_state_tag and cs.active_state_tag != "base":
                variant = cast.state_variants.get(cs.active_state_tag)
                if variant and variant.image_path:
                    entries.append((cs, self._to_absolute(variant.image_path)))
                    continue

            # Base composite from graph
            if cast.composite_path:
                entries.append((cs, self._to_absolute(cast.composite_path)))
                continue

            # Convention fallback
            entries.append((
                cs,
                self.project_dir / "cast" / "composites" / f"{cs.cast_id}_ref.png",
            ))

        return entries

    def _resolve_cast_composites(self, frame_id: str) -> list[Path]:
        """Resolve cast reference images for visible cast in this frame."""
        return [path for _cast_state, path in self._resolve_cast_reference_entries(frame_id)]

    def _resolve_stitched_cast_reference(self, frame_id: str) -> Optional[Path]:
        """Build a temporary stitched cast sheet for 3+ visible cast references.

        The stitched image collapses multiple cast refs into one landscape sheet,
        ordered left-to-right using each cast state's screen position so group
        frames arrive at the image model with clearer relative placement.
        """
        entries = [
            (cast_state, path)
            for cast_state, path in self._resolve_cast_reference_entries(frame_id)
            if path.exists()
        ]
        if len(entries) < _CAST_STITCH_MIN:
            return None

        ordered = sorted(
            entries,
            key=lambda item: (
                _screen_position_rank(item[0].screen_position or ""),
                item[0].cast_id,
            ),
        )
        digest = hashlib.sha1(
            "|".join(
                f"{cast_state.cast_id}:{cast_state.screen_position}:{cast_state.spatial_position}:{path}"
                for cast_state, path in ordered
            ).encode("utf-8")
        ).hexdigest()[:10]
        out_dir = self.project_dir / "cast" / "composites" / "group_refs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{frame_id}_group_{digest}.png"
        if out_path.exists():
            return out_path

        cols = min(3, len(ordered))
        rows = math.ceil(len(ordered) / cols)
        cell_w, cell_h = _CAST_STITCH_CELL
        gap = _CAST_STITCH_GAP
        canvas_w = cols * cell_w + (cols + 1) * gap
        canvas_h = rows * cell_h + (rows + 1) * gap
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (245, 242, 236, 255))

        for index, (_cast_state, path) in enumerate(ordered):
            row = index // cols
            col = index % cols
            with Image.open(path) as src:
                tile = ImageOps.contain(src.convert("RGBA"), (cell_w, cell_h), Image.Resampling.LANCZOS)
            x = gap + col * (cell_w + gap) + max((cell_w - tile.width) // 2, 0)
            y = gap + row * (cell_h + gap) + max((cell_h - tile.height) // 2, 0)
            canvas.alpha_composite(tile, (x, y))

        canvas.save(out_path)
        return out_path

    def _resolve_location(self, frame_id: str) -> Optional[Path]:
        """Resolve the primary location reference image for this frame."""
        frame = self.graph.frames.get(frame_id)
        if not frame or not frame.location_id:
            return None

        loc = self.graph.locations.get(frame.location_id)
        if not loc:
            logger.warning(
                "Location '%s' in frame '%s' not found in graph",
                frame.location_id,
                frame_id,
            )
            return None

        background = getattr(frame, "background", None)
        camera_facing = ""
        if background is not None:
            camera_facing = (
                getattr(background, "camera_facing", None)
                or (background.get("camera_facing") if isinstance(background, dict) else "")
                or ""
            )
        primary_path: Optional[Path] = None
        if loc.primary_image_path:
            primary_path = self._to_absolute(loc.primary_image_path)
        else:
            primary_path = self.project_dir / "locations" / "primary" / f"{frame.location_id}.png"

        if camera_facing:
            facing = camera_facing.strip().lower().replace("camera_facing_", "")
            variant = self.project_dir / "locations" / "variants" / f"{frame.location_id}_{facing}.png"
            if not variant.exists() and primary_path is not None and primary_path.exists():
                try:
                    from handlers.location_grid import extract_directional_location_variants

                    extract_directional_location_variants(primary_path, frame.location_id)
                except Exception:
                    logger.exception(
                        "Failed to extract directional location variants for %s from %s",
                        frame.location_id,
                        primary_path,
                    )
            if variant.exists():
                return variant

        return primary_path

    def _resolve_props(self, frame_id: str) -> list[Path]:
        """Resolve prop reference images for props active in this frame.

        Uses PropFrameState to identify which props appear here.
        Cap: _PROP_MAX (3).
        """
        prop_states = get_frame_prop_state_models(self.graph, frame_id)
        paths: list[Path] = []

        for ps in prop_states[:_PROP_MAX]:
            prop = self.graph.props.get(ps.prop_id)
            if not prop:
                logger.warning(
                    "Prop '%s' in frame '%s' not found in graph",
                    ps.prop_id,
                    frame_id,
                )
                continue

            # Graph-tracked path
            if prop.image_path:
                paths.append(self._to_absolute(prop.image_path))
                continue

            # Convention fallback: props/generated/{prop_id}.png
            paths.append(
                self.project_dir / "props" / "generated" / f"{ps.prop_id}.png"
            )

        return paths

    # ── Path utilities ────────────────────────────────────────────────────────

    def _to_absolute(self, path: str | Path) -> Path:
        """Return an absolute path, resolving relative paths via project_dir."""
        p = Path(path)
        return p if p.is_absolute() else self.project_dir / p

    def _to_relative(self, path: Path) -> Path:
        """Return path relative to project_dir if possible, otherwise return as-is."""
        try:
            return path.relative_to(self.project_dir)
        except ValueError:
            return path

    def _appearance_notes(self, cast_id: str) -> dict[str, str]:
        cast = self.graph.cast.get(cast_id)
        if cast is None:
            return {}

        identity = cast.identity
        notes: dict[str, str] = {}
        if identity.wardrobe_description:
            notes["wardrobe"] = identity.wardrobe_description
        elif identity.clothing:
            notes["wardrobe"] = ", ".join(identity.clothing)
        if identity.hair_color or identity.hair_length or identity.hair_style:
            notes["hair"] = " ".join(
                part
                for part in (identity.hair_color, identity.hair_length, identity.hair_style)
                if part
            )
        if identity.accessories:
            notes["accessories"] = ", ".join(identity.accessories)
        if identity.footwear:
            notes["footwear"] = identity.footwear
        if cast.composite_path:
            notes["composite_path"] = cast.composite_path
        return notes


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════


def _sha256(path: Path) -> Optional[str]:
    """Compute SHA-256 hex digest of a file. Returns None if unreadable."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None

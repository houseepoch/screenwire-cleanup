"""
ScreenWire — Prompt Pair Validator
====================================

Deterministic consistency checks between image and video prompt dicts
for each frame, produced by prompt_assembler.

Catches drift that can arise from:
  - separate assembly paths (image vs video prompts built independently)
  - video 4096-char compression dropping entities from the narrative
  - dialogue sync metadata diverging between prompt types
  - missing restaging guards in video direction

Returns a list of PromptPairIssue objects with category, severity,
description, and optional suggested_fix. Run after prompt assembly
and before asset generation.

Checks:
    a. CINEMATIC_TAG_MATCH     — same cinematic tag and family in both prompts
    b. CONTINUITY_PARITY       — cast, prop, location metadata in sync
    c. DIALOGUE_FIT_CONSISTENCY — dialogue span coverage coherent between both
    d. REFERENCE_LIST_PARITY   — ref image list coherent with shot packet data
    e. RESTAGING_GUARD         — "Do not restage" present for guarded dialogue roles

CLI:
    python3 graph/prompt_pair_validator.py --project-dir ./projects/test [--json] [--strict]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

if __package__:
    from .schema import NarrativeGraph, ShotPacket
    from .api import build_shot_packet
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from graph.schema import NarrativeGraph, ShotPacket
    from graph.api import build_shot_packet


# ─── Constants ─────────────────────────────────────────────────────────────────

# Dialogue roles that require "Do not restage" language in the video prompt.
# Without this guard the video model may spontaneously re-choreograph the scene.
_RESTAGING_GUARD_ROLES: frozenset[str] = frozenset({
    "listener_reaction",
    "speaker_sync",
    "bridge_coverage",
})

# Maximum expected reference images per image-generation call
_MAX_REF_IMAGES: int = 14

# Known valid values for video prompt's dialogue_fit_status
_VALID_FIT_STATUSES: frozenset[str] = frozenset({"fits", "overflows", "no_dialogue"})

# Subtitle/caption markers that must not appear in image prompts
_SUBTITLE_MARKERS: tuple[str, ...] = (
    "subtitle",
    "caption",
    "burned-in text",
    "text overlay",
)


# ─── Enums ────────────────────────────────────────────────────────────────────

class PromptPairCategory(str, Enum):
    """Categories of prompt pair validation checks (one per spec requirement)."""
    CINEMATIC_TAG_MATCH = "CINEMATIC_TAG_MATCH"
    CONTINUITY_PARITY = "CONTINUITY_PARITY"
    DIALOGUE_FIT_CONSISTENCY = "DIALOGUE_FIT_CONSISTENCY"
    REFERENCE_LIST_PARITY = "REFERENCE_LIST_PARITY"
    RESTAGING_GUARD = "RESTAGING_GUARD"


class IssueSeverity(str, Enum):
    """Severity of a prompt pair issue."""
    WARNING = "WARNING"   # logged; pipeline continues
    ERROR = "ERROR"       # blocks pipeline


# ─── Issue Model ──────────────────────────────────────────────────────────────

@dataclass
class PromptPairIssue:
    """A single validation problem found between an image/video prompt pair.

    Attributes:
        category:      Which check produced this issue.
        frame_id:      The frame being validated.
        severity:      WARNING (pipeline continues) or ERROR (blocks pipeline).
        description:   Human-readable description of the problem.
        suggested_fix: Optional actionable guidance for resolution.
    """
    category: PromptPairCategory
    frame_id: str
    severity: IssueSeverity
    description: str
    suggested_fix: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict (matches continuity_validator.py style)."""
        d: dict = {
            "category": self.category.value,
            "frame_id": self.frame_id,
            "severity": self.severity.value,
            "description": self.description,
        }
        if self.suggested_fix:
            d["suggested_fix"] = self.suggested_fix
        return d


# ─── Helpers ──────────────────────────────────────────────────────────────────

_TAG_FAMILY_RE = re.compile(r"^([A-Za-z]+)", re.ASCII)
_INVARIANT_LABEL_RE = re.compile(r"^([A-Z][A-Z0-9_ ]{0,29}):")


def _extract_tag_family(cinematic_tag: str) -> str:
    """Return the family letter(s) from a cinematic tag string.

    Examples:
        "D01.a +push"  → "D"
        "E02.b"        → "E"
        "A01.a -pan"   → "A"
    """
    m = _TAG_FAMILY_RE.match(cinematic_tag.strip())
    return m.group(1).upper() if m else ""


def _has_restaging_guard(video_prompt_text: str) -> bool:
    """Return True if the video prompt text contains a restaging prohibition phrase."""
    lower = video_prompt_text.lower()
    return (
        "do not restage" in lower
        or "do not re-stage" in lower
        or "no restaging" in lower
    )


def _labels_from_invariants(invariants: list[str]) -> list[str]:
    """Extract entity labels from shot packet invariant lines.

    Invariant lines are formatted as "LABEL: description…".
    Returns the LABEL portion only (uppercased).
    """
    labels: list[str] = []
    for line in invariants:
        m = _INVARIANT_LABEL_RE.match(line)
        if m:
            labels.append(m.group(1).strip())
    return labels


def _frame_id_from_prompts(image_prompt: dict, video_prompt: dict) -> str:
    """Best-effort frame_id extraction from either prompt dict."""
    return (
        image_prompt.get("frame_id")
        or video_prompt.get("frame_id")
        or "unknown"
    )


# ─── PromptPairValidator ──────────────────────────────────────────────────────

class PromptPairValidator:
    """Validates consistency between an image prompt dict and a video prompt dict.

    Both dicts are produced by prompt_assembler for the same FrameNode and must
    be internally consistent. This validator detects drift introduced by separate
    assembly paths, 4096-char compression, or assembly order bugs.

    Usage::

        validator = PromptPairValidator()
        issues = validator.validate(image_prompt, video_prompt, shot_packet)
        errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
    """

    def validate(
        self,
        image_prompt: dict,
        video_prompt: dict,
        shot_packet: ShotPacket,
    ) -> list[PromptPairIssue]:
        """Run all five prompt-pair checks and return aggregated issues.

        Args:
            image_prompt: Dict from assemble_image_prompt for a frame.
            video_prompt: Dict from assemble_video_prompt for the same frame.
            shot_packet:  ShotPacket for the same frame (from build_shot_packet).

        Returns:
            List of PromptPairIssue objects, ordered by check category.
        """
        issues: list[PromptPairIssue] = []
        issues.extend(self._check_cinematic_tag_match(image_prompt, video_prompt))
        issues.extend(self._check_continuity_parity(image_prompt, video_prompt, shot_packet))
        issues.extend(self._check_dialogue_fit_consistency(image_prompt, video_prompt, shot_packet))
        issues.extend(self._check_reference_list_parity(image_prompt, video_prompt, shot_packet))
        issues.extend(self._check_restaging_guard(image_prompt, video_prompt))
        return issues

    # ── a. CINEMATIC_TAG_MATCH ─────────────────────────────────────────────

    def _check_cinematic_tag_match(
        self, image_prompt: dict, video_prompt: dict
    ) -> list[PromptPairIssue]:
        """a. Both prompts must carry the same cinematic tag from the same family.

        Severity matrix:
          - Missing tag in either prompt               → ERROR
          - Tags differ AND families differ            → ERROR
          - Tags differ but families match (modifier)  → WARNING
        """
        issues: list[PromptPairIssue] = []
        frame_id = _frame_id_from_prompts(image_prompt, video_prompt)

        img_tag = (image_prompt.get("cinematic_tag") or "").strip()
        vid_tag = (video_prompt.get("cinematic_tag") or "").strip()

        if not img_tag:
            issues.append(PromptPairIssue(
                category=PromptPairCategory.CINEMATIC_TAG_MATCH,
                frame_id=frame_id,
                severity=IssueSeverity.ERROR,
                description="Image prompt is missing cinematic_tag",
                suggested_fix=(
                    "Re-run assemble_image_prompt. cinematic_tag must be set from "
                    "frame.cinematic_tag.full_tag."
                ),
            ))

        if not vid_tag:
            issues.append(PromptPairIssue(
                category=PromptPairCategory.CINEMATIC_TAG_MATCH,
                frame_id=frame_id,
                severity=IssueSeverity.ERROR,
                description="Video prompt is missing cinematic_tag",
                suggested_fix=(
                    "Re-run assemble_video_prompt. cinematic_tag must be set from "
                    "frame.cinematic_tag.full_tag."
                ),
            ))

        if img_tag and vid_tag and img_tag != vid_tag:
            img_family = _extract_tag_family(img_tag)
            vid_family = _extract_tag_family(vid_tag)

            if img_family != vid_family:
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.CINEMATIC_TAG_MATCH,
                    frame_id=frame_id,
                    severity=IssueSeverity.ERROR,
                    description=(
                        f"Cinematic tag family mismatch: "
                        f"image={img_tag!r} (family={img_family}) "
                        f"vs video={vid_tag!r} (family={vid_family})"
                    ),
                    suggested_fix=(
                        "Both prompts must be assembled from the same FrameNode. "
                        "Verify the same frame_id was passed to both assemblers."
                    ),
                ))
            else:
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.CINEMATIC_TAG_MATCH,
                    frame_id=frame_id,
                    severity=IssueSeverity.WARNING,
                    description=(
                        f"Cinematic tag mismatch within family '{img_family}': "
                        f"image={img_tag!r} vs video={vid_tag!r}"
                    ),
                    suggested_fix=(
                        "A modifier override may have been applied to one assembler path "
                        "but not the other. Both should read frame.cinematic_tag.full_tag."
                    ),
                ))

        return issues

    # ── b. CONTINUITY_PARITY ───────────────────────────────────────────────

    def _check_continuity_parity(
        self,
        image_prompt: dict,
        video_prompt: dict,
        shot_packet: ShotPacket,
    ) -> list[PromptPairIssue]:
        """b. Key metadata fields must be identical across both prompts and shot_packet.

        Checks:
          - frame_id and scene_id match across all three objects
          - dialogue_coverage_roles match exactly (compression must not alter them)
          - directing dict present in both or neither
          - Focal cast names (up to subject_count) appear in video prompt text
          - Location label appears in image prompt text
        """
        issues: list[PromptPairIssue] = []
        frame_id = shot_packet.frame_id

        # frame_id and scene_id must be identical across all three sources
        for field_name in ("frame_id", "scene_id"):
            img_val = image_prompt.get(field_name)
            vid_val = video_prompt.get(field_name)
            sp_val = getattr(shot_packet, field_name, None)
            vals = {v for v in (img_val, vid_val, sp_val) if v is not None}
            if len(vals) > 1:
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.CONTINUITY_PARITY,
                    frame_id=frame_id,
                    severity=IssueSeverity.ERROR,
                    description=(
                        f"{field_name} mismatch: image={img_val!r}, "
                        f"video={vid_val!r}, shot_packet={sp_val!r}"
                    ),
                    suggested_fix=(
                        "All three objects must reference the same frame. "
                        "Pass the same frame_id to build_shot_packet, "
                        "assemble_image_prompt, and assemble_video_prompt."
                    ),
                ))

        # dialogue_coverage_roles must match exactly between both prompts
        img_roles = sorted(image_prompt.get("dialogue_coverage_roles") or [])
        vid_roles = sorted(video_prompt.get("dialogue_coverage_roles") or [])
        if img_roles != vid_roles:
            issues.append(PromptPairIssue(
                category=PromptPairCategory.CONTINUITY_PARITY,
                frame_id=frame_id,
                severity=IssueSeverity.ERROR,
                description=(
                    f"dialogue_coverage_roles mismatch: "
                    f"image={img_roles} vs video={vid_roles}"
                ),
                suggested_fix=(
                    "Both assemblers derive dialogue_coverage_roles from the same "
                    "ShotPacket audio turns. Re-run both from the same shot packet."
                ),
            ))

        # directing dict must be present in both or absent in both
        img_has_directing = bool(image_prompt.get("directing"))
        vid_has_directing = bool(video_prompt.get("directing"))
        if img_has_directing != vid_has_directing:
            issues.append(PromptPairIssue(
                category=PromptPairCategory.CONTINUITY_PARITY,
                frame_id=frame_id,
                severity=IssueSeverity.WARNING,
                description=(
                    f"directing dict present in one prompt but not the other "
                    f"(image={img_has_directing}, video={vid_has_directing})"
                ),
                suggested_fix=(
                    "Both assemblers must populate directing from the same "
                    "FrameNode.directing model."
                ),
            ))

        # Focal cast (first subject_count entries from cast_invariants) must
        # survive 4096-char video compression and appear in the video prompt text.
        vid_text = video_prompt.get("prompt", "")
        cast_labels = _labels_from_invariants(shot_packet.cast_invariants)
        focal_count = min(shot_packet.subject_count, len(cast_labels))
        for label in cast_labels[:focal_count]:
            if label and vid_text and label.lower() not in vid_text.lower():
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.CONTINUITY_PARITY,
                    frame_id=frame_id,
                    severity=IssueSeverity.WARNING,
                    description=(
                        f"Focal cast '{label}' is missing from video prompt text "
                        "(likely dropped by 4096-char compression)"
                    ),
                    suggested_fix=(
                        f"Preserve the cast invariant for '{label}' during video "
                        "prompt compression — focal subjects must survive. "
                        "Reduce BACKGROUND or LOCATION sections before CAST INVARIANTS."
                    ),
                ))

        # Location label must appear in the image prompt
        img_text = image_prompt.get("prompt", "")
        for loc_label in _labels_from_invariants(shot_packet.location_invariants):
            if loc_label and img_text and loc_label.lower() not in img_text.lower():
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.CONTINUITY_PARITY,
                    frame_id=frame_id,
                    severity=IssueSeverity.WARNING,
                    description=(
                        f"Location '{loc_label}' from shot_packet not found in image prompt text"
                    ),
                    suggested_fix=(
                        "Check the LOCATION INVARIANTS section in assemble_image_prompt. "
                        "Location label should be present verbatim."
                    ),
                ))

        return issues

    # ── c. DIALOGUE_FIT_CONSISTENCY ────────────────────────────────────────

    def _check_dialogue_fit_consistency(
        self,
        image_prompt: dict,
        video_prompt: dict,
        shot_packet: ShotPacket,
    ) -> list[PromptPairIssue]:
        """c. Dialogue span coverage must be coherent between image and video prompts.

        Rules when dialogue_present=True:
          - video dialogue_line must be non-empty
          - video dialogue_fit_status must be "fits" or "overflows"
          - video dialogue_turn_count must equal shot_packet audio turn count
          - image prompt text must not contain subtitle/caption markers
        Rules when dialogue_present=False:
          - video dialogue_fit_status must be "no_dialogue"
          - video dialogue_line must be absent/None
        """
        issues: list[PromptPairIssue] = []
        frame_id = shot_packet.frame_id

        img_dlg = image_prompt.get("dialogue_present", False)
        vid_dlg = video_prompt.get("dialogue_present", False)

        if img_dlg != vid_dlg:
            issues.append(PromptPairIssue(
                category=PromptPairCategory.DIALOGUE_FIT_CONSISTENCY,
                frame_id=frame_id,
                severity=IssueSeverity.ERROR,
                description=(
                    f"dialogue_present mismatch: "
                    f"image={img_dlg}, video={vid_dlg}"
                ),
                suggested_fix=(
                    "Both assemblers read dialogue_present from the same ShotPacket. "
                    "Re-run both from the same shot packet."
                ),
            ))
            # Cannot make further reliable checks without agreement on dialogue presence
            return issues

        fit_status = video_prompt.get("dialogue_fit_status", "")

        if img_dlg:
            # video must have a dialogue_line
            if not video_prompt.get("dialogue_line"):
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.DIALOGUE_FIT_CONSISTENCY,
                    frame_id=frame_id,
                    severity=IssueSeverity.ERROR,
                    description=(
                        "dialogue_present=True but video prompt dialogue_line is empty/None"
                    ),
                    suggested_fix=(
                        "assemble_video_prompt must populate dialogue_line from "
                        "ShotPacket audio turns."
                    ),
                ))

            # fit_status must be "fits" or "overflows"
            if fit_status == "no_dialogue":
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.DIALOGUE_FIT_CONSISTENCY,
                    frame_id=frame_id,
                    severity=IssueSeverity.ERROR,
                    description=(
                        f"dialogue_present=True but video dialogue_fit_status={fit_status!r}"
                    ),
                    suggested_fix=(
                        "dialogue_fit_status must be 'fits' or 'overflows' when dialogue "
                        "is present. 'no_dialogue' is only valid for silent frames."
                    ),
                ))
            elif fit_status not in _VALID_FIT_STATUSES:
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.DIALOGUE_FIT_CONSISTENCY,
                    frame_id=frame_id,
                    severity=IssueSeverity.WARNING,
                    description=f"Unrecognised dialogue_fit_status value: {fit_status!r}",
                    suggested_fix=(
                        f"Expected one of: {sorted(_VALID_FIT_STATUSES)}"
                    ),
                ))

            # dialogue_turn_count must equal shot_packet audio turn count
            expected_turns = len(shot_packet.audio.turns)
            actual_turns = video_prompt.get("dialogue_turn_count", 0)
            if actual_turns != expected_turns:
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.DIALOGUE_FIT_CONSISTENCY,
                    frame_id=frame_id,
                    severity=IssueSeverity.WARNING,
                    description=(
                        f"dialogue_turn_count mismatch: video prompt has {actual_turns} "
                        f"but shot_packet.audio has {expected_turns} turn(s)"
                    ),
                    suggested_fix=(
                        "video prompt dialogue_turn_count must equal "
                        "len(shot_packet.audio.turns). Re-assemble from the current "
                        "shot packet."
                    ),
                ))

            # Image prompt must not carry subtitle rendering instructions
            img_text = image_prompt.get("prompt", "")
            for marker in _SUBTITLE_MARKERS:
                if img_text and marker.lower() in img_text.lower():
                    issues.append(PromptPairIssue(
                        category=PromptPairCategory.DIALOGUE_FIT_CONSISTENCY,
                        frame_id=frame_id,
                        severity=IssueSeverity.WARNING,
                        description=(
                            f"Image prompt contains subtitle/caption marker {marker!r} "
                            "— image generation does not render subtitles"
                        ),
                        suggested_fix=(
                            "Remove subtitle/caption language from the image prompt. "
                            "Image dialogue sections carry only sync cues and mood context, "
                            "not rendering instructions."
                        ),
                    ))

        else:
            # Silent frame: fit_status must be "no_dialogue"
            if fit_status and fit_status != "no_dialogue":
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.DIALOGUE_FIT_CONSISTENCY,
                    frame_id=frame_id,
                    severity=IssueSeverity.WARNING,
                    description=(
                        f"dialogue_present=False but video dialogue_fit_status={fit_status!r}"
                    ),
                    suggested_fix=(
                        "Set dialogue_fit_status='no_dialogue' for silent frames."
                    ),
                ))

            # dialogue_line must be absent/None
            if video_prompt.get("dialogue_line"):
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.DIALOGUE_FIT_CONSISTENCY,
                    frame_id=frame_id,
                    severity=IssueSeverity.WARNING,
                    description=(
                        f"dialogue_present=False but video dialogue_line is set: "
                        f"{video_prompt['dialogue_line']!r}"
                    ),
                    suggested_fix=(
                        "Clear dialogue_line when the frame has no dialogue coverage."
                    ),
                ))

        return issues

    # ── d. REFERENCE_LIST_PARITY ───────────────────────────────────────────

    def _check_reference_list_parity(
        self,
        image_prompt: dict,
        video_prompt: dict,
        shot_packet: ShotPacket,
    ) -> list[PromptPairIssue]:
        """d. Image ref list must be coherent with shot packet; video input must match image out.

        Checks:
          - image ref_images is non-empty
          - ref_images count does not exceed documented maximum (14)
          - If visible cast present, at least one cast composite ref is expected
          - video input_image_path filename matches image out_path filename
          - shot_packet_path is the same in both prompt dicts
        """
        issues: list[PromptPairIssue] = []
        frame_id = shot_packet.frame_id

        ref_images: list[str] = image_prompt.get("ref_images") or []
        visible_cast = shot_packet.visible_cast_ids or []

        # ref_images must not be empty
        if not ref_images:
            issues.append(PromptPairIssue(
                category=PromptPairCategory.REFERENCE_LIST_PARITY,
                frame_id=frame_id,
                severity=IssueSeverity.WARNING,
                description="Image prompt ref_images is empty — no reference images will be sent",
                suggested_fix=(
                    "resolve_ref_images should always return at least a cast composite "
                    "or location primary image. Verify asset files exist on disk."
                ),
            ))

        # ref_images must not exceed the documented cap
        if len(ref_images) > _MAX_REF_IMAGES:
            issues.append(PromptPairIssue(
                category=PromptPairCategory.REFERENCE_LIST_PARITY,
                frame_id=frame_id,
                severity=IssueSeverity.WARNING,
                description=(
                    f"Image prompt ref_images has {len(ref_images)} entries "
                    f"(documented max is {_MAX_REF_IMAGES})"
                ),
                suggested_fix=(
                    "The resolve_ref_images cap may be bypassed. "
                    "Verify the cap logic is applying correctly."
                ),
            ))

        # Visible cast members should have at least one cast composite in refs
        if visible_cast and ref_images:
            cast_refs = [
                r for r in ref_images
                if "cast" in r.lower() or "composite" in r.lower()
            ]
            if not cast_refs:
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.REFERENCE_LIST_PARITY,
                    frame_id=frame_id,
                    severity=IssueSeverity.WARNING,
                    description=(
                        f"Shot packet has {len(visible_cast)} visible cast member(s) "
                        "but no cast composite ref images found in ref_images"
                    ),
                    suggested_fix=(
                        "resolve_ref_images should include cast composites for each visible "
                        "cast member. Verify cast composite images exist in the project."
                    ),
                ))

        # video input_image_path filename must match image out_path filename
        img_out = (image_prompt.get("out_path") or "").strip()
        vid_input = (video_prompt.get("input_image_path") or "").strip()
        if img_out and vid_input:
            if Path(img_out).name != Path(vid_input).name:
                issues.append(PromptPairIssue(
                    category=PromptPairCategory.REFERENCE_LIST_PARITY,
                    frame_id=frame_id,
                    severity=IssueSeverity.ERROR,
                    description=(
                        f"Video input_image_path filename {Path(vid_input).name!r} does not "
                        f"match image out_path filename {Path(img_out).name!r}"
                    ),
                    suggested_fix=(
                        "assemble_video_prompt must set input_image_path to the composed "
                        "image file declared in image_prompt['out_path']."
                    ),
                ))

        # shot_packet_path must be consistent between both prompts
        img_sp = (image_prompt.get("shot_packet_path") or "").strip()
        vid_sp = (video_prompt.get("shot_packet_path") or "").strip()
        if img_sp and vid_sp and img_sp != vid_sp:
            issues.append(PromptPairIssue(
                category=PromptPairCategory.REFERENCE_LIST_PARITY,
                frame_id=frame_id,
                severity=IssueSeverity.WARNING,
                description=(
                    f"shot_packet_path differs between prompts: "
                    f"image={img_sp!r} vs video={vid_sp!r}"
                ),
                suggested_fix=(
                    "Both assemblers must write the same shot_packet_path for a given frame."
                ),
            ))

        return issues

    # ── e. RESTAGING_GUARD ─────────────────────────────────────────────────

    def _check_restaging_guard(
        self, image_prompt: dict, video_prompt: dict
    ) -> list[PromptPairIssue]:
        """e. Video prompt must contain "Do not restage" for guarded dialogue roles.

        Roles requiring an explicit restaging prohibition in the video prompt:
          - listener_reaction — tiny eye shifts, breath, and posture only
          - speaker_sync      — expression & breath only; preserve staging
          - bridge_coverage   — stitch exchange with minimal variation

        Without this guard the video model may spontaneously re-choreograph the
        scene, destroying eyeline locks and cast geography established by image gen.
        """
        issues: list[PromptPairIssue] = []
        frame_id = _frame_id_from_prompts(image_prompt, video_prompt)

        roles: list[str] = video_prompt.get("dialogue_coverage_roles") or []
        guarded = [r for r in roles if r in _RESTAGING_GUARD_ROLES]

        if not guarded:
            return []

        vid_text = video_prompt.get("prompt", "")
        if not _has_restaging_guard(vid_text):
            issues.append(PromptPairIssue(
                category=PromptPairCategory.RESTAGING_GUARD,
                frame_id=frame_id,
                severity=IssueSeverity.ERROR,
                description=(
                    f"Video prompt for dialogue role(s) {guarded} is missing "
                    "required 'Do not restage' guard language"
                ),
                suggested_fix=(
                    "Add an explicit restaging prohibition to the video prompt.\n"
                    "  listener_reaction / speaker_sync: "
                    "'Do not restage the room — tiny eye shifts, breath, and posture only.'\n"
                    "  bridge_coverage: "
                    "'Do not restage the room — stitch exchange with minimal variation; "
                    "one axis may change.'"
                ),
            ))

        return issues


# ─── Convenience Function ─────────────────────────────────────────────────────

def validate_all_prompt_pairs(
    graph: NarrativeGraph,
    image_prompts: dict[str, dict],
    video_prompts: dict[str, dict],
) -> list[PromptPairIssue]:
    """Run PromptPairValidator across all frames that have both prompt dicts.

    Iterates over every frame_id present in both *image_prompts* and
    *video_prompts* (in graph sequence order), builds the ShotPacket for each,
    and collects all issues.

    Frames present in only one of the two dicts are silently skipped.
    If a ShotPacket cannot be built for a frame, a CONTINUITY_PARITY ERROR is
    emitted for that frame and iteration continues.

    Args:
        graph:         Fully enriched NarrativeGraph (after Haiku enrichment).
        image_prompts: Mapping of frame_id → image prompt dict.
        video_prompts: Mapping of frame_id → video prompt dict.

    Returns:
        Flat list of PromptPairIssue objects across all validated frames,
        ordered by graph sequence then check category.
    """
    validator = PromptPairValidator()
    all_issues: list[PromptPairIssue] = []

    common_ids = set(image_prompts) & set(video_prompts)
    if not common_ids:
        return []

    # Process in graph sequence order for deterministic output
    if graph.frame_order:
        ordered_ids = [fid for fid in graph.frame_order if fid in common_ids]
    else:
        ordered_ids = sorted(common_ids)

    for frame_id in ordered_ids:
        if frame_id not in graph.frames:
            continue

        try:
            shot_packet = build_shot_packet(graph, frame_id)
        except Exception as exc:
            all_issues.append(PromptPairIssue(
                category=PromptPairCategory.CONTINUITY_PARITY,
                frame_id=frame_id,
                severity=IssueSeverity.ERROR,
                description=f"Could not build ShotPacket: {exc}",
                suggested_fix=(
                    "Ensure the graph is fully enriched and all referenced nodes exist. "
                    "Run the continuity validator first."
                ),
            ))
            continue

        all_issues.extend(
            validator.validate(image_prompts[frame_id], video_prompts[frame_id], shot_packet)
        )

    return all_issues


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate image/video prompt pair consistency across all frames.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        metavar="DIR",
        help="Project directory (must contain graph/narrative_graph.json and assembled prompts)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any WARNING issues exist (default: only ERRORs cause non-zero exit)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output issues as a JSON array",
    )
    args = parser.parse_args()

    if __package__:
        from .store import GraphStore
    else:
        from graph.store import GraphStore

    project_dir = Path(args.project_dir)
    store = GraphStore(project_dir)

    try:
        graph = store.load()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    # Load pre-assembled prompt JSON files written by prompt_assembler
    frames_dir = project_dir / "frames" / "prompts"
    video_dir = project_dir / "video" / "prompts"
    image_prompts: dict[str, dict] = {}
    video_prompts: dict[str, dict] = {}

    for frame_id in (graph.frame_order or list(graph.frames)):
        img_path = frames_dir / f"{frame_id}_image.json"
        vid_path = video_dir / f"{frame_id}_video.json"
        if img_path.exists():
            with img_path.open() as fh:
                image_prompts[frame_id] = json.load(fh)
        if vid_path.exists():
            with vid_path.open() as fh:
                video_prompts[frame_id] = json.load(fh)

    if not image_prompts and not video_prompts:
        print(
            "No assembled prompt files found. Run the prompt assembler first.",
            file=sys.stderr,
        )
        sys.exit(2)

    issues = validate_all_prompt_pairs(graph, image_prompts, video_prompts)
    errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
    warns  = [i for i in issues if i.severity == IssueSeverity.WARNING]

    if args.json_output:
        print(json.dumps([i.to_dict() for i in issues], indent=2))
    else:
        if not issues:
            print("✓ No prompt pair issues found.")
        else:
            col_w = 30
            for issue in issues:
                tag  = "ERROR" if issue.severity == IssueSeverity.ERROR else " WARN"
                print(
                    f"[{tag}] {issue.frame_id:>8} | "
                    f"{issue.category.value:<{col_w}} | "
                    f"{issue.description}"
                )

        print(
            f"\nSummary: {len(errors)} error(s), {len(warns)} warning(s) "
            f"— {len(issues)} total"
        )

    if errors:
        sys.exit(1)
    if args.strict and warns:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

"""
tests/test_corrective_fixes.py

Unit tests for the corrective fixes applied to the ScreenWire pipeline.
Verifies dynamic gate thresholds, image-size key resolution, refiner status
routing, and video-prompt tier compression — without touching the filesystem
or spawning subprocesses.
"""
import json
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from run_pipeline import (
    quality_gate_phase_1,
    quality_gate_phase_2,
    _resolve_regen_image_size,
    _refine_status_kind,
)
from graph.prompt_assembler import _serialize_video_prompt_sections


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_project(tmp_path: Path, *, output_size: str = "short") -> Path:
    """Minimal project scaffold under tmp_path for gate tests."""
    (tmp_path / "source_files").mkdir(parents=True)
    (tmp_path / "source_files" / "onboarding_config.json").write_text(
        json.dumps({"outputSize": output_size, "aspectRatio": "16:9"})
    )
    return tmp_path


def _make_phase1_project(
    tmp_path: Path,
    *,
    output_size: str = "short",
    num_scenes: int = 1,
    co_size: int = 6000,
) -> Path:
    base = _make_project(tmp_path, output_size=output_size)
    co_dir = base / "creative_output"
    co_dir.mkdir(parents=True)
    (co_dir / "creative_output.md").write_text("x" * co_size)
    (co_dir / "outline_skeleton.md").write_text("skeleton")
    scenes = co_dir / "scenes"
    scenes.mkdir()
    for i in range(num_scenes):
        (scenes / f"scene_{i:03d}.md").write_text("scene")
    return base


def _make_phase2_manifest(
    tmp_path: Path,
    *,
    output_size: str = "short",
    num_frames: int = 10,
    num_cast: int = 1,
    protagonist_role: str = "protagonist",
) -> Path:
    """Scaffold the bare minimum files quality_gate_phase_2 reads."""
    base = _make_project(tmp_path, output_size=output_size)

    cast_id = "cast_001_hero"
    frames = [
        {"frameId": f"f_{i:03d}", "castIds": [cast_id], "locationId": "loc_001"}
        for i in range(1, num_frames + 1)
    ]
    manifest = {
        "outputSize": output_size,
        "frames": frames,
        "cast": [{"castId": cast_id, "role": protagonist_role}],
    }
    (tmp_path / "project_manifest.json").write_text(json.dumps(manifest))

    # dialogue
    dialogue = [{"id": f"d{i}", "text": "hi"} for i in range(5)]
    (tmp_path / "dialogue.json").write_text(json.dumps(dialogue))

    # cast profiles
    cast_dir = tmp_path / "cast"
    cast_dir.mkdir()
    for i in range(num_cast):
        (cast_dir / f"cast_{i:03d}.json").write_text(json.dumps({"castId": f"cast_{i:03d}"}))

    # location
    loc_dir = tmp_path / "locations"
    loc_dir.mkdir()
    (loc_dir / "loc_001.json").write_text(json.dumps({"locationId": "loc_001"}))

    # graph — skip (gate falls back gracefully on import error)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — Dynamic gates
# ─────────────────────────────────────────────────────────────────────────────

class TestDynamicGatePhase1:
    def test_short_accepts_single_scene(self, tmp_path):
        """A 'short' project with exactly one scene draft must pass the gate."""
        base = _make_phase1_project(tmp_path, output_size="short", num_scenes=1)
        issues = quality_gate_phase_1(base)
        assert issues == [], f"Unexpected gate failures: {issues}"

    def test_medium_requires_two_scenes(self, tmp_path):
        """A 'medium' project with only one scene draft must fail the gate."""
        base = _make_phase1_project(tmp_path, output_size="medium", num_scenes=1)
        issues = quality_gate_phase_1(base)
        assert any("scene draft" in i for i in issues), (
            f"Expected scene-draft failure for medium project, got: {issues}"
        )

    def test_medium_passes_with_two_scenes(self, tmp_path):
        """A 'medium' project with two scene drafts must pass the scene check."""
        base = _make_phase1_project(tmp_path, output_size="medium", num_scenes=2)
        scene_issues = [i for i in quality_gate_phase_1(base) if "scene draft" in i]
        assert scene_issues == [], f"Unexpected scene issues: {scene_issues}"


class TestDynamicGatePhase2:
    def test_short_single_protagonist_accepts_one_cast(self, tmp_path):
        """short + single protagonist: one cast profile should satisfy the gate."""
        base = _make_phase2_manifest(
            tmp_path,
            output_size="short",
            num_frames=10,
            num_cast=1,
            protagonist_role="protagonist",
        )
        issues = quality_gate_phase_2(base)
        cast_issues = [i for i in issues if "cast profile" in i]
        assert cast_issues == [], f"Unexpected cast issues: {cast_issues}"

    def test_non_short_requires_two_cast(self, tmp_path):
        """non-short project with only one cast profile should surface the gap."""
        base = _make_phase2_manifest(
            tmp_path,
            output_size="medium",
            num_frames=10,
            num_cast=1,
            protagonist_role="protagonist",
        )
        issues = quality_gate_phase_2(base)
        cast_issues = [i for i in issues if "cast profile" in i]
        assert cast_issues, (
            "Expected cast-profile failure for medium project with 1 cast, got none"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Task 2/3 — _resolve_regen_image_size
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRegenImageSize:
    def _fake_path(self, name: str = "test_prop.json") -> Path:
        return Path(f"/fake/prompts/{name}")

    def test_prefers_size_key(self):
        """When both keys present and equal, returns without error."""
        result = _resolve_regen_image_size(
            {"size": "landscape_16_9", "image_size": "landscape_16_9"},
            prompt_file=self._fake_path(),
        )
        assert result == "landscape_16_9"

    def test_size_key_alone(self):
        result = _resolve_regen_image_size(
            {"size": "portrait_9_16"},
            prompt_file=self._fake_path(),
        )
        assert result == "portrait_9_16"

    def test_warns_on_legacy_key(self, capfd):
        """Legacy 'image_size' key is accepted but should produce a warning."""
        result = _resolve_regen_image_size(
            {"image_size": "square_hd"},
            prompt_file=self._fake_path("loc_rooftop_location.json"),
        )
        assert result == "square_hd"
        # The warning is emitted via log_warn which writes to stdout
        captured = capfd.readouterr()
        assert "image_size" in captured.out or "image_size" in captured.err, (
            "Expected a log_warn mentioning the legacy 'image_size' key"
        )

    def test_raises_on_missing_both(self):
        """Prompt with neither key must raise ValueError."""
        with pytest.raises(ValueError, match="missing both"):
            _resolve_regen_image_size({}, prompt_file=self._fake_path())

    def test_raises_on_conflicting_keys(self):
        """Conflicting size/image_size values must raise ValueError."""
        with pytest.raises(ValueError, match="conflicting"):
            _resolve_regen_image_size(
                {"size": "landscape_16_9", "image_size": "portrait_9_16"},
                prompt_file=self._fake_path(),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Task 4 — _refine_status_kind routing
# ─────────────────────────────────────────────────────────────────────────────

class TestRefineStatusKind:
    def test_routes_correctly(self):
        assert _refine_status_kind("grok-vision") == "refined"
        assert _refine_status_kind("skipped:no_image") == "skipped"
        assert _refine_status_kind("skipped:rate_limit") == "skipped"
        assert _refine_status_kind("failed:HTTPError") == "failed"
        assert _refine_status_kind("failed:TimeoutError") == "failed"
        assert _refine_status_kind("") == "unknown"
        assert _refine_status_kind("unknown_value") == "unknown"

    def test_case_insensitive(self):
        assert _refine_status_kind("SKIPPED:foo") == "skipped"
        assert _refine_status_kind("FAILED:bar") == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — _serialize_video_prompt_sections Tier 1 preservation
# ─────────────────────────────────────────────────────────────────────────────

class TestSerializeVideoPromptTier1:
    def _make_sections(self, *, bloat_chars: int = 0) -> list[str]:
        """Build a section list with Tier 1 blocks intact plus optional bloat."""
        tier3_bloat = "BACKGROUND:\n" + ("x " * (bloat_chars // 2)) if bloat_chars else ""
        return [
            "A cinematic shot of a rooftop at dusk.",
            "AUDIO:\nDialogue: 'We need to move now.'\nAmbient: City hum.",
            "MOTION CONTINUITY:\nCarry forward the rain-soaked coat from f_003.",
            tier3_bloat,
        ]

    def test_short_prompt_passes_through(self):
        """A prompt well under 4096 chars should be returned unchanged."""
        sections = self._make_sections()
        result = _serialize_video_prompt_sections(sections)
        assert "AUDIO:" in result
        assert "MOTION CONTINUITY:" in result

    def test_tier3_dropped_before_tier1(self):
        """When over the limit, Tier 3 blocks are dropped before Tier 1 blocks."""
        # Generate enough BACKGROUND bloat to push over 4096 chars
        sections = self._make_sections(bloat_chars=4000)
        # Should not raise — Tier 3 BACKGROUND is dropped to fit
        result = _serialize_video_prompt_sections(sections)
        assert "AUDIO:" in result, "Tier 1 AUDIO block was dropped — must not happen"
        assert "MOTION CONTINUITY:" in result, "Tier 1 MOTION CONTINUITY was dropped — must not happen"

    def test_raises_when_tier1_alone_exceeds_limit(self):
        """If Tier 1 blocks alone exceed the char limit, a ValueError must be raised."""
        from graph.prompt_assembler import MAX_VIDEO_PROMPT_CHARS
        huge_audio = "AUDIO:\n" + ("dialogue words " * (MAX_VIDEO_PROMPT_CHARS // 10))
        huge_continuity = "MOTION CONTINUITY:\n" + ("carry forward " * (MAX_VIDEO_PROMPT_CHARS // 10))
        sections = [huge_audio, huge_continuity]
        with pytest.raises(ValueError, match="tier1_block_sizes"):
            _serialize_video_prompt_sections(sections)

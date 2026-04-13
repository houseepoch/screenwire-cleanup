from __future__ import annotations

import json
from pathlib import Path

from project_report import generate_project_report
from video_prompt_projection import build_video_request_projection


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    _write(
        project_dir / "project_manifest.json",
        json.dumps({"project": {"id": "p1"}, "phases": {"phase_0": {"status": "complete"}}}, indent=2),
    )
    _write(project_dir / "creative_output" / "outline_skeleton.md", "# Outline\n")
    _write(project_dir / "creative_output" / "creative_output.md", "# Creative Output\n")
    _write(
        project_dir / "graph" / "narrative_graph.json",
        json.dumps(
            {
                "project": {"project_id": "p1"},
                "cast": {"cast_001": {"cast_id": "cast_001", "name": "Monday"}},
                "locations": {"loc_001": {"location_id": "loc_001", "name": "Apartment"}},
                "props": {},
                "scenes": {"scene_01": {"scene_id": "scene_01"}},
                "frames": {
                    "f_001": {
                        "frame_id": "f_001",
                        "scene_id": "scene_01",
                        "location_id": "loc_001",
                        "formula_tag": "F01",
                    },
                    "f_002": {
                        "frame_id": "f_002",
                        "scene_id": "scene_01",
                        "location_id": "loc_001",
                        "formula_tag": "F01",
                    },
                },
                "dialogue": {},
                "storyboard_grids": {},
                "cast_frame_states": {},
                "prop_frame_states": {},
                "location_frame_states": {},
                "edges": [
                    {
                        "edge_id": "cast_001__appears_in__f_001",
                        "source_id": "cast_001",
                        "target_id": "f_001",
                        "edge_type": "appears_in",
                    }
                ],
            },
            indent=2,
        ),
    )
    _write(
        project_dir / "frames" / "prompts" / "f_001_image.json",
        json.dumps({"frame_id": "f_001", "prompt": "image prompt", "reference_images": ["locations/primary/loc_001.png"]}, indent=2),
    )
    _write(
        project_dir / "frames" / "prompts" / "f_002_image.json",
        json.dumps({"frame_id": "f_002", "prompt": "image prompt 2", "reference_images": ["locations/primary/loc_001.png"]}, indent=2),
    )
    _write(
        project_dir / "video" / "prompts" / "f_001_video.json",
        json.dumps(
            {
                "frame_id": "f_001",
                "scene_id": "scene_01",
                "prompt": (
                    "Generate a cinematic motion clip with native audio.\n\n"
                    "AUDIO:\n"
                    "- Native spoken dialogue is required in this clip.\n"
                    '- Monday: "I can do this." | delivery steady\n'
                    "- Ambient layers: room tone"
                ),
                "dialogue_present": True,
                "dialogue_line": "I can do this.",
                "dialogue_turn_count": 1,
                "dialogue_fit_status": "fits",
                "camera_motion": "static",
                "shot_type": "medium_close_up",
                "input_image_path": "frames/composed/f_001_gen.png",
                "cast_bible_snapshot": {
                    "characters": [
                        {
                            "character_id": "cast_001",
                            "name": "Monday",
                            "pose": {
                                "modifiers": [
                                    "screen_position:frame_center",
                                    "facing_direction:toward_camera",
                                    "looking_at:camera",
                                ]
                            },
                        }
                    ]
                },
                "directing": {"camera_motivation": "Stay intimate on the turn."},
            },
            indent=2,
        ),
    )
    _write(project_dir / "reports" / "project_cover_summary.md", "# Cover Summary\nA theatrical summary.\n")
    _write(
        project_dir / "reports" / "project_cover_meta.json",
        json.dumps(
            {
                "summary": "A theatrical summary.",
                "tagline": "Trust the signal.",
                "topEntities": [
                    {"entityId": "cast_001", "name": "Monday", "frameCount": 2},
                    {"entityId": "loc_001", "name": "Apartment", "frameCount": 2},
                    {"entityId": "prop_001", "name": "Phone", "frameCount": 1},
                ],
            },
            indent=2,
        ),
    )
    cover = project_dir / "reports" / "project_cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"png")
    _write(project_dir / "scripts" / "custom.py", "print('hi')\n")
    composed = project_dir / "frames" / "composed" / "f_001_gen.png"
    composed.parent.mkdir(parents=True, exist_ok=True)
    composed.write_bytes(b"png")
    return project_dir


def test_generate_project_report_includes_snapshot_and_excludes_media(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)

    report_path = generate_project_report(project_dir)
    report = report_path.read_text(encoding="utf-8")

    assert report_path == project_dir / "reports" / "project_report.md"
    assert "## Snapshot Tree" in report
    assert "## Video Prompt Projection" in report
    assert "## Project Cover" in report
    assert "reports/video_prompt_projection.md" in report
    assert "reports/project_cover.png" in report
    assert "reports/project_cover_summary.md" in report
    assert "Trust the signal." in report
    assert "Monday (2), Apartment (2), Phone (1)" in report
    assert "creative_output/outline_skeleton.md" in report
    assert "frames/prompts/f_001_image.json" in report
    assert "video/prompts/f_001_video.json" in report
    assert "graph/narrative_graph.json" in report
    assert "└── composed" not in report
    assert "## Output Coverage" in report
    assert "- Planned graph frames: `2`" in report
    assert "- Composed frames generated: `1`" in report
    assert "- Missing composed frames: `1`" in report
    assert "`f_002`" in report
    assert "`loc_001`: `1/2` (`50.0%`)" in report
    assert "## Graph Edge Usage" in report
    assert "`appears_in`: `1`" in report
    assert (project_dir / "reports" / "video_prompt_projection.md").exists()
    projection = (project_dir / "reports" / "video_prompt_projection.md").read_text(encoding="utf-8")
    assert "#### Dialogue Prefix" in projection
    assert "I can do this." in projection


def test_build_video_request_projection_splits_dialogue_prefix_from_motion_prompt() -> None:
    payload = build_video_request_projection(
        {
            "frame_id": "f_001",
            "prompt": (
                "Generate a cinematic motion clip with native audio.\n\n"
                "AUDIO:\n"
                "- Native spoken dialogue is required in this clip.\n"
                '- Monday: "I can do this." | delivery steady\n'
                "- Ambient layers: room tone"
            ),
            "dialogue_present": True,
            "dialogue_line": "I can do this.",
            "dialogue_turn_count": 1,
            "camera_motion": "static",
            "shot_type": "medium_close_up",
            "cast_bible_snapshot": {"characters": []},
            "directing": {"camera_motivation": "Stay intimate."},
        }
    )

    assert payload["dialogue_text"] == "I can do this."
    assert 'Monday: "I can do this."' not in payload["motion_prompt"]
    assert payload["full_prompt"].startswith("I can do this.")


def test_generate_project_report_archives_previous_report(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)

    first_report = generate_project_report(project_dir)
    assert first_report.exists()

    _write(project_dir / "creative_output" / "creative_output.md", "# Creative Output\nUpdated\n")
    second_report = generate_project_report(project_dir)

    archive_reports = sorted((project_dir / "reports" / "archive").glob("*/project_report.md"))
    assert second_report.exists()
    assert archive_reports
    assert "# Project Report" in archive_reports[-1].read_text(encoding="utf-8")

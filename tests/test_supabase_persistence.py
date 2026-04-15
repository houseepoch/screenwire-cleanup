from __future__ import annotations

import json
from pathlib import Path

from supabase_persistence import guess_asset_kind, project_metadata_from_dir, should_persist_rel_path


def test_guess_asset_kind_classifies_core_paths() -> None:
    assert guess_asset_kind("source_files/pitch.md") == "source_file"
    assert guess_asset_kind("frames/composed/f_001_gen.png") == "frame_image"
    assert guess_asset_kind("video/export/project_demo_final.mp4") == "video_export"
    assert guess_asset_kind("graph/narrative_graph.json") == "graph_artifact"


def test_should_persist_rel_path_skips_cache_and_queue() -> None:
    assert should_persist_rel_path("frames/composed/f_001_gen.png") is True
    assert should_persist_rel_path(".cache/thumbnails/example.webp") is False
    assert should_persist_rel_path("dispatch/manifest_queue/update.json") is False


def test_project_metadata_from_dir_reads_manifest_and_onboarding(tmp_path: Path) -> None:
    (tmp_path / "source_files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project_manifest.json").write_text(
        json.dumps(
            {
                "projectId": "sw_lg_demo_project",
                "projectName": "Demo Project",
                "slug": "demo-project",
                "phases": {
                    "phase_0": {"status": "complete"},
                    "phase_1": {"status": "complete"},
                    "phase_2": {"status": "ready"},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "source_files" / "onboarding_config.json").write_text(
        json.dumps(
            {
                "frameBudget": 120,
                "mediaStyle": "live_clear",
                "creativeFreedom": "balanced",
                "aspectRatio": "16:9",
                "pipeline": "story_upload",
            }
        ),
        encoding="utf-8",
    )

    metadata = project_metadata_from_dir(tmp_path)

    assert metadata["id"] == tmp_path.name
    assert metadata["manifest_project_id"] == "sw_lg_demo_project"
    assert metadata["name"] == "Demo Project"
    assert metadata["slug"] == "demo-project"
    assert metadata["status"] == "ready"
    assert metadata["metadata"]["frameBudget"] == 120

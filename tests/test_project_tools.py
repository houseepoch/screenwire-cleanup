from __future__ import annotations

import json
from pathlib import Path

import llm.project_tools as project_tools


def test_build_project_tools_exposes_morpheus_media_and_research_tools() -> None:
    names = {tool["name"] for tool in project_tools.build_project_tools()}
    assert "query_graph_database" in names
    assert "get_frame_context" in names
    assert "grep_project_research" in names
    assert "generate_image_with_nanobanana" in names
    assert "edit_image_with_nanobanana" in names
    assert "generate_video_with_grok" in names
    assert "extend_video_with_grok" in names


def test_tool_executor_supports_query_and_grep(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    repo_root = tmp_path / "repo"
    skills_dir = repo_root / "skills"
    project_root.mkdir()
    repo_root.mkdir()
    skills_dir.mkdir()
    (project_root / "creative_output").mkdir()
    (project_root / "creative_output" / "creative_output.md").write_text(
        "Dialogue density is low here.\nAnother line.\n",
        encoding="utf-8",
    )

    class _FakeStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def exists(self):
            return True

        def load(self):
            return object()

    monkeypatch.setattr(project_tools, "GraphStore", _FakeStore)
    monkeypatch.setattr(
        project_tools,
        "query_graph",
        lambda _graph, node_type, filters=None: [
            {"node_type": node_type, "filters": filters or {}, "id": "scene_01"}
        ],
    )
    monkeypatch.setattr(
        project_tools,
        "get_frame_context",
        lambda _graph, frame_id: {"frame": {"frame_id": frame_id}, "scene": None},
    )

    execute = project_tools.make_project_tool_executor(
        project_root=project_root,
        repo_root=repo_root,
        skills_dir=skills_dir,
    )

    query_result = json.loads(
        execute(
            "query_graph_database",
            json.dumps({"node_type": "scene", "filters": {"location_id": "loc_001"}}),
        )
    )
    grep_result = json.loads(
        execute(
            "grep_project_research",
            json.dumps({"pattern": "dialogue density", "path": "creative_output"}),
        )
    )
    context_result = json.loads(
        execute("get_frame_context", json.dumps({"frame_id": "f_001"}))
    )

    assert query_result[0]["node_type"] == "scene"
    assert query_result[0]["filters"] == {"location_id": "loc_001"}
    assert grep_result[0]["path"] == "creative_output/creative_output.md"
    assert context_result["frame"]["frame_id"] == "f_001"


def test_tool_executor_media_routes_hit_internal_handlers(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    repo_root = tmp_path / "repo"
    skills_dir = repo_root / "skills"
    for path in (project_root, repo_root, skills_dir):
        path.mkdir(parents=True, exist_ok=True)
    (project_root / "inputs").mkdir()
    (project_root / "inputs" / "source.png").write_bytes(b"png")
    (project_root / "inputs" / "clip.mp4").write_bytes(b"mp4")

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    calls: list[tuple[str, dict]] = []

    def _fake_post(url: str, json: dict, timeout: float):
        calls.append((url, json))
        return _Response({"success": True, "path": json["output_path"]})

    monkeypatch.setattr(project_tools.httpx, "post", _fake_post)
    monkeypatch.setenv("SW_PORT", "8123")

    execute = project_tools.make_project_tool_executor(
        project_root=project_root,
        repo_root=repo_root,
        skills_dir=skills_dir,
    )
    json.loads(
        execute(
            "generate_image_with_nanobanana",
            json.dumps({"prompt": "test", "output_path": "frames/test.png"}),
        )
    )
    json.loads(
        execute(
            "edit_image_with_nanobanana",
            json.dumps(
                {
                    "input_path": "inputs/source.png",
                    "prompt": "make it blue",
                    "output_path": "frames/test_edit.png",
                }
            ),
        )
    )
    json.loads(
        execute(
            "generate_video_with_grok",
            json.dumps(
                {
                    "image_path": "inputs/source.png",
                    "prompt": "camera moves slowly",
                    "output_path": "video/test.mp4",
                }
            ),
        )
    )
    json.loads(
        execute(
            "extend_video_with_grok",
            json.dumps(
                {
                    "video_path": "inputs/clip.mp4",
                    "prompt": "continue the scene",
                    "output_path": "video/test_ext.mp4",
                }
            ),
        )
    )

    routes = [url.removeprefix("http://127.0.0.1:8123") for url, _payload in calls]
    assert routes == [
        "/internal/fresh-generation",
        "/internal/edit-image",
        "/internal/generate-video",
        "/internal/extend-video",
    ]
    assert calls[1][1]["input_path"].endswith("inputs/source.png")
    assert calls[3][1]["video_path"].endswith("inputs/clip.mp4")

from __future__ import annotations

from pathlib import Path

from build_api_reference import discover_source_files, render_api_reference


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_discover_source_files_limits_to_top_level_api_fragments(tmp_path: Path) -> None:
    source_dir = tmp_path / "AGENT_READ_HERE_FIRST"
    _write(source_dir / "API_TOOL_REFERENCE.md", "# API Tool Reference\n")
    _write(source_dir / "api_openai.md", "# OpenAI\n")
    _write(source_dir / "replicate_alpha.md", "# Alpha\n")
    _write(source_dir / "README.md", "# Ignore me\n")
    _write(source_dir / "nested" / "replicate_nested.md", "# Nested\n")

    sources = discover_source_files(source_dir)

    assert [path.name for path in sources] == [
        "API_TOOL_REFERENCE.md",
        "api_openai.md",
        "replicate_alpha.md",
    ]


def test_render_api_reference_includes_sources_and_normalizes_titles(tmp_path: Path) -> None:
    source_dir = tmp_path / "AGENT_READ_HERE_FIRST"
    api_source = source_dir / "API_TOOL_REFERENCE.md"
    replicate_source = source_dir / "replicate_p-image.md"

    _write(
        api_source,
        "# API & Tool Reference\n\nShared contract notes.\n",
    )
    _write(
        replicate_source,
        "# Replicate: P-Image\n\nModel details.\n",
    )

    content = render_api_reference(source_dir, [api_source, replicate_source])

    assert "# ScreenWire AI - API Reference" in content
    assert "`AGENT_READ_HERE_FIRST/API_TOOL_REFERENCE.md`" in content
    assert "`AGENT_READ_HERE_FIRST/replicate_p-image.md`" in content
    assert "## API & Tool Reference" in content
    assert "## Replicate: P-Image" in content
    assert "\nShared contract notes.\n" in content
    assert "\nModel details.\n" in content
    assert "# Replicate: P-Image\n\nModel details." not in content

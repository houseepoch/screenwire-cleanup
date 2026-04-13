from __future__ import annotations

from pathlib import Path

from build_architecture_reports import (
    DependencyEdge,
    analyze_python_dependencies,
    archive_existing_outputs,
    collect_repo_files,
    render_repo_snapshot_index,
    render_repo_snapshot_parts,
)


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def test_collect_repo_files_excludes_docs_architecture_output_and_git(tmp_path: Path) -> None:
    _write(tmp_path / ".git" / "config", "ignored")
    _write(tmp_path / ".env", "SECRET=value")
    _write(tmp_path / "docs" / "guide.md", "ignored")
    _write(tmp_path / "docs" / "Architecture" / "0_archived" / "stamp" / "old.md", "ignored")
    _write(tmp_path / "docs" / "Architecture" / "10_repo_snapshot.md", "generated")
    _write(tmp_path / "projects" / "demo.txt", "ignored")
    _write(tmp_path / "tests" / "projects" / "fixture.txt", "ignored")
    _write(tmp_path / "src" / "module.py", "print('ok')\n")

    files = collect_repo_files(tmp_path, output_dir=tmp_path / "docs" / "Architecture")

    assert [entry.relative_path for entry in files] == ["src/module.py"]


def test_render_repo_snapshot_includes_text_and_binary_sections(tmp_path: Path) -> None:
    text_path = tmp_path / "notes.md"
    binary_path = tmp_path / "clip.mp4"
    env_path = tmp_path / ".env"
    _write(text_path, "# Note\nbody\n")
    _write(binary_path, b"\x00\x01\x02")
    _write(env_path, "REPLICATE_API_TOKEN=r8_secretvalue123456\nXAI_API_KEY=xai-supersecret987654321\n")

    files = collect_repo_files(tmp_path, output_dir=tmp_path / "docs" / "Architecture")
    parts = render_repo_snapshot_parts(tmp_path, files, max_part_bytes=1024)
    content = "".join(part.content for part in parts)

    assert "## `.env`" not in content
    assert "REPLICATE_API_TOKEN" not in content
    assert "XAI_API_KEY" not in content
    assert "r8_secretvalue123456" not in content
    assert "xai-supersecret987654321" not in content
    assert "## `notes.md`" in content
    assert "```md" in content
    assert "# Note" in content
    assert "## `clip.mp4`" in content
    assert "- Type: binary" in content
    assert "- SHA256:" in content


def test_render_repo_snapshot_splits_into_multiple_parts(tmp_path: Path) -> None:
    _write(tmp_path / "huge.txt", "abcde\n" * 200)

    files = collect_repo_files(tmp_path, output_dir=tmp_path / "docs" / "Architecture")
    parts = render_repo_snapshot_parts(tmp_path, files, max_part_bytes=700)

    assert len(parts) >= 2
    assert parts[0].file_name == "10_repo_snapshot_part_001.md"
    assert parts[1].file_name == "10_repo_snapshot_part_002.md"
    for part in parts:
        assert len(part.content.encode("utf-8")) <= 700


def test_render_repo_snapshot_index_lists_all_parts(tmp_path: Path) -> None:
    _write(tmp_path / "a.txt", "hello\n")
    files = collect_repo_files(tmp_path, output_dir=tmp_path / "docs" / "Architecture")
    parts = render_repo_snapshot_parts(tmp_path, files, max_part_bytes=1024)

    index_doc = render_repo_snapshot_index(
        tmp_path,
        files,
        parts,
        max_part_bytes=1024,
    )

    assert "# Repo Snapshot Index" in index_doc
    assert "10_repo_snapshot_part_001.md" in index_doc
    assert "Snapshot part count" in index_doc


def test_analyze_python_dependencies_resolves_internal_and_external_imports(tmp_path: Path) -> None:
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(
        tmp_path / "pkg" / "a.py",
        "import os\nimport httpx\nfrom . import b\nfrom pkg import c\n",
    )
    _write(tmp_path / "pkg" / "b.py", "from pkg import c\n")
    _write(tmp_path / "pkg" / "c.py", "value = 1\n")

    analysis = analyze_python_dependencies(tmp_path, output_dir=tmp_path / "docs" / "Architecture")

    assert DependencyEdge("pkg.a", "pkg.b") in analysis.internal_edges
    assert DependencyEdge("pkg.a", "pkg.c") in analysis.internal_edges
    assert DependencyEdge("pkg.b", "pkg.c") in analysis.internal_edges
    assert analysis.stdlib_imports["os"] == 1
    assert analysis.external_imports["httpx"] == 1


def test_archive_existing_outputs_rotates_prior_generated_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "docs" / "Architecture"
    archive_dir = output_dir / "0_archived"
    _write(output_dir / "00_architecture_summary.md", "old summary")
    _write(output_dir / "20_python_dependency_report.md", "old report")
    _write(output_dir / "README.md", "keep me")
    _write(output_dir / "run_architecture_reports.sh", "#!/usr/bin/env bash\n")
    _write(output_dir / "nested" / "notes.md", "leave me")
    _write(output_dir / "notes.md", "leave me")

    archived = archive_existing_outputs(output_dir, archive_dir)

    archived_rel = sorted(path.relative_to(archive_dir).as_posix() for path in archived)
    assert len(archived_rel) == 2
    stamp_dirs = {rel.split("/", 1)[0] for rel in archived_rel}
    assert len(stamp_dirs) == 1
    stamp_dir = next(iter(stamp_dirs))
    assert (archive_dir / stamp_dir / "00_architecture_summary.md").exists()
    assert (archive_dir / stamp_dir / "20_python_dependency_report.md").exists()
    assert not (output_dir / "00_architecture_summary.md").exists()
    assert not (output_dir / "20_python_dependency_report.md").exists()
    assert (output_dir / "README.md").exists()
    assert (output_dir / "run_architecture_reports.sh").exists()
    assert (output_dir / "notes.md").exists()
    assert (output_dir / "nested" / "notes.md").exists()

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from telemetry import iso_now

from .materializer import materialize_manifest
from .schema import NarrativeGraph, Provenance
from .store import GraphStore


def _actor_name(actor: str = "", phase: str = "") -> str:
    if actor and phase:
        return f"{actor}:{phase}"
    return actor or phase


def touch_provenance(
    provenance: Provenance,
    *,
    run_id: str = "",
    actor: str = "",
    phase: str = "",
) -> None:
    """Apply run-scoped mutation metadata to a provenance record."""
    now = iso_now()
    if run_id:
        provenance.run_id = run_id
    if not provenance.created_at:
        provenance.created_at = now
    provenance.last_modified_at = now
    actor_name = _actor_name(actor, phase)
    if actor_name:
        provenance.last_modified_by = actor_name


def iter_graph_provenances(graph: NarrativeGraph) -> Iterable[Provenance]:
    """Yield every provenance record reachable from the canonical graph."""
    if hasattr(graph.project, "provenance"):
        yield graph.project.provenance
    yield graph.world.provenance
    yield graph.visual.provenance

    for registry in (
        graph.cast,
        graph.locations,
        graph.props,
        graph.scenes,
        graph.frames,
        graph.dialogue,
        graph.storyboard_grids,
        graph.cast_frame_states,
        graph.prop_frame_states,
        graph.location_frame_states,
    ):
        for node in registry.values():
            if hasattr(node, "provenance"):
                yield node.provenance

    for edge in graph.edges:
        if hasattr(edge, "provenance"):
            yield edge.provenance


def stamp_graph_run(
    graph: NarrativeGraph,
    *,
    run_id: str = "",
    actor: str = "",
    phase: str = "",
) -> None:
    """Stamp every graph provenance record with the active run context."""
    for provenance in iter_graph_provenances(graph):
        touch_provenance(provenance, run_id=run_id, actor=actor, phase=phase)


def project_relative_path(project_dir: str | Path, path_value: str | Path | None) -> str | None:
    """Convert a path to a project-relative POSIX string when possible."""
    if path_value is None:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        return path.as_posix()

    project_path = Path(project_dir)
    try:
        return path.relative_to(project_path).as_posix()
    except ValueError:
        return str(path)


def mark_cast_asset(
    graph: NarrativeGraph,
    cast_id: str,
    path_value: str | Path,
    *,
    run_id: str = "",
    actor: str = "",
    phase: str = "",
) -> None:
    cast = graph.cast.get(cast_id)
    if cast is None:
        return
    cast.composite_path = str(path_value)
    cast.composite_status = "generated"
    touch_provenance(cast.provenance, run_id=run_id, actor=actor, phase=phase)


def mark_location_asset(
    graph: NarrativeGraph,
    location_id: str,
    path_value: str | Path,
    *,
    run_id: str = "",
    actor: str = "",
    phase: str = "",
) -> None:
    location = graph.locations.get(location_id)
    if location is None:
        return
    location.primary_image_path = str(path_value)
    location.image_status = "generated"
    touch_provenance(location.provenance, run_id=run_id, actor=actor, phase=phase)


def mark_prop_asset(
    graph: NarrativeGraph,
    prop_id: str,
    path_value: str | Path,
    *,
    run_id: str = "",
    actor: str = "",
    phase: str = "",
) -> None:
    prop = graph.props.get(prop_id)
    if prop is None:
        return
    prop.image_path = str(path_value)
    touch_provenance(prop.provenance, run_id=run_id, actor=actor, phase=phase)


def mark_storyboard_asset(
    graph: NarrativeGraph,
    grid_id: str,
    *,
    composite_path: str | Path | None = None,
    cell_image_dir: str | Path | None = None,
    run_id: str = "",
    actor: str = "",
    phase: str = "",
) -> None:
    grid = graph.storyboard_grids.get(grid_id)
    if grid is None:
        return
    if composite_path:
        if grid.composite_image_path and grid.composite_image_path != str(composite_path):
            grid.storyboard_history.append(grid.composite_image_path)
        grid.composite_image_path = str(composite_path)
    if cell_image_dir:
        grid.cell_image_dir = str(cell_image_dir)
    grid.storyboard_status = "generated"
    touch_provenance(grid.provenance, run_id=run_id, actor=actor, phase=phase)


def mark_frame_composed(
    graph: NarrativeGraph,
    frame_id: str,
    path_value: str | Path,
    *,
    refs_used: list[str] | None = None,
    run_id: str = "",
    actor: str = "",
    phase: str = "",
) -> None:
    frame = graph.frames.get(frame_id)
    if frame is None:
        return
    frame.composed_image_path = str(path_value)
    if refs_used is not None:
        frame.refs_used = list(refs_used)
    frame.status = "image_composed"
    touch_provenance(frame.provenance, run_id=run_id, actor=actor, phase=phase)


def mark_frame_video(
    graph: NarrativeGraph,
    frame_id: str,
    path_value: str | Path,
    *,
    run_id: str = "",
    actor: str = "",
    phase: str = "",
) -> None:
    frame = graph.frames.get(frame_id)
    if frame is None:
        return
    frame.video_path = str(path_value)
    frame.status = "video_complete"
    touch_provenance(frame.provenance, run_id=run_id, actor=actor, phase=phase)


def save_graph_projection(
    graph: NarrativeGraph,
    project_dir: str | Path,
    *,
    store: GraphStore | None = None,
) -> Path:
    """Persist the canonical graph and refresh the manifest projection."""
    project_path = Path(project_dir)
    graph_store = store or GraphStore(project_path)
    graph_store.save(graph)
    materialize_manifest(graph, project_path / "project_manifest.json")
    return graph_store.graph_path

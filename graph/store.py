"""
Graph Store — Persistence layer for the NarrativeGraph
=======================================================

JSON-file-backed graph store. The master graph lives at
`graph/narrative_graph.json` in the project directory.

Morpheus interacts with the graph exclusively through this module.
All mutations are atomic (write-to-temp, then rename).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from .schema import NarrativeGraph, ProjectNode


class GraphStore:
    """Read/write the master NarrativeGraph from/to disk."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self.graph_dir = self.project_dir / "graph"
        self.graph_path = self.graph_dir / "narrative_graph.json"
        self.queue_dir = self.graph_dir / "assembly_queue"
        self.committed_dir = self.graph_dir / "committed"
        self._graph: Optional[NarrativeGraph] = None

    def ensure_dirs(self) -> None:
        """Create graph directories if they don't exist."""
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.committed_dir.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        """Check if a master graph file exists."""
        return self.graph_path.exists()

    def load(self) -> NarrativeGraph:
        """Load the master graph from disk."""
        if not self.graph_path.exists():
            raise FileNotFoundError(
                f"No master graph at {self.graph_path}. "
                "Use initialize() to create one."
            )
        raw = json.loads(self.graph_path.read_text(encoding="utf-8"))
        self._graph = NarrativeGraph.model_validate(raw)
        return self._graph

    def save(self, graph: Optional[NarrativeGraph] = None) -> Path:
        """Atomically save the master graph to disk.

        Writes to a temp file first, then renames. This prevents
        corruption if the process is interrupted mid-write.
        """
        g = graph or self._graph
        if g is None:
            raise ValueError("No graph to save. Load or initialize first.")
        if not isinstance(g, NarrativeGraph):
            raise TypeError(f"Expected NarrativeGraph, got {type(g).__name__}")

        # Re-validate before writing so direct in-memory mutations cannot bypass
        # the canonical graph contract.
        g = NarrativeGraph.model_validate(g.model_dump())

        self.ensure_dirs()

        # Atomic write: temp file → rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.graph_dir), suffix=".tmp", prefix="graph_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(g.model_dump_json(indent=2))
            # Atomic replace — safe on both Windows and POSIX
            os.replace(tmp_path, str(self.graph_path))
        except Exception:
            # Clean up temp file on failure
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
            raise

        self._graph = g
        return self.graph_path

    def initialize(self, project_id: str, **kwargs) -> NarrativeGraph:
        """Create a new empty master graph."""
        self.ensure_dirs()
        project = ProjectNode(project_id=project_id, **kwargs)
        graph = NarrativeGraph(project=project)
        self._graph = graph
        self.save(graph)
        return graph

    # ------------------------------------------------------------------
    # Overlay support — parallel write safety for swarm agents
    # ------------------------------------------------------------------

    def save_overlay(self, overlay_name: str, data: "NarrativeGraph") -> Path:
        """Save an overlay graph fragment to ``graph/overlay_{name}.json``.

        Parallel swarm agents write to separate overlay files instead of
        clobbering the base graph.  A later ``load_and_merge_overlays()``
        call (or the ``graph_merge_overlays`` skill) folds them back in.
        """
        if not isinstance(data, NarrativeGraph):
            raise TypeError(f"Expected NarrativeGraph, got {type(data).__name__}")

        self.ensure_dirs()
        overlay_path = self.graph_dir / f"overlay_{overlay_name}.json"

        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.graph_dir), suffix=".tmp", prefix=f"overlay_{overlay_name}_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data.model_dump_json(indent=2))
            os.replace(tmp_path, str(overlay_path))
        except Exception:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return overlay_path

    def list_overlays(self) -> list[Path]:
        """Return paths of all ``overlay_*.json`` files in the graph dir."""
        if not self.graph_dir.exists():
            return []
        return sorted(self.graph_dir.glob("overlay_*.json"))

    def load_and_merge_overlays(self) -> "NarrativeGraph":
        """Load the base graph, merge all overlay files, save, and clean up.

        Overlay graphs contribute **additive** data only — nodes, states,
        and edges present in an overlay but absent from the base are added.
        Existing base data is never removed or overwritten by an overlay.

        Returns the merged graph (also saved to disk).
        """
        graph = self.load()
        overlay_paths = self.list_overlays()
        if not overlay_paths:
            return graph

        for op in overlay_paths:
            raw = json.loads(op.read_text(encoding="utf-8"))
            overlay = NarrativeGraph.model_validate(raw)
            graph = self._merge_overlay_into(graph, overlay)

        self.save(graph)

        # Clean up overlay files after successful merge
        for op in overlay_paths:
            try:
                op.unlink(missing_ok=True)
            except OSError:
                pass

        return graph

    @staticmethod
    def _merge_overlay_into(base: "NarrativeGraph", overlay: "NarrativeGraph") -> "NarrativeGraph":
        """Merge overlay nodes/states/edges into the base graph additively.

        Dict registries: overlay entries are added if their key is absent in
        the base.  If the key already exists, overlay fields that are non-empty
        overwrite the base value (richer data wins).

        Edge list: overlay edges whose canonical key is absent are appended.
        """

        def _merge_dict(base_dict: dict, overlay_dict: dict) -> None:
            """Merge overlay dict into base dict. New keys are added;
            existing keys get non-default overlay fields merged in."""
            for key, overlay_node in overlay_dict.items():
                if key not in base_dict:
                    base_dict[key] = overlay_node
                else:
                    # Overlay wins for fields that are populated and differ
                    base_node = base_dict[key]
                    for field_name in overlay_node.model_fields:
                        overlay_val = getattr(overlay_node, field_name)
                        # Skip empty/default overlay values
                        if overlay_val is None:
                            continue
                        if isinstance(overlay_val, str) and not overlay_val.strip():
                            continue
                        if isinstance(overlay_val, (list, dict)) and not overlay_val:
                            continue
                        setattr(base_node, field_name, overlay_val)

        # Node registries (all dict[str, Node])
        _merge_dict(base.cast, overlay.cast)
        _merge_dict(base.locations, overlay.locations)
        _merge_dict(base.props, overlay.props)
        _merge_dict(base.scenes, overlay.scenes)
        _merge_dict(base.frames, overlay.frames)
        _merge_dict(base.dialogue, overlay.dialogue)
        _merge_dict(base.voices, overlay.voices)
        _merge_dict(base.storyboard_grids, overlay.storyboard_grids)

        # Per-frame state snapshots (dict[str, StateNode])
        _merge_dict(base.cast_frame_states, overlay.cast_frame_states)
        _merge_dict(base.prop_frame_states, overlay.prop_frame_states)
        _merge_dict(base.location_frame_states, overlay.location_frame_states)

        # Edges — list, keyed by canonical (source, target, edge_type)
        existing_edge_keys = {
            (e.source_id, e.target_id, e.edge_type) for e in base.edges
        }
        for edge in overlay.edges:
            edge_key = (edge.source_id, edge.target_id, edge.edge_type)
            if edge_key not in existing_edge_keys:
                base.edges.append(edge)
                existing_edge_keys.add(edge_key)

        # Ordered sequences — extend with new entries
        base_frame_set = set(base.frame_order)
        for fid in overlay.frame_order:
            if fid not in base_frame_set:
                base.frame_order.append(fid)
                base_frame_set.add(fid)

        base_scene_set = set(base.scene_order)
        for sid in overlay.scene_order:
            if sid not in base_scene_set:
                base.scene_order.append(sid)
                base_scene_set.add(sid)

        base_dialogue_set = set(base.dialogue_order)
        for did in overlay.dialogue_order:
            if did not in base_dialogue_set:
                base.dialogue_order.append(did)
                base_dialogue_set.add(did)

        return base

    @property
    def graph(self) -> NarrativeGraph:
        """Access the in-memory graph, loading from disk if needed."""
        if self._graph is None:
            self._graph = self.load()
        return self._graph

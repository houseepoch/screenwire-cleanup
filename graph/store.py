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

    @property
    def graph(self) -> NarrativeGraph:
        """Access the in-memory graph, loading from disk if needed."""
        if self._graph is None:
            self._graph = self.load()
        return self._graph

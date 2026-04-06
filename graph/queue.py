"""
Assembly Queue — The Holding Pen
=================================

Helper agents NEVER write to the master graph directly.
They output JSON payloads to the assembly queue. Morpheus reads
these payloads, audits them, and commits valid ones to the master graph.

Queue entries are JSON files in `graph/assembly_queue/` named:
    {chunk_index}_{agent_name}_{timestamp}.json

Each entry contains:
    - agent: which helper produced this
    - chunk_index: which prose chunk was being processed
    - payload_type: what kind of data (cast_node, frame_node, edge, etc.)
    - payload: the actual data (validated against schema)
    - provenance: required — entries without provenance are rejected

After Morpheus commits an entry, it moves to `graph/committed/`.
If rejected, it moves to `graph/rejected/` with a rejection reason.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class QueueEntry(BaseModel):
    """A single payload from a helper agent waiting for Morpheus to audit."""

    entry_id: str                           # Unique ID for this queue entry
    agent: str                              # Which helper agent produced this
    chunk_index: int                        # Which prose chunk was being processed
    payload_type: str                       # Node/edge type: "cast_node", "frame_node",
                                            # "cast_frame_state", "prop_frame_state",
                                            # "location_frame_state", "dialogue_node",
                                            # "scene_node", "edge", "location_node",
                                            # "prop_node", "world_context", "visual_direction"
    payload: dict                           # The actual data (raw dict, validated on commit)
    timestamp: float = Field(default_factory=time.time)

    # Provenance is MANDATORY — Morpheus rejects entries without it
    source_prose_chunk: str = ""            # Exact text that justified this extraction
    confidence: float = 1.0                 # Agent's self-assessed confidence


class AuditResult(BaseModel):
    """Result of Morpheus auditing a queue entry."""

    entry_id: str
    accepted: bool
    rejection_reason: Optional[str] = None
    continuity_conflicts: list[str] = Field(default_factory=list)
    corrections_applied: list[str] = Field(default_factory=list)


class AssemblyQueue:
    """Manages the holding pen between helper agents and the master graph."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self.queue_dir = self.project_dir / "graph" / "assembly_queue"
        self.committed_dir = self.project_dir / "graph" / "committed"
        self.rejected_dir = self.project_dir / "graph" / "rejected"

    def ensure_dirs(self) -> None:
        for d in [self.queue_dir, self.committed_dir, self.rejected_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def submit(self, entry: QueueEntry) -> Path:
        """Submit a payload to the assembly queue.

        Called by helper agents. Returns the path to the queue file.
        Rejects entries with empty provenance."""
        self.ensure_dirs()

        if not entry.source_prose_chunk.strip():
            raise ValueError(
                f"REJECTED: Entry {entry.entry_id} from {entry.agent} "
                "has no source_prose_chunk. All queue entries must include "
                "the exact prose text that justifies the extraction."
            )

        filename = f"{entry.chunk_index:04d}_{entry.agent}_{entry.entry_id}.json"
        path = self.queue_dir / filename
        path.write_text(
            entry.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return path

    def list_pending(self, chunk_index: Optional[int] = None) -> list[QueueEntry]:
        """List all pending queue entries, optionally filtered by chunk."""
        self.ensure_dirs()
        entries = []
        for f in sorted(self.queue_dir.glob("*.json")):
            raw = json.loads(f.read_text(encoding="utf-8"))
            entry = QueueEntry.model_validate(raw)
            if chunk_index is not None and entry.chunk_index != chunk_index:
                continue
            entries.append(entry)
        return entries

    def commit(self, entry_id: str) -> Path:
        """Move a queue entry to committed after Morpheus approves it."""
        src = self._find_entry(entry_id, self.queue_dir)
        if src is None:
            raise FileNotFoundError(f"Queue entry {entry_id} not found")
        dst = self.committed_dir / src.name
        src.rename(dst)
        return dst

    def reject(self, entry_id: str, reason: str) -> Path:
        """Move a queue entry to rejected with a reason annotation."""
        src = self._find_entry(entry_id, self.queue_dir)
        if src is None:
            raise FileNotFoundError(f"Queue entry {entry_id} not found")

        # Annotate the entry with rejection reason
        raw = json.loads(src.read_text(encoding="utf-8"))
        raw["_rejection_reason"] = reason
        raw["_rejected_at"] = time.time()

        dst = self.rejected_dir / src.name
        dst.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        src.unlink()
        return dst

    def _find_entry(self, entry_id: str, directory: Path) -> Optional[Path]:
        """Find a queue file by entry_id."""
        for f in directory.glob("*.json"):
            if entry_id in f.stem:
                return f
        return None

    def clear_committed(self) -> int:
        """Remove all committed entries (after successful graph save). Returns count."""
        count = 0
        for f in self.committed_dir.glob("*.json"):
            f.unlink()
            count += 1
        return count

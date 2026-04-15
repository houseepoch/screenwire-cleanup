#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(os.getenv("SCREENWIRE_APP_ROOT", Path(__file__).resolve().parents[1])).resolve()
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from supabase_persistence import get_supabase_persistence

PROJECTS_DIR = Path(os.getenv("SCREENWIRE_PROJECTS_ROOT", APP_DIR / "projects")).resolve()


async def main() -> None:
    load_dotenv(APP_DIR / ".env")
    persistence = get_supabase_persistence()
    if persistence is None:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for backfill.")

    project_dirs = [
        path
        for path in sorted(PROJECTS_DIR.iterdir())
        if path.is_dir() and path.name != "_template"
    ]
    if not project_dirs:
        print("No projects found to sync.")
        return

    total_synced = 0
    total_skipped = 0
    for project_dir in project_dirs:
        result = await persistence.sync_project_tree(project_dir)
        total_synced += int(result["synced"])
        total_skipped += int(result["skipped"])
        print(f"{project_dir.name}: synced={result['synced']} skipped={result['skipped']}")

    print(f"Done. synced={total_synced} skipped={total_skipped}")


if __name__ == "__main__":
    asyncio.run(main())

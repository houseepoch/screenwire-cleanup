# Supabase Persistence

This repo is still pipeline-first and local-filesystem-first. The Supabase layer added here is the persistence foundation that turns local project folders into a durable system of record:

- `projects` holds project metadata
- `project_assets` holds every persisted file/object mapping
- `project_exports` tracks final exports
- `pipeline_jobs` is the queue/job registry for async worker separation
- `project_graph_snapshots` stores the current canonical graph snapshot
- `project_graph_ops` stores append-only graph mutation events

## Environment

Set these server-side env vars:

```bash
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_PROJECT_ASSET_BUCKET=project-assets
SUPABASE_PROJECT_EXPORT_BUCKET=project-exports
SUPABASE_SIGNED_URL_TTL_SECONDS=3600
SCREENWIRE_EXECUTION_MODE=queue
SCREENWIRE_WORKER_NAME=
SCREENWIRE_WORKER_POLL_SECONDS=2
```

## Rollout

1. Apply the SQL migration in `supabase/migrations/`.
2. Configure the env vars above on the runtime that serves `server.py`.
3. Run the backfill tool to mirror existing local projects:

```bash
python3 scripts/sync_projects_to_supabase.py
```

4. Keep the local project tree on fast disk/volume for temp workspace and caches.
5. Use Supabase Storage as the durable media/object layer and Postgres as the durable metadata/graph/job layer.
6. Run the API/web process with `SCREENWIRE_EXECUTION_MODE=queue` so UI actions enqueue `pipeline_jobs` instead of spawning `run_pipeline.py` directly.
7. Run the worker loop separately:

```bash
python3 workers/supabase_pipeline_worker.py
```

## Graph Recommendation

Do not treat the graph as a single giant DB write on every node edit. The intended model is:

- local graph file remains the fast working copy
- `project_graph_ops` captures small mutation events
- `project_graph_snapshots` stores the current canonical snapshot

That gives you crash recovery and persistence without making every editor interaction pay the cost of a full remote graph rewrite.

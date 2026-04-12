# BUILD LOG — screenwire-pipeline

---

## Session 2026-04-11 — batch-of-10 concurrency hardening

### What was done

**Audit scope**: Full codebase search for every caller of `/internal/generate-*` and
`/internal/fresh-generation` endpoints to verify the 10-concurrent hard cap.

### Findings

| File | Status | Notes |
|------|--------|-------|
| `skills/graph_generate_assets` | **CHANGED** | Cap enforced — see below |
| `run_pipeline.py` Phase 5 | ✓ Already correct | `VIDEO_CONCURRENCY = 10` with thread-pool worker queue |
| `run_pipeline.py` Phase 3 assets | ✓ Already correct | Hardcodes `--batch-size 10` when calling `graph_generate_assets` |
| `run_pipeline.py` Phase 4 frames | ✓ Sequential by design | Each frame guides the next — comment says so |
| `graph/grid_generate.py` | ✓ Single-shot function | Loop in `_generate_storyboard_grids_phase3` is intentionally sequential (cascading continuity) |
| `skills/sw_generate_video` | ✓ No loop | Single-video CLI per invocation |
| `skills/sw_generate_frame` | ✓ No loop | Single-frame CLI per invocation |
| `skills/sw_generate_image` | ✓ No loop | Single-image CLI per invocation |
| `skills/sw_fresh_generation` | ✓ No loop | Single-image CLI per invocation |

### Change applied: `skills/graph_generate_assets`

**Problem**: The batch logic was structurally correct (slice → `asyncio.gather` per
slice, results printed OK/FAIL per item) and the default was already 10. However there
was zero cap enforcement — passing `--batch-size 50` would fire 50 concurrent requests.

**Fix**:
1. `generate_batch()` — added `batch_size = min(batch_size, 10)` as first statement,
   with `@AI_REASONING` tag explaining the rationale. Docstring updated.
2. `main()` — added warning + clamp before passing `args.batch_size` to `generate_batch()`.
   `--batch-size` help text updated to document the hard cap.

**Syntax check**: Passed (`ast.parse` clean).

### Context for next step

The pipeline is fully hardened. Every image/video generation path respects ≤10
concurrent API calls:
- Cast/location/prop asset generation: `generate_batch()` cap in `graph_generate_assets`
- Frame generation (Phase 4): intentionally sequential
- Video generation (Phase 5): `VIDEO_CONCURRENCY = 10` thread-pool queue in `run_pipeline.py`
- Storyboard generation: intentionally sequential (cascading continuity)

No further concurrency changes are needed unless a new bulk generation path is added.
When adding any new bulk caller of `/internal/generate-*`, replicate the
`generate_batch()` pattern (slice → gather, 10 max).

---

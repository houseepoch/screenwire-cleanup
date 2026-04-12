# Active Project Objects

> Open work items being tracked. When an item is completed, it is
> automatically archived to `archive/` with a timestamp and git reference.

---

## OBJ-001: CC-First Deterministic Graph Construction
- **Status:** ACTIVE — Spec complete, ready to build
- **Priority:** Critical
- **Spec:** `build_specs/cc_first_deterministic_spec.md` (1072 lines)
- **Implementation steps:**
  1. [ ] `graph/schema.py` — Add StagingBeat, CinematicTag models; replace FormulaTag enum
  2. [ ] `graph/cc_parser.py` — Python parser: skeleton ///TAGs → entities, frame markers → frames + dialogue
  3. [ ] `graph/haiku_enricher.py` — Haiku worker dispatch: per-frame enrichment (cast states, composition, environment, directing)
  4. [ ] `graph/grok_tagger.py` — Grok cinematic frame tagger: reads frame nodes, assigns D/E/R/A/C/T/S/M tags with definitions
  5. [ ] `graph/continuity_validator.py` — Deterministic Python graph integrity checks
  5b. [ ] `agent_prompts/morpheus_graph_auditor.md` — Lean QA auditor prompt (outline + graph only, no source material)
  6. [ ] `graph/prompt_assembler.py` — Replace FORMULA_SHOT/FORMULA_VIDEO with CinematicTag.ai_prompt_language
  7. [ ] `agent_prompts/creative_coordinator.md` — Rewrite with ///TAG format spec, ///DLG pointers, ///SCENE_STAGING
  8. [ ] `run_pipeline.py` — Phase 2 rewrite: Steps 2a (parser) → 2b (Haiku) → 2b.5 (Grok tagger) → 2c (validation) → 2d (assembly)
  9. [ ] Archive Morpheus Agent 1-4 prompts to `agent_prompts/archived_intents/`
  10. [ ] Test: full pipeline run on NAC source (stickiness 1, short_film)

## OBJ-002: Corrective Fixes (Landed, Pending Commit)
- **Status:** COMPLETE — All fixes applied, 15/15 tests passing
- **Priority:** Done
- **Note:** Some files will be further modified by OBJ-001. Commit after OBJ-001 implementation.

## OBJ-003: NAC Test Run (Paused)
- **Status:** PAUSED — Will re-run after OBJ-001 as the validation test
- **Project:** nac_corrective_test_001
- **Config:** Stickiness 1 (Reformat), short_film, live_retro_grain

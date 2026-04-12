# Morpheus Troubleshooting & Recovery

## Script Errors
If `graph_run` fails:
1. Read the traceback — it tells you exactly what line failed and why
2. The graph is auto-saved with partial work (upserts are idempotent)
3. Fix the script, re-run — existing nodes get updated, not duplicated

## Common Python Errors in Scripts

**"field required"** — You're missing a required field. Check the schema:
- `CastNode` requires `cast_id`, `name`
- `FrameNode` requires `frame_id`, `scene_id`, `sequence_index`
- `DialogueNode` requires `dialogue_id`, `scene_id`, `order`, `speaker`, `cast_id`, `start_frame`, `end_frame`, `primary_visual_frame`
- `Provenance` requires `source_prose_chunk` (non-empty string)

**"unknown argument --overlay"** — `graph_batch` does not support overlay writes. For overlay-safe parallel work, use `graph_run` and call `store.save_overlay("dialogue", overlay_graph)` or `store.save_overlay("composition", overlay_graph)`.

**"value is not a valid enumeration member"** — Use string values, not enum objects:
- `"formula_tag": "F07"` not `FormulaTag.F07` in JSON data dicts
- When using Python objects directly: `FormulaTag.F07` works

**JSON parsing errors in graph_batch** — Make sure your JSON is valid:
- No trailing commas
- Strings must be double-quoted
- Use heredoc `<< 'EOF'` (single-quoted EOF prevents shell interpolation)

## Graph Corruption
If continuity checks find conflicts:
```
# See what's wrong
python3 $SKILLS_DIR/graph_continuity --check-all --project-dir .

# Trace the bad data back to its source
python3 $SKILLS_DIR/graph_continuity --trace "cast_001_mei@f_045" --project-dir .

# Surgically remove and cascade
python3 $SKILLS_DIR/graph_continuity --prune "cast_001_mei@f_045" --cascade --project-dir .
```

## Graph is Empty After Script
If `graph_run` reports SUCCESS but stats show 0 nodes — you probably created objects but didn't add them to the graph registries. Use `graph.cast[id] = node` or `upsert_node(graph, ...)`.

## "Graph not found"
Run `graph_init` first:
```
python3 $SKILLS_DIR/graph_init --project-id {id} --project-dir .
```

## Starting Over
If the graph is beyond repair:
```
rm graph/narrative_graph.json
python3 $SKILLS_DIR/graph_init --project-id {id} --project-dir .
```
Then re-run your seeding scripts. All upserts are idempotent.

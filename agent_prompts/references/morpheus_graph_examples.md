# Graph Writing Examples

## Approach 1: graph_batch (JSON operations file)

Write a JSON file with all operations, run it once:

Use this only when you are writing directly to the base graph. Parallel overlay agents should use `graph_run` plus `store.save_overlay(...)` instead.

```
# Write the ops file
cat > batch_scene1.json << 'BATCH_EOF'
{
  "operations": [
    {"op": "upsert_node", "type": "cast", "data": {"cast_id": "cast_001_mei", "name": "Mei"}, "provenance": {"source_prose_chunk": "Mei, the jewel of Lu Xian", "generated_by": "morpheus", "confidence": 1.0}},
    {"op": "upsert_node", "type": "location", "data": {"location_id": "loc_001_lu_xian", "name": "Lu Xian Brothel"}, "provenance": {"source_prose_chunk": "the legendary brothel Lu Xian", "generated_by": "morpheus", "confidence": 1.0}},
    {"op": "upsert_node", "type": "frame", "data": {"frame_id": "f_001", "scene_id": "scene_01", "sequence_index": 1, "formula_tag": "F07", "narrative_beat": "Dawn light on Lu Xian courtyard", "suggested_duration": 8, "action_summary": "Morning mist drifts across the courtyard as lanterns flicker out"}, "provenance": {"source_prose_chunk": "Dawn breaks over the courtyard", "generated_by": "morpheus", "confidence": 1.0}},
    {"op": "create_edge", "source": "cast_001_mei", "target": "f_001", "edge_type": "appears_in", "provenance": {"source_prose_chunk": "Mei watches from her patio", "generated_by": "morpheus", "confidence": 1.0}}
  ]
}
BATCH_EOF

python3 $SKILLS_DIR/graph_batch --ops-file batch_scene1.json --project-dir .
```

## Approach 2: graph_run (Python script — MOST POWERFUL)

Write a Python script that uses the graph API directly. The graph and all API functions are pre-loaded. This is the most powerful tool — you can use loops, conditionals, error handling, and complex logic.

```
# Write the script
cat > seed_entities.py << 'SCRIPT_EOF'
# graph, api, schema, and all types are pre-loaded

# Seed all cast from skeleton data
cast_data = [
    {"cast_id": "cast_001_mei", "name": "Mei", "personality": "Intelligent, determined, romantic",
     "role": "protagonist", "arc_summary": "From courtesan to free woman",
     "identity": {"age_descriptor": "early 20s", "gender": "female", "ethnicity": "Chinese",
                  "build": "slender", "hair_color": "black", "hair_length": "long",
                  "hair_style": "elaborate updo with ribbon", "skin": "pale",
                  "clothing": ["blue kimono", "silk sash"], "wardrobe_description": "Elegant blue silk kimono with embroidered details"}},
    {"cast_id": "cast_002_lin", "name": "Lin", "personality": "Gentle, devoted, artistic",
     "role": "supporting", "arc_summary": "Unknowing object of love, receives Mei at the end",
     "identity": {"age_descriptor": "mid 20s", "gender": "male", "ethnicity": "Chinese",
                  "build": "lean", "hair_color": "black", "hair_length": "medium",
                  "clothing": ["simple cotton tunic", "work apron"], "wardrobe_description": "Simple cotton work clothes, rolled sleeves, dirt-stained apron"}},
]

prov = {"source_prose_chunk": "Character roster from skeleton", "generated_by": "morpheus", "confidence": 1.0}

for c in cast_data:
    # Convert nested identity dict to CastIdentity
    identity_data = c.pop("identity", {})
    cast_node = CastNode(
        cast_id=c["cast_id"],
        name=c["name"],
        personality=c.get("personality", ""),
        role=c.get("role", "supporting"),
        arc_summary=c.get("arc_summary", ""),
        identity=CastIdentity(**identity_data),
        provenance=Provenance(**prov),
    )
    graph.cast[cast_node.cast_id] = cast_node
    print(f"  Seeded cast: {cast_node.name}")

print(f"Total cast: {len(graph.cast)}")
SCRIPT_EOF

python3 $SKILLS_DIR/graph_run --script seed_entities.py --project-dir .
```

## Approach 3: Write a comprehensive seeding script for the whole scene

For each scene, write ONE script that does everything — entities, frames, states, dialogue, edges:

```
cat > process_scene_01.py << 'SCRIPT_EOF'
# Process Scene 1 — The Auction Hall
# graph, api, schema, and all types/functions are pre-loaded

prov = lambda text, conf=1.0: {"source_prose_chunk": text, "generated_by": "morpheus", "confidence": conf}

# ── Scene node
upsert_node(graph, "scene", {
    "scene_id": "scene_01", "scene_number": 1, "title": "The Jewel of Lu Xian",
    "location_id": "loc_001_salon", "time_of_day": "afternoon", "int_ext": "INT",
    "cast_present": ["cast_001_mei", "cast_003_chou", "cast_004_zhao", "cast_005_min_zhu"],
    "mood_keywords": ["tense", "elegant", "anticipatory"],
}, prov("INT. LU XIAN SALON — AFTERNOON"))

# ── Frames (sequential)
frames = [
    {"frame_id": "f_001", "formula_tag": "F07", "narrative_beat": "Afternoon light through silk screens...",
     "source_text": "The silk screens filtered the afternoon sun...",
     "suggested_duration": 8, "action_summary": "Golden light shifts through silk screens as dust motes drift across the empty salon"},
    {"frame_id": "f_002", "formula_tag": "F01", "narrative_beat": "Mei sits composed on cushion...",
     "source_text": "Mei sat perfectly still on the embroidered cushion...",
     "suggested_duration": 5, "action_summary": "Mei sits perfectly still, only her eyes tracking movement beyond the screens"},
    # ... more frames
]

for i, f in enumerate(frames):
    f["scene_id"] = "scene_01"
    f["sequence_index"] = i + 1
    if i > 0:
        f["previous_frame_id"] = frames[i-1]["frame_id"]
        f["continuity_chain"] = True
    if i < len(frames) - 1:
        f["next_frame_id"] = frames[i+1]["frame_id"]
    upsert_node(graph, "frame", f, prov(f["source_text"]))
    graph.frame_order.append(f["frame_id"])

# ── Cast frame states (absolute snapshots, propagated)
# Frame 1: Mei in background
propagate_cast_state(
    graph, "cast_001_mei", "f_000", "f_001",
    {
        "frame_role": "background", "posture": "sitting", "emotion": "composed",
        "spatial_position": "center_frame", "clothing_state": "base",
    },
    provenance=prov("Mei watches from her patio, composed and seated in the center frame."),
)
# Frame 2: Mei becomes subject
propagate_cast_state(
    graph, "cast_001_mei", "f_001", "f_002",
    {
        "frame_role": "subject", "emotion": "guarded_composure",
    },
    provenance=prov("Mei steps forward and becomes the subject, still holding guarded composure."),
)

print(f"Scene 01: {len(frames)} frames processed")
SCRIPT_EOF

python3 $SKILLS_DIR/graph_run --script process_scene_01.py --project-dir .
```

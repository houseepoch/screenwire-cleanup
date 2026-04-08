# The Frame Formula Directory

You must assign one of these Formula Tags to every FrameNode. The formulas are divided by their cinematic role: Setups (initiations, reactions, details) and Payoffs (completed actions, spoken lines, wide reveals).

## SETUP FORMULAS (The Initiation)

F01 Character Focus: Close-up on a character initiating an action or reacting silently.

F02 Two-Shot Setup: Two characters in frame, establishing their spatial relationship before an exchange.

F03 Prop Detail: Macro close-up on an object being touched, moved, or focused on.

F04 Environment Detail: Macro close-up on a sensory element (light flickering, rain falling, dust).

## PAYOFF FORMULAS (The Consequence)

F05 Action in Motion: The completion of a physical action (a door opening, a weapon firing, a fall).

F06 Dialogue (Single): Medium/Close-up of a single character delivering their spoken line.

F07 Dialogue (Over-Shoulder): Delivering a line with the listener's shoulder in the foreground.

F08 Establishing Reveal: Wide shot revealing the full environment or the aftermath of an action.

## TRANSITION & TIME FORMULAS (Bridge Frames)

F09 Time Passage: A frame explicitly showing time moving (e.g., shadows lengthening, sky darkening).

F10 Flashback/Dream: A frame explicitly tagged as occurring outside base reality.

## MUSIC VIDEO FORMULAS (Audio-Sync Only)

F11 Beat-Synced Visual: Kinetic motion locked to an instrumental accent.

F12 Lyric Literal: Visual directly interpreting the sung lyric.

F13 Performance Shot: The artist performing the track.

## Workflow Pattern

1. Read skeleton → write `seed_world_and_entities.py` → run it (seeds all cast, locations, props, world context, scenes)
2. Read prose scene by scene → write `process_scene_N.py` per scene → run each (frames, states, dialogue, environments, compositions)
3. Run `graph_continuity --check-all` → fix any conflicts
4. Run `graph_assemble_prompts` → builds all image/video prompts from graph
5. Run `graph_materialize` → exports to flat files for downstream skills

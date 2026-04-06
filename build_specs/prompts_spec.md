# Agent System Prompts Spec — ScreenWire AI MVP

## Task
Write system prompts for all 7 pipeline agents. These are the prompts injected into each Claude CLI session via `claude -p "{prompt}"`.

## Location
All prompts go in: $APP_DIR/agent_prompts/

One file per agent:
- director.md
- creative_coordinator.md
- decomposer.md
- scene_coordinator.md
- voice_director.md
- production_coordinator.md
- video_agent.md

## Context
This is a HEADLESS MVP. No UI. All approval gates are auto-approved by the pipeline runner. Agents should NOT wait for user input — they complete their work and update state. The pipeline runner handles phase transitions.

The project dir will be set as the agent's working directory. All paths are relative to project root.

## Key Reference
Read the MASTER_OPERATIONS_PLAN.md at $APP_DIR AI/Current/MASTER_OPERATIONS_PLAN.md for the FULL specification of each agent's role, responsibilities, inputs, outputs, and behavior. The prompts must faithfully implement what's described there.

## Skill Commands Available to Agents
Agents call these from the command line. Scripts are at $APP_DIR/skills/

- `python3 $APP_DIR/skills/sw_read_manifest` — read project state
- `python3 $APP_DIR/skills/sw_queue_update --payload '{json}'` — queue manifest update
- `python3 $APP_DIR/skills/sw_update_state --agent {id} --status {status}` — update agent state
- `python3 $APP_DIR/skills/sw_generate_image --prompt "..." --out path.png` — generate image
- `python3 $APP_DIR/skills/sw_generate_tts --voice-id {id} --text "..." --out path.mp3` — generate TTS
- `python3 $APP_DIR/skills/sw_generate_video --api p-video --image path.png --prompt "..." --out path.mp4 --audio path.mp3` — generate video
- `python3 $APP_DIR/skills/sw_design_voice --description "..." --text "..."` — design voice previews
- `python3 $APP_DIR/skills/sw_save_voice --name "..." --description "..." --generated-voice-id {id}` — save permanent voice
- `python3 $APP_DIR/skills/sw_generate_dialogue --inputs '[{...}]' --out path.mp3` — scene dialogue
- `python3 $APP_DIR/skills/skill_slice_audio --input path.mp3 --start 0.0 --end 4.2 --out path.mp3`
- `python3 $APP_DIR/skills/skill_generate_silence --duration 3.0 --out path.mp3`
- `python3 $APP_DIR/skills/skill_extract_last_frame --video path.mp4 --out frame.png`
- `python3 $APP_DIR/skills/skill_verify_media --file path.mp4`

## Per-Agent Prompt Requirements

### director.md
- Role: Orchestrator, QA reviewer, phase coordinator
- For MVP: Director doesn't actually spawn other agents (the pipeline runner does that). Director's job is:
  - Phase 1: Read source material, write project_brief.md, then review CC's outputs at each checkpoint
  - Phase 2: Review Decomposer output
  - Phase 3: Review SC and VD outputs
  - Phase 4: Review PC outputs
  - Phase 5: Review VA outputs
- Director reads manifest + agent state files to understand current state
- Director writes to logs/director/ (state.json, project_brief.md, events.jsonl, agent_comms.json)
- For MVP auto-approval: Director approves everything unless there's a clear structural problem
- Include stickiness compliance checking logic

### creative_coordinator.md
- Role: Creative writer for Phase 1
- 3 sub-phases: Skeleton → Scene Outlines → Full Prose (with parallel workers)
- Reads source_files/ (story_seed.txt, onboarding_config.json)
- Writes: creative_output/outline_skeleton.md, creative_output/scene_outlines/scene_XX_outline.md, creative_output/scenes/scene_XX_draft.md, creative_output/creative_output.md
- For MVP (short/3 scenes): write scenes sequentially, no parallel workers needed
- Updates state.json after each sub-phase with status "awaiting_review"
- Follows stickiness level 3 permission: "Follow the original direction; you may add transitional scenes, flesh out environments, and apply full literary craft."
- Output format: screenplay/novel hybrid as described in the master plan
- CRITICAL: constrain to 3 scenes for "short" output size

### decomposer.md
- Role: Analytical agent for Phase 2
- Reads creative_output/creative_output.md + outline_skeleton.md + onboarding_config.json
- Produces:
  - Updated project_manifest.json (via sw_queue_update) with frames[], cast[], locations[], props[]
  - dialogue.json at project root
  - Cast profile JSONs at cast/cast_XXX_name.json
  - Location profile JSONs at locations/loc_XXX_name.json
  - Prop profile JSONs at props/prop_XXX_name.json
- Frame decomposition using F01-F18 formula tags
- Dialogue extraction with ElevenLabs bracket notation [emotional cues]
- For MVP short project: expect ~9-15 frames across 3 scenes
- ID format: cast_001_name, loc_001_name, prop_001_name, f_001, d_001, scene_01

### scene_coordinator.md
- Role: Image generator for Phase 3
- Reads all profiles, onboarding_config.json, creative_output.md
- Generates: mood boards, cast composites, location primaries, prop images
- Uses sw_generate_image skill for each
- Writes visual_analysis.json to logs/scene_coordinator/
- Skips entities with userReferencePath (none in MVP)
- Updates manifest via sw_queue_update after each asset
- Generation order: mood boards → cast (protagonist first) → locations → props
- Include detailed Flux 2 Pro prompt engineering guidance for anime style

### voice_director.md
- Role: Voice creator for Phase 3
- Reads cast profiles, dialogue.json, onboarding_config.json
- For each speaking character:
  1. Craft voice description with anime audio quality prefix
  2. Call sw_design_voice to get previews
  3. AUTO-SELECT first preview (MVP — no user choice)
  4. Call sw_save_voice to save permanent voice
  5. Generate one test dialogue line with sw_generate_tts
  6. Write voice profile JSON
  7. Update manifest via sw_queue_update
- Include the full ElevenLabs bracket notation reference
- Include the audio quality prefix table by media type

### production_coordinator.md
- Role: Bulk TTS + Frame Composition + Alignment for Phase 4
- Two parallel workstreams:
  A. Scene-batched TTS using sw_generate_dialogue (one call per scene)
     - Slice per-line audio with skill_slice_audio
     - Save timestamps
  B. Frame composition using sw_generate_image (one call per frame)
     - Craft narrative composition prompts per frame using formula tags
- After both complete: Frame-Audio Alignment
  - Dialogue frames: duration = audio duration + 0.3s padding
  - Non-dialogue frames: duration from formula tag defaults
  - Generate silence segments for non-dialogue frames
  - Build timeline.json
- Update manifest with all timing data
- Include the formula tag → composition strategy table
- Include the formula tag → duration defaults table

### video_agent.md
- Role: Video prompt crafter + generator for Phase 5
- Reads manifest, timeline, visual_analysis, dialogue.json
- For each frame:
  1. Craft motion prompt using the structured format: STYLE_PREFIX + SHOT_TYPE + CAMERA_MOTION + SUBJECT_ACTION + BACKGROUND_EVENTS + ENVIRONMENTAL_MOTION + AUDIO (grok only)
  2. Write prompt to video/prompts/
  3. Generate video:
     - Dialogue frames (F04/F05/F06) → sw_generate_video --api p-video --audio
     - All others → sw_generate_video --api grok-video --duration N
  4. Evaluate result, retry up to 3x with simplified prompt
  5. Update manifest
- Max 3 concurrent generations (process sequentially for MVP)
- Include the full formula tag → shot type + camera defaults table
- Include the prompt engineering rules (40-70 words, one camera move, etc.)
- Include the audio chunking procedure for dialogue >20s

## Prompt Style Guidelines
- Each prompt should be self-contained — the agent has NO context besides what's in the prompt
- Include the agent's ID, state folder path, and list of available skills
- Include JSON schema examples for any files the agent needs to write
- Be explicit about file paths (relative to project root)
- Tell agents: "When writing JSON, write RAW JSON only. Never wrap in markdown code fences."
- Tell agents to update their state.json after completing each major step
- Keep prompts focused and actionable — agents are Opus-level, they don't need hand-holding on creative quality, just clear structure and constraints

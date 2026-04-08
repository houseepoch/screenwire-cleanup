# Scaffold Spec — ScreenWire AI MVP

## Task
Create the project scaffold for the headless MVP test pipeline.

## Project Root
$APP_DIR/

## 1. Create Full Directory Tree

At: $APP_DIR/test_project/sw_test001_greenhouse-letter/

Use mkdir -p for every directory:

```
sw_test001_greenhouse-letter/
├── source_files/
├── config/
├── creative_output/
│   ├── scene_outlines/
│   └── scenes/
├── cast/
│   ├── composites/
│   │   └── archive/
│   └── user_references/
├── locations/
│   ├── primary/
│   ├── derived/
│   └── user_references/
├── props/
│   ├── generated/
│   └── user_references/
├── assets/
│   └── active/
│       └── mood/
├── frames/
│   ├── composed/
│   │   └── archive/
│   └── prompts/
├── audio/
│   ├── dialogue/
│   │   ├── scenes/
│   │   └── archive/
│   ├── narration/
│   ├── segments/
│   └── analysis/
├── video/
│   ├── prompts/
│   ├── clips/
│   │   ├── archive/
│   │   └── normalized/
│   ├── assembled/
│   └── export/
├── logs/
│   ├── director/
│   ├── creative_coordinator/
│   ├── decomposer/
│   ├── scene_coordinator/
│   ├── voice_director/
│   │   └── previews/
│   ├── production_coordinator/
│   └── video_agent/
├── dispatch/
│   ├── updates/
│   ├── manifest_queue/
│   │   └── dead_letters/
│   └── flags/
└── project_manifest.json
```

## 2. Write source_files/onboarding_config.json

```json
{
  "projectName": "The Greenhouse Letter",
  "projectId": "sw_test001",
  "pipeline": "story_upload",
  "mediaStyle": "new_digital_anime",
  "aspectRatio": "16:9",
  "outputSize": "short",
  "stickinessLevel": 3,
  "stickinessLabel": "Expand",
  "stickinessPermission": "Expand. Follow the source material's direction but round out incomplete areas. Add transitional scenes, supporting details, and environmental context the source implies but doesn't show. All additions must serve what's already demonstrated — supporting information, not new story.",
  "style": ["cinematic", "dreamlike"],
  "genre": ["drama"],
  "mood": ["melancholic", "mysterious", "hopeful"],
  "extraDetails": "Keep it intimate and quiet. The greenhouse should feel like a character itself — alive despite being abandoned. The ending should feel like the first breath after holding one in.",
  "sourceFiles": ["story_seed.txt"],
  "createdAt": "2026-04-01T18:00:00Z"
}
```

## 3. Write source_files/story_seed.txt

A short, evocative 2-3 paragraph story seed about:
- Sarah (30s, sharp eyes softened by exhaustion) finds a leather journal in an abandoned greenhouse
- Inside is a letter from someone she lost, telling her "If you're reading this, you found the garden. That means you're ready."
- She calls James (old friend/ex-partner) and asks him to come see something
- She decides to stay and rebuild the greenhouse
Make it rich with sensory detail but concise.

## 4. Write project_manifest.json

```json
{
  "projectId": "sw_test001",
  "projectName": "The Greenhouse Letter",
  "slug": "greenhouse-letter",
  "status": "phase_0_complete",
  "version": 1,
  "phases": {
    "phase_0": {"status": "complete", "completedAt": "2026-04-01T18:00:00Z"},
    "phase_1": {"status": "ready"},
    "phase_2": {"status": "pending"},
    "phase_3": {"status": "pending"},
    "phase_4": {"status": "pending"},
    "phase_5": {"status": "pending"},
    "phase_6": {"status": "pending"}
  },
  "cast": [],
  "locations": [],
  "props": [],
  "frames": [],
  "dialoguePath": "dialogue.json",
  "cost": {
    "estimate": {"llm": 0, "imageGen": 0, "videoGen": 0, "tts": 0, "total": 0},
    "actual": {"llm": 0, "imageGen": 0, "videoGen": 0, "tts": 0, "total": 0},
    "budget_cap": null,
    "warnings": []
  }
}
```

## 5. Write .env.example at $APP_DIR/.env.example

```
FAL_KEY=
ELEVENLABS_API_KEY=
REPLICATE_API_TOKEN=
```

## Rules
- Use mkdir -p for all dirs
- All JSON must be valid
- Do NOT write any Python code

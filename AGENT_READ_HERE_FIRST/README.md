# AGENT_READ_HERE_FIRST

Priority 0 onboarding for agents working in the screenwire-pipeline.

## Read Order
1. **REMEMBER.md** — Architectural decisions, lessons learned, critical context
2. **Build spec** — `build_specs/cc_first_deterministic_spec.md` (1072 lines, the active implementation spec)
3. **PROJECT_STATUS/** — at repo root (`PROJECT_STATUS/ACTIVE_OBJECTS.md`) for current work items

## Replicate Model References

These fragment files are the source inputs for the generated repo-root
`API_REFERENCE.md`. Rebuild with `python3 build_api_reference.py` or run
`python3 build_api_reference.py --watch` while editing files in this directory.

| Document | Model | Type | Cost |
|----------|-------|------|------|
| [replicate_p-image.md](replicate_p-image.md) | PrunaAI P-Image | Text-to-image | $0.005/image |
| [replicate_p-image-upscale.md](replicate_p-image-upscale.md) | PrunaAI P-Image-Upscale | Image upscaling | $0.005–$0.01/image |
| [replicate_nano-banana-2.md](replicate_nano-banana-2.md) | Google Nano Banana 2 (Gemini 3.1 Flash) | Text-to-image / Editing | See doc |
| [replicate_nano-banana-pro.md](replicate_nano-banana-pro.md) | Google Nano Banana Pro (Gemini 3 Pro) | Text-to-image / Editing | $0.15–$0.30/image |
| [replicate_grok-imagine-video.md](replicate_grok-imagine-video.md) | xAI Grok Imagine Video | Image-to-video | See doc |

## External APIs
- **xAI (Grok)** — Vision refinement (grok-4-1-fast-non-reasoning) + cinematic frame tagging
- **Replicate** — Image generation (p-image, nano-banana), video generation (grok-imagine-video)
- **Anthropic** — CC (Opus), Haiku frame enrichment workers

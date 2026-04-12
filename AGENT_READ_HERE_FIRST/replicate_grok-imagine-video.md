# Replicate: Grok Imagine Video (xAI)

> **Purpose**: Image-to-video model that animates still images into short videos with synchronized audio.

## Links

- **Replicate Model**: https://replicate.com/xai/grok-imagine-video

## Specs

| Property | Value |
|----------|-------|
| Type | Image-to-video / Text-to-video / Video editing |
| Architecture | Autoregressive mixture-of-experts (Aurora) |
| Video Duration | 1–15 seconds |
| Resolutions | 480p, 720p |
| Aspect Ratios | 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3 |
| Audio | Native generation, auto-synchronized |
| Video Edit Input Limit | 8.7 seconds |

## Pricing

Pricing is usage-based on Replicate. Exact per-run cost not published on the model page — check Replicate billing for current rates. Video models are typically priced per second of output.

## Generation Modes

| Mode | Description |
|------|-------------|
| **Normal** | Balanced, professional results |
| **Fun** | Dynamic, playful interpretations |
| **Custom** | Precise control over generation |

## Input Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | string | *required* | Text description of desired video content |
| `image` | URI | — | Input image to animate (image-to-video mode) |
| `video` | URI | — | Input video to edit (video editing mode, max 8.7s) |
| `mode` | string | `"normal"` | `normal`, `fun`, or `custom` |
| `duration` | integer | — | Video length in seconds (1–15) |
| `resolution` | string | `"720p"` | `480p` or `720p` |
| `aspect_ratio` | string | `"16:9"` | 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3 |

*Note: Full parameter schema not published on Replicate — above is inferred from documentation. Check the API tab for definitive schema.*

## Output

Returns a video file URL (MP4) with synchronized audio.

## Model Notes

- **Three modes**: text-to-video, image-to-video, and video editing
- Native audio generation — background music, sound effects, and ambient audio are generated and synced automatically
- Supports character animation with potential lip-sync capabilities
- Prompt structure recommendation: "Subject + Action + Setting + Camera + Lighting/Mood" in natural sentences
- Good for: product showcases, portrait animation, creative content, social media clips
- Video editing mode accepts input videos up to 8.7 seconds
- Uses xAI's Aurora autoregressive mixture-of-experts architecture

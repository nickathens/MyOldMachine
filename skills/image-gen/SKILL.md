# Media Generation (Image + Video)

Generate images and videos using Higgsfield CLI (primary) with Pollinations.ai as a free fallback for images.

**Higgsfield:** 20+ image models, 16 video models, up to 4K, requires CLI auth.
**Pollinations:** Free tier, 768x768 max, sana model, no auth needed (images only).

## Setup

Higgsfield is optional. Without it, the skill silently falls back to Pollinations for images and cannot generate video.

To enable Higgsfield (per-user, stores auth in the running user's home directory):

```bash
npm install -g @higgsfield/cli
higgsfield auth login          # opens browser OAuth, stores token in ~/.config/higgsfield/
higgsfield account status      # confirms your plan and credits
```

The CLI stores its token in the user's home directory. **Do not commit any auth file or share its contents.** Different MOM users (slot accounts) authenticate independently — each user runs `higgsfield auth login` once in their own session.

## Image Generation

```bash
# Default (Nano Banana 2 / nano_banana_flash, fast and high-quality)
python skills/image-gen/scripts/generate.py "a futuristic cityscape at sunset" -o /tmp/city.jpg

# Specific model
python skills/image-gen/scripts/generate.py "a mountain landscape" -o /tmp/mountain.jpg -m nano-pro

# Landscape aspect ratio
python skills/image-gen/scripts/generate.py "ocean waves" -o /tmp/ocean.jpg -a 16:9

# Image-to-image (reference image)
python skills/image-gen/scripts/generate.py "make it cyberpunk" -o /tmp/cyber.jpg -r /path/to/input.jpg

# Free tier fallback (no credits needed, no auth)
python skills/image-gen/scripts/generate.py "a cat" -o /tmp/cat.jpg --backend pollinations

# Auto mode (tries Higgsfield first, falls back to Pollinations)
python skills/image-gen/scripts/generate.py "a cat" -o /tmp/cat.jpg --backend auto
```

## Video Generation

```bash
# Default video (Kling 3.0, 16:9)
python skills/image-gen/scripts/generate.py "a wave crashing on a rocky shore" -o /tmp/wave.mp4 --video

# Specific video model
python skills/image-gen/scripts/generate.py "cinematic drone shot over mountains" -o /tmp/drone.mp4 --video -m veo3

# Portrait video (9:16 for social)
python skills/image-gen/scripts/generate.py "a person walking in rain" -o /tmp/rain.mp4 --video -a 9:16

# Image-to-video (animate a still image)
python skills/image-gen/scripts/generate.py "camera slowly pans across the scene" -o /tmp/animated.mp4 --video -r /path/to/image.jpg

# Cost check before video generation
python skills/image-gen/scripts/generate.py "a sunset timelapse" --video --cost -m veo3
```

## Utility Commands

```bash
# List all model aliases (image + video)
python skills/image-gen/scripts/generate.py --list-models

# Check cost before generating (dry run)
python skills/image-gen/scripts/generate.py "a portrait" --cost -m gpt

# Check account balance (requires Higgsfield auth)
python skills/image-gen/scripts/generate.py --balance
```

## Options

| Flag | Description |
|------|-------------|
| `-o`, `--output` | Output file path (auto: .jpg for images, .mp4 for video) |
| `-m`, `--model` | Model name or alias (auto-selects based on --video flag) |
| `-a`, `--aspect-ratio` | Aspect ratio: 1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 5:4, 4:5, 21:9, 9:21 |
| `-r`, `--ref-image` | Reference image for image-to-image or image-to-video |
| `--video` | Generate video instead of image |
| `--backend` | Backend: auto (default, tries Higgsfield then Pollinations), higgsfield, pollinations |
| `--resolution` | Resolution: 1k, 2k, 4k (default: 2k, images only) |
| `--duration` | Video duration in seconds (model-dependent) |
| `--extra` | JSON string of extra model params (e.g. `'{"quality":"high"}'`) |
| `--list-models` | List all model aliases and exit |
| `--cost` | Estimate credits cost without generating |
| `--balance` | Show account credits balance |
| `--user` | Telegram user ID for per-user iteration tracking |

## Image Model Aliases

| Alias | Model | Best for |
|-------|-------|----------|
| `nano` | Nano Banana | Budget-friendly, realistic |
| `nano2` | Nano Banana 2 | Fast, high-quality (default) |
| `nano-pro` | Nano Banana Pro | Ultimate quality, text rendering |
| `gpt` | GPT Image 2 | Text rendering, editing, next-gen |
| `grok` | Grok Image | Creative, distinctive style |
| `flux` | FLUX.2 | Precise prompt adherence |
| `flux-kontext` | Flux Kontext | Context-aware generation |
| `soul` | Higgsfield Soul V2 | Character consistency |
| `cinematic` | Cinematic Studio 2.5 | Cinematic frames |
| `seedream` | Seedream 4.5 | High detail |
| `seedream-lite` | Seedream V5 Lite | Fast, lightweight |
| `hazel` | OpenAI Hazel | OpenAI's latest |
| `kling` | Kling O1 Image | Detailed scenes |
| `auto` | Image Auto | Let Higgsfield pick |

## Video Model Aliases

| Alias | Model | Notes |
|-------|-------|-------|
| `kling` | Kling 3.0 | Default video model, high quality |
| `kling2.6` | Kling 2.6 | Previous gen |
| `veo3` | Google Veo 3 | Cinematic, photorealistic |
| `veo3.1` | Google Veo 3.1 | Latest, highest quality |
| `veo3-lite` | Google Veo 3.1 Lite | Faster, lower cost |
| `seedance` | Seedance 2.0 | Precise motion control |
| `seedance1.5` | Seedance 1.5 Pro | Previous gen |
| `cinematic3` | Cinematic Studio 3.0 | Film-grade output |
| `cinematic-video` | Cinematic Studio Video V2 | Stylized video |
| `grok-video` | Grok Video | Creative, distinctive |
| `hailuo` | Minimax Hailuo | Fast, reliable |
| `wan` | Wan 2.7 | Latest Wan model |
| `wan2.6` | Wan 2.6 | Previous gen |
| `soul-cast` | Soul Cast | Character-consistent video |
| `marketing` | Marketing Studio Video | Product/marketing content |

## Credit Costs (approximate)

| Tier | Credits | Models |
|------|---------|--------|
| Image cheap | 1-1.5 | nano, nano2 |
| Image mid | 2 | nano-pro, cinematic, flux, seedream, kling |
| Image expensive | 7 | gpt, hazel |
| Image free | 0 | Pollinations (`--backend pollinations`) |
| Video | 10-25 | kling (10), seedance (22), veo3.1 (22), cinematic3 (25) |

Every Higgsfield generation auto-reports credits used and remaining balance.

## Prompt Refinement (MANDATORY for Mini App / structured flows)

When a media generation request comes from a structured source (Mini App, scripted pipeline), the calling agent MUST:

1. **Read the model-specific guide** from `skills/image-gen/models/` BEFORE refining.
2. **Refine the prompt** following that guide's techniques (structure, length, keywords, what to avoid).
3. **Estimate cost** with `--cost` flag, including all params (model, aspect, resolution, duration, extras).
4. **Present all three** (original prompt, refined prompt, cost estimate) and wait for approval.
5. **Never generate without explicit user approval.** Credits are real money.

### Guide file mapping

| Model aliases | Guide file |
|---|---|
| nano, nano2, nano-pro | `nano-banana.md` |
| gpt, hazel | `gpt-image.md` |
| flux, flux-kontext | `flux.md` |
| grok | `grok.md` |
| grok-video | `grok-video.md` |
| soul, soul-cinematic, soul-location | `soul.md` |
| cinematic | `cinematic-studio.md` |
| seedream, seedream-lite | `seedream.md` |
| kling (video), kling2.6 | `kling-video.md` |
| veo3, veo3.1, veo3-lite | `veo.md` |
| seedance, seedance1.5 | `seedance.md` |
| cinematic3, cinematic-video, cinematic-v2 | `cinematic-video.md` |
| hailuo | `hailuo.md` |
| wan, wan2.6 | `wan.md` |
| soul-cast | `soul-cast.md` |
| marketing | `marketing-studio.md` |

## Iteration (Image-to-Image / Video-from-Image)

When a user asks to modify, adjust, iterate on, or improve a previously generated image:

1. **Check for the last generation breadcrumb** at `/tmp/media_gen_last/last_{user_id}.json`
2. **Use `-r` flag** to pass the previous output as a reference image
3. **Pass `--user {user_id}`** to track the new output for future iterations

```bash
# Iterate on last generated image (make it more blue)
python skills/image-gen/scripts/generate.py "same scene but with more blue tones" -o /tmp/iter.jpg -r /tmp/generated_image.jpg --user <telegram_id>

# Animate a previously generated image
python skills/image-gen/scripts/generate.py "camera slowly pans across" -o /tmp/anim.mp4 --video -r /tmp/generated_image.jpg --user <telegram_id>
```

The breadcrumb is keyed by Telegram user ID, so multi-user installs do not collide. The breadcrumb path itself is volatile (`/tmp`) and is wiped on reboot.

**NEVER generate from scratch when the user says "make it more X", "change the Y", "iterate", "adjust", "modify", or similar.** Always use the reference image.

## Notes

- Default backend: Higgsfield CLI (requires `higgsfield auth login`)
- Pollinations fallback is always available for images (free, no auth)
- Video generation timeout: 10 minutes (videos take longer to render)
- Use `--cost` before expensive generations to check credit impact
- Higgsfield auth is per-OS-user. In multi-user MOM, each slot account runs its own `higgsfield auth login`.
- The `httpx` dependency is required (declared in `deps.json`).

# Media Generation (Image + Video + 3D + Audio)

Generate images, videos, 3D meshes, and audio using Higgsfield CLI (primary) with Pollinations.ai as a free fallback for images.

**Higgsfield:** a broad catalog of image and video models plus 3D (text/image to mesh) and audio (music/speech), up to 4K, requires CLI auth. The wrapper wires the prompt-driven generators; run `higgsfield model list` to see the full catalog available on your CLI version, including editing ops (upscale, outpaint, background removal) that overlap the dedicated `upscale` / `background-removal` skills.
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

# Keyframing: first frame to last frame (cinematic control)
python skills/image-gen/scripts/generate.py "smooth transition" -o /tmp/kf.mp4 --video --start-image first.jpg --end-image last.jpg

# Motion/style from a source clip (video-to-video reference)
python skills/image-gen/scripts/generate.py "same motion, new subject" -o /tmp/mv.mp4 --video --video-references source.mp4

# Cost check before video generation
python skills/image-gen/scripts/generate.py "a sunset timelapse" --video --cost -m veo3
```

## 3D Generation (`--threed`)

Generates a 3D mesh. Default model `text-to-3d` (Tripo). Output defaults to `.glb`.

```bash
# Text to 3D mesh
python skills/image-gen/scripts/generate.py "a low-poly treasure chest, game asset" -o /tmp/chest.glb --threed

# Higher geometry + texture detail
python skills/image-gen/scripts/generate.py "a stylized sword" -o /tmp/sword.glb --threed --extra '{"geometry_quality":"detailed","texture_quality":"detailed"}'

# Image to 3D (needs a reference image)
python skills/image-gen/scripts/generate.py "turn this into a 3D model" -o /tmp/model.glb --threed -m image-to-3d -r /path/to/photo.jpg
```

## Audio Generation (`--audio`)

Generates music or speech. Default model `music` (Sonilo, needs a duration; defaults to 10s). Output defaults to `.mp3`.

```bash
# Text to music (duration required, defaults to 10s)
python skills/image-gen/scripts/generate.py "warm cinematic piano, slow, melancholic" -o /tmp/theme.mp3 --audio -m music --duration 30

# Text to speech / audio (Seed Audio)
python skills/image-gen/scripts/generate.py "welcome to the show" -o /tmp/vo.mp3 --audio -m speech
```

For neural TTS with local voices, prefer the dedicated `text-to-speech` skill; use `--audio -m music` here for generative music, which that skill does not cover.

### Voiced Speech (named/cloned voices)

Seed Audio can speak in a specific voice from a roster of preset voices (or a cloned one).
Requires a Higgsfield CLI new enough to expose `voices` and voiced `seed_audio` params.

```bash
# List the voice catalog (id, name, type)
python skills/image-gen/scripts/generate.py --list-voices

# Speak in a named preset voice, with optional pitch/speed
python skills/image-gen/scripts/generate.py "Welcome to the show." -o /tmp/vo.mp3 \
  --audio -m speech --voice-id <voice_id> --voice-type preset --pitch 0 --speed 0
```

`--voice-type` is `preset` (built-in) or `element` (a cloned voice). Cost is ~0.2 credits.
The result is a WAV even when written to a .mp3 path (the bytes are valid audio; pass
`--extra '{"format":"mp3"}'` for true mp3).

## Post-Production Workflows (`--workflow`)

Transform an existing video (or image). These run through Higgsfield's workflow/create
pipeline, not the plain generators. Always `--cost` first: video workflows regenerate
frames and are priced accordingly (a 2s reframe is ~15 credits).

```bash
# Reframe: re-aspect a finished clip to another ratio (no reshoot)
python skills/image-gen/scripts/generate.py --workflow reframe \
  --video-input clip.mp4 -a 9:16 --extra '{"resolution":"720p"}' -o /tmp/vertical.mp4

# Dubbing: dub a video into another language (Greek NOT supported; eng/spa/fra/deu/ita/... )
python skills/image-gen/scripts/generate.py --workflow dubbing \
  --video-input clip.mp4 --target-language spa -o /tmp/dubbed.mp4

# Voice change: swap the speaking voice (voice id from --list-voices)
python skills/image-gen/scripts/generate.py --workflow voice_change \
  --video-input clip.mp4 --voice-id <voice_id> --voice-type preset -o /tmp/revoiced.mp4

# Draw-to-video: sketch-guided edit of a frame
python skills/image-gen/scripts/generate.py --workflow draw_to_video \
  --video-input clip.mp4 --sketch frame.png --extra '{"timestamp":3.2}' --prompt "make the jacket red" -o /tmp/edited.mp4

# Image decompose: split an image into layers (image + mode)
python skills/image-gen/scripts/generate.py --workflow image_decompose \
  -r photo.jpg --extra '{"mode":"granular"}' -o /tmp/layer.png
```

Workflow notes:
- **reframe / dubbing / voice_change / draw_to_video** dispatch via `generate workflow`; **image_decompose / kling3_0_motion_control** are prompt-less `generate create` models. `--workflow` routes both correctly.
- The source clip goes to `--video-input`; a reference image to `-r`; a sketch to `--sketch`.
- `--cost` requires `--duration` (the segment length to price); the actual run derives duration from the input, so do not pass `--duration` to the run. Workflow resolution/mode/timestamp go via `--extra`.
- `kling3_0_motion_control` (motion transfer from an image + a source clip) is wired but needs both an image and a video reference; drive it with `-r image.jpg --video-input source.mp4 --extra '{"mode":"std"}'`.

## Soul Character References (`soul_id.py`)

Train a character identity once from 5-20 images, then reuse it for consistent stills and
video across a whole production (the real mechanism behind character consistency, beyond
prompt discipline). Training is a paid job; check the returned status before relying on it.

```bash
# Train a new Soul (5-20 images; --model soul-2 or soul-cinematic)
python skills/image-gen/scripts/soul_id.py create --name Alice --model soul-2 \
  --image a1.jpg --image a2.jpg --image a3.jpg --image a4.jpg --image a5.jpg

python skills/image-gen/scripts/soul_id.py list          # existing refs
python skills/image-gen/scripts/soul_id.py get <soul_id> # inspect one
python skills/image-gen/scripts/soul_id.py wait <soul_id> # poll training
```

## Brand Imagery (`brand.py`)

Backend prompt enhancement for commercial work. `--enhance-only` returns the enhanced
prompt(s) for free (no image jobs); without it, every returned image is downloaded to
`--output-dir` (default `/tmp/media_gen_brand`).

```bash
# Product photoshoot (modes: product_shot, lifestyle_scene, hero_banner, ad_creative_pack,
#   moodboard_pin, conceptual_product, restyle, virtual_model_tryout, closeup_product_with_person, social_carousel)
python skills/image-gen/scripts/brand.py photoshoot --mode product_shot \
  --prompt "hero shot of the bottle" --image bottle.jpg --count 3

# See the backend-enhanced prompt without spending (free)
python skills/image-gen/scripts/brand.py photoshoot --mode lifestyle_scene \
  --prompt "bottle for IG" --image bottle.jpg --enhance-only

# Marketplace cards (scopes: main, product-images, aplus, full-set)
python skills/image-gen/scripts/brand.py cards --scope full-set \
  --prompt "peach lemonade can" --image can.png
```

Photoshoot enhancement can target an expensive model (e.g. gpt_image_2, ~7 cr/image), so
`--enhance-only` first, then generate. `--count` is 1-10.

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
| `--threed` | Generate a 3D mesh (default output `.glb`) |
| `--audio` | Generate audio: music or speech (default output `.mp3`) |
| `--workflow` | Post-production workflow: reframe, dubbing, voice_change, draw_to_video, image_decompose, kling3_0_motion_control |
| `--video-input` | Source video for a workflow (reframe/dubbing/voice_change/draw_to_video) |
| `--sketch` | Sketch/drawing frame for the draw_to_video workflow |
| `--target-language` | Target language code for dubbing (eng, spa, fra, deu, ita, ...; no Greek) |
| `--start-image`, `--end-image` | First/last-frame keyframes (video create) |
| `--video-references` | Motion/style source clip for a video model |
| `--voice-id`, `--voice-type` | Voice for speech / voice_change (preset or element); see `--list-voices` |
| `--pitch`, `--speed` | Voice pitch/speed for speech (seed_audio) |
| `--backend` | Backend: auto (default, tries Higgsfield then Pollinations), higgsfield, pollinations |
| `--resolution` | Resolution: 1k, 2k, 4k (default: 2k, images only) |
| `--duration` | Duration in seconds (video and music; model-dependent) |
| `--extra` | JSON string of extra model params (e.g. `'{"quality":"high"}'`) |
| `--list-models` | List all model aliases and exit |
| `--list-voices` | List text-to-speech voices and exit |
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
| `recraft` | Recraft V4.1 | Design, logos, vector (`--extra '{"model_type":"vector"}'`) |
| `nano-lite` | Nano Banana 2 Lite | Cheapest Nano Pro tier (`thinking` MINIMAL/HIGH) |
| `soul-cinema` | Soul Cinema Studio | Cinematic character frames |
| `auto` | Image Auto | Let Higgsfield pick |

## Video Model Aliases

| Alias | Model | Notes |
|-------|-------|-------|
| `kling` | Kling 3.0 | Default video model, high quality |
| `kling-turbo` | Kling 3.0 Turbo | Faster, cheaper Kling 3.0 |
| `kling2.6` | Kling 2.6 | Previous gen |
| `veo3` | Google Veo 3 | Cinematic, photorealistic |
| `veo3.1` | Google Veo 3.1 | Latest, highest quality |
| `veo3-lite` | Google Veo 3.1 Lite | Faster, lower cost |
| `gemini` | Gemini Omni Flash | Google multimodal video |
| `seedance` | Seedance 2.0 | Precise motion control |
| `seedance-mini` | Seedance 2.0 Mini | Cheaper Seedance |
| `seedance1.5` | Seedance 1.5 Pro | Previous gen |
| `cinematic3` | Cinematic Studio 3.0 | Film-grade output |
| `cinematic3.5` | Cinematic Studio Video 3.5 | Newest cinematic (forces English) |
| `cinematic-video` | Cinematic Studio Video V2 | Stylized video |
| `grok-video` | Grok Video | Creative, distinctive |
| `hailuo` | Minimax Hailuo | Fast, reliable |
| `h3`, `hailuo3` | MiniMax H3 (Hailuo 3.0) | 2K + native audio; structured prompt format, priciest in the catalog |
| `wan` | Wan 2.7 | Latest Wan model |
| `wan2.6` | Wan 2.6 | Previous gen |
| `soul-cast` | Soul Cast | Character-consistent video |
| `marketing` | Marketing Studio Video | Product/marketing content |

## 3D + Audio Model Aliases

| Alias | Model | Kind | Notes |
|-------|-------|------|-------|
| `text-to-3d`, `3d` | Tripo 3D | `--threed` | Text to mesh (`.glb`) |
| `image-to-3d` | Image to 3D | `--threed` | Needs `-r` reference image |
| `music` | Sonilo Music | `--audio` | Generative music (needs `--duration`) |
| `speech` | Seed Audio | `--audio` | Text to speech / audio |

## Credit Costs (approximate)

| Tier | Credits | Models |
|------|---------|--------|
| Image cheap | 1-1.5 | nano, nano2, recraft, nano-lite, soul-cinema |
| Image mid | 2 | nano-pro, cinematic, flux, seedream, kling |
| Image expensive | 7 | gpt, hazel |
| Image free | 0 | Pollinations (`--backend pollinations`) |
| Video | 7-60 | kling-turbo (7), kling (10), seedance-mini (12), seedance (22), veo3.1 (22), gemini (24), cinematic3 (25), cinematic3.5 (25), h3 (20 at the 5s minimum, 4/second, 60 at 15s) |
| 3D | 5 | text-to-3d, image-to-3d |
| Audio | 1 | music, speech |

Figures are approximate and vary by CLI version and account. Always run `--cost` before generating. Every Higgsfield generation auto-reports credits used and remaining balance.

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
| h3, hailuo3 | `minimax-h3.md` |
| wan, wan2.6 | `wan.md` |
| soul-cast | `soul-cast.md` |
| marketing | `marketing-studio.md` |
| recraft, nano-lite | `nano-banana.md` |
| soul-cinema | `soul.md` |
| seedance-mini | `seedance.md` |
| kling-turbo | `kling-video.md` |
| gemini (video) | `gemini-omni.md` |
| cinematic3.5 | `cinematic-video.md` |

**3D and audio models** (`tripo`, `text-to-3d`, `image-to-3d`, `music`, `speech`) have no prompt-refinement guide yet. Use the model's own `--cost` output and parameter list until one is written.

**Cross-cutting guide (not a model):** to build a consistent character or brand figure across stills — medium-grey backdrop, the Locked Identity Spec, the six-panel sheet, and model + outfit routing — read `character-consistency.md`. Lock the character there, then animate it in `seedance.md`.

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

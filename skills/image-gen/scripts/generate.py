#!/usr/bin/env python3
"""Generate images using Higgsfield CLI (primary) with Pollinations.ai fallback.

Higgsfield: 20+ models, up to 4K, requires CLI auth (`higgsfield auth login`).
Pollinations: Free tier, 768x768 max, sana model, no auth needed.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

LAST_GEN_DIR = Path("/tmp/media_gen_last")


def _save_last_generation(output_path: str, media_type: str, model: str, prompt: str, user_id: str | None = None):
    """Save breadcrumb of last successful generation for iteration support."""
    LAST_GEN_DIR.mkdir(exist_ok=True)
    breadcrumb = {
        "path": str(output_path),
        "type": media_type,
        "model": model,
        "prompt": prompt,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    filename = f"last_{user_id}.json" if user_id else "last.json"
    crumb_file = LAST_GEN_DIR / filename
    crumb_file.write_text(json.dumps(breadcrumb, indent=2))


MODEL_ALIASES = {
    "nano": "nano_banana",
    "nano2": "nano_banana_flash",
    "nano-pro": "nano_banana_2",
    "gpt": "gpt_image_2",
    "grok": "grok_image",
    "flux": "flux_2",
    "flux-kontext": "flux_kontext",
    "soul": "text2image_soul_v2",
    "cinematic": "cinematic_studio_2_5",
    "soul-cinematic": "soul_cinematic",
    "soul-location": "soul_location",
    "seedream": "seedream_v4_5",
    "seedream-lite": "seedream_v5_lite",
    "hazel": "openai_hazel",
    "kling": "kling_omni_image",
    "auto": "image_auto",
    "ms": "ms_image",
    "ms-studio": "marketing_studio_image",
    "z": "z_image",
    # --- new image models from the live catalog ---
    "recraft": "recraft_v4_1",
    "nano-lite": "nano_banana_2_lite",
    "soul-cinema": "soul_cinema_studio",
    # `soul_cast` reports type=image on the live catalog and takes aspect_ratio +
    # budget + prompt, no duration. It sat in VIDEO_MODEL_ALIASES until 2026-08-07,
    # which wrote its stills into a .mp4 container.
    "soul-cast": "soul_cast",
}

DEFAULT_MODEL = "nano_banana_flash"

VIDEO_MODEL_ALIASES = {
    "veo3": "veo3",
    "veo3.1": "veo3_1",
    "veo3-lite": "veo3_1_lite",
    "kling": "kling3_0",
    "kling2.6": "kling2_6",
    "seedance": "seedance_2_0",
    "seedance1.5": "seedance1_5",
    "cinematic3": "cinematic_studio_3_0",
    "cinematic-video": "cinematic_studio_video",
    "cinematic-v2": "cinematic_studio_video_v2",
    "grok-video": "grok_video",
    "hailuo": "minimax_hailuo",
    "wan": "wan2_7",
    "wan2.6": "wan2_6",
    "marketing": "marketing_studio_video",
    # --- new video models from the live catalog ---
    "seedance-mini": "seedance_2_0_mini",
    "kling-turbo": "kling3_0_turbo",
    "gemini": "gemini_omni",
    "cinematic3.5": "cinematic_studio_video_3_5",
    # MiniMax H3 (Hailuo 3.0). Distinct model from `hailuo` (minimax_hailuo),
    # which stays in the catalog and wants a completely different prompt style.
    "h3": "minimax_h3",
    "hailuo3": "minimax_h3",
    # --- video models added 2026-08-07 after a full catalog sweep ---
    "flux-video": "flux_3_video",
    "flux3-video": "flux_3_video",
    "grok-video1.5": "grok_video_v15",
    "happy-horse": "happy_horse_video",
    "seedance2.5": "seedance_2_5",
}

# What the Mini App's image and video menu shows for each model, in the menu's
# own order. The app keeps no list of its own (GET /api/media/menu reads menu()
# below), so a model aliased above reaches the menu with no edit to the app, and
# tests/test_image_gen_menu.py fails until it has a row here.
#   name         the card's label
#   credits      (cheapest, dearest) across the settings that move the price:
#                per image, per second for a video with a duration, per clip for
#                one without (veo3)
#   ratios       the model's aspect_ratio values, [] where it has no such param
#   resolutions  images only: the model's resolution values, [] where it has none
#   ref          "optional" or "required" where the model takes a reference
#                image, None where it refuses one ("Model does not accept media
#                inputs")
# Read off `higgsfield model get` and priced with `higgsfield generate cost`
# (free, no job) on CLI 1.1.26, 2026-10-06. veo3 needs a start image to quote at
# all; a placeholder upload id is enough for the price and uploads nothing.
MODEL_MENU = {
    # --- images ---
    "nano_banana_flash": {"name": "Nano 2", "credits": (1.5, 3), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "auto"], "resolutions": ["1k", "2k", "4k"], "ref": "optional"},
    "nano_banana": {"name": "Nano", "credits": (1, 1), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"], "resolutions": [], "ref": "optional"},
    "nano_banana_2": {"name": "Nano Pro", "credits": (2, 4), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"], "resolutions": ["1k", "2k", "4k"], "ref": "optional"},
    "nano_banana_2_lite": {"name": "Nano 2 Lite", "credits": (1, 1), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "auto"], "resolutions": ["1k"], "ref": "optional"},
    "gpt_image_2": {"name": "GPT Image 2", "credits": (0.5, 11), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "auto"], "resolutions": ["1k", "2k", "4k"], "ref": "optional"},
    "openai_hazel": {"name": "Hazel", "credits": (2, 6), "ratios": ["1:1", "3:2", "2:3", "auto"], "resolutions": [], "ref": "optional"},
    "flux_2": {"name": "FLUX 2", "credits": (1, 6), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"], "resolutions": ["1k", "2k"], "ref": "optional"},
    "flux_kontext": {"name": "Kontext", "credits": (1.5, 1.5), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"], "resolutions": [], "ref": "optional"},
    "grok_image": {"name": "Grok", "credits": (1, 2), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "auto"], "resolutions": ["1k", "2k"], "ref": "optional"},
    "text2image_soul_v2": {"name": "Soul V2", "credits": (0.12, 0.12), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"], "resolutions": [], "ref": "optional"},
    "soul_cinematic": {"name": "Soul Cinematic", "credits": (0.12, 0.12), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"], "resolutions": [], "ref": "optional"},
    "soul_location": {"name": "Soul Location", "credits": (0.12, 0.12), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "9:21"], "resolutions": [], "ref": None},
    "soul_cinema_studio": {"name": "Soul Cinema", "credits": (0.12, 0.12), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"], "resolutions": [], "ref": "optional"},
    "soul_cast": {"name": "Soul Cast", "credits": (0.12, 0.12), "ratios": ["16:9"], "resolutions": [], "ref": None},
    "seedream_v4_5": {"name": "Seedream 4.5", "credits": (1, 1), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"], "resolutions": [], "ref": "optional"},
    "seedream_v5_lite": {"name": "Seedream Lite", "credits": (1, 1), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"], "resolutions": [], "ref": "optional"},
    "cinematic_studio_2_5": {"name": "Cinematic 2.5", "credits": (2, 4), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"], "resolutions": ["1k", "2k", "4k"], "ref": "optional"},
    "kling_omni_image": {"name": "Kling O1", "credits": (0.5, 0.5), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "auto"], "resolutions": ["1k", "2k"], "ref": "optional"},
    "recraft_v4_1": {"name": "Recraft V4.1", "credits": (1.25, 10), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5"], "resolutions": ["1k", "2k"], "ref": None},
    "ms_image": {"name": "MS Image", "credits": (0.5, 12), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "auto"], "resolutions": ["1k", "2k", "4k"], "ref": "optional"},
    "marketing_studio_image": {"name": "MS Studio", "credits": (2, 4), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "auto"], "resolutions": ["1k", "2k", "4k"], "ref": "optional"},
    "z_image": {"name": "Z Image", "credits": (0.15, 0.15), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"], "resolutions": [], "ref": None},
    "image_auto": {"name": "Auto", "credits": (2, 2), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"], "resolutions": [], "ref": "optional"},
    # --- video ---
    "kling3_0": {"name": "Kling 3.0", "credits": (1.5, 6), "ratios": ["1:1", "16:9", "9:16"], "ref": "optional"},
    "kling3_0_turbo": {"name": "Kling 3.0 Turbo", "credits": (1.5, 2), "ratios": ["1:1", "16:9", "9:16"], "ref": "optional"},
    "kling2_6": {"name": "Kling 2.6", "credits": (1, 2), "ratios": ["1:1", "16:9", "9:16"], "ref": "optional"},
    "veo3_1": {"name": "Veo 3.1", "credits": (4, 15), "ratios": ["16:9", "9:16"], "ref": "optional"},
    "veo3_1_lite": {"name": "Veo 3.1 Lite", "credits": (1.5, 1.5), "ratios": ["16:9", "9:16", "auto"], "ref": "optional"},
    "veo3": {"name": "Veo 3", "credits": (22, 58), "ratios": ["16:9", "9:16"], "ref": "required"},
    "seedance_2_5": {"name": "Seedance 2.5", "credits": (3, 12), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "auto"], "ref": "optional"},
    "seedance_2_0": {"name": "Seedance 2.0", "credits": (1, 22), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "auto"], "ref": "optional"},
    "seedance_2_0_mini": {"name": "Seedance 2.0 Mini", "credits": (0.5, 1), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "auto"], "ref": "optional"},
    "seedance1_5": {"name": "Seedance 1.5", "credits": (0.6, 3), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "auto"], "ref": "optional"},
    "minimax_h3": {"name": "MiniMax H3", "credits": (2, 2), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "auto"], "ref": "optional"},
    "minimax_hailuo": {"name": "Hailuo", "credits": (1, 1), "ratios": [], "ref": "optional"},
    "wan2_7": {"name": "Wan 2.7", "credits": (1.5, 2.5), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"], "ref": "optional"},
    "wan2_6": {"name": "Wan 2.6", "credits": (1.5, 2), "ratios": ["1:1", "16:9", "9:16"], "ref": "optional"},
    "grok_video_v15": {"name": "Grok Video 1.5", "credits": (2.5, 8), "ratios": [], "ref": "optional"},
    "grok_video": {"name": "Grok Video", "credits": (1.5, 1.5), "ratios": ["1:1", "16:9", "9:16"], "ref": "optional"},
    "flux_3_video": {"name": "FLUX 3 Video", "credits": (5.5, 9), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "2:1", "auto"], "ref": "optional"},
    "happy_horse_video": {"name": "Happy Horse", "credits": (2.5, 4.5), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"], "ref": "optional"},
    "gemini_omni": {"name": "Gemini Omni", "credits": (3, 3), "ratios": ["16:9", "9:16"], "ref": "optional"},
    "cinematic_studio_3_0": {"name": "Cinematic 3.0", "credits": (3.5, 24), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "auto"], "ref": "optional"},
    "cinematic_studio_video_3_5": {"name": "Cinematic Video 3.5", "credits": (3.5, 10), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "auto"], "ref": "optional"},
    "cinematic_studio_video_v2": {"name": "Cinematic V2", "credits": (1.5, 2), "ratios": ["1:1", "16:9", "9:16"], "ref": "optional"},
    "cinematic_studio_video": {"name": "Cinematic Video", "credits": (1, 1.6), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"], "ref": "optional"},
    "marketing_studio_video": {"name": "Marketing Studio", "credits": (3.5, 10), "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "auto"], "ref": "optional"},
}

# Video models whose live schema has no `aspect_ratio` param at all. Sending it
# is a hard rejection ("Unknown params: aspect_ratio"), which broke every cost
# check on `hailuo` while main() defaulted video to 16:9. Measured 2026-08-07;
# read off MODEL_MENU since 2026-10-06, so the two cannot disagree.
NO_ASPECT_RATIO_MODELS = {job for job, row in MODEL_MENU.items() if not row["ratios"]}

# Params the API refuses to default for you: omitting them fails the model's own
# CEL rules even though `model get` prints a default. `minimax_hailuo` rejects
# every prompt-only call with "start_image or end_image is required unless
# variant is 'minimax-2.3'", so the variant has to go on the wire explicitly.
REQUIRED_DEFAULTS = {
    "minimax_hailuo": {"variant": "minimax-2.3"},
}

# --- new modalities: output is a mesh / an audio file, not jpg/mp4 ---
THREED_MODEL_ALIASES = {
    "text-to-3d": "tripo_3d",
    "3d": "tripo_3d",
    "image-to-3d": "image_to_3d",
}

AUDIO_MODEL_ALIASES = {
    "music": "sonilo_music",
    "speech": "seed_audio",
    "audio": "seed_audio",
}

DEFAULT_3D_MODEL = "text-to-3d"
DEFAULT_AUDIO_MODEL = "music"

KIND_ALIASES = {
    "image": MODEL_ALIASES,
    "video": VIDEO_MODEL_ALIASES,
    "3d": THREED_MODEL_ALIASES,
    "audio": AUDIO_MODEL_ALIASES,
}

# --- net-new post-production workflows/models (Higgsfield 1.1.x) ---
# `workflow list` mixes two dispatch mechanisms, verified live on the 1.1.x CLI:
#   cmd="workflow" -> run via `generate workflow <name>` (video post-production, source clip)
#   cmd="create"   -> run via `generate create <name>`   (prompt-less create models)
# Cost is uniform for both: `generate cost <name>`. Each takes an existing media input.
WORKFLOWS = {
    "reframe": {"cmd": "workflow", "media": "video", "out": ".mp4"},                # re-aspect a video (aspect_ratio + video required)
    "draw_to_video": {"cmd": "workflow", "media": "video", "out": ".mp4"},          # sketch-guided edit (video + prompt required)
    "dubbing": {"cmd": "workflow", "media": "video", "out": ".mp4"},                # dub into another language (target_language required)
    "voice_change": {"cmd": "workflow", "media": "video", "out": ".mp4"},           # swap the voice (voice_id required)
    "image_decompose": {"cmd": "create", "media": "image", "out": ".png"},          # split an image into layers (image + mode)
    "kling3_0_motion_control": {"cmd": "create", "media": "video", "out": ".mp4"},  # motion transfer (image + video refs)
}


def resolve_model(model: str, kind: str = "image") -> str:
    """Resolve a friendly alias to a Higgsfield job_set_type for the given kind."""
    return KIND_ALIASES.get(kind, MODEL_ALIASES).get(model, model)


VIDEO_DURATIONS = {
    "kling3_0": {"type": "slider", "default": 5, "min": 3, "max": 15},
    "kling2_6": {"type": "preset", "default": 5, "options": [5, 10]},
    "veo3": None,
    "veo3_1": {"type": "preset", "default": 8, "options": [4, 6, 8]},
    "veo3_1_lite": {"type": "preset", "default": 8, "options": [4, 6, 8]},
    # Bounds below are the validator's own numbers, read off the rejection
    # messages from `generate cost` on 2026-08-07, not the vendor's docs.
    "seedance_2_0": {"type": "slider", "default": 5, "min": 4, "max": 15},
    "seedance1_5": {"type": "preset", "default": 4, "options": [4, 8, 12]},
    "minimax_hailuo": {"type": "preset", "default": 6, "options": [6, 10]},
    "wan2_7": {"type": "slider", "default": 5, "min": 2, "max": 15},
    "wan2_6": {"type": "preset", "default": 5, "options": [5, 10, 15]},
    "grok_video": {"type": "slider", "default": 5, "min": 1, "max": 15},
    # The floor of these three is 4, not 5 ("Input should be greater than or
    # equal to 4", re-probed 2026-10-06). The validator sets them no ceiling at
    # all (100 s quotes 500 credits), so the ceilings here are unchanged.
    "cinematic_studio_3_0": {"type": "slider", "default": 5, "min": 4, "max": 20},
    "cinematic_studio_video": {"type": "preset", "default": 5, "options": [5, 10]},
    "cinematic_studio_video_v2": {"type": "slider", "default": 5, "min": 3, "max": 12},
    "marketing_studio_video": {"type": "slider", "default": 15, "min": 4, "max": 60},
    # --- new video/audio models ---
    "seedance_2_0_mini": {"type": "slider", "default": 5, "min": 4, "max": 15},
    "kling3_0_turbo": {"type": "slider", "default": 5, "min": 3, "max": 15},
    # Any whole second from 4 to 10, not the 4/6/8 presets this said until
    # 2026-10-06: 5, 7, 9 and 10 all quote, 3 and 11 are refused.
    "gemini_omni": {"type": "slider", "default": 8, "min": 4, "max": 10},
    # MiniMax documents a 4s floor. The Higgsfield route refused anything under 5
    # on 2026-08-07 and takes 4 since: re-probed 2026-10-06, 4 s quotes 8
    # credits and 3 s gives "Input should be greater than or equal to 4".
    "minimax_h3": {"type": "slider", "default": 5, "min": 4, "max": 15},
    "cinematic_studio_video_3_5": {"type": "slider", "default": 15, "min": 4, "max": 20},
    "sonilo_music": {"type": "slider", "default": 10, "min": 5, "max": 60},
    # --- added 2026-08-07 ---
    "flux_3_video": {"type": "slider", "default": 5, "min": 5, "max": 20},
    "grok_video_v15": {"type": "slider", "default": 5, "min": 2, "max": 15},
    "happy_horse_video": {"type": "slider", "default": 5, "min": 3, "max": 15},
    # The 2026-08-07 sweep recorded a 5s floor; re-probed 2026-08-09 and the real
    # floor is 4 ("--duration 3" gives "Input should be greater than or equal to 4",
    # "--duration 4" quotes 26 credits at 720p). The cheapest 720p roll read as
    # unavailable until this was corrected.
    "seedance_2_5": {"type": "slider", "default": 5, "min": 4, "max": 30},
}

MODEL_PARAMS = {
    # `variant`, not `model`: "Unknown params: model" (re-probed 2026-10-06),
    # the same rename veo3 and minimax_hailuo had, so the Mini App's FLUX 2
    # card failed every request with the default it sent.
    "flux_2": {"variant": {"options": ["pro", "flex", "max"], "default": "pro"}},
    "gpt_image_2": {"quality": {"options": ["low", "medium", "high"], "default": "high"}},
    "imagegen_2_0": {"quality": {"options": ["low", "medium", "high"], "default": "high"}},
    "grok_image": {"mode": {"options": ["std", "quality"], "default": "std"}},
    "text2image_soul_v2": {"quality": {"options": ["1.5k", "2k"], "default": "2k"}},
    "soul_cinematic": {"quality": {"options": ["1.5k", "2k"], "default": "2k"}},
    "seedream_v4_5": {"quality": {"options": ["basic", "high"], "default": "basic"}},
    "seedream_v5_lite": {"quality": {"options": ["basic", "high"], "default": "basic"}},
    "openai_hazel": {"quality": {"options": ["low", "medium", "high"], "default": "medium"}},
    "kling3_0": {
        "mode": {"options": ["std", "pro", "4k"], "default": "std"},
        "sound": {"options": ["on", "off"], "default": "on"},
    },
    "kling2_6": {"sound": {"type": "bool", "default": True}},
    # The flag is `variant`, not `model`: `--model` is rejected outright with
    # "Unknown params: model". Verified on veo3, veo3_1 and minimax_hailuo.
    "veo3": {"variant": {"options": ["veo-3-preview", "veo-3-fast"], "default": "veo-3-fast"}},
    "veo3_1": {
        "variant": {"options": ["veo-3-1-preview", "veo-3-1-fast"], "default": "veo-3-1-fast"},
        "quality": {"options": ["basic", "high", "ultra"], "default": "basic"},
    },
    "veo3_1_lite": {"generate_audio": {"type": "bool", "default": False}},
    "seedance_2_0": {
        "mode": {"options": ["std", "fast"], "default": "std"},
        "genre": {"options": ["auto", "action", "horror", "comedy", "noir", "drama", "epic"], "default": "auto"},
        # `fast` covers 480p/720p only; 1080p and 4k need mode `std`.
        "resolution": {"options": ["480p", "720p", "1080p", "4k"], "default": "720p"},
        "bitrate_mode": {"options": ["standard", "high"], "default": "standard"},
        "generate_audio": {"type": "bool", "default": True},
    },
    "seedance1_5": {
        "resolution": {"options": ["480p", "720p", "1080p"], "default": "720p"},
        "generate_audio": {"type": "bool", "default": True},
    },
    "minimax_hailuo": {
        "variant": {"options": ["minimax", "minimax-fast", "minimax-2.3", "minimax-2.3-fast"], "default": "minimax-2.3"},
        # `resolution` is unreachable through the CLI: bare digits are sent as a
        # number ("resolution should be string"), and a quoted value is sent with
        # the quotes ("allowed: 512,768,1080"). No spelling works, so the model is
        # pinned to its 768 default. Upstream CLI bug, measured 2026-08-07.
    },
    "wan2_7": {"resolution": {"options": ["720p", "1080p"], "default": "720p"}},
    "wan2_6": {"quality": {"options": ["720p", "1080p"], "default": "720p"}},
    "cinematic_studio_video": {
        "sound": {"type": "bool", "default": True},
        "slow_motion": {"type": "bool", "default": False},
    },
    "cinematic_studio_video_v2": {
        "mode": {"options": ["pro", "std"], "default": "std"},
        "genre": {"options": ["auto", "action", "horror", "comedy", "western", "suspense", "intimate", "spectacle"], "default": "auto"},
    },
    "marketing_studio_video": {
        "mode": {"options": ["ugc", "ugc_how_to", "ugc_unboxing", "product_showcase", "product_review", "tv_spot", "wild_card", "ugc_virtual_try_on", "virtual_try_on"], "default": "ugc"},
        "resolution": {"options": ["480p", "720p", "1080p"], "default": "720p"},
        "generate_audio": {"type": "bool", "default": False},
    },
    "ms_image": {
        "quality": {"options": ["low", "medium", "high"], "default": "low"},
    },
    # --- new models from the live catalog ---
    "recraft_v4_1": {
        "model_type": {"options": ["standard", "vector", "utility", "utility_vector"], "default": "standard"},
        "resolution": {"options": ["1k", "2k"], "default": "2k"},
    },
    "nano_banana_2_lite": {
        "thinking": {"options": ["MINIMAL", "HIGH"], "default": "HIGH"},
    },
    "soul_cinema_studio": {
        "quality": {"options": ["1.5k", "2k"], "default": "2k"},
    },
    "seedance_2_0_mini": {
        "genre": {"options": ["auto", "action", "horror", "comedy", "noir", "drama", "epic"], "default": "auto"},
        "resolution": {"options": ["480p", "720p"], "default": "720p"},
        "bitrate_mode": {"options": ["standard", "high"], "default": "standard"},
        "generate_audio": {"type": "bool", "default": True},
    },
    # --- added 2026-08-07 ---
    "flux_3_video": {
        "resolution": {"options": ["720p", "1080p"], "default": "720p"},
        "generate_audio": {"type": "bool", "default": True},
    },
    "grok_video_v15": {
        # 1080p is refused whenever reference media is attached.
        "resolution": {"options": ["480p", "720p", "1080p"], "default": "720p"},
    },
    "happy_horse_video": {
        "resolution": {"options": ["720p", "1080p"], "default": "720p"},
    },
    "seedance_2_5": {
        "mode": {"options": ["t2v", "omni_reference", "video_edit", "video_extension"], "default": "t2v"},
        # 1080p (9.0 cr/s) and bitrate_mode both went live between 2026-08-10 and
        # 2026-08-19; the old table said neither existed. 4k is still refused.
        "resolution": {"options": ["480p", "720p", "1080p"], "default": "720p"},
        "bitrate_mode": {"options": ["standard", "high"], "default": "standard"},
        "generate_audio": {"type": "bool", "default": True},
        "extension_mode": {"options": ["backward", "forward"], "default": None},
    },
    "kling3_0_turbo": {"resolution": {"options": ["720p", "1080p"], "default": "720p"}},
    "cinematic_studio_video_3_5": {
        "prompt_language": {"options": ["en", "zh"], "default": "en"},
        "genre": {"options": ["auto", "action", "horror", "comedy", "noir", "drama", "epic"], "default": "auto"},
        "resolution": {"options": ["480p", "720p", "1080p"], "default": "720p"},
        "multi_shot_mode": {"options": ["auto", "custom"], "default": "custom"},
    },
    "tripo_3d": {
        "geometry_quality": {"options": ["standard", "detailed"], "default": "standard"},
        "texture_quality": {"options": ["standard", "detailed"], "default": "standard"},
        "pbr": {"type": "bool", "default": True},
        "texture": {"type": "bool", "default": True},
    },
    "seed_audio": {"format": {"options": ["wav", "mp3", "pcm", "ogg_opus"], "default": "mp3"}},
}

DEFAULT_VIDEO_MODEL = "kling3_0"

ASPECT_RATIOS = {
    "1:1": (1024, 1024),
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
    "3:2": (1200, 800),
    "2:3": (800, 1200),
    "5:4": (1280, 1024),
    "4:5": (1024, 1280),
    "21:9": (1680, 720),
    "9:21": (720, 1680),
}


def _credits_label(lo: float, hi: float, per_second: bool) -> str:
    span = f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"
    return f"{span} cr/s" if per_second else f"{span} cr"


def menu() -> dict:
    """The Mini App's image and video menu: one card per model, in MODEL_MENU order.

    A card carries everything the app draws, so the app keeps no copy of any of
    it: the alias to send back, the label and price, the ratios, resolutions and
    durations it may offer, whether a reference image is taken, and the model's
    own options. Ratios are cut to the eleven ASPECT_RATIOS, the set the Mini App
    server accepts.
    """
    kinds = (("image", MODEL_ALIASES, DEFAULT_MODEL), ("video", VIDEO_MODEL_ALIASES, DEFAULT_VIDEO_MODEL))
    cards = {"image": [], "video": []}
    for job, row in MODEL_MENU.items():
        for kind, aliases, default in kinds:
            # The first alias wins where a model has two (h3 and hailuo3).
            alias = next((a for a, j in aliases.items() if j == job), None)
            if alias is None:
                continue
            duration = VIDEO_DURATIONS.get(job) if kind == "video" else None
            # The app preselects every option it shows and sends it, so one with
            # no default would go out on every request: seedance_2_5's
            # extension_mode is refused outside mode video_extension. An image's
            # resolution has its own row, so recraft's copy here would draw twice.
            options = {k: v for k, v in MODEL_PARAMS.get(job, {}).items()
                       if v.get("default") is not None and not (kind == "image" and k == "resolution")}
            card = {
                "id": alias,
                "name": row["name"],
                "cost": _credits_label(*row["credits"], per_second=bool(duration)),
                "ratios": [r for r in row["ratios"] if r in ASPECT_RATIOS],
                "ref": row["ref"],
                "options": options,
            }
            if kind == "image":
                card["resolutions"] = row["resolutions"]
            else:
                card["duration"] = duration
            if job == default:
                card["default"] = True
            cards[kind].append(card)
    return cards


def _aspect_args(job: str, aspect_ratio: str | None) -> list[str]:
    """--aspect_ratio for a ratio somebody chose, unless the model has none.

    Nothing is held back as "the default" any more. Until 2026-10-06 1:1 (an
    image) and 16:9 (a video) were never sent, on the theory that every model
    defaults to them, and five do not: soul_cinema_studio defaults to 16:9,
    marketing_studio_video to 9:16, and minimax_h3, flux_3_video and
    cinematic_studio_video_3_5 to auto. A square Soul Cinema or a landscape
    Marketing Studio picked in the Mini App came out in the other shape.
    """
    if not aspect_ratio or job in NO_ASPECT_RATIO_MODELS:
        return []
    return ["--aspect_ratio", aspect_ratio]


def _resolution_args(job: str, resolution: str | None) -> list[str]:
    """--resolution for an image, where the model has a resolution at all.

    2k used to be held back as "the default", and eight of the image models
    that offer 2k default to 1k: nano_banana_flash quotes 1.5 credits with no
    resolution, the same as 1k, and 2 at 2k (2026-10-06). 2K is what the Mini
    App preselects, so it bought a 1K picture. A model with no resolution
    setting (soul, seedream, ...) refuses the flag outright, "Unknown params:
    resolution", so it is never sent there; one the menu does not know is sent
    whatever was asked.
    """
    if not resolution:
        return []
    known = MODEL_MENU.get(job)
    if known is not None and not known.get("resolutions"):
        return []
    return ["--resolution", resolution]


def get_account_status() -> dict:
    try:
        result = subprocess.run(
            ["higgsfield", "account", "status", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def list_voices() -> list[dict]:
    """Return the text-to-speech voice catalog (preset + cloned)."""
    try:
        result = subprocess.run(
            ["higgsfield", "voices", "list", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                return data.get("items", [])
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def estimate_cost(model: str, prompt: str = "test", aspect_ratio: str | None = None,
                   resolution: str | None = None, duration: int | None = None,
                   kind: str = "image", extra_params: dict | None = None,
                   ref_image: str | None = None, start_image: str | None = None,
                   end_image: str | None = None, video_references: str | None = None) -> dict:
    """Quote a job the way it will be created: same params, same media.

    The media flags matter to the price: a video_references clip takes
    flux_3_video from 5.5 to 13 credits a second. The quote used to send no
    media at all, so a 5 s continuation quoted 27.5 credits and charged 65
    (Linux bot review 2026-09-27).
    """
    resolved = resolve_model(model, kind)
    cmd = ["higgsfield", "generate", "cost", resolved, "--prompt", prompt, "--json"]
    cmd.extend(_aspect_args(resolved, aspect_ratio))
    if kind == "image":
        cmd.extend(_resolution_args(resolved, resolution))
    # as create sends it: a video model without a duration param (veo3) takes none
    if duration is not None and (resolved == "sonilo_music"
                                 or (kind == "video" and VIDEO_DURATIONS.get(resolved))):
        cmd.extend(["--duration", str(duration)])
    for k, v in REQUIRED_DEFAULTS.get(resolved, {}).items():
        if not (extra_params or {}).get(k):
            cmd.extend([f"--{k}", str(v)])
    if ref_image:
        cmd.extend(["--image", ref_image])
    if start_image:
        cmd.extend(["--start-image", start_image])
    if end_image:
        cmd.extend(["--end-image", end_image])
    if video_references:
        cmd.extend(["--video-references", video_references])
    _append_extra(cmd, extra_params)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            acct = get_account_status()
            data["credits_remaining"] = acct.get("credits", "unknown")
            return data
    except Exception as e:
        return {"error": str(e)}
    return {"error": f"Cost check failed: {result.stderr.strip()[:200]}"}


def generate_higgsfield(
    prompt: str,
    output_path: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    ref_image: str | None = None,
    extra_params: dict | None = None,
) -> dict:
    resolved = MODEL_ALIASES.get(model, model)

    cmd = [
        "higgsfield", "generate", "create", resolved,
        "--prompt", prompt,
        "--wait",
        "--json",
    ]

    # None is the model's own default; anything chosen is sent.
    cmd.extend(_aspect_args(resolved, aspect_ratio))
    cmd.extend(_resolution_args(resolved, resolution))

    if ref_image:
        cmd.extend(["--image", ref_image])

    if extra_params:
        for k, v in extra_params.items():
            if v is not None and v != "":
                if isinstance(v, bool):
                    cmd.extend([f"--{k}", str(v).lower()])
                else:
                    cmd.extend([f"--{k}", str(v)])

    try:
        # 180s was too short for nano-pro at 2k: the CLI was killed while the paid
        # job was still running, so the job completed and charged but this function
        # reported failure and the caller silently fell back to the free model.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "authenticate" in stderr.lower() or "token" in stderr.lower():
                return {"success": False, "path": "", "error": "Higgsfield not authenticated. Run: higgsfield auth login"}
            return {"success": False, "path": "", "error": f"Higgsfield error: {stderr[:500]}"}

        data = json.loads(result.stdout)

        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, str):
            data = {"result_url": data}

        url = data.get("result_url", "")
        if not url:
            return {"success": False, "path": "", "error": f"No result URL in response: {result.stdout[:300]}"}

        with httpx.Client(timeout=60, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return {"success": False, "path": "", "error": f"Failed to download result: HTTP {resp.status_code}"}

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)

        acct = get_account_status()
        result_data = {
            "success": True,
            "path": str(output_path),
            "model": data.get("display_name", resolved),
            "url": url,
            "credits_used": data.get("credits_used", ""),
            "credits_remaining": acct.get("credits", ""),
            "error": "",
        }
        return result_data

    except subprocess.TimeoutExpired:
        return {"success": False, "path": "", "error": "Generation timed out (900s). The job may still have run and been charged — check: higgsfield generate list"}
    except json.JSONDecodeError:
        return {"success": False, "path": "", "error": f"Invalid JSON from CLI: {result.stdout[:300]}"}
    except Exception as e:
        return {"success": False, "path": "", "error": str(e)}


def generate_video(
    prompt: str,
    output_path: str,
    model: str = DEFAULT_VIDEO_MODEL,
    aspect_ratio: str | None = None,
    duration: int | None = None,
    ref_image: str | None = None,
    extra_params: dict | None = None,
    start_image: str | None = None,
    end_image: str | None = None,
    video_references: str | None = None,
) -> dict:
    resolved = VIDEO_MODEL_ALIASES.get(model, model)

    cmd = [
        "higgsfield", "generate", "create", resolved,
        "--prompt", prompt,
        "--wait",
        "--wait-timeout", "10m",
        "--wait-interval", "5s",
        "--json",
    ]

    cmd.extend(_aspect_args(resolved, aspect_ratio))

    dur_info = VIDEO_DURATIONS.get(resolved)
    if duration and dur_info:
        cmd.extend(["--duration", str(duration)])

    for k, v in REQUIRED_DEFAULTS.get(resolved, {}).items():
        if not (extra_params or {}).get(k):
            cmd.extend([f"--{k}", str(v)])

    if ref_image:
        cmd.extend(["--image", ref_image])

    # Keyframing (first/last frame) and a motion/style source clip. All auto-uploaded.
    if start_image:
        cmd.extend(["--start-image", start_image])
    if end_image:
        cmd.extend(["--end-image", end_image])
    if video_references:
        cmd.extend(["--video-references", video_references])

    if extra_params:
        for k, v in extra_params.items():
            if v is not None and v != "":
                if isinstance(v, bool):
                    cmd.extend([f"--{k}", str(v).lower()])
                else:
                    cmd.extend([f"--{k}", str(v)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=660,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "authenticate" in stderr.lower() or "token" in stderr.lower():
                return {"success": False, "path": "", "error": "Higgsfield not authenticated. Run: higgsfield auth login"}
            return {"success": False, "path": "", "error": f"Higgsfield error: {stderr[:500]}"}

        data = json.loads(result.stdout)

        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, str):
            data = {"result_url": data}

        url = data.get("result_url", "")
        if not url:
            return {"success": False, "path": "", "error": f"No result URL in response: {result.stdout[:300]}"}

        with httpx.Client(timeout=120, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return {"success": False, "path": "", "error": f"Failed to download result: HTTP {resp.status_code}"}

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)

        acct = get_account_status()
        result_data = {
            "success": True,
            "path": str(output_path),
            "model": data.get("display_name", resolved),
            "url": url,
            "credits_used": data.get("credits_used", ""),
            "credits_remaining": acct.get("credits", ""),
            "error": "",
        }
        return result_data

    except subprocess.TimeoutExpired:
        return {"success": False, "path": "", "error": "Video generation timed out (10min)"}
    except json.JSONDecodeError:
        return {"success": False, "path": "", "error": f"Invalid JSON from CLI: {result.stdout[:300]}"}
    except Exception as e:
        return {"success": False, "path": "", "error": str(e)}


def _extract_result_url(data: dict) -> str:
    """Pull the output URL from a job result. Most jobs put it at result_url; some
    (e.g. image_decompose) leave that null and return outputs under params.medias[].data.url."""
    url = data.get("result_url") or data.get("min_result_url") or ""
    if url:
        return url
    medias = (data.get("params") or {}).get("medias") or []
    for m in medias:
        if isinstance(m, dict):
            candidate = (m.get("data") or {}).get("url")
            if candidate:
                return candidate
    return ""


def _submit_and_download(cmd: list[str], output_path: str, timeout: int = 660,
                          dl_timeout: int = 180, fallback_model: str = "") -> dict:
    """Run an already-built higgsfield create/workflow command, parse the result URL,
    download it to output_path, and return the standard result dict.

    Shared by the generic create path (generate_job) and the workflow path
    (generate_workflow). The proven image (generate_higgsfield) and video
    (generate_video) functions keep their own bodies to avoid any regression on
    the live pipeline.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "authenticate" in stderr.lower() or "token" in stderr.lower():
                return {"success": False, "path": "", "error": "Higgsfield not authenticated. Run: higgsfield auth login"}
            return {"success": False, "path": "", "error": f"Higgsfield error: {stderr[:500]}"}

        data = json.loads(result.stdout)

        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, str):
            data = {"result_url": data}

        url = _extract_result_url(data)
        if not url:
            return {"success": False, "path": "", "error": f"No result URL in response: {result.stdout[:300]}"}

        with httpx.Client(timeout=dl_timeout, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return {"success": False, "path": "", "error": f"Failed to download result: HTTP {resp.status_code}"}

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)

        acct = get_account_status()
        return {
            "success": True,
            "path": str(output_path),
            "model": data.get("display_name", fallback_model),
            "url": url,
            "credits_used": data.get("credits_used", ""),
            "credits_remaining": acct.get("credits", ""),
            "error": "",
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "path": "", "error": f"Generation timed out ({timeout}s)"}
    except json.JSONDecodeError:
        return {"success": False, "path": "", "error": f"Invalid JSON from CLI: {result.stdout[:300]}"}
    except Exception as e:
        return {"success": False, "path": "", "error": str(e)}


def _append_extra(cmd: list[str], extra_params: dict | None) -> None:
    """Append --key value pairs for extra model/workflow params (booleans lowercased)."""
    if not extra_params:
        return
    for k, v in extra_params.items():
        if v is not None and v != "":
            cmd.extend([f"--{k}", str(v).lower() if isinstance(v, bool) else str(v)])


def generate_job(
    prompt: str,
    output_path: str,
    resolved_model: str,
    duration: int | None = None,
    ref_media: str | None = None,
    extra_params: dict | None = None,
    timeout: int = 660,
) -> dict:
    """Generic create -> wait -> download for kinds beyond image/video (3D meshes, audio)."""
    cmd = [
        "higgsfield", "generate", "create", resolved_model,
        "--prompt", prompt,
        "--wait",
        "--wait-timeout", "10m",
        "--wait-interval", "5s",
        "--json",
    ]
    if duration is not None:
        cmd.extend(["--duration", str(duration)])
    if ref_media:
        cmd.extend(["--image", ref_media])
    _append_extra(cmd, extra_params)
    return _submit_and_download(cmd, output_path, timeout=timeout, fallback_model=resolved_model)


def _workflow_params(
    video: str | None = None,
    image_refs: list[str] | str | None = None,
    sketch: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    duration: int | None = None,
    prompt: str | None = None,
    voice_id: str | None = None,
    voice_type: str | None = None,
    target_language: str | None = None,
    extra_params: dict | None = None,
) -> list[str]:
    """Build the shared --flag value list for a workflow. Used by both the cost
    estimate (`generate cost <name>`) and the run (`generate workflow <name>`), since
    Higgsfield validates the same params (including required media) for both."""
    params: list[str] = []
    if video:
        params.extend(["--video", video])
    if image_refs:
        for img in (image_refs if isinstance(image_refs, list) else [image_refs]):
            if img:
                params.extend(["--image", img])
    if sketch:
        params.extend(["--sketch", sketch])
    if aspect_ratio:
        params.extend(["--aspect-ratio", aspect_ratio])
    if resolution:
        params.extend(["--resolution", resolution])
    if duration is not None:
        params.extend(["--duration", str(duration)])
    if prompt:
        params.extend(["--prompt", prompt])
    if voice_id:
        params.extend(["--voice-id", voice_id])
    if voice_type:
        params.extend(["--voice-type", voice_type])
    if target_language:
        params.extend(["--target-language", target_language])
    _append_extra(params, extra_params)
    return params


def generate_workflow(name: str, output_path: str, timeout: int = 660, **kwargs) -> dict:
    """Run a Higgsfield post-production workflow or prompt-less create model.

    Covers reframe, draw_to_video, dubbing, voice_change (via `generate workflow`) and
    image_decompose, kling3_0_motion_control (via `generate create`). Local media paths
    are auto-uploaded by the CLI. Flag names use the CLI's hyphenated form.
    """
    verb = WORKFLOWS.get(name, {}).get("cmd", "workflow")
    # duration is a cost-estimation input for these workflows; the run derives duration
    # from the input media and rejects the param (e.g. reframe). Output duration for
    # workflows that support it (draw_to_video) can still be forced via --extra.
    kwargs.pop("duration", None)
    cmd = [
        "higgsfield", "generate", verb, name,
        "--wait",
        "--wait-timeout", "10m",
        "--wait-interval", "5s",
        "--json",
    ] + _workflow_params(**kwargs)
    return _submit_and_download(cmd, output_path, timeout=timeout, fallback_model=name)


def workflow_cost(name: str, **kwargs) -> dict:
    """Estimate a workflow's cost via `generate cost <name>`. Requires the same media
    and required params as the run itself, so pass the same kwargs as generate_workflow."""
    cmd = ["higgsfield", "generate", "cost", name, "--json"] + _workflow_params(**kwargs)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            acct = get_account_status()
            data["credits_remaining"] = acct.get("credits", "unknown")
            return data
    except Exception as e:
        return {"error": str(e)}
    return {"error": f"Cost check failed: {result.stderr.strip()[:200]}"}


def generate_pollinations(
    prompt: str,
    output_path: str,
    width: int = 768,
    height: int = 768,
    seed: int | None = None,
    enhance: bool = False,
) -> dict:
    # Enforce the 768 ceiling proportionally: clamping each side alone turned
    # a 1280x720 request into 768x720 (audit F33, 2026-09-06).
    longest = max(width, height)
    if longest > 768:
        factor = 768 / longest
        width = max(1, round(width * factor))
        height = max(1, round(height * factor))

    encoded_prompt = urllib.parse.quote(prompt)
    params = {"width": width, "height": height, "nologo": "true"}
    if seed is not None:
        params["seed"] = seed
    if enhance:
        params["enhance"] = "true"

    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?{query}"

    for attempt in range(3):
        try:
            with httpx.Client(timeout=90, follow_redirects=True) as client:
                resp = client.get(url)

            if resp.status_code == 200:
                # Decode before believing: a 200 with an HTML error page was
                # saved as the picture (audit F33, 2026-09-06).
                try:
                    from io import BytesIO

                    from PIL import Image as _PILImage
                    with _PILImage.open(BytesIO(resp.content)) as probe:
                        probe.verify()
                    decodable = True
                except Exception:
                    decodable = False
                if decodable:
                    out = Path(output_path)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(resp.content)
                    return {"success": True, "path": str(output_path), "model": "sana (Pollinations)", "error": ""}
                if attempt < 2:
                    time.sleep(3)
                    continue
                return {"success": False, "path": "",
                        "error": f"Pollinations returned a non-image body "
                                 f"({resp.headers.get('content-type', '?')}, {len(resp.content)} bytes)"}

            if resp.status_code == 429 and attempt < 2:
                print(f"Rate limited. Waiting 16s... (attempt {attempt + 1}/3)", file=sys.stderr)
                time.sleep(16)
                continue

            return {"success": False, "path": "", "error": f"Pollinations error {resp.status_code}: {resp.text[:300]}"}

        except httpx.TimeoutException:
            if attempt < 2:
                time.sleep(5)
                continue
            return {"success": False, "path": "", "error": "Pollinations timed out (90s)"}
        except Exception as e:
            return {"success": False, "path": "", "error": str(e)}

    return {"success": False, "path": "", "error": "All retries exhausted"}


def main():
    parser = argparse.ArgumentParser(description="Generate images and videos (Higgsfield primary, Pollinations fallback for images)")
    parser.add_argument("prompt", nargs="?", default="", help="Text prompt for generation")
    parser.add_argument("-o", "--output", default=None, help="Output file path")
    parser.add_argument("-m", "--model", default=None, help="Model name or alias")
    # 'auto', '1:2' and '2:1' are per model values (gpt_image_2, grok_image,
    # flux_3_video ...) that the fixed-size table below does not list; the
    # model's own schema is what refuses a ratio it does not take.
    parser.add_argument("-a", "--aspect-ratio", default=None,
                        choices=sorted({*ASPECT_RATIOS, "auto", "1:2", "2:1"}),
                        help="Aspect ratio (includes per-model values like 'auto' and '2:1'; default: the model's own)")
    parser.add_argument("-r", "--ref-image", default=None, help="Reference image path for image-to-image or image-to-video")
    parser.add_argument("--resolution", default=None, choices=["1k", "2k", "4k"],
                        help="Image resolution, where the model has one (default: the model's own, 1k on most)")
    parser.add_argument("--backend", default="auto", choices=["higgsfield", "pollinations", "auto"], help="Backend (default: auto, tries Higgsfield then Pollinations)")
    parser.add_argument("--video", action="store_true", help="Generate video instead of image")
    parser.add_argument("--threed", "--3d", action="store_true", dest="threed", help="Generate a 3D asset (mesh) instead of an image")
    parser.add_argument("--audio", action="store_true", help="Generate audio (music or speech) instead of an image")
    parser.add_argument("--duration", type=int, default=None, help="Duration in seconds (video/music; model-dependent)")
    parser.add_argument("--list-models", action="store_true", help="List available models and exit")
    parser.add_argument("--cost", action="store_true", help="Estimate credits cost without generating")
    parser.add_argument("--balance", action="store_true", help="Show account credits balance and exit")
    parser.add_argument("--model-params", action="store_true", help="Print MODEL_PARAMS as JSON and exit")
    parser.add_argument("--menu", action="store_true",
                        help="Print the Mini App's image and video menu (names, prices, settings) as JSON and exit")
    parser.add_argument("--extra", default=None, help="JSON string of extra model params (e.g. '{\"quality\":\"high\"}')")
    parser.add_argument("--user", default=None, help="User ID for per-user generation tracking")
    parser.add_argument("-W", "--width", type=int, default=768, help="Width for Pollinations (max 768)")
    parser.add_argument("-H", "--height", type=int, default=768, help="Height for Pollinations (max 768)")
    parser.add_argument("-s", "--seed", type=int, default=None, help="Seed for Pollinations reproducibility")
    parser.add_argument("--enhance", action="store_true", help="Let Pollinations enhance the prompt")
    # --- Workflows (post-production: reframe, dubbing, voice_change, draw_to_video, motion control, decompose) ---
    parser.add_argument("--workflow", default=None, choices=sorted(WORKFLOWS.keys()), help="Run a post-production workflow instead of a create job")
    parser.add_argument("--video-input", default=None, dest="video_input", help="Source video for a workflow (reframe/dubbing/voice_change/draw_to_video/motion control)")
    parser.add_argument("--sketch", default=None, help="Sketch/drawing frame for the draw_to_video workflow")
    parser.add_argument("--target-language", default=None, dest="target_language", help="Target language code for the dubbing workflow (e.g. spa, fra, deu)")
    # --- Video keyframing + motion reference (generate create, video models) ---
    parser.add_argument("--start-image", default=None, dest="start_image", help="First-frame keyframe image (video create)")
    parser.add_argument("--end-image", default=None, dest="end_image", help="Last-frame keyframe image (video create)")
    parser.add_argument("--video-references", default=None, dest="video_references", help="Motion/style source clip for a video model")
    # --- Voiced text-to-speech (seed_audio) ---
    parser.add_argument("--voice-id", default=None, dest="voice_id", help="Voice id for voiced speech (--audio -m speech) or voice_change. See --list-voices")
    parser.add_argument("--voice-type", default=None, dest="voice_type", choices=["preset", "element"], help="Voice type: preset (built-in) or element (cloned)")
    parser.add_argument("--pitch", type=int, default=None, help="Voice pitch rate for speech (seed_audio pitch_rate)")
    parser.add_argument("--speed", type=int, default=None, help="Voice speech rate for speech (seed_audio speech_rate)")
    parser.add_argument("--list-voices", action="store_true", help="List available text-to-speech voices and exit")

    args = parser.parse_args()

    if args.balance:
        acct = get_account_status()
        if acct:
            print(json.dumps({"credits": acct.get("credits", "unknown"), "plan": acct.get("subscription_plan_type", "unknown"), "email": acct.get("email", "unknown")}, indent=2))
        else:
            print(json.dumps({"error": "Could not fetch account status"}, indent=2))
        return

    if args.model_params:
        print(json.dumps({
            "model_params": MODEL_PARAMS,
            "video_durations": VIDEO_DURATIONS,
            "image_aliases": MODEL_ALIASES,
            "video_aliases": VIDEO_MODEL_ALIASES,
            "threed_aliases": THREED_MODEL_ALIASES,
            "audio_aliases": AUDIO_MODEL_ALIASES,
        }, indent=2))
        return

    if args.menu:
        print(json.dumps(menu(), indent=2))
        return

    if args.list_models:
        print("Image aliases:")
        for alias, model in sorted(MODEL_ALIASES.items()):
            print(f"  {alias:16s} -> {model}")
        print("\nVideo aliases:")
        for alias, model in sorted(VIDEO_MODEL_ALIASES.items()):
            print(f"  {alias:16s} -> {model}")
        print("\n3D aliases (use --threed):")
        for alias, model in sorted(THREED_MODEL_ALIASES.items()):
            print(f"  {alias:16s} -> {model}")
        print("\nAudio aliases (use --audio):")
        for alias, model in sorted(AUDIO_MODEL_ALIASES.items()):
            print(f"  {alias:16s} -> {model}")
        print("\nRun 'higgsfield model list' for the full model catalog.")
        return

    if args.list_voices:
        voices = list_voices()
        if voices:
            print("Text-to-speech voices (use --voice-id <id> --voice-type <type> with --audio -m speech):")
            for v in voices:
                print(f"  {v.get('id', ''):38s}  {v.get('name', ''):16s}  {v.get('voice_type', '')}")
        else:
            print("No voices returned (check auth).")
        return

    extra_params = {}
    if args.extra:
        try:
            extra_params = json.loads(args.extra)
        except json.JSONDecodeError:
            print(json.dumps({"error": "Invalid JSON in --extra"}), file=sys.stderr)
            sys.exit(1)

    if args.workflow:
        wf = WORKFLOWS[args.workflow]
        wf_kind = "video" if wf["media"] == "video" else "image"
        if args.output is None:
            args.output = f"/tmp/generated_workflow{wf['out']}"
        # Workflow resolution (480p/720p/1080p) travels via --extra, not the image-style
        # --resolution flag (1k/2k/4k); pull it out so it reaches the CLI correctly.
        wf_res = None
        if extra_params:
            wf_res = extra_params.pop("resolution", None)
        wf_kwargs = dict(
            video=args.video_input,
            image_refs=args.ref_image,
            sketch=args.sketch,
            aspect_ratio=args.aspect_ratio,
            resolution=wf_res,
            duration=args.duration,
            prompt=args.prompt or None,
            voice_id=args.voice_id,
            voice_type=args.voice_type,
            target_language=args.target_language,
            extra_params=extra_params or None,
        )
        if args.cost:
            print(json.dumps(workflow_cost(args.workflow, **wf_kwargs), indent=2))
            return
        print(f"Running workflow {args.workflow}...", file=sys.stderr)
        result = generate_workflow(args.workflow, args.output, **wf_kwargs)
        print(json.dumps(result, indent=2))
        if result["success"]:
            _save_last_generation(args.output, wf_kind, args.workflow, args.prompt or args.workflow, user_id=args.user)
        else:
            sys.exit(1)
        return

    # Resolve the generation kind (image is the default). video > 3d > audio priority.
    if args.video:
        kind = "video"
    elif args.threed:
        kind = "3d"
    elif args.audio:
        kind = "audio"
    else:
        kind = "image"

    if args.output is None:
        args.output = {
            "video": "/tmp/generated_video.mp4",
            "3d": "/tmp/generated_asset.glb",
            "audio": "/tmp/generated_audio.mp3",
            "image": "/tmp/generated_image.jpg",
        }[kind]

    if args.model is None:
        args.model = {
            "video": DEFAULT_VIDEO_MODEL,
            "3d": DEFAULT_3D_MODEL,
            "audio": DEFAULT_AUDIO_MODEL,
            "image": DEFAULT_MODEL,
        }[kind]

    # No -a means the model's own ratio. 1:1 and 16:9 used to be filled in here
    # and then withheld as if every model defaulted to them (see _aspect_args),
    # so leaving it unset sends the same as before and a chosen ratio is sent.

    # Music (sonilo_music) requires a duration; default to 10s if the user did not set one.
    if kind == "audio" and resolve_model(args.model, "audio") == "sonilo_music" and args.duration is None:
        args.duration = 10

    if args.cost:
        if not args.prompt:
            print(json.dumps({"error": "Prompt required for cost estimation"}, indent=2))
            sys.exit(1)
        if args.backend == "pollinations" and kind == "image":
            print(json.dumps({"credits": 0, "credits_exact": 0, "credits_remaining": "N/A (free tier)", "note": "Pollinations is free"}, indent=2))
            return
        cost = estimate_cost(
            args.model, args.prompt,
            aspect_ratio=args.aspect_ratio if kind in ("image", "video") else None,
            resolution=args.resolution if kind == "image" else None,
            duration=args.duration, kind=kind, extra_params=extra_params or None,
            # the same media the create call will send
            ref_image=args.ref_image, start_image=args.start_image,
            end_image=args.end_image, video_references=args.video_references,
        )
        print(json.dumps(cost, indent=2))
        return

    if not args.prompt:
        parser.error("prompt is required for generation")

    if kind == "video":
        print(f"Generating video with {args.model}...", file=sys.stderr)
        result = generate_video(args.prompt, args.output, model=args.model, aspect_ratio=args.aspect_ratio,
                                duration=args.duration, ref_image=args.ref_image, extra_params=extra_params or None,
                                start_image=args.start_image, end_image=args.end_image,
                                video_references=args.video_references)
    elif kind in ("3d", "audio"):
        resolved = resolve_model(args.model, kind)
        label = "3D asset" if kind == "3d" else "audio"
        print(f"Generating {label} with {resolved}...", file=sys.stderr)
        job_duration = args.duration if resolved == "sonilo_music" else None
        job_extra = dict(extra_params)
        if resolved == "seed_audio":
            # Voiced speech: fold the voice controls into seed_audio's params.
            for key, val in (("voice_id", args.voice_id), ("voice_type", args.voice_type),
                             ("pitch_rate", args.pitch), ("speech_rate", args.speed)):
                if val is not None and key not in job_extra:
                    job_extra[key] = val
        result = generate_job(args.prompt, args.output, resolved,
                              duration=job_duration, ref_media=args.ref_image, extra_params=job_extra or None)
    else:
        print(f"Generating image with {args.backend}...", file=sys.stderr)
        if args.backend == "pollinations":
            w = min(args.width, 768) if args.width != 768 else ASPECT_RATIOS.get(args.aspect_ratio, (768, 768))[0]
            h = min(args.height, 768) if args.height != 768 else ASPECT_RATIOS.get(args.aspect_ratio, (768, 768))[1]
            result = generate_pollinations(args.prompt, args.output, width=min(w, 768), height=min(h, 768),
                                           seed=args.seed, enhance=args.enhance)
        elif args.backend == "auto":
            result = generate_higgsfield(args.prompt, args.output, model=args.model, aspect_ratio=args.aspect_ratio,
                                         resolution=args.resolution, ref_image=args.ref_image, extra_params=extra_params or None)
            if not result["success"]:
                print(f"Higgsfield failed ({result['error']}). Falling back to Pollinations...", file=sys.stderr)
                w, h = ASPECT_RATIOS.get(args.aspect_ratio, (768, 768))
                result = generate_pollinations(args.prompt, args.output, width=min(w, 768), height=min(h, 768),
                                               seed=args.seed, enhance=args.enhance)
        else:
            result = generate_higgsfield(args.prompt, args.output, model=args.model, aspect_ratio=args.aspect_ratio,
                                         resolution=args.resolution, ref_image=args.ref_image, extra_params=extra_params or None)

    print(json.dumps(result, indent=2))
    if result["success"]:
        _save_last_generation(args.output, kind, args.model, args.prompt, user_id=args.user)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

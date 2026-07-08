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
    "soul-cast": "soul_cast",
    "marketing": "marketing_studio_video",
    # --- new video models from the live catalog ---
    "seedance-mini": "seedance_2_0_mini",
    "kling-turbo": "kling3_0_turbo",
    "gemini": "gemini_omni",
    "cinematic3.5": "cinematic_studio_video_3_5",
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


def resolve_model(model: str, kind: str = "image") -> str:
    """Resolve a friendly alias to a Higgsfield job_set_type for the given kind."""
    return KIND_ALIASES.get(kind, MODEL_ALIASES).get(model, model)


VIDEO_DURATIONS = {
    "kling3_0": {"type": "slider", "default": 5, "min": 3, "max": 15},
    "kling2_6": {"type": "preset", "default": 5, "options": [5, 10]},
    "veo3": None,
    "veo3_1": {"type": "preset", "default": 8, "options": [4, 6, 8]},
    "veo3_1_lite": {"type": "preset", "default": 8, "options": [4, 6, 8]},
    "seedance_2_0": {"type": "slider", "default": 5, "min": 5, "max": 30},
    "seedance1_5": {"type": "preset", "default": 4, "options": [4, 8, 12]},
    "minimax_hailuo": {"type": "preset", "default": 6, "options": [6, 10]},
    "wan2_7": {"type": "slider", "default": 5, "min": 3, "max": 15},
    "wan2_6": {"type": "preset", "default": 5, "options": [5, 10, 15]},
    "grok_video": {"type": "slider", "default": 5, "min": 1, "max": 15},
    "soul_cast": None,
    "cinematic_studio_3_0": {"type": "slider", "default": 5, "min": 5, "max": 20},
    "cinematic_studio_video": {"type": "preset", "default": 5, "options": [5, 10]},
    "cinematic_studio_video_v2": {"type": "slider", "default": 5, "min": 3, "max": 10},
    "marketing_studio_video": {"type": "slider", "default": 15, "min": 5, "max": 60},
    # --- new video/audio models ---
    "seedance_2_0_mini": {"type": "slider", "default": 5, "min": 5, "max": 30},
    "kling3_0_turbo": {"type": "slider", "default": 5, "min": 3, "max": 15},
    "gemini_omni": {"type": "preset", "default": 8, "options": [4, 6, 8]},
    "cinematic_studio_video_3_5": {"type": "slider", "default": 15, "min": 5, "max": 20},
    "sonilo_music": {"type": "slider", "default": 10, "min": 5, "max": 60},
}

MODEL_PARAMS = {
    "flux_2": {"model": {"options": ["pro", "flex", "max"], "default": "pro"}},
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
    "veo3": {"model": {"options": ["veo-3-preview", "veo-3-fast"], "default": "veo-3-fast"}},
    "veo3_1": {
        "model": {"options": ["veo-3-1-preview", "veo-3-1-fast"], "default": "veo-3-1-fast"},
        "quality": {"options": ["basic", "high", "ultra"], "default": "basic"},
    },
    "veo3_1_lite": {"generate_audio": {"type": "bool", "default": False}},
    "seedance_2_0": {
        "mode": {"options": ["std", "fast"], "default": "std"},
        "genre": {"options": ["auto", "action", "horror", "comedy", "noir", "drama", "epic"], "default": "auto"},
        "resolution": {"options": ["480p", "720p", "1080p"], "default": "720p"},
    },
    "seedance1_5": {"resolution": {"options": ["480p", "720p", "1080p"], "default": "720p"}},
    "minimax_hailuo": {
        "model": {"options": ["minimax", "minimax-fast", "minimax-2.3", "minimax-2.3-fast"], "default": "minimax-2.3"},
        "resolution": {"options": ["512", "768", "1080"], "default": "768"},
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
        "bitrate_mode": {"options": ["standard", "high"], "default": "high"},
        "generate_audio": {"type": "bool", "default": True},
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


def estimate_cost(model: str, prompt: str = "test", aspect_ratio: str | None = None,
                   resolution: str | None = None, duration: int | None = None,
                   kind: str = "image", extra_params: dict | None = None) -> dict:
    resolved = resolve_model(model, kind)
    cmd = ["higgsfield", "generate", "cost", resolved, "--prompt", prompt, "--json"]
    if aspect_ratio:
        cmd.extend(["--aspect_ratio", aspect_ratio])
    if resolution and resolution != "2k":
        cmd.extend(["--resolution", resolution])
    if duration is not None and (kind == "video" or resolved == "sonilo_music"):
        cmd.extend(["--duration", str(duration)])
    if extra_params:
        for k, v in extra_params.items():
            if v is not None and v != "":
                if isinstance(v, bool):
                    cmd.extend([f"--{k}", str(v).lower()])
                else:
                    cmd.extend([f"--{k}", str(v)])
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
    aspect_ratio: str = "1:1",
    resolution: str = "2k",
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

    if aspect_ratio and aspect_ratio != "1:1":
        cmd.extend(["--aspect_ratio", aspect_ratio])

    if resolution and resolution != "2k":
        cmd.extend(["--resolution", resolution])

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
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
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
        return {"success": False, "path": "", "error": "Generation timed out (180s)"}
    except json.JSONDecodeError:
        return {"success": False, "path": "", "error": f"Invalid JSON from CLI: {result.stdout[:300]}"}
    except Exception as e:
        return {"success": False, "path": "", "error": str(e)}


def generate_video(
    prompt: str,
    output_path: str,
    model: str = DEFAULT_VIDEO_MODEL,
    aspect_ratio: str = "16:9",
    duration: int | None = None,
    ref_image: str | None = None,
    extra_params: dict | None = None,
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

    if aspect_ratio and aspect_ratio != "16:9":
        cmd.extend(["--aspect_ratio", aspect_ratio])

    dur_info = VIDEO_DURATIONS.get(resolved)
    if duration and dur_info:
        cmd.extend(["--duration", str(duration)])

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


def generate_job(
    prompt: str,
    output_path: str,
    resolved_model: str,
    duration: int | None = None,
    ref_media: str | None = None,
    extra_params: dict | None = None,
    timeout: int = 660,
) -> dict:
    """Generic create -> wait -> download for kinds beyond image/video (3D meshes, audio).

    Note: image (generate_higgsfield) and video (generate_video) keep their own
    proven functions to avoid regressions on the live pipeline; this generic path
    serves the new 3D/audio kinds. A later cleanup could fold all three into this one.
    """
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

    if extra_params:
        for k, v in extra_params.items():
            if v is not None and v != "":
                if isinstance(v, bool):
                    cmd.extend([f"--{k}", str(v).lower()])
                else:
                    cmd.extend([f"--{k}", str(v)])

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

        url = data.get("result_url", "")
        if not url:
            return {"success": False, "path": "", "error": f"No result URL in response: {result.stdout[:300]}"}

        with httpx.Client(timeout=180, follow_redirects=True) as client:
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
            "model": data.get("display_name", resolved_model),
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


def generate_pollinations(
    prompt: str,
    output_path: str,
    width: int = 768,
    height: int = 768,
    seed: int | None = None,
    enhance: bool = False,
) -> dict:
    width = min(width, 768)
    height = min(height, 768)

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
                content_type = resp.headers.get("content-type", "")
                if "image" in content_type or len(resp.content) > 1000:
                    out = Path(output_path)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(resp.content)
                    return {"success": True, "path": str(output_path), "model": "sana (Pollinations)", "error": ""}

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
    parser.add_argument("-a", "--aspect-ratio", default=None, choices=list(ASPECT_RATIOS.keys()), help="Aspect ratio")
    parser.add_argument("-r", "--ref-image", default=None, help="Reference image path for image-to-image or image-to-video")
    parser.add_argument("--resolution", default="2k", choices=["1k", "2k", "4k"], help="Resolution (default: 2k)")
    parser.add_argument("--backend", default="auto", choices=["higgsfield", "pollinations", "auto"], help="Backend (default: auto, tries Higgsfield then Pollinations)")
    parser.add_argument("--video", action="store_true", help="Generate video instead of image")
    parser.add_argument("--threed", "--3d", action="store_true", dest="threed", help="Generate a 3D asset (mesh) instead of an image")
    parser.add_argument("--audio", action="store_true", help="Generate audio (music or speech) instead of an image")
    parser.add_argument("--duration", type=int, default=None, help="Duration in seconds (video/music; model-dependent)")
    parser.add_argument("--list-models", action="store_true", help="List available models and exit")
    parser.add_argument("--cost", action="store_true", help="Estimate credits cost without generating")
    parser.add_argument("--balance", action="store_true", help="Show account credits balance and exit")
    parser.add_argument("--model-params", action="store_true", help="Print MODEL_PARAMS as JSON and exit")
    parser.add_argument("--extra", default=None, help="JSON string of extra model params (e.g. '{\"quality\":\"high\"}')")
    parser.add_argument("--user", default=None, help="User ID for per-user generation tracking")
    parser.add_argument("-W", "--width", type=int, default=768, help="Width for Pollinations (max 768)")
    parser.add_argument("-H", "--height", type=int, default=768, help="Height for Pollinations (max 768)")
    parser.add_argument("-s", "--seed", type=int, default=None, help="Seed for Pollinations reproducibility")
    parser.add_argument("--enhance", action="store_true", help="Let Pollinations enhance the prompt")

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

    extra_params = {}
    if args.extra:
        try:
            extra_params = json.loads(args.extra)
        except json.JSONDecodeError:
            print(json.dumps({"error": "Invalid JSON in --extra"}), file=sys.stderr)
            sys.exit(1)

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

    if args.aspect_ratio is None and kind in ("image", "video"):
        args.aspect_ratio = "16:9" if kind == "video" else "1:1"

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
        )
        print(json.dumps(cost, indent=2))
        return

    if not args.prompt:
        parser.error("prompt is required for generation")

    if kind == "video":
        print(f"Generating video with {args.model}...", file=sys.stderr)
        result = generate_video(args.prompt, args.output, model=args.model, aspect_ratio=args.aspect_ratio,
                                duration=args.duration, ref_image=args.ref_image, extra_params=extra_params or None)
    elif kind in ("3d", "audio"):
        resolved = resolve_model(args.model, kind)
        label = "3D asset" if kind == "3d" else "audio"
        print(f"Generating {label} with {resolved}...", file=sys.stderr)
        job_duration = args.duration if resolved == "sonilo_music" else None
        result = generate_job(args.prompt, args.output, resolved,
                              duration=job_duration, ref_media=args.ref_image, extra_params=extra_params or None)
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

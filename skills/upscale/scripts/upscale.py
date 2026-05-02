#!/usr/bin/env python3
"""Image upscaling using Real-ESRGAN.

Uses the Python API directly. The `python -m realesrgan` CLI does not exist
in realesrgan 0.3.0 (no __main__ module).

Also compatibility-patches torchvision: basicsr imports
`torchvision.transforms.functional_tensor`, which was removed in torchvision
>=0.17. We alias it to `torchvision.transforms.functional` before any basicsr
import, so no manual site-packages edits are required.
"""
import argparse
import os
import sys
import urllib.request
from pathlib import Path


def _patch_torchvision():
    """Alias torchvision.transforms.functional_tensor -> .functional if missing.

    basicsr expects the legacy module. Safe no-op when the legacy module
    still exists (older torchvision), since we only install the alias when
    the import would otherwise fail.
    """
    try:
        import torchvision.transforms.functional_tensor  # noqa: F401
    except ModuleNotFoundError:
        import torchvision.transforms.functional as _tvf
        sys.modules["torchvision.transforms.functional_tensor"] = _tvf


_patch_torchvision()

# Heavy imports only after the patch is in place.
import cv2  # noqa: E402
import torch  # noqa: E402
from basicsr.archs.rrdbnet_arch import RRDBNet  # noqa: E402
from realesrgan import RealESRGANer  # noqa: E402

MODEL_URLS = {
    2: (
        "RealESRGAN_x2plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
    ),
    4: (
        "RealESRGAN_x4plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
    ),
}

GFPGAN_URL = (
    "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.3.pth"
)


def _cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    d = base / "realesrgan"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {dest.name} ...", file=sys.stderr)
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)
    return dest


def _get_model_path(scale: int) -> Path:
    if scale not in MODEL_URLS:
        raise ValueError(f"Unsupported scale {scale}; use 2 or 4.")
    filename, url = MODEL_URLS[scale]
    return _download(url, _cache_dir() / filename)


def _select_device() -> torch.device:
    """Pick the best usable torch device.

    CUDA is skipped when the local GPU's compute capability isn't in the
    PyTorch build's supported set (e.g. GTX 970 sm_52 on modern wheels).
    Everything else falls back to CPU cleanly.
    """
    if not torch.cuda.is_available():
        return torch.device("cpu")
    try:
        major, minor = torch.cuda.get_device_capability(0)
    except Exception:
        return torch.device("cpu")
    supported = set()
    try:
        for arch in torch.cuda.get_arch_list():
            # arch strings look like "sm_80" / "compute_86"
            num = "".join(c for c in arch if c.isdigit())
            if len(num) >= 2:
                supported.add((int(num[:-1]), int(num[-1])))
    except Exception:
        pass
    if supported and (major, minor) not in supported:
        return torch.device("cpu")
    return torch.device("cuda")


def _build_upsampler(scale: int, tile: int) -> RealESRGANer:
    model = RRDBNet(
        num_in_ch=3,
        num_out_ch=3,
        num_feat=64,
        num_block=23,
        num_grow_ch=32,
        scale=scale,
    )
    device = _select_device()
    return RealESRGANer(
        scale=scale,
        model_path=str(_get_model_path(scale)),
        model=model,
        tile=tile,
        tile_pad=10,
        pre_pad=0,
        half=False,  # fp16 is CUDA-only; keep off so CPU path is correct
        device=device,
    )


def _build_face_enhancer(upsampler, outscale: int):
    from gfpgan import GFPGANer
    model_path = _download(GFPGAN_URL, _cache_dir() / "GFPGANv1.3.pth")
    return GFPGANer(
        model_path=str(model_path),
        upscale=outscale,
        arch="clean",
        channel_multiplier=2,
        bg_upsampler=upsampler,
    )


def upscale_image(
    input_path: str,
    output_path: str,
    scale: int = 4,
    face_enhance: bool = False,
    tile: int = 256,
) -> None:
    img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {input_path}")

    print(f"Upscaling {input_path} by {scale}x ...", file=sys.stderr)
    upsampler = _build_upsampler(scale, tile)

    if face_enhance:
        enhancer = _build_face_enhancer(upsampler, scale)
        _, _, output = enhancer.enhance(
            img, has_aligned=False, only_center_face=False, paste_back=True
        )
    else:
        output, _ = upsampler.enhance(img, outscale=scale)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(output_path, output)
    print(f"Saved: {output_path}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-ESRGAN image upscaler")
    parser.add_argument("input", help="Input image path")
    parser.add_argument("output", help="Output image path")
    parser.add_argument(
        "--scale", "-s", type=int, default=4, choices=[2, 4],
        help="Upscale factor (default: 4)",
    )
    parser.add_argument(
        "--face", "-f", action="store_true",
        help="Enable GFPGAN face enhancement",
    )
    parser.add_argument(
        "--tile", type=int, default=256,
        help="Tile size for low-memory systems; 0 disables tiling (default: 256)",
    )
    args = parser.parse_args()
    upscale_image(args.input, args.output, args.scale, args.face, args.tile)
    return 0


if __name__ == "__main__":
    sys.exit(main())

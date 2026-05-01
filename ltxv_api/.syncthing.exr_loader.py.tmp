"""ZIP-of-EXR-frames loader for the V2V-HDR endpoint.

The HDR endpoint returns ``exr_frames_url``, a presigned URL to a ZIP archive
containing one EXR file per frame in scene-linear float. This module:

1. Downloads the ZIP to a temp dir (via the caller's :class:`LTXVClient`).
2. Extracts every ``.exr`` member.
3. Loads each EXR with OpenCV's ``IMREAD_UNCHANGED | IMREAD_ANYDEPTH`` so
   the original float precision is preserved (no clamp -- HDR pixels can
   legitimately exceed 1.0).
4. BGR -> RGB, stacks to ``[N, H, W, 3]`` float32, returns a torch tensor.

OpenCV's EXR codec is not enabled by default; we set
``OPENCV_IO_ENABLE_OPENEXR=1`` at module import. On builds that don't ship
EXR support compiled in (rare but possible on stripped wheels) the ``cv2``
import will succeed but ``cv2.imread`` returns ``None`` for EXRs -- we
detect that explicitly and raise with a clear remediation.

Color space caveat: Lightricks documents the EXR as "scene-linear 16-bit"
without specifying primaries. Source-code inspection of their local
distillation (Lightricks/ComfyUI-LTXVideo's ``hdr.py``) shows the inverse
LogC3 transform but no colour matrix -- the data inherits LogC3's canonical
companion primaries (linear ARRI Wide Gamut 3). Treat as linear AWG3 and
apply an AWG3 -> ACEScg / Rec.709 IDT downstream. See README's HDR section.
"""
from __future__ import annotations

import logging
import os
import zipfile
from pathlib import Path
from typing import List, Tuple

# OpenCV's EXR codec is gated on this env var. Set it before importing cv2 so
# the codec is available the first time the module loads.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import numpy as np  # noqa: E402

log = logging.getLogger("comfyui-ltxv-api.exr-loader")


def _torch():
    import torch  # noqa: WPS433
    return torch


def _cv2():
    try:
        import cv2  # noqa: WPS433
    except ImportError as exc:
        raise RuntimeError(
            "opencv-python is required for EXR decoding. "
            "Install with: pip install 'opencv-python>=4.8'"
        ) from exc
    return cv2


def extract_exr_paths(zip_path: Path, dest_dir: Path) -> List[Path]:
    """Extract every ``.exr`` member of *zip_path* into *dest_dir*.

    Returns the list of extracted EXR paths, sorted by member name (which
    matches the LTX server's frame ordering -- members are zero-padded
    numeric like ``frame_00001.exr``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: List[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        members = sorted(
            (m for m in zf.namelist() if m.lower().endswith(".exr") and not m.endswith("/")),
            key=lambda n: n.lower(),
        )
        for member in members:
            # Defend against zip-slip; only emit the basename into dest_dir.
            safe = Path(member).name
            target = dest_dir / safe
            with zf.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted.append(target)
    return extracted


def load_exr_frame(path: Path) -> np.ndarray:
    """Decode a single EXR file to a ``[H, W, 3]`` float32 RGB array.

    No clamping -- the LTX HDR output is scene-linear and can exceed 1.0.
    OpenCV reads BGR; we swap to RGB. Single-channel EXRs are broadcast
    to RGB; 4-channel (RGBA) drops alpha.
    """
    cv2 = _cv2()
    arr = cv2.imread(str(path), cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
    if arr is None:
        raise RuntimeError(
            f"OpenCV failed to read EXR {path}. The OpenCV build may lack EXR "
            "support, or the file may be corrupt. Reinstall opencv-python (the "
            "default wheels include EXR support) and ensure "
            "OPENCV_IO_ENABLE_OPENEXR=1 is set in the environment."
        )
    arr = np.asarray(arr, dtype=np.float32, order="C")
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    elif arr.shape[-1] == 4:
        arr = arr[..., :3]
    elif arr.shape[-1] != 3:
        raise RuntimeError(
            f"unexpected EXR channel count {arr.shape[-1]} in {path}; "
            "expected 1, 3, or 4."
        )
    # OpenCV is BGR -> swap to RGB.
    return arr[..., ::-1].copy()


def load_exr_zip(zip_path: Path, dest_dir: Path) -> Tuple["torch.Tensor", List[Path]]:  # type: ignore[name-defined]
    """Convenience: extract a ZIP of EXR frames and stack them into a tensor.

    Returns ``(tensor, frame_paths)`` where ``tensor`` is shaped
    ``[N, H, W, 3]`` float32 (NHWC, ComfyUI IMAGE convention) and
    ``frame_paths`` is the on-disk list of extracted EXRs (so the caller
    can clean them up if desired).
    """
    torch = _torch()
    paths = extract_exr_paths(zip_path, dest_dir)
    if not paths:
        raise RuntimeError(f"ZIP {zip_path} contains no .exr members")

    frames = [load_exr_frame(p) for p in paths]
    if len({f.shape for f in frames}) > 1:
        # All frames must share H/W; LTX guarantees this server-side, but
        # surface a clear error if it ever drifts.
        shapes = [f.shape for f in frames]
        raise RuntimeError(f"EXR frames have inconsistent shapes: {shapes}")

    stack = np.stack(frames, axis=0)
    return torch.from_numpy(stack), paths

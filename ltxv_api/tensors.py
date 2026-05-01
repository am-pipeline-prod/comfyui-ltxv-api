"""Conversions between ComfyUI's IMAGE tensors and on-disk media files.

ComfyUI's IMAGE convention is a ``torch.Tensor`` shaped ``[B, H, W, C]`` with
floats in ``[0, 1]`` and ``C == 3`` (RGB). Single images are batches of size 1,
and video is represented as a multi-frame batch.

The LTX API accepts ``image_uri`` / ``video_uri`` as either an HTTPS URL or a
base64 data URI. v1 of this package uses base64 data URIs exclusively -- no
upload pre-flight required, no signed-URL plumbing -- which keeps the node
surface small at the cost of ~33% over-the-wire overhead. For very large
inputs that hit request-size limits, the node accepts a verbatim URL string
as an alternative to the IMAGE/VIDEO socket.

Color management is intentionally minimal here -- the API returns tonemapped
SDR MP4 (or scene-linear EXR via the HDR endpoint, handled separately by
``exr_loader``). Any colour-aware I/O should happen upstream of these nodes
(e.g. via am-pipe-media-io's OCIO-aware nodes) before the data reaches this
layer.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import requests

log = logging.getLogger("comfyui-ltxv-api.tensors")

DEFAULT_VIDEO_FPS = 24.0
PNG_EXT = ".png"
MP4_EXT = ".mp4"


def _torch():
    import torch  # noqa: WPS433 -- intentional lazy import
    return torch


def temp_path(suffix: str, *, prefix: str = "ltxv_") -> Path:
    """Allocate a temp file in ComfyUI's temp dir if available, else system tmp.

    The file is created (and immediately closed) so callers can write to it.
    """
    base: Optional[str] = None
    try:
        import folder_paths  # type: ignore[import-not-found]
        base = folder_paths.get_temp_directory()
    except Exception:  # noqa: BLE001 -- ComfyUI not in path is fine for unit tests
        base = None
    if base:
        os.makedirs(base, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=base)
    os.close(fd)
    return Path(path)


# ---------------------------------------------------------------------------
# IMAGE tensor -> PNG / data URI
# ---------------------------------------------------------------------------

def _tensor_frame_to_uint8(frame) -> np.ndarray:
    """Convert a single ``[H, W, C]`` float tensor in [0, 1] to uint8 numpy."""
    arr = frame.detach().cpu().numpy()
    arr = np.clip(arr, 0.0, 1.0)
    return (arr * 255.0 + 0.5).astype(np.uint8)


def image_tensor_to_png(image, *, frame_index: int = 0) -> Path:
    """Write the ``frame_index``-th image of a batch to a temp PNG.

    Returns the file path. The caller is responsible for deleting it (or
    relying on ComfyUI's temp-dir cleanup).
    """
    if image is None or image.shape[0] == 0:
        raise ValueError("image_tensor_to_png: empty IMAGE batch")
    from PIL import Image  # lazy

    frame = image[frame_index]
    arr = _tensor_frame_to_uint8(frame)
    if arr.ndim != 3 or arr.shape[2] not in (1, 3, 4):
        raise ValueError(f"unexpected image tensor shape: {tuple(frame.shape)}")
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)

    path = temp_path(PNG_EXT)
    Image.fromarray(arr).save(path, format="PNG", compress_level=3)
    return path


def image_tensor_to_data_uri(image, *, frame_index: int = 0) -> str:
    """Encode a single frame of an IMAGE batch as a ``data:image/png;base64,...`` URI.

    The frame is selected by ``frame_index`` (default 0 -- first frame). The
    PNG bytes are encoded in-memory; no temp file is touched. Useful for
    ``image_uri`` / ``last_frame_uri`` request fields.
    """
    if image is None or image.shape[0] == 0:
        raise ValueError("image_tensor_to_data_uri: empty IMAGE batch")
    from PIL import Image  # lazy

    frame = image[frame_index]
    arr = _tensor_frame_to_uint8(frame)
    if arr.ndim != 3 or arr.shape[2] not in (1, 3, 4):
        raise ValueError(f"unexpected image tensor shape: {tuple(frame.shape)}")
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)

    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", compress_level=3)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def png_bytes_to_image_tensor(data: bytes):
    """Decode PNG/JPG bytes to a ``[1, H, W, 3]`` float tensor."""
    from PIL import Image  # lazy
    torch = _torch()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


# ---------------------------------------------------------------------------
# IMAGE batch <-> MP4 (via imageio + imageio-ffmpeg)
# ---------------------------------------------------------------------------

def _require_imageio():
    try:
        import imageio.v3 as iio  # noqa: WPS433
    except ImportError as exc:
        raise RuntimeError(
            "imageio is required for video round-trips. "
            "Install with: pip install 'imageio[ffmpeg]>=2.25'"
        ) from exc
    return iio


def image_batch_to_mp4(image, *, fps: float = DEFAULT_VIDEO_FPS) -> Path:
    """Encode a multi-frame IMAGE batch to an MP4 in the temp dir.

    Uses libx264 yuv420p so the result is broadly compatible. Even/even
    dimensions are enforced -- libx264 rejects odd widths/heights at yuv420p.
    """
    if image is None or image.shape[0] == 0:
        raise ValueError("image_batch_to_mp4: empty IMAGE batch")
    iio = _require_imageio()

    torch = _torch()
    if not isinstance(image, torch.Tensor):
        raise TypeError(f"expected torch.Tensor, got {type(image)!r}")

    arr = image.detach().cpu().numpy()
    arr = np.clip(arr, 0.0, 1.0)
    arr = (arr * 255.0 + 0.5).astype(np.uint8)
    if arr.ndim == 3:  # safety: single-frame edge case
        arr = arr[None, ...]
    if arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)

    h, w = arr.shape[1:3]
    if w % 2:
        arr = arr[:, :, : w - 1, :]
    if h % 2:
        arr = arr[:, : h - 1, :, :]

    path = temp_path(MP4_EXT)
    iio.imwrite(
        path,
        arr,
        plugin="FFMPEG",
        codec="libx264",
        fps=fps,
        macro_block_size=1,
        pixelformat="yuv420p",
        output_params=["-movflags", "+faststart"],
    )
    return path


def mp4_probe(path: Path) -> Tuple[int, int, int, float]:
    """Cheap metadata-only probe: ``(width, height, frame_count, fps)``.

    Reads the container header via imageio's FFmpeg plugin without decoding
    the full pixel stream. Used by VIDEO-emitting nodes that don't need the
    IMAGE batch for their own output -- only the metadata sockets.

    Falls back to a full-decode shape probe if the metadata is incomplete
    on this container (rare, but happens for MP4s without proper duration
    headers).
    """
    iio = _require_imageio()
    width = height = frame_count = 0
    fps = DEFAULT_VIDEO_FPS
    try:
        meta = iio.immeta(path, plugin="FFMPEG")
        size = meta.get("size") or meta.get("source_size")
        if size and len(size) == 2:
            width, height = int(size[0]), int(size[1])
        fps = float(meta.get("fps", DEFAULT_VIDEO_FPS))
        nframes = meta.get("nframes")
        if isinstance(nframes, (int, float)) and nframes != float("inf"):
            frame_count = int(nframes)
        elif meta.get("duration") and fps:
            frame_count = int(round(float(meta["duration"]) * fps))
    except Exception:  # noqa: BLE001 -- fall through to full decode
        pass

    if width == 0 or height == 0 or frame_count == 0:
        # Header didn't carry full geometry; fall back to a full decode.
        tensor, fps = mp4_to_image_batch(path)
        frame_count = int(tensor.shape[0])
        height = int(tensor.shape[1])
        width = int(tensor.shape[2])

    return width, height, frame_count, float(fps)


def mp4_to_image_batch(path: Path) -> Tuple["torch.Tensor", float]:  # type: ignore[name-defined]
    """Decode an MP4 file to ``([T, H, W, 3] float tensor, fps)``."""
    iio = _require_imageio()
    torch = _torch()

    frames = iio.imread(path, plugin="FFMPEG", index=None)  # all frames
    if frames.ndim == 3:  # single-frame
        frames = frames[None, ...]
    if frames.shape[-1] == 4:
        frames = frames[..., :3]  # drop alpha if present
    arr = frames.astype(np.float32) / 255.0

    fps = DEFAULT_VIDEO_FPS
    try:
        meta = iio.immeta(path, plugin="FFMPEG")
        fps = float(meta.get("fps", DEFAULT_VIDEO_FPS))
    except Exception:  # noqa: BLE001
        pass

    return torch.from_numpy(arr), fps


def mp4_file_to_data_uri(path: Path) -> str:
    """Read an MP4 file and encode as ``data:video/mp4;base64,...``.

    The whole file is loaded into memory -- this is fine for the LTX API's
    typical input-clip sizes (a few MB to a few tens of MB) but unsuitable
    for very large inputs. For those, pass a public HTTPS URL instead.
    """
    blob = Path(path).read_bytes()
    encoded = base64.b64encode(blob).decode("ascii")
    return f"data:video/mp4;base64,{encoded}"


# ---------------------------------------------------------------------------
# Remote URL helpers
# ---------------------------------------------------------------------------

def download_url_to_path(url: str, suffix: str, *, timeout: float = 300.0) -> Path:
    """Download *url* to a temp file with the given *suffix* (e.g. ``.mp4``)."""
    path = temp_path(suffix)
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with open(path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
    return path


# ---------------------------------------------------------------------------
# Resolution / VIDEO-input helpers
# ---------------------------------------------------------------------------

def resolve_video_uri(
    *,
    video=None,
    video_url: str = "",
    fps_override: float = DEFAULT_VIDEO_FPS,
) -> Tuple[str, Optional[Path]]:
    """Resolve the inputs of a video-consuming node into a ``video_uri`` string.

    Resolution order (first non-empty wins):

    1. ``video_url`` -- explicit HTTPS URL string typed/wired by the artist.
       Used verbatim. Recommended for large inputs.
    2. ``video`` -- a ComfyUI ``VIDEO`` socket. When the VIDEO is backed by
       an on-disk MP4 (``VideoFromFile``), the original bytes are read
       straight off disk and base64-encoded -- preserves audio and avoids
       a re-encode. When it isn't (``VideoFromComponents``, partner API
       wrappers), components are extracted and re-encoded as MP4 via
       libx264 at the source's native fps; in that fallback path **the
       audio stream is dropped on upload** because libx264-only mux has
       no audio track. Use ``video_url`` if pristine audio matters.

    Returns ``(uri, temp_mp4_path)``. ``temp_mp4_path`` is the on-disk MP4
    we wrote (so the caller can clean up after the request); ``None`` when
    the artist provided an external URL or when we read straight from a
    file-backed VIDEO without writing anything new.
    """
    url = (video_url or "").strip()
    if url:
        return url, None

    if video is None:
        raise ValueError(
            "no video input -- wire either `video` (VIDEO) or "
            "`video_url` (STRING) on this node."
        )

    from . import video_type  # local import to avoid a hard dependency at module load

    # Fast path: file-backed VIDEO. Read the container bytes directly to
    # preserve audio and avoid a libx264 round trip.
    src_path = video_type.get_source_path(video)
    if src_path is not None:
        return mp4_file_to_data_uri(src_path), None

    # Component-derived VIDEO (no backing file). Re-encode the IMAGE
    # batch to MP4. AUDIO is lost in this branch -- documented in the
    # docstring; users who need audio-preserving uploads should use
    # video_url with a public HTTPS URL.
    components = video_type.get_components(video)
    if components is None:
        raise ValueError(
            "VIDEO input could not be decomposed into components; cannot upload."
        )
    images, _audio, src_fps = components
    fps = float(src_fps) if src_fps and src_fps > 0 else fps_override
    mp4 = image_batch_to_mp4(images, fps=fps)
    return mp4_file_to_data_uri(mp4), mp4

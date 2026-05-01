"""Bridge to ComfyUI's native ``VIDEO`` type.

ComfyUI's stock VIDEO socket is backed by ``comfy_api.latest`` (the
``InputImpl.VideoFromFile`` / ``InputImpl.VideoFromComponents`` classes plus
``Types.VideoComponents``). Custom nodes wire into this by constructing one
of those subclasses and emitting it on a ``RETURN_TYPES = ("VIDEO", ...)``
socket; consumers (``SaveVideo``, ``GetVideoComponents``, partner API nodes)
call the abstract ``VideoInput`` interface to lazy-decode or pull components.

This module is a thin defensive wrapper that degrades gracefully on older
ComfyUI versions without ``comfy_api.latest``:

* :func:`is_available` -- True when ``comfy_api.latest`` imports cleanly.
* :func:`make_video_from_file` -- wraps an MP4 path in ``VideoFromFile`` so
  downstream ``SaveVideo`` / ``GetVideoComponents`` consume it without a
  re-encode round trip.
* :func:`get_components` -- extract ``(images, audio, frame_rate)`` from an
  upstream VIDEO. Used when the API call needs IMAGE pixels (e.g. to encode
  an MP4 request body).
* :func:`get_source_path` -- best-effort access to the on-disk file behind
  a ``VideoFromFile``. When available, the request body can use the
  original bytes directly (preserves audio + avoids a libx264 re-encode);
  otherwise we fall back to encoding from components.

Modeled on ``am-pipe-media-io``'s ``_core.video_type`` so the same idiom
works across the studio's custom-node packages.
"""
from __future__ import annotations

import logging
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional, Tuple

log = logging.getLogger("comfyui-ltxv-api.video-type")


try:
    from comfy_api.latest import InputImpl as _InputImpl  # type: ignore[import-not-found]
    from comfy_api.latest import Types as _Types  # type: ignore[import-not-found]
    _COMFY_API_AVAILABLE = True
except Exception as _e:  # pragma: no cover -- old ComfyUI or import-time failure
    _InputImpl = None  # type: ignore[assignment]
    _Types = None  # type: ignore[assignment]
    _COMFY_API_AVAILABLE = False
    log.info(
        "[ltxv-api/video-type] comfy_api.latest not importable (%s); "
        "VIDEO sockets will emit None until ComfyUI is updated.",
        _e,
    )


def is_available() -> bool:
    """True when the native ComfyUI VIDEO type is reachable."""
    return _COMFY_API_AVAILABLE


def make_video_from_file(path: str) -> Optional[Any]:
    """Wrap *path* in a ``VideoFromFile`` for emission on a VIDEO socket.

    Returns ``None`` when ``comfy_api.latest`` isn't importable so the caller
    can omit the socket value gracefully. The file at *path* must persist
    for the lifetime of the workflow run -- ``VideoFromFile`` is lazy and
    reads from disk on demand.
    """
    if not _COMFY_API_AVAILABLE:
        return None
    try:
        return _InputImpl.VideoFromFile(path)
    except Exception as e:
        log.warning("[ltxv-api/video-type] VideoFromFile(%r) failed: %s", path, e)
        return None


def make_video_from_components(
    images,
    fps: float,
    *,
    audio: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> Optional[Any]:
    """Wrap an image-batch tensor + fps (+ optional audio) into a VIDEO.

    *images* must be a torch tensor shaped ``(N, H, W, C)`` in [0, 1] -- the
    ComfyUI IMAGE convention. *fps* is converted to :class:`fractions.Fraction`
    via ``Fraction(fps).limit_denominator(...)`` so common cinema rates
    (23.976, 29.97) round-trip cleanly through the VIDEO subsystem.
    """
    if not _COMFY_API_AVAILABLE:
        return None
    if fps is None or fps <= 0:
        log.warning("[ltxv-api/video-type] fps=%r invalid; emitting None VIDEO", fps)
        return None
    try:
        rate = Fraction(float(fps)).limit_denominator(1_000_000)
        components = _Types.VideoComponents(
            images=images,
            frame_rate=rate,
            audio=audio,
            metadata=metadata,
        )
        return _InputImpl.VideoFromComponents(components)
    except Exception as e:
        log.warning("[ltxv-api/video-type] VideoFromComponents failed: %s", e)
        return None


def get_components(video: Any) -> Optional[Tuple[Any, Optional[dict], float]]:
    """Extract ``(images, audio, frame_rate_float)`` from a VIDEO input.

    Mirrors the stock ``GetVideoComponents`` node. Returns ``None`` when
    extraction fails (caller should fall back to its STRING URL or warn).
    """
    if video is None:
        return None
    try:
        components = video.get_components()
    except Exception as e:
        log.warning("[ltxv-api/video-type] video.get_components() failed: %s", e)
        return None
    images = getattr(components, "images", None)
    audio = getattr(components, "audio", None)
    frame_rate = getattr(components, "frame_rate", None)
    try:
        fps = float(frame_rate) if frame_rate is not None else 0.0
    except Exception:
        fps = 0.0
    if images is None:
        log.warning("[ltxv-api/video-type] VIDEO components carried no images; ignoring")
        return None
    return images, audio, fps


def get_source_path(video: Any) -> Optional[Path]:
    """Best-effort: return the on-disk path backing a ``VideoFromFile``.

    Returns ``None`` for non-file VIDEO sources (``VideoFromComponents``,
    in-memory video objects from partner nodes). When non-None, the caller
    can read the original container bytes directly -- preserves audio and
    skips a libx264 re-encode round trip.

    The probe is defensive across ComfyUI versions: tries
    ``get_stream_source()`` first (current API), then falls back to
    common attribute names that have been used historically.
    """
    if video is None:
        return None

    # Method-style accessors first.
    for method_name in ("get_stream_source", "get_path", "source_path"):
        method = getattr(video, method_name, None)
        if callable(method):
            try:
                result = method()
            except Exception:
                continue
            if isinstance(result, (str, Path)):
                p = Path(result)
                if p.exists():
                    return p

    # Attribute-style fallbacks.
    for attr_name in ("_file", "path", "_path", "file"):
        value = getattr(video, attr_name, None)
        if isinstance(value, (str, Path)):
            p = Path(value)
            if p.exists():
                return p

    return None

"""LTXV API Video-to-Video — ComfyUI node.

Wraps ``POST /v1/retake``. Officially Lightricks calls this "retake" -- it
replaces a section of a source video with newly-synthesised content. Setting
``start_time=0`` and ``duration=<full clip length>`` coerces it into a
full-clip video-to-video regen.

Inputs:

* ``video`` (VIDEO socket) -- preferred when chained from another VIDEO-emitting
  node (Load Video, another LTXV node). When the VIDEO is backed by an
  on-disk MP4 (``VideoFromFile``), the original container bytes ride
  straight to the API -- audio preserved, no re-encode.
* ``video_url`` (STRING) -- a public HTTPS URL. Used verbatim by the API.
  Recommended for very large inputs that would push base64 past request
  size limits, or when input audio fidelity matters and the source isn't
  file-backed.

Output is the regenerated MP4 wrapped as a native ComfyUI VIDEO socket.

Auth: ``LTXV_API_KEY`` env var (preferred), or the studio config / user
TOML fallback chain in :mod:`ltxv_api.config`.

Server-side constraints (reproduce in tooltips so the artist sees them
on the canvas):

* Min input frames: 73 (~3s @ 24fps).
* Section duration must be ≥ 2 seconds.
* Max input resolution: 3840×2160 (4K).
* Output capped at 1920×1080 or 1080×1920.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .. import tensors, video_type
from ..client import LTXVClient
from ..config import resolve_api_key

log = logging.getLogger("comfyui-ltxv-api.video-to-video")


V2V_MODEL_CHOICES = ("ltx-2-3-pro", "ltx-2-pro")
V2V_RESOLUTION_CHOICES = ("(auto)", "1920x1080", "1080x1920")
V2V_MODE_CHOICES = ("replace_audio_and_video", "replace_video", "replace_audio")


class LTXVAPIVideoToVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": "Describe what should change in the video.",
                    "tooltip": (
                        "Optional but strongly recommended -- the prompt steers what "
                        "the regenerated section should look like."
                    ),
                }),
                "model": (list(V2V_MODEL_CHOICES), {
                    "default": "ltx-2-3-pro",
                    "tooltip": (
                        "Retake supports the Pro variants only. ltx-2-3-pro is the "
                        "newer LTX-2.3 family and is the API default."
                    ),
                }),
                "start_time": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 600.0, "step": 0.1,
                    "tooltip": (
                        "Seconds into the input where the regenerated section starts. "
                        "Set to 0 (combined with duration=full clip length) for a "
                        "full-clip video-to-video regen."
                    ),
                }),
                "duration": ("FLOAT", {
                    "default": 4.0, "min": 2.0, "max": 60.0, "step": 0.1,
                    "tooltip": (
                        "Section length in seconds. API minimum is 2.0. Set to the "
                        "full clip length (combined with start_time=0) for a full "
                        "video-to-video regen."
                    ),
                }),
                "mode": (list(V2V_MODE_CHOICES), {
                    "default": "replace_audio_and_video",
                    "tooltip": (
                        "Which streams to regenerate. replace_audio_and_video = both. "
                        "replace_video = keep original audio, replace pixels. "
                        "replace_audio = keep pixels, replace audio."
                    ),
                }),
                "resolution": (list(V2V_RESOLUTION_CHOICES), {
                    "default": "(auto)",
                    "tooltip": (
                        "Output resolution. (auto) lets the API mirror the input. "
                        "1920x1080 / 1080x1920 are the documented supported outputs."
                    ),
                }),
                "fps_for_encoding": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 60.0, "step": 0.001,
                    "tooltip": (
                        "FPS used when re-encoding a component-derived VIDEO "
                        "(VideoFromComponents) to MP4 for the request body. "
                        "Ignored when video_url is used or when the VIDEO is "
                        "file-backed (the original bytes ride through unchanged)."
                    ),
                }),
            },
            "optional": {
                "video": ("VIDEO", {
                    "tooltip": (
                        "Source video. The native input type for retake. When the "
                        "VIDEO is backed by an on-disk MP4 (Load Video, another "
                        "LTXV node) the original bytes are uploaded directly -- "
                        "audio preserved, no re-encode."
                    ),
                }),
                "video_url": ("STRING", {
                    "default": "", "multiline": False,
                    "placeholder": "(optional) HTTPS URL — used verbatim instead of base64",
                    "tooltip": (
                        "Public HTTPS URL of the input video. When non-empty, this "
                        "wins over the `video` socket and the request uses the URL "
                        "verbatim. Recommended for inputs too large for base64 "
                        "(10s of MB+)."
                    ),
                }),
            },
        }

    RETURN_TYPES = (
        "VIDEO", "STRING", "INT", "INT", "FLOAT", "INT",
    )
    RETURN_NAMES = (
        "video", "info",
        "width", "height", "frame_rate", "frame_count",
    )
    OUTPUT_TOOLTIPS = (
        "Native ComfyUI VIDEO wrapping the downloaded MP4 (lazy decode). "
        "Wire to SaveVideo / GetVideoComponents / partner API nodes. Audio "
        "track depends on `mode`: replace_audio_and_video / replace_audio "
        "ship API-generated audio; replace_video keeps the source's audio.",
        "Human-readable summary.",
        "Frame width in pixels.",
        "Frame height in pixels.",
        "MP4 frame rate as probed from the container.",
        "Number of frames in the MP4 container.",
    )
    FUNCTION = "execute"
    CATEGORY = "LTXV API"

    def execute(
        self,
        prompt: str,
        model: str,
        start_time: float,
        duration: float,
        mode: str,
        resolution: str,
        fps_for_encoding: float,
        video: Optional[Any] = None,
        video_url: str = "",
    ):
        if duration < 2.0:
            raise ValueError(
                f"duration={duration:.2f}s is below the LTX retake minimum (2.0s)."
            )

        video_uri, temp_input_mp4 = tensors.resolve_video_uri(
            video=video,
            video_url=video_url,
            fps_override=float(fps_for_encoding),
        )
        log.info(
            "[ltxv-api/video-to-video] POST /v1/retake model=%s start=%.2f dur=%.2f mode=%s res=%s url=%s",
            model, start_time, duration, mode, resolution, bool(video_url.strip()),
        )

        api_key = resolve_api_key()
        client = LTXVClient(api_key)

        out_path = tensors.temp_path(tensors.MP4_EXT, prefix="ltxv_v2v_")
        try:
            client.retake(
                out_path,
                video_uri=video_uri,
                start_time=float(start_time),
                duration=float(duration),
                prompt=(prompt or None),
                mode=mode,
                resolution=(resolution if resolution != "(auto)" else None),
                model=model,
            )
        finally:
            # Clean up the encoded-input MP4 if we created one (component-
            # derived VIDEO branch). File-backed inputs (None) and URLs need
            # no cleanup.
            if temp_input_mp4 is not None:
                try:
                    temp_input_mp4.unlink()
                except OSError:
                    pass

        width, height, n_frames, decoded_fps = tensors.mp4_probe(out_path)

        video_obj = video_type.make_video_from_file(str(out_path))
        if video_obj is None:
            raise RuntimeError(
                "Native ComfyUI VIDEO type isn't reachable (comfy_api.latest "
                "import failed). Update ComfyUI to a recent build that ships "
                "the VIDEO type."
            )

        info_str = (
            f"{width}x{height} @ {decoded_fps:.3f}fps, {n_frames} frames "
            f"(model={model}, mode={mode}, start={start_time:.2f}s, dur={duration:.2f}s)"
        )
        return (video_obj, info_str, width, height, float(decoded_fps), n_frames)

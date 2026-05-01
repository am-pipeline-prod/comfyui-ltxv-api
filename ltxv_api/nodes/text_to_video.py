"""LTXV API Text-to-Video — ComfyUI node.

Wraps ``POST /v1/text-to-video``. The API returns an MP4 (frames + optional
audio in one container); we stream it to ComfyUI's temp dir, wrap it as a
native ComfyUI ``VIDEO`` socket via ``VideoFromFile`` (lazy, zero re-decode),
and emit it. Workflows that need separate IMAGE frames or AUDIO chain
ComfyUI's stock ``GetVideoComponents`` downstream.

Auth: ``LTXV_API_KEY`` env var (preferred), or the studio config / user
TOML fallback chain in :mod:`ltxv_api.config`.
"""
from __future__ import annotations

import logging

from .. import tensors, video_type
from ..client import LTXVClient
from ..config import resolve_api_key
from ._common import (
    CAMERA_MOTION_CHOICES,
    RESOLUTION_CHOICES,
    T2V_MODEL_CHOICES,
    resolve_camera_motion,
    resolve_resolution,
)

log = logging.getLogger("comfyui-ltxv-api.text-to-video")


class LTXVAPITextToVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": "Describe the video you want to generate.",
                    "tooltip": "Text prompt describing the desired video content.",
                }),
                "model": (list(T2V_MODEL_CHOICES), {
                    "default": "ltx-2-3-fast",
                    "tooltip": (
                        "LTX model variant. ltx-2-fast / ltx-2-3-fast = lower cost, "
                        "lower quality. ltx-2-pro / ltx-2-3-pro = higher quality, "
                        "higher cost. The 2-3 line is the newer LTX-2.3 family."
                    ),
                }),
                "duration": ("INT", {
                    "default": 4, "min": 1, "max": 30,
                    "tooltip": "Output video length in seconds. Billed per second.",
                }),
                "resolution": (list(RESOLUTION_CHOICES), {
                    "default": "1920x1080",
                    "tooltip": (
                        "Output frame size. Pick (custom) and type into "
                        "resolution_custom for non-listed sizes."
                    ),
                }),
                "resolution_custom": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Only used when resolution=(custom). e.g. 1280x720",
                    "tooltip": "Literal 'WxH' override. Ignored unless resolution=(custom).",
                }),
                "fps": ("INT", {
                    "default": 24, "min": 1, "max": 60,
                    "tooltip": "Output frame rate (LTX API default 24).",
                }),
                "generate_audio": ("BOOLEAN", {
                    "default": True,
                    "tooltip": (
                        "When On, the API generates a synced audio track and includes "
                        "it in the MP4 (rides through the VIDEO socket). Off = silent video."
                    ),
                }),
                "camera_motion": (list(CAMERA_MOTION_CHOICES), {
                    "default": CAMERA_MOTION_CHOICES[0],
                    "tooltip": (
                        "Optional camera-motion hint. (unset) = let the model pick. "
                        "Other values steer the camera deterministically."
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
        "Wire to SaveVideo / GetVideoComponents / partner API nodes. "
        "Carries the API-generated audio track when generate_audio=True.",
        "Human-readable summary: dimensions, fps, frame count, model.",
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
        duration: int,
        resolution: str,
        resolution_custom: str,
        fps: int,
        generate_audio: bool,
        camera_motion: str,
    ):
        if not prompt.strip():
            raise ValueError("prompt is required (text-to-video without text is not supported).")

        resolution_value = resolve_resolution(resolution, resolution_custom)
        camera_value = resolve_camera_motion(camera_motion)

        api_key = resolve_api_key()
        client = LTXVClient(api_key)

        out_path = tensors.temp_path(tensors.MP4_EXT, prefix="ltxv_t2v_")
        log.info(
            "[ltxv-api/text-to-video] POST /v1/text-to-video model=%s duration=%ds res=%s fps=%d audio=%s camera=%s",
            model, duration, resolution_value, fps, generate_audio, camera_value or "(unset)",
        )
        client.text_to_video(
            out_path,
            prompt=prompt,
            model=model,
            duration=int(duration),
            resolution=resolution_value,
            fps=int(fps),
            generate_audio=bool(generate_audio),
            camera_motion=camera_value,
        )

        # Header-only probe for the metadata sockets; the VIDEO socket
        # itself is lazy and re-decodes through VideoFromFile only when a
        # downstream consumer asks for frames.
        width, height, n_frames, decoded_fps = tensors.mp4_probe(out_path)

        video_obj = video_type.make_video_from_file(str(out_path))
        if video_obj is None:
            raise RuntimeError(
                "Native ComfyUI VIDEO type isn't reachable (comfy_api.latest "
                "import failed). Update ComfyUI to a recent build that ships "
                "the VIDEO type, or wire the request flow through a custom "
                "video reader downstream."
            )

        info_str = (
            f"{width}x{height} @ {decoded_fps:.3f}fps, {n_frames} frames "
            f"(model={model}, duration={duration}s)"
        )
        return (video_obj, info_str, width, height, float(decoded_fps), n_frames)

"""LTXV API Image-to-Video — ComfyUI node.

Wraps ``POST /v1/image-to-video``. Accepts an IMAGE input (first frame),
encodes it as a base64 PNG data URI, sends to LTX, and returns the MP4 as
both an IMAGE batch and a VIDEO socket.

Optional ``last_frame`` IMAGE input (LTX-2.3 models only) drives end-frame
interpolation.

Auth: ``LTXV_API_KEY`` env var (preferred), or the studio config / user
TOML fallback chain in :mod:`ltxv_api.config`.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .. import tensors, video_type
from ..client import LTXVClient
from ..config import resolve_api_key
from ._common import (
    CAMERA_MOTION_CHOICES,
    RESOLUTION_CHOICES,
    T2V_MODEL_CHOICES,
    resolve_camera_motion,
    resolve_resolution,
    silent_audio_stub,
)

log = logging.getLogger("comfyui-ltxv-api.image-to-video")


class LTXVAPIImageToVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {
                    "tooltip": "First frame of the generated video. Single frame; if a batch is wired, frame 0 is used.",
                }),
                "prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": "Describe the desired animation.",
                    "tooltip": "Text describing how the image should animate.",
                }),
                "model": (list(T2V_MODEL_CHOICES), {
                    "default": "ltx-2-fast",
                    "tooltip": (
                        "LTX model variant. last_frame is only honored by ltx-2-3-fast / ltx-2-3-pro."
                    ),
                }),
                "duration": ("INT", {
                    "default": 4, "min": 1, "max": 30,
                    "tooltip": "Output video length in seconds. Billed per second.",
                }),
                "resolution": (list(RESOLUTION_CHOICES), {
                    "default": "1920x1080",
                    "tooltip": "Output frame size. Pick (custom) for non-listed sizes.",
                }),
                "resolution_custom": ("STRING", {
                    "default": "", "multiline": False,
                    "placeholder": "Only used when resolution=(custom). e.g. 1280x720",
                    "tooltip": "Literal 'WxH' override. Ignored unless resolution=(custom).",
                }),
                "fps": ("INT", {
                    "default": 24, "min": 1, "max": 60,
                    "tooltip": "Output frame rate (LTX API default 24).",
                }),
                "generate_audio": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "When On, the API generates a synced audio track in the MP4.",
                }),
                "camera_motion": (list(CAMERA_MOTION_CHOICES), {
                    "default": CAMERA_MOTION_CHOICES[0],
                    "tooltip": "Optional camera-motion hint. (unset) = let the model pick.",
                }),
            },
            "optional": {
                "last_frame": ("IMAGE", {
                    "tooltip": (
                        "Optional final frame for interpolation. ltx-2-3-fast / ltx-2-3-pro only — "
                        "ignored by the legacy ltx-2 models."
                    ),
                }),
            },
        }

    RETURN_TYPES = (
        "IMAGE", "VIDEO", "AUDIO", "STRING", "INT", "INT", "FLOAT", "INT",
    )
    RETURN_NAMES = (
        "image", "video", "audio", "info",
        "width", "height", "frame_rate", "frame_count",
    )
    OUTPUT_TOOLTIPS = (
        "Decoded frames as IMAGE batch (N×H×W×C float32 in [0,1]).",
        "Native ComfyUI VIDEO wrapping the downloaded MP4 (lazy decode).",
        "Audio track from the MP4 (silent stub when generate_audio=Off).",
        "Human-readable summary.",
        "Frame width in pixels.",
        "Frame height in pixels.",
        "MP4 frame rate as decoded from the container.",
        "Number of frames decoded into the IMAGE batch.",
    )
    FUNCTION = "execute"
    CATEGORY = "LTXV API"

    def execute(
        self,
        image,
        prompt: str,
        model: str,
        duration: int,
        resolution: str,
        resolution_custom: str,
        fps: int,
        generate_audio: bool,
        camera_motion: str,
        last_frame: Optional[Any] = None,
    ):
        if image is None or image.shape[0] == 0:
            raise ValueError("image input is required (image-to-video).")
        if not prompt.strip():
            raise ValueError("prompt is required.")

        resolution_value = resolve_resolution(resolution, resolution_custom)
        camera_value = resolve_camera_motion(camera_motion)

        image_uri = tensors.image_tensor_to_data_uri(image, frame_index=0)
        last_frame_uri = None
        if last_frame is not None and last_frame.shape[0] > 0:
            if not model.startswith("ltx-2-3"):
                log.warning(
                    "[ltxv-api/image-to-video] last_frame wired but model=%s does not "
                    "support it; the field will be sent and the server may reject it. "
                    "Switch to ltx-2-3-fast / ltx-2-3-pro to use last-frame interpolation.",
                    model,
                )
            last_frame_uri = tensors.image_tensor_to_data_uri(last_frame, frame_index=0)

        api_key = resolve_api_key()
        client = LTXVClient(api_key)

        out_path = tensors.temp_path(tensors.MP4_EXT, prefix="ltxv_i2v_")
        log.info(
            "[ltxv-api/image-to-video] POST /v1/image-to-video model=%s duration=%ds res=%s fps=%d audio=%s last_frame=%s",
            model, duration, resolution_value, fps, generate_audio, last_frame_uri is not None,
        )
        client.image_to_video(
            out_path,
            image_uri=image_uri,
            prompt=prompt,
            model=model,
            duration=int(duration),
            resolution=resolution_value,
            fps=int(fps),
            generate_audio=bool(generate_audio),
            camera_motion=camera_value,
            last_frame_uri=last_frame_uri,
        )

        out_image, decoded_fps = tensors.mp4_to_image_batch(out_path)
        n_frames = int(out_image.shape[0])
        height = int(out_image.shape[1])
        width = int(out_image.shape[2])

        video_obj = video_type.make_video_from_file(str(out_path))
        audio = silent_audio_stub()

        info_str = (
            f"{width}x{height} @ {decoded_fps:.3f}fps, {n_frames} frames "
            f"(model={model}, duration={duration}s, last_frame={last_frame_uri is not None})"
        )
        return (out_image, video_obj, audio, info_str, width, height, float(decoded_fps), n_frames)

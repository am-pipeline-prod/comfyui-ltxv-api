"""Shared helpers for the LTXV API ComfyUI nodes."""
from __future__ import annotations

from typing import Any, Dict

import torch

# Resolution dropdown values shared across the sync endpoints. The LTX API
# accepts many sizes; these are the broadly-supported "documented defaults"
# we surface in the dropdown to keep the canvas tidy. An artist who wants a
# different size can pick `(custom)` and type the literal "WxH" string into
# the `resolution_custom` widget.
RESOLUTION_CHOICES = (
    "1920x1080",
    "1080x1920",
    "1280x720",
    "720x1280",
    "1024x576",
    "576x1024",
    "(custom)",
)
RESOLUTION_CUSTOM = "(custom)"

# Models that accept text-to-video / image-to-video (the four documented
# variants).
T2V_MODEL_CHOICES = ("ltx-2-fast", "ltx-2-pro", "ltx-2-3-fast", "ltx-2-3-pro")

# Camera-motion enum on text-to-video / image-to-video. The empty-string
# entry means "don't send the field" -- the API picks its own default.
CAMERA_MOTION_NONE = "(unset)"
CAMERA_MOTION_CHOICES = (
    CAMERA_MOTION_NONE,
    "static",
    "dolly_in",
    "dolly_out",
    "dolly_left",
    "dolly_right",
    "jib_up",
    "jib_down",
    "focus_shift",
)


def silent_audio_stub() -> Dict[str, Any]:
    """1-sample silent AUDIO dict, matching the ComfyUI AUDIO convention."""
    return {
        "waveform": torch.zeros((1, 1, 1), dtype=torch.float32),
        "sample_rate": 48000,
    }


def empty_image_tensor() -> "torch.Tensor":
    """1×64×64×3 black frame -- the conventional empty IMAGE placeholder."""
    return torch.zeros((1, 64, 64, 3), dtype=torch.float32)


def resolve_resolution(choice: str, custom: str) -> str:
    """Resolve the (dropdown, custom) widget pair to a literal ``WxH`` string."""
    if choice == RESOLUTION_CUSTOM:
        value = (custom or "").strip()
        if not value:
            raise ValueError(
                "resolution dropdown is set to (custom) but the custom field is empty. "
                "Type a literal 'WxH' value (e.g. '1280x720') or pick a built-in choice."
            )
        # Light validation -- catch typos before sending to the server.
        try:
            w_str, h_str = value.lower().split("x", 1)
            int(w_str)
            int(h_str)
        except (ValueError, AttributeError):
            raise ValueError(
                f"resolution_custom={value!r} is not a 'WxH' integer pair."
            )
        return value
    return choice


def resolve_camera_motion(choice: str):
    """Drop the camera-motion field from the request when set to ``(unset)``."""
    if choice == CAMERA_MOTION_NONE or not choice:
        return None
    return choice

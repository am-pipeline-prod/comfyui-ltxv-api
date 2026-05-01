"""ComfyUI LTXV API -- custom nodes for the Lightricks LTX REST API.

Registers four nodes under the "LTXV API" category:

* ``LTXVAPITextToVideo``     -- POST /v1/text-to-video
* ``LTXVAPIImageToVideo``    -- POST /v1/image-to-video
* ``LTXVAPIVideoToVideo``    -- POST /v1/retake (officially "retake"; usable as
  full-clip V2V regen by setting start_time=0, duration=full)
* ``LTXVAPIVideoToVideoHDR`` -- async POST /v2/video-to-video-hdr; returns
  scene-linear EXR frames (HDR-safe IMAGE batch)
"""
from __future__ import annotations

import logging
import os
import sys

# --- Make ``ltxv_api`` importable when ComfyUI loads this folder as a custom
# node. The folder name is "comfyui-ltxv-api" (with hyphens), which isn't a
# valid Python identifier, so ComfyUI's loader uses
# ``importlib.util.spec_from_file_location`` rather than a normal import --
# meaning ``ltxv_api`` won't resolve via relative imports either, since the
# enclosing module name is hyphenated. We therefore put this folder on
# ``sys.path`` so ``import ltxv_api`` works as an absolute import.
#
# Note: we deliberately do NOT have a top-level ``nodes`` package here, since
# that would shadow ComfyUI's own ``nodes`` module already in sys.modules.
# All node classes live under ``ltxv_api.nodes``.
_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ltxv_api.nodes import (  # noqa: E402
    LTXVAPIImageToVideo,
    LTXVAPITextToVideo,
    LTXVAPIVideoToVideo,
    LTXVAPIVideoToVideoHDR,
)

NODE_CLASS_MAPPINGS = {
    "LTXVAPITextToVideo":     LTXVAPITextToVideo,
    "LTXVAPIImageToVideo":    LTXVAPIImageToVideo,
    "LTXVAPIVideoToVideo":    LTXVAPIVideoToVideo,
    "LTXVAPIVideoToVideoHDR": LTXVAPIVideoToVideoHDR,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LTXVAPITextToVideo":     "LTXV API Text to Video",
    "LTXVAPIImageToVideo":    "LTXV API Image to Video",
    "LTXVAPIVideoToVideo":    "LTXV API Video to Video (Retake)",
    "LTXVAPIVideoToVideoHDR": "LTXV API Video to Video HDR",
}

logging.getLogger("comfyui-ltxv-api").info(
    "[comfyui-ltxv-api] loaded %d node(s)", len(NODE_CLASS_MAPPINGS),
)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

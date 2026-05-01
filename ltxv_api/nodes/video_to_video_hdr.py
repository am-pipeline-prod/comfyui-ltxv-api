"""LTXV API Video-to-Video HDR — ComfyUI node.

Wraps the **async** ``POST /v2/video-to-video-hdr`` + ``GET /v2/video-to-video-hdr/{id}``
polling loop. The endpoint regenerates an SDR input as a sequence of
**scene-linear EXR frames** (HDR) and returns a presigned URL to a ZIP of
those frames.

Output is an IMAGE batch of float32 frames -- HDR-safe (values may exceed
1.0, which is the whole point). The endpoint is image-native: there's no
audio track and no native time base on the EXR ZIP. To re-encode the IMAGE
batch back into a VIDEO (e.g. for SaveVideo), wire ``image`` plus the
``frame_rate`` metadata socket into stock ``CreateVideo``. For HDR-safe
export wire ``image`` into an EXR-aware writer (e.g. ``AM Write Image``
with ``ext=exr``) instead.

Pricing (per :doc:`api-reference-snapshot.md` and the LTX docs):

* 1080p: $0.20/s -- max 181 frames (~7s @ 24fps)
* 1440p: $0.40/s -- max 101 frames (~4s @ 24fps)
* 4K:    $0.80/s -- max  41 frames (~2s @ 24fps)

(Confirm live pricing on the LTX developer console -- these numbers are
captured at the time of writing and may change.)

Color space caveat: Lightricks documents the EXR output as "scene-linear"
without specifying primaries. Source-code inspection of their local
distillation shows an inverse LogC3 transform with no colour matrix --
treat the data as **linear ARRI Wide Gamut 3** (LogC3's canonical companion
primaries) and apply an AWG3→ACEScg / AWG3→Rec.709 IDT downstream. See
the README's HDR section.

Auth: ``LTXV_API_KEY`` env var (preferred), or the studio config / user
TOML fallback chain in :mod:`ltxv_api.config`.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from .. import exr_loader, tensors
from ..client import (
    LTXVClient,
    LTXVError,
    JobResult,
    JobStatus,
    hdr_result_url,
)
from ..config import resolve_api_key

log = logging.getLogger("comfyui-ltxv-api.video-to-video-hdr")


class LTXVAPIVideoToVideoHDR:
    """Asynchronous HDR (scene-linear EXR) video-to-video.

    BILLING: per-second pricing depends on output resolution -- 1080p $0.20/s,
    1440p $0.40/s, 4K $0.80/s. Confirm current rates on the LTX developer
    console before queuing long inputs.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "fps_for_encoding": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 60.0, "step": 0.001,
                    "tooltip": (
                        "FPS used when re-encoding a component-derived VIDEO "
                        "(VideoFromComponents) to MP4 for the request body. "
                        "Ignored when video_url is used or when the VIDEO is "
                        "file-backed."
                    ),
                }),
                "output_fps": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 60.0, "step": 0.001,
                    "tooltip": (
                        "FPS reported on the `frame_rate` output socket. The EXR "
                        "ZIP from the API doesn't carry a native time base, so "
                        "this is what downstream consumers (CreateVideo, AM Write "
                        "Image) will see for cadence."
                    ),
                }),
                "poll_timeout_seconds": ("INT", {
                    "default": 600, "min": 60, "max": 3600,
                    "tooltip": (
                        "Maximum total wait for the async HDR job to reach a "
                        "terminal state (completed / failed). LTX results are "
                        "available for 24h once terminal."
                    ),
                }),
                "keep_temp_exrs": ("BOOLEAN", {
                    "default": False,
                    "tooltip": (
                        "When On, the extracted EXR files are left on disk so "
                        "downstream tooling (DJV, Nuke) can reach them. Off "
                        "(default) deletes them after loading the IMAGE batch."
                    ),
                }),
            },
            "optional": {
                "video": ("VIDEO", {
                    "tooltip": (
                        "Source SDR video. Native input type. When the VIDEO is "
                        "backed by an on-disk MP4 (Load Video, another LTXV node) "
                        "the original bytes are uploaded directly. Tier limits "
                        "apply: ≤1080p max 181 frames, ≤1440p max 101, ≤4K max 41."
                    ),
                }),
                "video_url": ("STRING", {
                    "default": "", "multiline": False,
                    "placeholder": "(optional) HTTPS URL — used verbatim instead of base64",
                    "tooltip": (
                        "Public HTTPS URL of the SDR input. When non-empty, wins "
                        "over the `video` socket. Recommended for inputs that "
                        "would balloon the base64 request body."
                    ),
                }),
            },
        }

    RETURN_TYPES = (
        "IMAGE", "STRING", "INT", "INT", "FLOAT", "INT",
    )
    RETURN_NAMES = (
        "image", "info",
        "width", "height", "frame_rate", "frame_count",
    )
    OUTPUT_TOOLTIPS = (
        "HDR EXR frames as IMAGE batch (N×H×W×3 float32, *not clipped to [0,1]* — "
        "scene-linear values can and do exceed 1.0). The HDR endpoint is "
        "image-native; there is no VIDEO output socket. To re-encode for "
        "SDR delivery, wire `image` + `frame_rate` into stock CreateVideo.",
        "Human-readable summary including the LTX job id.",
        "Frame width in pixels.",
        "Frame height in pixels.",
        "Tagged frame rate (== output_fps).",
        "Number of EXR frames returned.",
    )
    FUNCTION = "execute"
    CATEGORY = "LTXV API"
    DESCRIPTION = (
        "Asynchronous HDR (scene-linear EXR) video-to-video. "
        "Pricing: 1080p $0.20/s, 1440p $0.40/s, 4K $0.80/s. "
        "Output is float32 IMAGE batch -- values may exceed 1.0."
    )

    def execute(
        self,
        fps_for_encoding: float,
        output_fps: float,
        poll_timeout_seconds: int,
        keep_temp_exrs: bool,
        video: Optional[Any] = None,
        video_url: str = "",
    ):
        # Resolve input -> video_uri (URL or base64 data URI).
        video_uri, temp_input_mp4 = tensors.resolve_video_uri(
            video=video,
            video_url=video_url,
            fps_override=float(fps_for_encoding),
        )

        api_key = resolve_api_key()
        client = LTXVClient(api_key)

        # Optional ComfyUI canvas progress bar -- imports lazily so unit tests
        # can run without a ComfyUI runtime in path.
        progress = _make_progress_bar()

        log.info(
            "[ltxv-api/v2v-hdr] POST /v2/video-to-video-hdr (uri=%s)",
            "<url>" if video_url.strip() else "<data-uri>",
        )
        try:
            submitted = client.submit_video_to_video_hdr(video_uri=video_uri)
        finally:
            if temp_input_mp4 is not None:
                try:
                    temp_input_mp4.unlink()
                except OSError:
                    pass

        log.info("[ltxv-api/v2v-hdr] job submitted id=%s status=%s", submitted.id, submitted.status)

        def _on_progress(job: JobResult) -> None:
            if progress is not None:
                # Coarse step -- the API doesn't expose a percentage; tick once
                # per poll so the canvas shows something is happening.
                try:
                    progress.update(1)
                except Exception:  # noqa: BLE001
                    pass
            log.info("[ltxv-api/v2v-hdr] poll status=%s id=%s", job.status, job.id)

        try:
            terminal = client.poll_video_to_video_hdr(
                submitted.id,
                timeout=float(poll_timeout_seconds),
                on_progress=_on_progress,
            )
        except LTXVError:
            log.exception("[ltxv-api/v2v-hdr] polling failed for job %s", submitted.id)
            raise

        if terminal.status != JobStatus.COMPLETED:
            err = terminal.error or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise LTXVError(
                f"HDR job {terminal.id} ended in status={terminal.status!r}: {msg or '(no detail)'}",
                response_body=terminal.raw,
            )

        zip_url = hdr_result_url(terminal)
        if not zip_url:
            raise LTXVError(
                f"HDR job {terminal.id} completed but no exr_frames_url in result; "
                f"raw result: {terminal.result!r}",
                response_body=terminal.raw,
            )

        # Download the EXR ZIP and extract.
        zip_path = tensors.temp_path(".zip", prefix="ltxv_hdr_")
        log.info("[ltxv-api/v2v-hdr] downloading EXR ZIP -> %s", zip_path)
        try:
            client.download(zip_url, zip_path)
        except LTXVError:
            try:
                zip_path.unlink()
            except OSError:
                pass
            raise

        extract_dir = Path(str(zip_path) + "_frames")
        try:
            tensor, frame_paths = exr_loader.load_exr_zip(zip_path, extract_dir)
        finally:
            try:
                zip_path.unlink()
            except OSError:
                pass

        try:
            n_frames = int(tensor.shape[0])
            height = int(tensor.shape[1])
            width = int(tensor.shape[2])

            info_str = (
                f"{width}x{height} {n_frames} EXR frames @ {output_fps:.3f}fps "
                f"(job={terminal.id})"
            )
            return (tensor, info_str, width, height, float(output_fps), n_frames)
        finally:
            if not keep_temp_exrs:
                _cleanup_paths(frame_paths)
                try:
                    extract_dir.rmdir()
                except OSError:
                    pass


def _make_progress_bar():
    try:
        import comfy.utils  # type: ignore[import-not-found]
        return comfy.utils.ProgressBar(100)
    except Exception:  # noqa: BLE001
        return None


def _cleanup_paths(paths) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass

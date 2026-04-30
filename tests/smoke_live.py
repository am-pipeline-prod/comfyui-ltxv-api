"""Live smoke test against the real LTX API.

⚠️ EVERY RUN BILLS YOUR ACCOUNT.

Run from the repo root with the API key resolvable (env, studio file, or
user config). Skipped automatically when no key is reachable.

Endpoints tested (smallest possible parameters that the API actually accepts;
note: 720p was rejected by ltx-2-fast / ltx-2-3-fast in early smoke runs --
1920x1080 is the only confirmed-accepted value across every current model):

* ``/v1/text-to-video``      -- 1920x1080, 4s @ 24fps, ltx-2-fast.
* ``/v1/image-to-video``     -- 1920x1080, 4s @ 24fps, ltx-2-3-fast,
  using a tiny synthesised PNG as the first frame.
* ``/v1/retake``             -- ltx-2-pro, mode=replace_video, start=0,
  duration=2.0s. Uses the t2v output as the input video so we don't need
  to publish a sample asset somewhere.
* ``/v2/video-to-video-hdr`` -- async, polls until terminal. Uses the
  same t2v output as the source.

Usage:
    cd <repo-root>
    /opt/comfyui/venv/bin/python -m tests.smoke_live --tests=t2v,i2v,v2v,hdr

By default all four are run. Pass ``--tests=t2v`` (etc.) to run a subset.
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# OpenCV EXR codec gate -- set before any cv2 import inside the package.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

from ltxv_api import (  # noqa: E402
    ApiKeyNotFoundError,
    LTXVClient,
    LTXVError,
    JobStatus,
    resolve_api_key,
)
from ltxv_api.client import hdr_result_url  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smoke")


def _tiny_png_data_uri() -> str:
    """Synthesise a 64x64 solid-blue PNG and return as a data URI.

    Avoids a network dependency on a hosted sample image.
    """
    import base64
    from PIL import Image
    img = Image.new("RGB", (64, 64), (40, 80, 160))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _mp4_to_data_uri(path: Path) -> str:
    import base64
    blob = path.read_bytes()
    return "data:video/mp4;base64," + base64.b64encode(blob).decode("ascii")


def smoke_text_to_video(client: LTXVClient, out_dir: Path) -> Path:
    out = out_dir / "smoke_t2v.mp4"
    log.info("[t2v] POST /v1/text-to-video -> %s", out)
    client.text_to_video(
        out,
        prompt="A blue square slowly rotating against a white background.",
        model="ltx-2-fast",
        duration=4,
        resolution="1920x1080",
        fps=24,
        generate_audio=False,
    )
    log.info("[t2v] OK; mp4 size=%d bytes", out.stat().st_size)
    return out


def smoke_image_to_video(client: LTXVClient, out_dir: Path) -> Path:
    out = out_dir / "smoke_i2v.mp4"
    log.info("[i2v] POST /v1/image-to-video -> %s", out)
    client.image_to_video(
        out,
        image_uri=_tiny_png_data_uri(),
        prompt="The blue square zooms in slowly.",
        model="ltx-2-3-fast",
        duration=4,
        resolution="1920x1080",
        fps=24,
        generate_audio=False,
    )
    log.info("[i2v] OK; mp4 size=%d bytes", out.stat().st_size)
    return out


def smoke_video_to_video(client: LTXVClient, source_mp4: Path, out_dir: Path) -> Path:
    out = out_dir / "smoke_v2v.mp4"
    log.info("[v2v] POST /v1/retake -> %s", out)
    client.retake(
        out,
        video_uri=_mp4_to_data_uri(source_mp4),
        start_time=0.0,
        duration=2.0,
        prompt="Convert the look to film noir, monochrome.",
        mode="replace_video",
        model="ltx-2-pro",
    )
    log.info("[v2v] OK; mp4 size=%d bytes", out.stat().st_size)
    return out


def smoke_hdr(client: LTXVClient, source_mp4: Path, out_dir: Path) -> None:
    log.info("[hdr] POST /v2/video-to-video-hdr (async)")
    submitted = client.submit_video_to_video_hdr(video_uri=_mp4_to_data_uri(source_mp4))
    log.info("[hdr] submitted id=%s status=%s", submitted.id, submitted.status)
    terminal = client.poll_video_to_video_hdr(
        submitted.id,
        timeout=10 * 60,
        on_progress=lambda j: log.info("[hdr] poll status=%s", j.status),
    )
    if terminal.status != JobStatus.COMPLETED:
        raise SystemExit(
            f"[hdr] job {terminal.id} ended status={terminal.status} error={terminal.error}"
        )

    zip_url = hdr_result_url(terminal)
    if not zip_url:
        raise SystemExit(f"[hdr] no exr_frames_url in {terminal.result!r}")
    log.info("[hdr] completed; downloading EXR ZIP from %s", zip_url[:80] + "...")
    zip_path = out_dir / "smoke_hdr.zip"
    client.download(zip_url, zip_path)
    log.info("[hdr] OK; zip size=%d bytes", zip_path.stat().st_size)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tests",
        default="t2v,i2v,v2v,hdr",
        help="Comma-separated subset of {t2v,i2v,v2v,hdr}. Default = all four.",
    )
    args = parser.parse_args()
    selected = {t.strip() for t in args.tests.split(",") if t.strip()}
    valid = {"t2v", "i2v", "v2v", "hdr"}
    if not selected.issubset(valid):
        print(f"--tests must be a subset of {sorted(valid)}", file=sys.stderr)
        return 2

    try:
        key = resolve_api_key()
    except ApiKeyNotFoundError as exc:
        log.error("no API key: %s", exc)
        return 2
    log.info("API key resolved (length=%d)", len(key))

    client = LTXVClient(key)
    out_dir = Path(tempfile.mkdtemp(prefix="ltxv_smoke_"))
    log.info("smoke output dir: %s", out_dir)

    t2v_path: Path | None = None
    failures: list[str] = []
    if "t2v" in selected:
        try:
            t2v_path = smoke_text_to_video(client, out_dir)
        except (LTXVError, OSError) as exc:
            log.exception("[t2v] FAILED")
            failures.append(f"t2v: {exc}")

    if "i2v" in selected:
        try:
            smoke_image_to_video(client, out_dir)
        except (LTXVError, OSError) as exc:
            log.exception("[i2v] FAILED")
            failures.append(f"i2v: {exc}")

    # v2v / hdr both want a source video. Re-use t2v's output if present;
    # otherwise generate a small one solely for these tests.
    if ("v2v" in selected or "hdr" in selected) and t2v_path is None:
        try:
            t2v_path = smoke_text_to_video(client, out_dir)
        except (LTXVError, OSError) as exc:
            log.exception("could not generate v2v/hdr source via t2v")
            failures.append(f"v2v/hdr-src: {exc}")

    if "v2v" in selected and t2v_path is not None:
        try:
            smoke_video_to_video(client, t2v_path, out_dir)
        except (LTXVError, OSError) as exc:
            log.exception("[v2v] FAILED")
            failures.append(f"v2v: {exc}")

    if "hdr" in selected and t2v_path is not None:
        try:
            smoke_hdr(client, t2v_path, out_dir)
        except (LTXVError, OSError, SystemExit) as exc:
            log.exception("[hdr] FAILED")
            failures.append(f"hdr: {exc}")

    if failures:
        log.error("%d failure(s):\n  %s", len(failures), "\n  ".join(failures))
        return 1
    log.info("all selected smokes passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

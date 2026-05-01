"""Typed HTTP client for the Lightricks LTX REST API.

Covers the four endpoints the ComfyUI nodes wrap:

* ``POST /v1/text-to-video``                 -- sync MP4 (binary)
* ``POST /v1/image-to-video``                -- sync MP4 (binary)
* ``POST /v1/retake``                        -- sync MP4 (binary)
* ``POST /v2/video-to-video-hdr``            -- async; returns 202 + job id
* ``GET  /v2/video-to-video-hdr/{id}``       -- async; poll until terminal

The sync endpoints stream their MP4 response body straight to a temp file --
the node layer decodes that file into IMAGE tensors and wraps it as a
ComfyUI ``VIDEO`` socket via ``VideoFromFile``.

The async polling loop uses exponential backoff starting at 5s, capped at
30s, with a ~10min default ceiling -- matches the LTX docs' "poll every 5s
until status is completed or failed" guidance with extra resilience for
transient 429s.

Reference:
    docs/api-reference-snapshot.md -- frozen snapshot of the LTX API
    documentation as of the time of writing. The client's request/response
    shapes are pinned to that snapshot; if Lightricks ships incompatible
    changes, diff there first.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

import requests

log = logging.getLogger("comfyui-ltxv-api.client")

DEFAULT_BASE_URL = "https://api.ltx.video"
USER_AGENT = "comfyui-ltxv-api/0.1.0 (+https://github.com/am-pipeline-prod/comfyui-ltxv-api)"

# Async polling: LTX docs recommend "poll every 5s". We start there and back off
# on 429 to be friendly.
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
MAX_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_POLL_TIMEOUT_SECONDS = 10 * 60  # 10 min ceiling per HDR job


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LTXVError(RuntimeError):
    """Base class. Carries the API's machine-readable error info if any."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        response_body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.response_body = response_body


class LTXVAuthError(LTXVError):
    """401 -- invalid or missing API key."""


class LTXVBillingError(LTXVError):
    """402 -- insufficient credits."""


class LTXVNotFoundError(LTXVError):
    """404 -- job (or other resource) not found / expired."""


class LTXVValidationError(LTXVError):
    """400 -- request validation failed."""


class LTXVSafetyError(LTXVError):
    """422 -- content rejected by safety filters."""


class LTXVRateLimitError(LTXVError):
    """429 -- rate limit exceeded. Retry with exponential backoff."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class JobResult:
    """An async job document as returned by ``/v2/.../{id}``.

    Fields mirror the documented ``V2JobStatusResponse`` shape; unknown fields
    are kept in ``raw`` so future API additions surface without code changes.
    """
    id: str
    status: str
    created_at: Optional[str]
    completed_at: Optional[str]
    result: Optional[Dict[str, Any]]
    error: Optional[Dict[str, Any]]
    raw: Dict[str, Any]


# Job lifecycle per the LTX async docs:
#   "pending" -> "processing" -> "completed" | "failed"
class JobStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINAL = frozenset({COMPLETED, FAILED})


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LTXVClient:
    """Synchronous HTTP client for the LTX REST API.

    Designed for ComfyUI custom-node use: cheap to construct, safe to share
    across nodes within a single workflow run, no global state.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        })

    # -- core request plumbing ---------------------------------------------

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Make an HTTP request that expects a JSON response.

        Raises a typed :class:`LTXVError` subclass on non-2xx.
        """
        url = f"{self._base_url}{path}"
        try:
            resp = self._session.request(
                method,
                url,
                json=json_body,
                params=params,
                timeout=timeout if timeout is not None else self._timeout,
                headers={"Accept": "application/json"},
            )
        except requests.RequestException as exc:
            raise LTXVError(f"network error contacting {url}: {exc}") from exc
        return self._parse_json_or_raise(resp)

    def _request_binary(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        out_path: Path,
        timeout: Optional[float] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Path:
        """Make an HTTP request that expects a binary stream response (MP4).

        Streams the body to *out_path*; on non-2xx, attempts to parse a JSON
        error envelope from the (typically small) error body and raises.
        Returns ``out_path`` on success.
        """
        url = f"{self._base_url}{path}"
        # Sync video generation can take a while -- ramp the timeout up.
        eff_timeout = timeout if timeout is not None else max(self._timeout * 20, 600.0)
        try:
            with self._session.request(
                method,
                url,
                json=json_body,
                stream=True,
                timeout=eff_timeout,
                headers={"Accept": "application/octet-stream"},
            ) as resp:
                if not (200 <= resp.status_code < 300):
                    # Pull the (small) error body and raise.
                    self._parse_json_or_raise(resp)
                    # _parse_json_or_raise always raises in this branch,
                    # but make the control flow explicit for type checkers.
                    raise LTXVError("unreachable", status_code=resp.status_code)
                with open(out_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if chunk:
                            fh.write(chunk)
                            if progress_callback is not None:
                                try:
                                    progress_callback(len(chunk))
                                except Exception:  # noqa: BLE001
                                    log.exception("progress_callback raised; continuing")
        except requests.RequestException as exc:
            raise LTXVError(f"network error contacting {url}: {exc}") from exc
        return out_path

    @staticmethod
    def _parse_json_or_raise(resp: requests.Response) -> Any:
        if 200 <= resp.status_code < 300:
            if resp.status_code == 204 or not resp.content:
                return None
            try:
                return resp.json()
            except json.JSONDecodeError:
                return resp.text

        # Try to parse the documented error envelope. Defensive: some
        # transports (e.g. CDN errors) return non-JSON.
        body: Any = None
        code: Optional[str] = None
        message: str = f"HTTP {resp.status_code}"
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                code = err.get("type") or err.get("code")
                message = err.get("message", message)
        except json.JSONDecodeError:
            body = resp.text

        kwargs = dict(code=code, status_code=resp.status_code, response_body=body)
        if resp.status_code == 401:
            raise LTXVAuthError(message, **kwargs)
        if resp.status_code == 402:
            raise LTXVBillingError(message, **kwargs)
        if resp.status_code == 404:
            raise LTXVNotFoundError(message, **kwargs)
        if resp.status_code == 400:
            raise LTXVValidationError(message, **kwargs)
        if resp.status_code == 422:
            raise LTXVSafetyError(message, **kwargs)
        if resp.status_code == 429:
            raise LTXVRateLimitError(message, **kwargs)
        raise LTXVError(message, **kwargs)

    # -- sync MP4 endpoints --------------------------------------------------

    def text_to_video(
        self,
        out_path: Path,
        *,
        prompt: str,
        model: str,
        duration: int,
        resolution: str,
        fps: Optional[int] = None,
        generate_audio: Optional[bool] = None,
        camera_motion: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Path:
        """``POST /v1/text-to-video`` -> stream MP4 to *out_path*."""
        body = _drop_none({
            "prompt": prompt,
            "model": model,
            "duration": duration,
            "resolution": resolution,
            "fps": fps,
            "generate_audio": generate_audio,
            "camera_motion": camera_motion,
        })
        return self._request_binary(
            "POST", "/v1/text-to-video",
            json_body=body, out_path=out_path,
            progress_callback=progress_callback,
        )

    def image_to_video(
        self,
        out_path: Path,
        *,
        image_uri: str,
        prompt: str,
        model: str,
        duration: int,
        resolution: str,
        fps: Optional[int] = None,
        generate_audio: Optional[bool] = None,
        camera_motion: Optional[str] = None,
        last_frame_uri: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Path:
        """``POST /v1/image-to-video`` -> stream MP4 to *out_path*.

        ``image_uri`` (and optional ``last_frame_uri``) accept either an HTTPS
        URL or a base64 data URI (``data:image/png;base64,...``).
        ``last_frame_uri`` is only honored by the ``ltx-2-3-*`` models.
        """
        body = _drop_none({
            "image_uri": image_uri,
            "prompt": prompt,
            "model": model,
            "duration": duration,
            "resolution": resolution,
            "fps": fps,
            "generate_audio": generate_audio,
            "camera_motion": camera_motion,
            "last_frame_uri": last_frame_uri,
        })
        return self._request_binary(
            "POST", "/v1/image-to-video",
            json_body=body, out_path=out_path,
            progress_callback=progress_callback,
        )

    def retake(
        self,
        out_path: Path,
        *,
        video_uri: str,
        start_time: float,
        duration: float,
        prompt: Optional[str] = None,
        mode: Optional[str] = None,
        resolution: Optional[str] = None,
        model: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Path:
        """``POST /v1/retake`` -> stream MP4 to *out_path*.

        ``video_uri`` accepts an HTTPS URL or a base64 data URI
        (``data:video/mp4;base64,...``). Set ``start_time=0`` and
        ``duration=<full clip length>`` to coerce the endpoint into a
        full-clip video-to-video regen. ``mode`` defaults to
        ``replace_audio_and_video`` server-side.
        """
        body = _drop_none({
            "video_uri": video_uri,
            "start_time": start_time,
            "duration": duration,
            "prompt": prompt,
            "mode": mode,
            "resolution": resolution,
            "model": model,
        })
        return self._request_binary(
            "POST", "/v1/retake",
            json_body=body, out_path=out_path,
            progress_callback=progress_callback,
        )

    # -- async HDR endpoint --------------------------------------------------

    def submit_video_to_video_hdr(self, *, video_uri: str) -> JobResult:
        """``POST /v2/video-to-video-hdr`` -> 202 with job id + created_at."""
        body = {"video_uri": video_uri}
        data = self._request_json("POST", "/v2/video-to-video-hdr", json_body=body)
        return _parse_job(data)

    def get_video_to_video_hdr(self, job_id: str) -> JobResult:
        """``GET /v2/video-to-video-hdr/{id}`` -> current job status."""
        data = self._request_json("GET", f"/v2/video-to-video-hdr/{job_id}")
        return _parse_job(data)

    # -- polling -------------------------------------------------------------

    def poll_video_to_video_hdr(
        self,
        job_id: str,
        *,
        interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
        on_progress: Optional[Callable[[JobResult], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> JobResult:
        """Poll an HDR job until ``status`` is ``completed`` or ``failed``.

        Exponential backoff on 429 (RPM), capped at
        :data:`MAX_POLL_INTERVAL_SECONDS`. Returns the final JobResult --
        callers are expected to inspect ``status`` rather than rely on
        exceptions for the ``failed`` case.
        """
        deadline = now() + timeout
        backoff = max(interval, 1.0)
        while True:
            try:
                job = self.get_video_to_video_hdr(job_id)
            except LTXVRateLimitError as exc:
                backoff = min(backoff * 2, MAX_POLL_INTERVAL_SECONDS)
                log.warning(
                    "LTX rate-limited while polling %s (%s); sleeping %.1fs",
                    job_id, exc.code, backoff,
                )
                if now() + backoff > deadline:
                    raise
                sleep(backoff)
                continue

            backoff = max(interval, 1.0)  # success resets backoff
            if on_progress is not None:
                try:
                    on_progress(job)
                except Exception:  # noqa: BLE001 -- progress callback must never break polling
                    log.exception("on_progress callback raised; continuing")

            if job.status in JobStatus.TERMINAL:
                return job

            if now() + interval > deadline:
                raise LTXVError(
                    f"timed out after {timeout:.0f}s waiting for HDR job {job_id} "
                    f"(last status: {job.status})"
                )
            sleep(min(interval, MAX_POLL_INTERVAL_SECONDS))

    # -- raw download helper -------------------------------------------------

    def download(
        self,
        url: str,
        out_path: Path,
        *,
        timeout: float = 600.0,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Path:
        """Download an arbitrary URL (e.g. an HDR ``exr_frames_url`` ZIP).

        Uses a fresh, unauthenticated request so signed URLs aren't double-
        signed; the LTX-issued result URLs are presigned and time-limited.
        """
        try:
            with requests.get(url, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(out_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if chunk:
                            fh.write(chunk)
                            if progress_callback is not None:
                                try:
                                    progress_callback(len(chunk))
                                except Exception:  # noqa: BLE001
                                    log.exception("progress_callback raised; continuing")
        except requests.RequestException as exc:
            raise LTXVError(f"network error downloading {url}: {exc}") from exc
        return out_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drop_none(d: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop keys whose values are ``None``; LTX rejects nulls in some fields."""
    return {k: v for k, v in d.items() if v is not None}


def _parse_job(data: Any) -> JobResult:
    if not isinstance(data, dict):
        raise LTXVError(f"unexpected job response shape: {data!r}", response_body=data)
    return JobResult(
        id=data.get("id", ""),
        status=data.get("status", ""),
        created_at=data.get("created_at"),
        completed_at=data.get("completed_at"),
        result=data.get("result") if isinstance(data.get("result"), dict) else None,
        error=data.get("error") if isinstance(data.get("error"), dict) else None,
        raw=data,
    )


def hdr_result_url(job: JobResult) -> Optional[str]:
    """Extract the EXR-frames URL from a completed HDR job's ``result`` object.

    The LTX docs note that "available keys depend on the endpoint" -- for the
    HDR endpoint, the documented field is ``exr_frames_url``. We fall back to
    a small set of plausible alternates so a server-side rename surfaces as
    a clear error rather than a silent ``None``.
    """
    if job.result is None:
        return None
    for key in ("exr_frames_url", "frames_url", "url"):
        value = job.result.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def iterate_pages(
    iter_call: Callable[..., Dict[str, Any]],
    *,
    page_size: int = 50,
) -> Iterator[Dict[str, Any]]:
    """Generic cursor-pagination helper. Not used today, kept for parity with
    other studio API clients in case the LTX docs grow a list endpoint."""
    cursor: Optional[str] = None
    while True:
        page = iter_call(limit=page_size, cursor=cursor)
        yield page
        cursor = page.get("next_cursor") or page.get("cursor")
        if not cursor:
            return

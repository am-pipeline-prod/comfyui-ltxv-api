"""Tests for ltxv_api.client error mapping and HDR polling logic.

These tests do NOT hit the live LTX API. The smoke test in
``tests/smoke_live.py`` handles end-to-end validation against the real service.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ltxv_api import (  # noqa: E402
    LTXVAuthError,
    LTXVBillingError,
    LTXVClient,
    LTXVError,
    LTXVNotFoundError,
    LTXVRateLimitError,
    LTXVSafetyError,
    LTXVValidationError,
    JobStatus,
)
from ltxv_api.client import _parse_job, hdr_result_url  # noqa: E402


def _mock_response(status_code: int, json_body=None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.content = b"x" if (json_body or text) else b""
    resp.text = text
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


@pytest.fixture
def client():
    """Real Session so attributes like `.headers` exist; mock `.request` per test."""
    session = requests.Session()
    session.request = MagicMock(name="request")
    return LTXVClient("test-key", session=session)


@pytest.mark.parametrize(
    "status, exc_cls",
    [
        (400, LTXVValidationError),
        (401, LTXVAuthError),
        (402, LTXVBillingError),
        (404, LTXVNotFoundError),
        (422, LTXVSafetyError),
        (429, LTXVRateLimitError),
        (500, LTXVError),
        (503, LTXVError),
    ],
)
def test_status_code_maps_to_typed_error(client, status, exc_cls):
    body = {"type": "error", "error": {"type": "TEST_CODE", "message": f"hit {status}"}}
    client._session.request.return_value = _mock_response(status, body)
    with pytest.raises(exc_cls) as info:
        client.get_video_to_video_hdr("any-id")
    assert info.value.code == "TEST_CODE"
    assert info.value.status_code == status


def test_2xx_returns_json(client):
    body = {
        "id": "job_x",
        "status": "pending",
        "created_at": "2026-04-30T00:00:00Z",
    }
    client._session.request.return_value = _mock_response(200, body)
    job = client.get_video_to_video_hdr("job_x")
    assert job.id == "job_x"
    assert job.status == "pending"


def test_authorization_header_set_to_bearer():
    """The LTX API expects ``Authorization: Bearer <key>``, not ``x-api-key``."""
    c = LTXVClient("the-key")
    assert c._session.headers["Authorization"] == "Bearer the-key"


def test_drop_none_in_body_does_not_send_optional_fields(client, tmp_path):
    """Optional fields default to None in the public surface; we must not
    serialise them as ``null`` (LTX rejects ``null`` for some fields)."""
    # Stream-mode mock: we never enter the body, just intercept the call.
    response = MagicMock(spec=requests.Response)
    response.status_code = 200
    response.iter_content = lambda chunk_size=None: iter([b""])
    response.__enter__ = lambda s: s
    response.__exit__ = lambda *a: None
    client._session.request.return_value = response

    out_path = tmp_path / "out.mp4"
    client.text_to_video(
        out_path,
        prompt="hello",
        model="ltx-2-fast",
        duration=2,
        resolution="720x1280",
        # fps / generate_audio / camera_motion intentionally omitted
    )
    sent_body = client._session.request.call_args.kwargs["json"]
    assert sent_body == {
        "prompt": "hello",
        "model": "ltx-2-fast",
        "duration": 2,
        "resolution": "720x1280",
    }


def test_parse_job_minimal():
    job = _parse_job({
        "id": "job_x",
        "status": "pending",
        "created_at": "2026-04-30T00:00:00Z",
    })
    assert job.id == "job_x"
    assert job.status == "pending"
    assert job.result is None
    assert job.error is None


def test_parse_job_completed():
    job = _parse_job({
        "id": "job_y",
        "status": "completed",
        "created_at": "2026-04-30T00:00:00Z",
        "completed_at": "2026-04-30T00:01:00Z",
        "result": {"exr_frames_url": "https://cdn.ltx.video/job_y/frames.zip"},
    })
    assert job.status == "completed"
    assert hdr_result_url(job) == "https://cdn.ltx.video/job_y/frames.zip"


def test_hdr_result_url_falls_back_to_alternates():
    """A server-side rename of the result key should still produce a URL."""
    job = _parse_job({
        "id": "job_z",
        "status": "completed",
        "result": {"frames_url": "https://cdn.ltx.video/z.zip"},
    })
    assert hdr_result_url(job) == "https://cdn.ltx.video/z.zip"


def test_hdr_result_url_returns_none_when_no_result():
    job = _parse_job({"id": "job_q", "status": "failed", "error": {"message": "x"}})
    assert hdr_result_url(job) is None


def test_poll_completes(client):
    seq = [
        {"id": "j", "status": "pending"},
        {"id": "j", "status": "processing"},
        {"id": "j", "status": "completed",
         "result": {"exr_frames_url": "https://cdn/j.zip"}},
    ]
    client._session.request.side_effect = [_mock_response(200, b) for b in seq]
    progress = []
    job = client.poll_video_to_video_hdr(
        "j",
        interval=0.0,
        timeout=60.0,
        on_progress=lambda j: progress.append(j.status),
        sleep=lambda _s: None,
    )
    assert job.status == JobStatus.COMPLETED
    assert progress == ["pending", "processing", "completed"]


def test_poll_returns_failed_status_without_raising(client):
    seq = [
        {"id": "j", "status": "processing"},
        {"id": "j", "status": "failed", "error": {"type": "INTERNAL", "message": "boom"}},
    ]
    client._session.request.side_effect = [_mock_response(200, b) for b in seq]
    job = client.poll_video_to_video_hdr(
        "j", interval=0.0, timeout=60.0, sleep=lambda _s: None,
    )
    assert job.status == JobStatus.FAILED
    assert job.error == {"type": "INTERNAL", "message": "boom"}


def test_poll_backs_off_on_rate_limit(client):
    seq = [
        _mock_response(429, {"error": {"type": "RATE_LIMIT", "message": "slow down"}}),
        _mock_response(200, {"id": "j", "status": "completed",
                             "result": {"exr_frames_url": "u"}}),
    ]
    client._session.request.side_effect = seq
    sleeps = []
    job = client.poll_video_to_video_hdr(
        "j", interval=10.0, timeout=600.0, sleep=sleeps.append,
    )
    assert job.status == JobStatus.COMPLETED
    assert len(sleeps) == 1
    assert sleeps[0] >= 10.0  # the backoff sleep


def test_poll_times_out(client):
    client._session.request.return_value = _mock_response(
        200, {"id": "j", "status": "processing"},
    )
    fake_now = iter([0.0, 0.0, 100.0, 100.0, 200.0, 200.0])
    with pytest.raises(LTXVError, match="timed out"):
        client.poll_video_to_video_hdr(
            "j",
            interval=10.0,
            timeout=50.0,
            sleep=lambda _s: None,
            now=lambda: next(fake_now),
        )

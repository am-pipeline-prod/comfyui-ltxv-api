# LTX API reference snapshot

Frozen reference of the four LTX REST endpoints this package wraps, captured
**2026-04-30** from [docs.ltx.video](https://docs.ltx.video).

When Lightricks ships incompatible changes, the diff vs this file is what
contributors should consult first — the live docs may have moved on, but
the implementation in `ltxv_api/client.py` is pinned to the shapes here.

---

## Common

* **Base URL:** `https://api.ltx.video`
* **Auth header:** `Authorization: Bearer <LTXV_API_KEY>`
* **Content-Type (request):** `application/json`
* **Error envelope (all endpoints):**
  ```json
  {
    "type": "error",
    "error": {
      "type": "<machine-readable code>",
      "message": "<human-readable message>"
    }
  }
  ```
* **Common status codes:** `400` validation, `401` auth, `402` billing,
  `422` safety filter, `429` rate limit, `500` server, `503` unavailable,
  `504` timeout.

## Asset URI fields (`image_uri`, `video_uri`, `last_frame_uri`)

Every endpoint that takes an asset accepts either:

1. A publicly-accessible HTTPS URL.
2. A base64 data URI (`data:image/png;base64,...` / `data:video/mp4;base64,...`).
   Convenient but ~33% over-the-wire overhead — prefer URLs for large inputs.

Documented supported image formats: PNG, JPEG, WebP, AVIF, HEIF. (See
[Input Formats](https://docs.ltx.video/input-formats) for current limits.)

---

## 1. `POST /v1/text-to-video`

Sync. Generates a video from a text prompt.

**Request body:**

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `prompt` | string | yes | — | Text describing the desired video. |
| `model` | enum | yes | — | `ltx-2-fast` / `ltx-2-pro` / `ltx-2-3-fast` / `ltx-2-3-pro`. |
| `duration` | integer | yes | — | Seconds. |
| `resolution` | string | yes | — | e.g. `1920x1080`. |
| `fps` | integer | no | `24` | Frame rate. |
| `generate_audio` | boolean | no | `true` | Synced AI-generated audio. |
| `camera_motion` | enum | no | — | `dolly_in` / `dolly_out` / `dolly_left` / `dolly_right` / `jib_up` / `jib_down` / `static` / `focus_shift`. |

**Response:** `200` with `Content-Type: application/octet-stream` (MP4 binary).

---

## 2. `POST /v1/image-to-video`

Sync. Animates a still image.

**Request body:**

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `image_uri` | string | yes | — | URL or base64 data URI for the first frame. |
| `prompt` | string | yes | — | Text describing the animation. |
| `model` | enum | yes | — | Same set as text-to-video. |
| `duration` | integer | yes | — | Seconds. |
| `resolution` | string | yes | — | e.g. `1920x1080`. |
| `fps` | integer | no | `24` | Frame rate. |
| `generate_audio` | boolean | no | `true` | Synced AI-generated audio. |
| `last_frame_uri` | string | no | — | Final frame for end-frame interpolation. **`ltx-2-3-*` models only.** |
| `camera_motion` | enum | no | — | Same set as text-to-video. |

**Response:** `200` with `Content-Type: application/octet-stream` (MP4 binary).

---

## 3. `POST /v1/retake`

Sync. Officially Lightricks' "retake" feature — replaces a section of a
source video with newly-synthesised content. Coercible into a full-clip
video-to-video regen by setting `start_time=0, duration=<full clip length>`.

**Request body:**

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `video_uri` | string | yes | — | URL or base64 data URI for the input video. |
| `start_time` | number (seconds) | yes | — | Section start. |
| `duration` | number (seconds) | yes | — | Section length. **Min 2.0.** |
| `prompt` | string | no | — | Description of desired changes. |
| `mode` | enum | no | `replace_audio_and_video` | `replace_audio` / `replace_video` / `replace_audio_and_video`. |
| `resolution` | enum | no | (auto) | `1920x1080` or `1080x1920`. |
| `model` | enum | no | `ltx-2-3-pro` | `ltx-2-pro` or `ltx-2-3-pro`. |

**Server-side constraints:**

* Min input frames: 73 (~3s @ 24fps).
* Max input resolution: 3840×2160 (4K).

**Response:** `200` with `Content-Type: application/octet-stream` (MP4 binary).

---

## 4. `POST /v2/video-to-video-hdr` (async)

Regenerates an SDR input as a sequence of scene-linear EXR frames. The
endpoint is async — the response is just an acknowledgement that the job
has been queued.

**Request body:**

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `video_uri` | string | yes | — | URL or base64 data URI for the SDR input. |

**Response:** `202 Accepted` with body:

```json
{
  "id": "<job-id>",
  "created_at": "<ISO 8601 timestamp>"
}
```

**Tier limits (per current LTX docs):**

| Tier | Max frames | ~Duration @ 24fps |
|---|---|---|
| ≤1080p | 181 | ~7s |
| ≤1440p | 101 | ~4s |
| ≤4K | 41 | ~2s |

**Pricing (per current LTX docs, captured 2026-04-30):**

* 1080p: **$0.20/s**
* 1440p: **$0.40/s**
* 4K: **$0.80/s**

These rates may change — confirm on the LTX developer console before
queuing long inputs.

---

## 5. `GET /v2/video-to-video-hdr/{id}` (async polling)

**Path:** `id` from the `POST /v2/video-to-video-hdr` response.

**Response:**

```json
{
  "id": "<job-id>",
  "status": "pending" | "processing" | "completed" | "failed",
  "created_at": "<ISO 8601 timestamp>",
  "completed_at": "<ISO 8601 timestamp>",   // present once terminal
  "result": {                                // present when status == completed
    "exr_frames_url": "<presigned URL to ZIP of EXRs>"
  },
  "error": {                                 // present when status == failed
    "type": "<machine-readable code>",
    "message": "<human-readable message>"
  }
}
```

**Polling guidance (per LTX docs):**

* Poll every 5 seconds.
* Stop when status reaches `completed` or `failed`.
* Status and `exr_frames_url` are available for **24 hours** after the job
  reaches a terminal state. After expiry the endpoint returns `404`.

The client uses exponential backoff on 429 (capped at 30s) plus a 10-minute
total timeout by default.

---

## Footnotes

* The HDR endpoint's docstring says "available keys depend on the endpoint";
  for HDR specifically the documented field is `exr_frames_url`. The client
  also tolerates `frames_url` / `url` as fallbacks so a server-side rename
  surfaces as a clear error rather than a silent `None`.

* The Input Formats page documents the size, duration, and codec limits for
  each `*_uri` field. Those limits are not duplicated here because they
  change more frequently than the field shapes; consult
  [docs.ltx.video/input-formats](https://docs.ltx.video/input-formats) for
  the live numbers.

# Architecture

This document captures the why behind the package layout. The README has
the user-facing usage docs; this is for contributors and future readers.

## Layered structure

```
comfyui-ltxv-api/
├── __init__.py                       # ComfyUI registration only
├── ltxv_api/
│   ├── __init__.py                   # public surface re-exports
│   ├── config.py                     # API key resolution (3-tier)
│   ├── client.py                     # typed HTTP client + polling
│   ├── tensors.py                    # IMAGE ↔ MP4/PNG/data-URI
│   ├── exr_loader.py                 # ZIP → HDR EXR → IMAGE tensor
│   └── nodes/
│       ├── __init__.py
│       ├── _common.py                # widget choices + small helpers
│       ├── text_to_video.py
│       ├── image_to_video.py
│       ├── video_to_video.py         # Retake
│       └── video_to_video_hdr.py     # Async, returns EXR
├── docs/
│   ├── ARCHITECTURE.md               # (this file)
│   └── api-reference-snapshot.md     # frozen API reference
├── example_workflows/                # 4 ready-to-load JSONs
└── tests/                            # unit + descriptions + live smoke
```

The split is:

* **Top-level `__init__.py`** — only what ComfyUI needs to register the
  nodes (`NODE_CLASS_MAPPINGS`, `NODE_DISPLAY_NAME_MAPPINGS`). Everything
  else lives under `ltxv_api/`.
* **`ltxv_api/`** — pure-Python library code, importable as a module
  outside ComfyUI for tests and ad-hoc scripting.
* **`ltxv_api/nodes/`** — ComfyUI-aware code. The node classes are thin
  glue: they parse widget values, call the library, and shape the result
  into the IMAGE/VIDEO/AUDIO/info socket tuple.

The hyphen / underscore split (`comfyui-ltxv-api/` directory containing
`ltxv_api/` package) follows ComfyUI custom-node convention. The top-level
`__init__.py` adds its own folder to `sys.path` so `import ltxv_api` works
under ComfyUI's `importlib.util.spec_from_file_location` loader.

## Three-tier API key resolution

`config.resolve_api_key()` checks, in order:

1. `LTXV_API_KEY` environment variable.
2. Studio env files at `Z:\admin\config\ltx-video.env` (Windows) /
   `/_pipeline/admin/config/ltx-video.env` (Linux). Silently absent on
   non-studio installs.
3. User config TOML at `~/.config/comfyui-ltxv-api/config.toml` (or
   `%APPDATA%\comfyui-ltxv-api\config.toml` on Windows) with one
   `api_key = "..."` field.

The key is never accepted as a node-input widget — widgets get serialised
into saved workflows and screenshots. Reading from env / config file keeps
the secret out of the workflow surface.

The same idiom is used by sister package
[comfyui-beeble-ai](https://github.com/am-pipeline-prod/comfyui-beeble-ai);
both packages can share a studio config directory without collision because
each one looks at its own `<vendor>.env` filename.

## Sync vs async endpoints

Three endpoints are sync (`/v1/...`): the server holds the connection open
until the MP4 is generated and streams it back as the response body. The
client streams the body to a temp file, then the node decodes it.

The HDR endpoint is async (`/v2/...`): submit returns 202 with a job id;
the client polls `GET /v2/video-to-video-hdr/{id}` until status reaches
`completed` / `failed`. The completed response carries `result.exr_frames_url`,
a presigned URL to a ZIP of EXRs (24h availability).

Polling cadence: LTX docs recommend "every 5s". The client starts there
and backs off exponentially up to 30s on 429s; 10 min default ceiling.

## Socket surface — single native type per direction, matching the API

Each endpoint has exactly one native data type at the wire level, and our
node sockets mirror that:

| Endpoint | Wire type | Input socket | Output socket |
|---|---|---|---|
| `/v1/text-to-video` | MP4 (frames + audio) | (none) | `VIDEO` |
| `/v1/image-to-video` | MP4 in MP4 out | `IMAGE` (still) | `VIDEO` |
| `/v1/retake` | MP4 in MP4 out | `VIDEO` | `VIDEO` |
| `/v2/video-to-video-hdr` | MP4 in / EXR ZIP out | `VIDEO` | `IMAGE` batch |

The three sync endpoints are video-native (audio inline in the MP4 container);
HDR is image-native (per-frame EXR sequence, silent, no time base).
Conversion between IMAGE and VIDEO domains happens **exactly at the
IMAGE↔VIDEO boundary**, in ComfyUI's stock `CreateVideo` /
`GetVideoComponents` nodes. The workflow author chooses where to convert.

**This shape went through two false starts** before settling here. v0.1
emitted parallel `IMAGE + VIDEO + AUDIO` sockets on every node (mimicking
[am-pipe-media-io](https://github.com/am-pipeline-prod/am-pipe-media-io)'s
older convention) but the parallel sockets were two views of the same MP4
and pushed redundant state through the graph. v0.2 collapsed everything to
`IMAGE`-only — which silently dropped the API's generated audio, leaking a
real capability of the endpoints. v0.3 (current) is the API-faithful shape:
single native socket per direction, audio rides inline through `VIDEO`,
and the HDR node honestly admits it returns image data.

## VIDEO type bridge

ComfyUI's native `VIDEO` type lives under `comfy_api.latest`
(`InputImpl.VideoFromFile`, `InputImpl.VideoFromComponents`,
`Types.VideoComponents`). The bridge in `video_type.py` is a thin
defensive wrapper modelled on
[am-pipe-media-io's `_core.video_type`](https://github.com/am-pipeline-prod/am-pipe-media-io)
— it tries to import `comfy_api.latest`, and degrades gracefully (`is_available()`
returns False, the wrappers return `None`) when the import fails. So the
package loads on older ComfyUI without raising; it just can't actually emit
a VIDEO socket until ComfyUI is updated.

Three helpers are exposed:

* **`make_video_from_file(path)`** — wraps the downloaded response MP4 in
  `VideoFromFile`. Lazy: downstream consumers (`SaveVideo`,
  `GetVideoComponents`, partner API nodes) re-decode on demand. The MP4
  file lives in ComfyUI's temp dir for the lifetime of the workflow run.
* **`get_components(video)`** — extracts `(images, audio, frame_rate)` from
  an upstream VIDEO. Used by V2V / HDR when the input VIDEO is component-derived
  and we have to re-encode for the request body.
* **`get_source_path(video)`** — best-effort access to the on-disk file
  behind a `VideoFromFile`. When non-None, the request body reads those
  original container bytes directly (preserving audio + skipping a libx264
  round-trip); when None, we fall through to `get_components` + re-encode.

## Tensor I/O

* IMAGE → PNG bytes / data URI: PIL, lossless PNG (`compress_level=3`).
  Used by I2V's `image_uri` (and `last_frame_uri`).
* IMAGE batch → MP4: imageio + libx264 + yuv420p, even/even dimensions
  enforced (libx264 rejects odd-sized frames at yuv420p). Used as the
  fallback when a `VIDEO` input is component-derived (no backing file).
  **Audio is dropped** in this branch — documented on the relevant
  tooltips and in the README.
* MP4 file → base64 data URI: read whole file, base64.b64encode. Used for
  the fast path on file-backed `VIDEO` inputs (preserves audio +
  skips re-encode).
* MP4 → IMAGE batch: imageio + ffmpeg. Drops alpha if present. Only used
  in the response path on the rare branch where `mp4_probe` falls through.
* MP4 metadata-only probe (`mp4_probe`): `iio.immeta(...)` reads the
  container header for `(width, height, frame_count, fps)` without
  decoding the pixel stream. Used by all VIDEO-emitting nodes to populate
  their metadata sockets without a wasted decode.
* EXR → IMAGE: OpenCV `IMREAD_UNCHANGED | IMREAD_ANYDEPTH`, BGR→RGB,
  float32, **no clamp** (HDR pixels can exceed 1.0). Only used by the
  HDR node.

## MP4 file lifecycle

Sync endpoints (T2V / I2V / V2V) stream their MP4 response body into a
temp file in ComfyUI's temp directory (`folder_paths.get_temp_directory()`),
then wrap that path in `VideoFromFile` for the VIDEO socket. **The file
must persist for the lifetime of the workflow run** — `VideoFromFile` is
lazy and reads from disk on demand. ComfyUI manages temp-dir cleanup
across sessions; we don't unlink in the node ourselves.

Input MP4s built from component-derived VIDEO inputs (the re-encode
fallback path) are deleted in `finally:` once the API call returns,
since they were created solely to build the request body.

## Error handling

The client raises a typed hierarchy:

* `LTXVError` — base; carries `.code`, `.status_code`, `.response_body`.
* `LTXVAuthError` (401), `LTXVBillingError` (402),
  `LTXVNotFoundError` (404), `LTXVValidationError` (400),
  `LTXVSafetyError` (422), `LTXVRateLimitError` (429).

Nodes don't catch these by default — they propagate to ComfyUI's queue,
which surfaces them as red node errors with the message intact. The HDR
node treats `terminal.status == "failed"` as `LTXVError` (as opposed to
the ComfyUI auth/billing exceptions, which are HTTP-level).

## Testing strategy

* `tests/test_config.py` — config resolution; mocks env + filesystem.
* `tests/test_client.py` — client; mocks `requests` via a `Session` shim.
* `tests/smoke_load.py` — the package imports under a stripped Python
  environment (no `torch` needed) and registers four nodes.
* `tests/smoke_descriptions.py` — every node's `INPUT_TYPES` /
  `RETURN_TYPES` are well-formed.
* `tests/smoke_live.py` — **billable**, gated behind an env var.
  Runs the cheapest possible call against each endpoint.

The non-live tests should pass in CI without a key. The live test exists
mostly to catch breaking API changes from Lightricks; it's manually
invoked.

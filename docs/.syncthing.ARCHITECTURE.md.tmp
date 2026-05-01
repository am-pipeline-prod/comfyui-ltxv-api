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
│   ├── tensors.py                    # IMAGE/VIDEO ↔ MP4/PNG/data-URI
│   ├── exr_loader.py                 # ZIP → HDR EXR → IMAGE tensor
│   ├── video_type.py                 # ComfyUI VIDEO type bridge
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

## VIDEO type bridge

ComfyUI gained a native `VIDEO` type via `comfy_api.latest`. The bridge
in `video_type.py` is the same shape as
[am-pipe-media-io's `_core.video_type`](https://github.com/am-pipeline-prod/am-pipe-media-io/blob/main/am_pipe_media_io/_core/video_type.py)
— it tries to import `comfy_api.latest`, and degrades gracefully (emits
`None` on the VIDEO socket) when the import fails. So the package loads
on older ComfyUI without raising.

* SDR nodes wrap the downloaded MP4 with `VideoFromFile(path)` — no
  re-encode, downstream nodes consume the MP4 directly.
* The HDR node has no source MP4 (it gets EXRs), so it synthesises a
  VIDEO via `VideoFromComponents(VideoComponents(images, fps, audio))`.
  Useful when wiring directly into `SaveVideo`; for HDR-safe export, prefer
  the IMAGE socket and an EXR-aware writer.

## Tensor I/O

* IMAGE → PNG bytes / data URI: PIL, lossless PNG (`compress_level=3`).
* IMAGE batch → MP4: imageio + libx264 + yuv420p, even/even dimensions
  enforced (libx264 rejects odd-sized frames at yuv420p).
* MP4 → IMAGE batch: imageio + ffmpeg, drops alpha if present.
* MP4 file → base64 data URI: read whole file, base64.b64encode.
* EXR → IMAGE: OpenCV `IMREAD_UNCHANGED | IMREAD_ANYDEPTH`, BGR→RGB,
  float32, **no clamp** (HDR pixels can exceed 1.0).

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

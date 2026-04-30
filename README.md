# ComfyUI LTXV API

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-custom%20node-7d3aed)](https://github.com/comfyanonymous/ComfyUI)

ComfyUI custom nodes wrapping the [Lightricks LTX REST API](https://docs.ltx.video).

This package is **API-only** — it sends requests to `api.ltx.video` and
decodes the responses into ComfyUI tensors. It does **not** run inference
locally. If you want to host LTX yourself (32 GB+ VRAM, ~100 GB of model
weights), use the official local-inference repo instead:
[Lightricks/ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo).

## Nodes

| Node | Endpoint | Output |
|---|---|---|
| **LTXV API Text to Video** | `POST /v1/text-to-video` | MP4 → IMAGE batch + VIDEO + AUDIO |
| **LTXV API Image to Video** | `POST /v1/image-to-video` | MP4 → IMAGE batch + VIDEO + AUDIO |
| **LTXV API Video to Video (Retake)** | `POST /v1/retake` | MP4 → IMAGE batch + VIDEO + AUDIO |
| **LTXV API Video to Video HDR** ⭐ | `POST /v2/video-to-video-hdr` (async) | EXR ZIP → float32 IMAGE batch (HDR) + synthesised VIDEO |

All nodes appear under the **LTXV API** category in the ComfyUI node menu.
Every node emits both `IMAGE` and `VIDEO` sockets in parallel — wire whichever
is more convenient for your downstream graph.

## Installation

### Via ComfyUI-Manager (once published to the Registry)

Search for `comfyui-ltxv-api` in ComfyUI-Manager and click Install.

### Manual

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/am-pipeline-prod/comfyui-ltxv-api.git
cd comfyui-ltxv-api
pip install -r requirements.txt
```

Restart ComfyUI. You should see "LTXV API" in the node menu.

### EXR support (HDR node only)

The HDR node decodes scene-linear EXR frames via OpenCV. The default
`opencv-python` wheels ship with EXR support compiled in, but the codec is
gated on an environment variable:

```bash
export OPENCV_IO_ENABLE_OPENEXR=1
```

The package sets this in-process at import time, so it works out of the box
when ComfyUI loads the node. If you launch ComfyUI from a wrapper script
(systemd unit, Docker entrypoint), set the variable in that wrapper too so
the codec is available before any other custom node tries to read EXR.

## API key setup

Get your key from the LTX developer console (linked from
[docs.ltx.video](https://docs.ltx.video)). The node looks for it in this
order; the first one that resolves wins:

### 1. Environment variable (preferred for scripted / containerised setups)

```bash
export LTXV_API_KEY="ltxv_..."
```

Add this to your shell rc, your systemd `EnvironmentFile=`, or your ComfyUI
launch wrapper. Per-OS examples:

```bash
# Linux: ~/.bashrc or ~/.zshrc
echo 'export LTXV_API_KEY="ltxv_..."' >> ~/.bashrc

# Linux: systemd EnvironmentFile (e.g. /etc/comfyui.env)
LTXV_API_KEY=ltxv_...

# macOS: launchctl
launchctl setenv LTXV_API_KEY "ltxv_..."

# Windows: PowerShell user env var (persistent)
[System.Environment]::SetEnvironmentVariable('LTXV_API_KEY','ltxv_...','User')
```

### 2. Per-user config file

* **Linux / macOS:** `~/.config/comfyui-ltxv-api/config.toml`
* **Windows:** `%APPDATA%\comfyui-ltxv-api\config.toml`

with this single line:

```toml
api_key = "ltxv_..."
```

### 3. Studio config (am-pipeline-prod only)

Internal users on the am-pipeline-prod NAS don't need to do anything: the
key already lives at `Z:\admin\config\ltx-video.env` (Windows) /
`/_pipeline/admin/config/ltx-video.env` (Linux / OVH). The node picks it
up automatically.

> **Why not a node-input widget?** Widgets get baked into saved workflow
> JSON files and into screenshots people post on Discord. Reading from
> env / config file keeps the key out of the workflow surface.

## Pricing reminder

LTX bills **per second of generated video**. Confirm current rates on
[the LTX docs](https://docs.ltx.video) and your developer console before
queuing long inputs. Captured rates at the time of writing (subject to
change):

* `ltx-2-fast` / `ltx-2-3-fast`: lower cost, lower quality.
* `ltx-2-pro` / `ltx-2-3-pro`: higher cost, higher quality.
* HDR (`v2/video-to-video-hdr`): **$0.20/s at 1080p, $0.40/s at 1440p,
  $0.80/s at 4K** — also subject to per-tier frame caps (1080p: 181 frames,
  1440p: 101, 4K: 41).

The HDR node's display name carries a ⭐ to remind you to double-check
billing before each run.

## Quick examples

The four endpoints in their simplest forms:

### Text to Video

1. Drop **LTXV API Text to Video**.
2. Set `prompt`, `model = ltx-2-fast`, `duration = 4`, `resolution = 1280x720`.
3. Wire `video` output → `Save Video` (or `AM Write Video`).
4. Queue Prompt.

### Image to Video

1. Drop **Load Image** → wire to **LTXV API Image to Video** `image`.
2. Set `prompt`, `model`, `duration`, `resolution`.
3. (Optional, ltx-2-3 models only) wire a second **Load Image** to `last_frame`
   for end-frame interpolation.
4. Queue Prompt.

### Video to Video (Retake)

1. Wire any VIDEO-emitting node (Read Video, another LTXV node) to
   **LTXV API Video to Video (Retake)** `video`. Or use the `image` (IMAGE
   batch) socket, or paste a public URL into `video_url`.
2. Set `prompt`, `model = ltx-2-3-pro`, `start_time = 0`,
   `duration = <full clip length>` for a full-clip regen. The endpoint
   minimum is 2.0s and the input must be at least 73 frames.
3. `mode` controls which streams to regenerate (`replace_audio_and_video`,
   `replace_video`, `replace_audio`).
4. Queue Prompt.

### Video to Video HDR ⭐

1. Wire input the same way as the Retake node (video / image / video_url).
2. Set `output_fps` to the rate you want the synthesised VIDEO socket to
   carry — the EXR ZIP itself doesn't have a time base.
3. Queue Prompt. The node submits the async job, polls until terminal,
   downloads the EXR ZIP, and emits a float32 IMAGE batch.
4. Wire `image` → an EXR-aware writer (e.g. **AM Write Image** with
   `ext=exr`) for HDR-safe export. The synthesised `video` socket is
   useful when you only need an SDR preview encode downstream.

Four ready-to-load workflow JSONs live in
[`example_workflows/`](example_workflows/).

## HDR scene-linear EXR — color-space caveat

Lightricks documents the HDR EXR output as **"scene-linear 16-bit"** and
stops there. The blog post, the model card, and the API docs all decline
to specify primaries (Rec.709? Rec.2020? AWG3? AP0? AP1?).

What the upstream source code actually does (per [Lightricks/ComfyUI-LTXVideo
hdr.py](https://github.com/Lightricks/ComfyUI-LTXVideo/blob/master/hdr.py)):

* Applies the standard **ARRI LogC3 EI 800** inverse transfer.
* **No color matrix** — the docstring claims only "linear [0, inf)".

Working assumption for OCIO / pipeline integration: treat as
**linear ARRI Wide Gamut 3 (AWG3)** (the canonical companion primaries
to LogC3). Apply an `AWG3 → ACEScg` or `AWG3 → Rec.709` IDT downstream
and verify visually against a reference. If saturation feels off, retag
as linear Rec.709 and compare — this is a 30-second A/B test.

**Do not assume linear sRGB / Rec.709 by default just because EXR readers
often default to it.** The file does not carry that interpretation.

This is a known limitation of the current beta-era release; worth
re-checking if Lightricks publishes a colour-management note in a later
version.

## Inputs reference

For the verbatim API field reference, see
[`docs/api-reference-snapshot.md`](docs/api-reference-snapshot.md). The most
commonly tweaked fields:

* **`model`** — `ltx-2-fast` / `ltx-2-pro` / `ltx-2-3-fast` / `ltx-2-3-pro`
  for sync endpoints; `ltx-2-pro` / `ltx-2-3-pro` for retake.
* **`duration`** — seconds. Billed per second.
* **`resolution`** — built-in dropdown plus a `(custom)` fallback for
  non-listed sizes.
* **`generate_audio`** — defaults to true. Off saves a small amount on
  generation time and produces a silent MP4.
* **`camera_motion`** — optional. `(unset)` lets the model pick.

## Outputs

All four nodes emit the same socket shape (so workflows can swap one node
for another without rewiring):

| Socket | Type | Notes |
|---|---|---|
| `image` | IMAGE | `[N, H, W, 3]` float32. SDR endpoints clamp to `[0, 1]`; the HDR node returns scene-linear values that may exceed 1.0. |
| `video` | VIDEO | Native ComfyUI VIDEO. SDR nodes wrap the downloaded MP4 (lazy decode). The HDR node synthesises one from components. |
| `audio` | AUDIO | Audio track when present. Silent stub on the HDR node and on SDR nodes that ran with `generate_audio=False`. |
| `info` | STRING | Human-readable summary of dimensions / fps / frame count / model / job id. |
| `width`, `height` | INT | Frame dimensions. |
| `frame_rate` | FLOAT | MP4 frame rate (SDR nodes) / output_fps (HDR node). |
| `frame_count` | INT | Number of frames in the IMAGE batch. |

## Troubleshooting

**`No LTX API key found`** — see [API key setup](#api-key-setup) above.

**`HTTP 401`** — your key is invalid or revoked. Check the LTX developer
console.

**`HTTP 402`** — insufficient credits. Top up the account.

**`HTTP 422`** — the prompt or input was rejected by the LTX safety filter.
Soften the prompt and retry.

**`HTTP 429`** — rate limit. The node retries with exponential backoff on
the HDR polling path; sync endpoints surface 429 as an error so you can
adjust your queue cadence.

**HDR job times out** — the default `poll_timeout_seconds` is 600 (10 min).
Long inputs at 4K can take longer; raise the timeout.

**EXR decode fails with "OpenCV failed to read EXR"** — your `opencv-python`
build doesn't ship the EXR codec. The pip wheels do; if you installed from
a system package or a stripped wheel, reinstall with `pip install --upgrade
opencv-python`.

**Base64 request body rejected** — for very large inputs, use the
`video_url` STRING widget instead of the `video` / `image` sockets and
pass a public HTTPS URL.

## Development

```bash
git clone https://github.com/am-pipeline-prod/comfyui-ltxv-api.git
cd comfyui-ltxv-api
pip install -r requirements.txt
# Run unit tests (no live API calls):
python -m pytest tests/
```

Architecture notes: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

API reference snapshot (frozen at the time the package was written):
[`docs/api-reference-snapshot.md`](docs/api-reference-snapshot.md).

## Roadmap (post-v1)

Out of scope for v1 (deliberately, to keep the surface small). Tracked here
for future iterations:

* `audio-to-video` endpoint wrapper.
* Webhook / streaming response variant for the sync endpoints.
* Custom signed-URL pre-upload (so very large inputs don't have to ride
  base64 OR be hosted publicly elsewhere).
* Batch-of-prompts variant of text-to-video.
* Retry-strategy widget on each node.

## Contributing

Issues and PRs welcome on
[github.com/am-pipeline-prod/comfyui-ltxv-api](https://github.com/am-pipeline-prod/comfyui-ltxv-api).

PR conventions:

* Small, focused PRs preferred.
* If you change the request body of an endpoint, update
  [`docs/api-reference-snapshot.md`](docs/api-reference-snapshot.md) in the
  same PR so future API drift remains visible as a diff.
* Run `python -m pytest tests/` before opening the PR.
* No new runtime dependencies without a matching update to
  `requirements.txt` AND `pyproject.toml`.

## License

[MIT](LICENSE). LTX, LTXV, and Lightricks are trademarks of their respective
owner; this project is community-built and not affiliated with Lightricks.

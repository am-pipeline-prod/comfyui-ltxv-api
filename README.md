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

> **Heads up:** LTX is a **paid API** — every generation bills your LTX
> account. You are responsible for your own usage costs. See the
> [Pricing reminder](#pricing-reminder) section below for current rates.

> **Maintenance:** This is a **self-serve** project. It works for me and
> I'm sharing it as a starting point — feel free to use, fork, or copy
> the code (MIT). I'm **not actively maintaining it**: bug reports + PRs
> may sit unanswered, and LTX API changes may take a while to land here
> (or never). If you depend on it, plan to maintain your own fork.

## Nodes

| Node | Endpoint | Input | Output |
|---|---|---|---|
| **LTXV API Text to Video** | `POST /v1/text-to-video` | (widgets only) | **VIDEO** (MP4, audio inline) |
| **LTXV API Image to Video** | `POST /v1/image-to-video` | **IMAGE** (still) | **VIDEO** (MP4, audio inline) |
| **LTXV API Video to Video (Retake)** | `POST /v1/retake` | **VIDEO** | **VIDEO** (MP4, audio per `mode`) |
| **LTXV API Video to Video HDR** | `POST /v2/video-to-video-hdr` (async) | **VIDEO** (SDR) | **IMAGE** batch (HDR EXR, float32) |

All nodes appear under the **LTXV API** category in the ComfyUI node menu.

**Single native socket per direction, matching the API.** The three sync
endpoints are video-native (the API returns an MP4 with optional audio in
one container), so we emit `VIDEO`. The HDR endpoint is image-native (it
returns a ZIP of per-frame EXR with no audio and no native time base),
so we emit `IMAGE` batch.

**Conversion happens at the IMAGE↔VIDEO boundary, in stock ComfyUI nodes:**

* IMAGE batch → VIDEO downstream: `CreateVideo` (then `SaveVideo`).
* VIDEO → IMAGE batch / AUDIO upstream of consumers that need the parts:
  `GetVideoComponents`.

So a "T2V + see frames" workflow is `T2V → GetVideoComponents → PreviewImage`;
a "T2V + save MP4" workflow is `T2V → SaveVideo` (direct, no helper). The
choice of where to convert lives in the workflow author's hands.

**Audio is preserved** through the VIDEO socket on the three sync nodes
(when `generate_audio=True` for T2V/I2V, or per `mode` for V2V). HDR has
no audio by definition.

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

Double-check billing on the LTX developer console before each HDR run.

## Quick examples

The four endpoints in their simplest forms:

### Text to Video

1. Drop **LTXV API Text to Video**.
2. Set `prompt`, `model = ltx-2-3-fast`, `duration = 4`, `resolution = 1920x1080`.
3. Wire `video` → **SaveVideo** (direct — audio rides through automatically).
4. Queue Prompt.

> **Resolution / fps note.** LTX maintains a per-`(model, resolution, fps)`
> accepted list and rejects any combination not on it (HTTP 400 with a
> message like `"FPS 24 is not supported by model ltx-2-fast at resolution
> 1920x1080"`). Confirmed-accepted at the time of writing:
> `ltx-2-3-fast` + `1920x1080` + `fps=24` (this is what the i2v / t2v
> example workflows use). Other combinations may also work — try them
> and let the API errors guide you.

### Image to Video

1. Drop **Load Image** → wire to **LTXV API Image to Video** `image`.
2. Set `prompt`, `model`, `duration`, `resolution = 1920x1080`.
3. (Optional, ltx-2-3 models only) wire a second **Load Image** to `last_frame`
   for end-frame interpolation.
4. Wire `video` → **SaveVideo**.
5. Queue Prompt.

### Video to Video (Retake)

1. **Load Video** → wire its `VIDEO` directly into **LTXV API Video to
   Video (Retake)** `video`. Or paste a public URL into `video_url`.
2. Set `prompt`, `model = ltx-2-3-pro`, `start_time = 0`,
   `duration = <full clip length>` for a full-clip regen. The endpoint
   minimum is 2.0s and the input must be at least 73 frames.
3. `mode` controls which streams to regenerate (`replace_audio_and_video`,
   `replace_video`, `replace_audio`).
4. Wire `video` → **SaveVideo**.
5. Queue Prompt.

> **Pristine input audio.** When the `video` socket is backed by an on-disk
> MP4 (Load Video, another LTXV node) the original container bytes are
> uploaded directly — audio preserved, no libx264 re-encode. If the source
> is component-derived (e.g. a workflow that built a VIDEO from frames),
> the request body is re-encoded as video-only MP4 and the input audio is
> dropped on upload. Use `video_url` if pristine audio matters and the
> source isn't file-backed.

### Video to Video HDR

1. **Load Video** → wire its `VIDEO` into the HDR node `video`. Or use
   `video_url`.
2. Set `output_fps` to the rate you want reported on the `frame_rate`
   output — the EXR ZIP itself doesn't have a native time base.
3. Queue Prompt. The node submits the async job, polls until terminal,
   downloads the EXR ZIP, and emits a float32 IMAGE batch.
4. Wire `image` → an EXR-aware writer (e.g. **AM Write Image** with
   `ext=exr`) for HDR-safe export. For an SDR preview encode, chain
   `image` + `frame_rate` into stock **CreateVideo** then **SaveVideo**.

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

The three sync nodes (T2V / I2V / V2V) emit the same shape:

| Socket | Type | Notes |
|---|---|---|
| `video` | VIDEO | Native ComfyUI VIDEO wrapping the response MP4 (lazy-decoded via `VideoFromFile`). Audio rides through inline when generated. |
| `info` | STRING | Human-readable summary of dimensions / fps / frame count / model. |
| `width`, `height` | INT | Frame dimensions. |
| `frame_rate` | FLOAT | MP4 frame rate as probed from the container header. |
| `frame_count` | INT | Number of frames in the MP4 container. |

The HDR node has the same metadata sockets but **outputs `IMAGE` instead
of `VIDEO`**, because the HDR endpoint really does return a ZIP of per-frame
EXRs (no audio, no native time base). To bring HDR back into a VIDEO,
chain `image` + `frame_rate` into stock `CreateVideo` downstream.

| Socket | Type | Notes (HDR only) |
|---|---|---|
| `image` | IMAGE | `[N, H, W, 3]` float32, **scene-linear, NOT clipped to [0, 1]**. |
| `frame_rate` | FLOAT | The `output_fps` widget value (the EXR ZIP has no native fps to probe). |

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
`video_url` STRING widget instead of the `video` socket and pass a
public HTTPS URL.

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

# Deployment — Docker

The Python server ships with a [`Dockerfile`](../Dockerfile) so you can build and
run it without installing FFmpeg, Python, `uv`, or any of the ML dependencies on
the host. This is the recommended way to deploy `yt-mcp` to a server or to run it
in an isolated environment.

> **Scope:** This covers the primary Python server (`server/`). The archived
> TypeScript server is not containerized.

---

## Quick start

```bash
# Build the image (first build downloads PyTorch + models toolchain; ~a few minutes)
docker build -t yt-mcp .

# Run it. The server speaks MCP over stdio, so -i (interactive stdin) is required.
docker run -i --rm yt-mcp
```

On its own, `docker run` just opens a JSON-RPC channel on stdin/stdout and waits —
there is no web UI. In practice an **MCP client** spawns this command for you (see
[MCP integration](#mcp-integration) below).

---

## Why stdio, and why `-i`

`yt-mcp` is not a long-lived network service. Like every stdio MCP server, it
reads JSON-RPC requests from **stdin** and writes responses to **stdout**; the MCP
client owns the process lifecycle. That has two consequences for Docker:

- You **must** pass `-i` (`--interactive`) so the container keeps stdin open.
  Without it the server sees EOF immediately and exits.
- Do **not** pass `-t` (a TTY). MCP framing is raw bytes, not a terminal session;
  a TTY would corrupt the stream.

So the canonical invocation is `docker run -i --rm yt-mcp`.

---

## Persisting the cache

The first call for a video pays a download + transcription cost; later calls for
the same URL are served from disk. Inside the container, all of that lives under
`/data/cache`:

| Path | Contents |
|---|---|
| `/data/cache/videos/<video_id>/` | Downloaded `video.mp4`, `audio.wav`, `info.json` (set via `YT_CACHE_DIR`) |
| `/data/cache/whisper/` | Whisper model weights (Whisper honors `XDG_CACHE_HOME`) |

`/data/cache` is declared as a `VOLUME`. Mount a named volume there so videos and
model weights survive container restarts — otherwise every run re-downloads the
~142 MB `base` model (and re-downloads each video):

```bash
docker run -i --rm -v yt-mcp-cache:/data/cache yt-mcp
```

The container runs as a non-root user (`app`, uid 10001). A **named** volume (as
above) inherits the right ownership automatically. If you bind-mount a host
directory instead, make sure it's writable by uid 10001.

---

## MCP integration

MCP clients launch the server as a subprocess. Point them at `docker` with the
interactive run command. The `--rm` form is intentional: the client starts a
fresh container per session.

**Claude Code:**

```bash
claude mcp add -s user yt-mcp -- docker run -i --rm -v yt-mcp-cache:/data/cache yt-mcp
```

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yt-mcp": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "yt-mcp-cache:/data/cache", "yt-mcp"]
    }
  }
}
```

> The first tool call in a fresh session still downloads the Whisper weights into
> the volume; subsequent sessions reuse them.

---

## Environment variables

These are baked into the image but can be overridden with `-e`:

| Variable | Image default | Description |
|---|---|---|
| `YT_CACHE_DIR` | `/data/cache/videos` | Per-video download cache |
| `XDG_CACHE_HOME` | `/data/cache` | Whisper writes weights to `$XDG_CACHE_HOME/whisper` |

---

## Design decision: CPU-only torch

**The image installs the CPU-only build of PyTorch on purpose.**

Whisper transcription runs on CPU by default (`model="base"`), and that is what
this server uses. The stock `torch` wheel on PyPI, however, declares the entire
NVIDIA CUDA stack (cuDNN, cuBLAS, NCCL, cuFFT, …) as Linux dependencies — roughly
**3 GB compressed, 5-6 GB unpacked**. In a CPU deployment those libraries are
never loaded; they would only inflate the image, the registry storage, and cold
start times.

To avoid that, [`pyproject.toml`](../pyproject.toml) sources `torch` from the
[PyTorch CPU wheel index](https://download.pytorch.org/whl/cpu), scoped to Linux:

```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch-cpu", marker = "sys_platform == 'linux'" }]
```

(`torch` is also declared directly in `[project.dependencies]` because `uv` only
honors `[tool.uv.sources]` for the project's *direct* dependencies — left
transitive via `openai-whisper`, the mapping would be ignored.)

| | CPU-only (this image) | Lockfile-faithful CUDA |
|---|---|---|
| Image size | **~2.8 GB** | ~6 GB |
| Whisper acceleration | CPU only | GPU, *if* host has NVIDIA + `--gpus all` |
| Runs on a plain CPU host | ✅ | ✅ (CUDA libs sit unused) |
| Best for | Most deployments | Dedicated GPU hosts |

The `sys_platform == 'linux'` marker scopes this to the container only. On macOS,
`torch` still resolves from PyPI (already CPU/MPS there), so **local development on
a Mac is unaffected**.

### Building a GPU variant

If you specifically deploy on NVIDIA GPU hardware and want accelerated Whisper:

1. Remove the `[tool.uv.sources]` `torch` mapping (and optionally the
   `pytorch-cpu` index) from `pyproject.toml`.
2. Re-lock: `uv lock`. This re-introduces the CUDA dependencies on Linux.
3. Build on a CUDA-enabled base image (e.g. `nvidia/cuda:*-runtime-*`) instead of
   `python:3.11-slim`, and run with `--gpus all` plus the
   [NVIDIA Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit)
   installed on the host.

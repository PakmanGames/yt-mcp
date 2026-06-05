# syntax=docker/dockerfile:1
#
# yt-mcp — Python MCP server for local YouTube analysis (transcript, frames,
# audio, unified timeline). See README.md and docs/deployment.md.
#
# DESIGN DECISION (issue #1 — CPU-only torch):
#   Whisper runs on CPU by default (model="base"), so this image deliberately
#   installs the CPU-only torch build. pyproject.toml maps torch to the
#   pytorch-cpu wheel index on Linux, which drops the ~3 GB NVIDIA CUDA stack
#   that the stock PyPI torch wheel would otherwise pull in. Result: a ~2.8 GB
#   image that runs anywhere, instead of a ~6 GB GPU image whose CUDA libraries
#   never execute on a CPU host. To build a GPU variant, remove the torch source
#   mapping in pyproject.toml, re-lock, and rebuild on a CUDA base image.
#
# The server speaks MCP (JSON-RPC) over **stdio**, so the container must be run
# interactively — `docker run -i --rm yt-mcp`. It is not a long-lived network
# service; the MCP client owns the process lifecycle over stdin/stdout.

FROM python:3.11-slim

# uv provides fast, lockfile-faithful dependency installs. Copy the static
# binary from the official image; the 0.11 tag tracks the version used locally.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

# System runtime libraries the Python pipeline shells out to or links against:
#   ffmpeg       — yt-dlp remux/download + FFmpeg keyframe extraction
#   libsndfile1  — soundfile / librosa audio decoding
#   libgl1       — provides libGL.so.1 for opencv-python (cv2)
#   libglib2.0-0 — provides libgthread for opencv-python
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# uv build-time behavior:
#   UV_COMPILE_BYTECODE=1 — precompile .pyc up front for faster cold starts
#   UV_LINK_MODE=copy     — copy wheels into the venv (avoids hardlink warnings
#                           when the uv cache lives on a separate mount)
#   UV_PYTHON_DOWNLOADS=0 — use the image's Python 3.11, don't fetch another
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Install dependencies as their own layer for caching. Because the project is
# non-package (`[tool.uv] package = false`), uv installs only the locked deps —
# the lockfiles are the sole inputs it needs here, so this layer is reused on
# every rebuild that doesn't change dependencies.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Application code (changes most often → copied after the dependency layer).
COPY server/ ./server/

# Resolve `python` to the synced virtualenv interpreter.
ENV PATH="/app/.venv/bin:$PATH"

# Persisted cache. Both the downloaded videos/audio (YT_CACHE_DIR) and the
# Whisper model weights (Whisper honors XDG_CACHE_HOME → /data/cache/whisper)
# live under /data/cache, so mounting a single volume there avoids re-downloading
# videos and re-fetching model weights on every container start.
ENV XDG_CACHE_HOME=/data/cache \
    YT_CACHE_DIR=/data/cache/videos

# Run as a non-root user. Only the cache needs to be writable by `app`; the venv
# and source under /app are read/execute-only and stay root-owned (chowning the
# 1.5 GB venv into a new layer would nearly double the image). Create and own the
# cache before declaring the volume so a fresh named volume inherits `app`'s
# write permission.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/cache/videos \
    && chown -R app:app /data/cache
VOLUME ["/data/cache"]
USER app

ENTRYPOINT ["python", "server/main.py"]

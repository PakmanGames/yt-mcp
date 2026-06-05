# yt-mcp — Roadmap & TODO

A running backlog of improvements and ideas for the project. This is a **living document** — add ideas as they come up, move them between sections as priorities shift, and check items off (or move them to [Done](#done)) as they ship.

> **Last reviewed:** 2026-06-05
> **Scope:** the Python server (`server/`) is the primary implementation; the TypeScript server (`src/`) is archived. See [SPEC.md](SPEC.md) for the current contract.

## How to use this file

- Each item has a **Why** (the problem it solves), a rough **Effort** estimate, and **Evidence/links** grounding it in the codebase.
- **Effort:** `S` = a few hours · `M` = a day or two · `L` = multi-day / needs design.
- Sections are ordered by priority: **[Now](#now)** → **[Next](#next)** → **[Later](#later)**. **[Ideas](#ideas)** is an unranked parking lot. **[Done](#done)** records what's shipped.
- Prefer linking a GitHub issue/PR when an item becomes active work.

---

## Now

High-leverage, self-contained, ready to start.

- [ ] **Add CI (GitHub Actions).** `S`
  - **Why:** There is no `.github/workflows/` yet, but the suite has **164 tests** that are fully mocked, network-free, and run in ~27s. Nothing currently prevents a regression from landing on `main`.
  - **Sketch:** Workflow on push/PR that runs `uv sync` then `uv run pytest`, across a Python `3.10`–`3.12` matrix. Optionally cache the uv store.
  - **Evidence/links:** no `.github/`; `pyproject.toml` (uv-managed); [docs/testing.md](docs/testing.md).

- [x] **Provide a Dockerfile.** `M` — resolves issue **#1**.
  - **Why:** Open request to make build/deploy easy; `ffmpeg` is the one heavyweight system dependency, so a prebuilt image removes the main setup friction.
  - **Done:** [`Dockerfile`](Dockerfile) — `python:3.11-slim` + `ffmpeg`/`uv`, `uv sync --frozen --no-dev`, non-root user, stdio entrypoint, `/data/cache` volume persisting both videos (`YT_CACHE_DIR`) and Whisper weights (`XDG_CACHE_HOME`). Ships **CPU-only torch** (CUDA stack dropped → ~2.8 GB vs ~6 GB); see [docs/deployment.md](docs/deployment.md).
  - **Evidence/links:** GitHub issue **#1** ("Create a docker file for easy build and deployment"); `ffmpeg` dependency per [SPEC.md §13](SPEC.md#13-compatibility-and-dependencies).

---

## Next

Valuable, but want a small refactor or a decision first.

- [ ] **Add a console entry point / make it installable.** `M`
  - **Why:** MCP clients must currently point at an absolute path to `server/main.py`. A real entry point enables `uvx yt-mcp` / `uv run yt-mcp` and a one-line MCP registration.
  - **Sketch:** Extract a `main()` from the `if __name__ == "__main__"` block in `server/main.py`; add `[project.scripts] yt-mcp = "server.main:main"`; flip `tool.uv.package` to `true` (or restructure into an importable package). Update README MCP-integration section.
  - **Evidence/links:** `server/main.py:206` (`mcp.run()` under `__main__`); `pyproject.toml` (`package = false`); [SPEC.md §4](SPEC.md#4-mcp-interface).

- [ ] **Decide the TypeScript server's fate.** `S` (decision) + `S` (execution)
  - **Why:** `src/` (8 files), 4 `*.test.ts`, and `package.json`/`pnpm-lock.yaml`/`tsconfig.json`/`vitest.config.ts` are all still tracked for a server marked "archived" — dual-toolchain maintenance and reader confusion for no active benefit.
  - **Options:** (a) snapshot to an `archive/typescript-server` branch or tag and remove from `main` *(recommended)*; or (b) move it under `archive/` to keep it clearly off the primary path.
  - **Evidence/links:** `git ls-files | grep -E '^(src/|.*\.test\.ts|package\.json|tsconfig|vitest)'`.

- [ ] **Add an opt-in live integration smoke test.** `M`
  - **Why:** The unit suite mocks yt-dlp/Whisper/ffmpeg entirely, so it cannot catch a real-world break (e.g. a yt-dlp API change or model-loading failure).
  - **Sketch:** One `@pytest.mark.integration` test against the documented sample video, skipped by default, run on a CI schedule (depends on CI item above). Gate on a network/opt-in flag.
  - **Evidence/links:** manual recipe already in [docs/testing.md](docs/testing.md) ("Live smoke test").

---

## Later

Real improvements, lower urgency.

- [ ] **Cache hygiene.** `M`
  - **Why:** `clear_cache()` exists only as a Python method (not MCP-exposed), and there is no size cap or eviction — `YT_CACHE_DIR` (default `/tmp/yt-analysis-cache`) grows unbounded across sessions.
  - **Sketch:** Add an LRU / total-size limit, and/or expose a cache-management MCP tool (clear by video ID, clear all, report size).
  - **Evidence/links:** `server/utils/downloader.py:115` (`clear_cache`); [SPEC.md §8](SPEC.md#8-caching-model).

- [ ] **Expand the configuration surface.** `S`
  - **Why:** Only `YT_CACHE_DIR` is configurable. Operators may want to set defaults without editing code.
  - **Sketch:** Env-driven defaults for Whisper `model_size`, PySceneDetect `threshold` (27.0), animation threshold (3%), and `min_segment_sec` (5.0). Keep current values as fallbacks; document in SPEC §9.
  - **Evidence/links:** thresholds in [SPEC.md §7](SPEC.md#7-pipeline-algorithms-and-thresholds).

- [ ] **Faster transcription option.** `M`
  - **Why:** Whisper `base` on CPU dominates first-call latency. `faster-whisper` (CTranslate2) is typically several times faster at similar accuracy; GPU is faster still.
  - **Sketch:** Optional backend selection, defaulting to current `openai-whisper` so behavior is unchanged unless opted in.
  - **Evidence/links:** `server/tools/transcript.py`; [SPEC.md §11](SPEC.md#11-resource-and-performance-characteristics).

- [ ] **Structured logging.** `S`
  - **Why:** Errors are surfaced to the client as `{"error": ...}`, but there's no operator-side logging to diagnose failures or measure stage timings. `stderr` is reserved for diagnostics and isn't currently used.
  - **Sketch:** Add `logging` to `stderr` at each pipeline stage (download/transcribe/frames/audio/timeline) with timings; never write to `stdout` (reserved for JSON-RPC).
  - **Evidence/links:** [SPEC.md §4](SPEC.md#4-mcp-interface) (`stderr` is diagnostic-only).

---

## Ideas

Unranked parking lot — capture now, triage later.

- [ ] Optional output cache for tool *results* (not just the download), keyed by video ID + tool + params, to skip recomputation across sessions.
- [ ] Per-tool token-budget guardrails (e.g. warn/refuse when `include_frames=true` on a long video) instead of relying on documentation.
- [ ] Speaker diarization / multi-speaker labeling in the transcript.
- [ ] Support for non-YouTube sources that yt-dlp already handles (gate behind explicit opt-in).
- [ ] Publish the image to a registry (GHCR) once the Dockerfile lands.

---

## Done

- [x] **Documentation overhaul** — added [SPEC.md](SPEC.md) (formal contract) and replaced ASCII art with Mermaid/UML diagrams across the README and `docs/architecture.md`; corrected stale test counts and Whisper sizes. *(PR #3, merged)*
- [x] **Packaging migration to uv** — `pyproject.toml` + `uv.lock` replace `requirements*.txt`; `uv sync` / `uv run pytest` (164 passing) verified.

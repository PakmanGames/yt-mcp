# TypeScript Server — Component Reference (archived)

> **This server is not under active development.**
>
> The Python server (`server/`) is the primary implementation of `yt-mcp`. The TypeScript server is kept in the repository for reference only and will not receive further updates. For setup and usage, see the [README](../README.md) and [docs/python-server.md](python-server.md).

The TypeScript server (`src/`) is a cloud-based MCP server that uses Google's Gemini API to analyze YouTube videos. Gemini understands YouTube URLs natively, so no local video download is required for summarization or Q&A. Frame extraction tools do use yt-dlp + ffmpeg locally.

---

## Quick start

```bash
pnpm install
pnpm build
GEMINI_API_KEY=your-key node dist/index.js
```

Or in development (no build step):
```bash
GEMINI_API_KEY=your-key pnpm dev
```

---

## Component Reference

### `src/index.ts` — MCP Server entry point

Instantiates the MCP server using `@modelcontextprotocol/sdk`, registers `ListTools` and `CallTool` handlers, and routes each tool call to the appropriate client method. Runs on stdio transport.

**Startup sequence:**

1. Create `GeminiVideoClient` — throws and exits if `GEMINI_API_KEY` is missing
2. Create `YouTubeMetadataClient` if `YOUTUBE_API_KEY` or `GEMINI_API_KEY` is set (metadata is optional)
3. Create `ScreenshotExtractor` — dependency checks are deferred until a screenshot tool is actually called
4. Register `ListToolsRequestSchema` handler → returns the `TOOLS` array
5. Register `CallToolRequestSchema` handler → routes by `name`

**Error handling:** All tool call errors are caught and returned as `{ content: [{ type: "text", text: errorText }], isError: true }`. The error type determines the prefix (`"Validation error:"`, `"Dependency error:"`, `"Screenshot extraction failed:"`, etc.).

---

### `src/tools.ts` — Tool definitions

Exports a `TOOLS: Tool[]` array containing the JSON Schema definitions for all five MCP tools. This is what MCP clients use to understand what tools are available and what parameters they accept.

Each tool object has:
- `name` — the string identifier used in `CallToolRequest`
- `description` — natural language description shown to the AI assistant
- `inputSchema` — JSON Schema object with `properties` and `required`

**Defined tools:**

| Tool name | Required params | Optional params |
|-----------|----------------|-----------------|
| `summarize_video` | `youtube_url` | `detail_level` |
| `ask_about_video` | `youtube_url`, `question` | — |
| `extract_screenshots` | `youtube_url` | `count`, `output_dir`, `focus`, `resolution` |
| `get_video_timestamps` | `youtube_url` | `count`, `focus` |
| `extract_frames` | `youtube_url`, `timestamps` | `output_dir`, `resolution` |

To add a new tool, add its definition here and add a matching `case` in `index.ts`.

---

### `src/validators.ts` — Input validation and URL parsing

All input schemas are defined here using [Zod](https://zod.dev). Validation runs before any API calls, ensuring type safety and user-friendly error messages.

**Exported schemas:**

| Schema | Used by |
|--------|---------|
| `YouTubeUrlSchema` | All tools |
| `DetailLevelSchema` | `summarize_video` |
| `ResolutionSchema` | `extract_screenshots`, `extract_frames` |
| `SummarizeInputSchema` | `summarize_video` |
| `AskInputSchema` | `ask_about_video` |
| `ExtractScreenshotsInputSchema` | `extract_screenshots` |
| `GetVideoTimestampsInputSchema` | `get_video_timestamps` |
| `ExtractFramesInputSchema` | `extract_frames` |

**Exported types** (inferred from schemas):
`DetailLevel`, `Resolution`, `SummarizeInput`, `AskInput`, `ExtractScreenshotsInput`, `GetVideoTimestampsInput`, `ExtractFramesInput`

**Exported functions:**

#### `validateYouTubeUrl(url) → string`

Parses the URL through `YouTubeUrlSchema`. Throws a Zod error if invalid. Used internally by `extractVideoId`.

#### `extractVideoId(url) → string`

Validates the URL and extracts the video ID. Handles `youtube.com/watch?v=`, `youtu.be/`, and `youtube.com/shorts/` formats.

**Accepted URL pattern:**
```
/^https?:\/\/(?:www\.)?(?:youtube\.com\/(?:watch\?v=|shorts\/)|youtu\.be\/)[\w-]+/
```

---

### `src/gemini-client.ts` — Gemini API wrapper

#### Error classes

**`VideoAnalysisError`** — Base class for all Gemini-related errors. Surfaced to the user as plain text in the MCP response.

**`VideoAccessError extends VideoAnalysisError`** — Thrown when Gemini cannot access the video (private, geo-restricted, or deleted). Message tells the user to verify the video is public.

#### `TimestampResult` interface

```typescript
interface TimestampResult {
  timestamps: Array<{
    time_seconds: number;      // seconds from start
    time_formatted: string;    // "MM:SS" or "HH:MM:SS"
    description: string;       // what makes this moment visually significant
  }>;
  video_duration_seconds: number;
}
```

#### `GeminiVideoClient`

```typescript
class GeminiVideoClient {
  constructor()  // reads GEMINI_API_KEY and GEMINI_MODEL from environment
}
```

| Method | Returns | Description |
|--------|---------|-------------|
| `summarize(url, detailLevel)` | `Promise<string>` | Summarize video at the specified detail level |
| `ask(url, question)` | `Promise<string>` | Answer a free-form question about the video |
| `extractTimestamps(url, count, focus?)` | `Promise<TimestampResult>` | Identify key visual timestamps |
| `analyze(url, prompt)` | `Promise<string>` | Low-level: send any prompt with the video |

**`analyze()` implementation:** Passes the YouTube URL as `fileData.fileUri` in the Gemini content request. Gemini fetches and processes the video natively — no local download. The current date is injected into the system context so Gemini can reason about recency.

**`extractTimestamps()` implementation:** Sends a structured prompt asking Gemini to return raw JSON (no markdown fences). Parses the response and validates that `timestamps` is an array with valid `time_seconds` values. Throws `VideoAnalysisError` if parsing fails, including the raw response for debugging.

**Detail level prompts:**

| Level | Prompt behavior |
|-------|----------------|
| `"brief"` | 2–3 sentence summary capturing the main point |
| `"medium"` | Key points with MM:SS timestamps |
| `"detailed"` | Comprehensive breakdown: topics, quotes, conclusions |

**Error mapping in `analyze()`:**

| Response pattern | Error thrown |
|-----------------|--------------|
| Contains `"video"` / `"file"` / `"access"` | `VideoAccessError` |
| Contains `"quota"` / `"rate"` | `VideoAnalysisError` (rate limit message) |
| Any other error | `VideoAnalysisError` (generic message) |

---

### `src/screenshot-extractor.ts` — Frame extraction

#### Error classes

**`DependencyError`** — Thrown when `yt-dlp` or `ffmpeg` is not found in PATH. Message includes the install command for macOS and Linux.

**`ScreenshotExtractionError`** — Thrown when a specific frame extraction fails. Optionally includes the `timestamp` (seconds) where the failure occurred.

#### `Screenshot` interface

```typescript
interface Screenshot {
  timestamp_seconds: number;
  timestamp_formatted: string;  // "M:SS"
  description: string;
  base64: string;               // JPEG data encoded as base64 string
  mimeType: "image/jpeg";
  filePath?: string;            // only set when output_dir was specified
}
```

#### `ExtractOptions` interface

```typescript
interface ExtractOptions {
  outputDir?: string;      // directory to save files
  quality?: number;        // JPEG quality 1–100 (default: 85)
  resolution?: Resolution; // "thumbnail"|"small"|"medium"|"large"|"full"
}
```

#### `ScreenshotExtractor`

```typescript
class ScreenshotExtractor {
  async checkDependencies(): Promise<void>
  async extractFrame(url, timestampSeconds, outputPath, quality?, resolution?): Promise<void>
  async extractScreenshots(url, timestamps, options?): Promise<Screenshot[]>
  async extractFramesAtTimestamps(url, timestampSeconds[], options?): Promise<Screenshot[]>
}
```

**`checkDependencies()`:** Runs `which yt-dlp` and `which ffmpeg`. Caches the paths internally. Throws `DependencyError` if either is missing.

**`extractFrame()` implementation:**

1. Runs `yt-dlp -f "bestvideo[height<=N]/best[height<=N]" -g <url>` to get the direct video stream URL (no full download — just the URL)
2. Runs `ffmpeg -ss <timestamp> -i <stream_url> -vframes 1 -q:v <quality> -vf scale=-1:<height>` to extract a single frame
3. The `-ss` flag before `-i` enables fast input seeking — FFmpeg jumps to the timestamp without decoding the whole video

**Resolution to height mapping:**

| Resolution | Height |
|-----------|--------|
| `thumbnail` | 160px |
| `small` | 360px |
| `medium` | 720px |
| `large` | 1080px |
| `full` | Original (no scaling applied) |

**`extractScreenshots()` behavior:**
- Creates a temp directory if no `outputDir` is given
- Processes timestamps sequentially to avoid hammering the stream CDN
- Partial failure tolerance: if some timestamps fail, returns the successful screenshots and logs errors; only throws if *all* timestamps fail
- Temp files are cleaned up when no `outputDir` is specified

**`extractFramesAtTimestamps()`:** Convenience wrapper that converts a plain `number[]` of seconds into the `{time_seconds, time_formatted, description}` format expected by `extractScreenshots()`.

---

### `src/youtube-metadata.ts` — YouTube Data API client

#### `VideoMetadata` interface

```typescript
interface VideoMetadata {
  title: string;
  channelTitle: string;
  description: string;
  publishedAt: string;    // ISO 8601 datetime string
  thumbnailUrl: string;   // URL to high-resolution thumbnail
}
```

#### `YouTubeMetadataClient`

```typescript
class YouTubeMetadataClient {
  constructor(apiKey: string)
  async getMetadata(youtubeUrl: string): Promise<VideoMetadata>
}
```

Uses the official `googleapis` library to call `youtube.videos.list` with `part: ["snippet"]`. The `apiKey` can be a dedicated YouTube Data API v3 key, or the Gemini API key if the YouTube Data API is enabled in the same Google Cloud project.

Metadata enrichment is entirely optional — if `YouTubeMetadataClient` is not initialized or `getMetadata()` throws, the tool falls back gracefully and returns the Gemini analysis without the metadata header.

---

## Running Tests

```bash
pnpm test          # watch mode
pnpm test:run      # single run (CI)
```

Tests are written with [Vitest](https://vitest.dev) and live in `tests/`. The test suite covers:
- `validators.test.ts` — URL parsing and schema validation edge cases
- `gemini-client.test.ts` — timestamp extraction parsing and error mapping
- `screenshot-extractor.test.ts` — frame extraction pipeline and dependency checks
- `tools.test.ts` — tool definition schema completeness

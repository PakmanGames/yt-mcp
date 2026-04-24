# Extending yt-mcp

This guide explains how to add new tools to the Python server (`server/`), which is the primary implementation. The TypeScript server (`src/`) is not under active development; its extension pattern is documented at the [end of this file](#adding-a-tool-to-the-typescript-server--archived) for reference only.

---

## Adding a tool to the Python server

The Python server uses [FastMCP](https://github.com/jlowin/fastmcp). Adding a tool is as simple as decorating a function with `@mcp.tool()`.

### Step 1 — Write the tool function in `server/main.py`

```python
@mcp.tool()
def my_new_tool(
    youtube_url: Annotated[str, "Full YouTube URL"],
    my_param: Annotated[str, "Description of what this param does"] = "default",
) -> str:
    """
    One-line summary of what this tool does.

    Longer explanation that appears in the MCP tool description.
    Include when to use it and any important caveats.
    """
    try:
        video_path, audio_path, info = downloader.download(youtube_url)
    except DownloadError as e:
        return json.dumps({"error": str(e)})
    except FileNotFoundError:
        return json.dumps({"error": "ffmpeg not found — install it: brew install ffmpeg"})

    try:
        result = do_something(video_path, my_param)
    except Exception as e:
        return json.dumps({"error": f"Analysis failed: {e}"})

    return json.dumps({
        "title": info.title,
        "duration": info.duration,
        "result": result,
    }, ensure_ascii=False)
```

**Rules to follow:**
- Always catch `DownloadError` and `FileNotFoundError` from `downloader.download()`
- Always catch exceptions from analysis logic and return `{"error": "..."}` — never let an exception propagate to FastMCP
- Return `json.dumps(...)` with `ensure_ascii=False` so non-ASCII characters (multilingual transcripts, etc.) survive serialization
- Use `Annotated[type, "description"]` for every parameter — FastMCP uses this to build the MCP tool schema automatically

### Step 2 — Add supporting logic in a new or existing module

If your tool needs significant logic, add it to an existing file in `server/tools/` or create a new one:

```python
# server/tools/my_feature.py
"""One-line description of what this module provides."""


def my_analysis(video_path: str, param: str) -> dict:
    """
    Describe what this function does, what it returns, and any edge cases.

    Returns:
        {
            "field_one": "value",
            "field_two": 42
        }
    """
    # implementation here
    ...
```

Then import it in `main.py`:
```python
from server.tools.my_feature import my_analysis
```

### Step 3 — Update `requirements.txt` if you added a dependency

```
my-new-library>=1.0.0
```

### Step 4 — Write and run tests

Add a test file `tests/test_my_feature.py` following the patterns in [docs/testing.md](testing.md). At minimum, cover:

- `DownloadError` from `downloader.download()` → `{"error": "..."}` response
- `FileNotFoundError` (FFmpeg not on PATH) → `{"error": "..."}` response
- Analysis pipeline failure → `{"error": "..."}` response
- Happy path → correct JSON structure returned

```bash
python -m pytest tests/test_my_feature.py -v
```

To run the full suite and confirm nothing is broken:

```bash
python -m pytest
```

For a live smoke test against a real video (no mocks), call your code directly:

```bash
python -c "
from server.utils.downloader import VideoDownloader
from server.tools.my_feature import my_analysis

# Short public video — fast to download and cache
URL = 'https://www.youtube.com/watch?v=M1GYqy0tHV0'
d = VideoDownloader()
vp, ap, info = d.download(URL)
print(my_analysis(vp, 'default'))
"
```

See [**docs/testing.md**](testing.md) for the full testing guide, fixture reference, and mock patterns.

---

## Adding a tool to the TypeScript server (archived)

> The TypeScript server is not under active development. This section is kept for reference only.

The TypeScript server requires changes in three files: `tools.ts`, `validators.ts`, and `index.ts`.

### Step 1 — Add the tool schema to `src/tools.ts`

```typescript
{
  name: "my_new_tool",
  description:
    "One-line description of what this tool does and when to use it.",
  inputSchema: {
    type: "object",
    properties: {
      youtube_url: {
        type: "string",
        description:
          "Full YouTube URL (youtube.com/watch?v=ID, youtu.be/ID, or youtube.com/shorts/ID)",
      },
      my_param: {
        type: "string",
        enum: ["option_a", "option_b"],
        default: "option_a",
        description: "Description of what this param controls",
      },
    },
    required: ["youtube_url"],
  },
},
```

### Step 2 — Add input validation to `src/validators.ts`

```typescript
export const MyNewToolInputSchema = z.object({
  youtube_url: YouTubeUrlSchema,
  my_param: z.enum(["option_a", "option_b"]).default("option_a"),
});

export type MyNewToolInput = z.infer<typeof MyNewToolInputSchema>;
```

### Step 3 — Add the case to `src/index.ts`

Import your schema and add a `case` block in the `switch (name)` statement:

```typescript
import { MyNewToolInputSchema } from "./validators.js";

// ... inside the switch (name) block:

case "my_new_tool": {
  const input = MyNewToolInputSchema.parse(args);

  const result = await geminiClient.ask(
    input.youtube_url,
    `Do something specific: ${input.my_param}`
  );

  return {
    content: [{ type: "text", text: result }],
  };
}
```

### Step 4 — Add supporting logic if needed

If the tool needs substantial logic, create a new file in `src/`:

```typescript
// src/my-feature.ts

export class MyFeatureClient {
  async doSomething(url: string, param: string): Promise<string> {
    // implementation
  }
}
```

Import and instantiate it in `index.ts` alongside the existing clients.

### Step 5 — Add tests

Create `tests/my-feature.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { MyNewToolInputSchema } from "../src/validators.js";

describe("MyNewToolInputSchema", () => {
  it("accepts valid input", () => {
    const input = MyNewToolInputSchema.parse({
      youtube_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    });
    expect(input.my_param).toBe("option_a"); // default applied
  });

  it("rejects invalid URL", () => {
    expect(() =>
      MyNewToolInputSchema.parse({ youtube_url: "not-a-url" })
    ).toThrow();
  });
});
```

Run tests:
```bash
pnpm test:run
```

---

## Choosing where to put new logic

All new tools should go in the Python server. The module to extend depends on what the tool does:

| Your tool needs... | Module |
|---|---|
| Exact transcript text with timestamps | `server/tools/transcript.py` |
| Audio dB levels, tempo, music detection | `server/tools/audio.py` |
| Scene cut detection or frame extraction | `server/tools/frames.py` |
| Unified multi-signal timeline | `server/tools/timeline.py` |
| Download / cache management | `server/utils/downloader.py` |

If your tool needs semantic understanding (summaries, Q&A), implement it in the Python server and pass the structured JSON output as context to an LLM call in your own application layer — rather than using the TypeScript server.

---

## General guidelines

- **Keep tools focused.** Each tool should do one thing well. The `get_full_context` tool is an exception — it exists as a convenience wrapper combining all signals.
- **Return structured JSON.** This lets the AI assistant parse and reason about the data, not just display it.
- **Handle errors at every boundary.** Don't let an FFmpeg failure surface as an unhandled exception — always return `{"error": "..."}`.
- **Document the return schema.** Add a docstring showing example output. This helps both humans and AI assistants understand what to expect.
- **Cache expensive operations.** If your tool runs a slow computation (model inference, video decoding), cache results to disk keyed by video ID. See `VideoDownloader` in `server/utils/downloader.py` for the established pattern.

[English](README.md) | [日本語](README.ja.md) | [中文](README.zh-CN.md)

# muse-airi-bridge

A local Python bridge that connects [AIRI](https://github.com/moeru-ai/airi)'s "OpenAI Compatible" provider to your existing Muse main chat via [muse-cli](https://github.com/nikships/muse-cli). No modifications to AIRI itself are required.

**Only a 40-character invisible identification header plus the raw user input is posted to Muse.**
All decorations added by AIRI (timestamps, reply headers, skill instructions, and `[Context]`) are stripped out, and AIRI's persona prompt and past conversation history are never sent. Muse uses its own configured persona, memory, and built-in tools.

---

## Prerequisites & Supported Environment

- **Prerequisite Tools**:
  - [AIRI](https://github.com/moeru-ai/airi): **v0.14.1** request format is targeted (major decoration formats were also verified against `main` at investigation time, but other versions are not guaranteed).
  - [muse-cli](https://github.com/nikships/muse-cli): `muse-cli==0.3.10`
    - A pinned version is installed into the `uv` virtual environment without modifying your global environment.
    - Existing `~/.config/muse-cli/config.json` and `cookies.txt` are used in **read-only** mode.
- **OS & Runtime**: Windows, Python 3.10+, Git, `uv`

---

## Installation & Startup (PowerShell)

Run the following commands in the directory containing this `README.md`:

```powershell
uv sync --locked
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    $bridgeKey = python -c "import secrets; print(secrets.token_urlsafe(32))"
    (Get-Content .env -Raw).Replace('replace-with-a-long-random-local-key', $bridgeKey) | Set-Content .env -Encoding utf8
}
uv run --env-file .env muse-airi-bridge doctor
uv run --env-file .env muse-airi-bridge serve
```

### Configuration & Operational Notes

- **Local API Key (`MUSE_BRIDGE_API_KEY`)**
  - Set the `MUSE_BRIDGE_API_KEY` from `.env` in AIRI as well.
  - This is an authentication key strictly for the local bridge, not an OpenAI API key (`.env` is gitignored).
- **Connection Diagnostics (`doctor`)**
  - The `doctor` command verifies connectivity solely by fetching history. It never posts messages, creates or updates config files, or prints cookies.
  - If you get an authentication error, refresh your login session using your normal `muse-cli` installation.
- **Upgrading from `0.1.0`**
  - If a `0.1.0` server is running, stop it in its terminal before running `uv sync --locked` and restarting (Windows cannot replace executables while they are in use).
- **Custom Paths**
  - If your `muse-cli` configuration lives in a non-default location, set `MUSE_BRIDGE_CONFIG_DIR` in `.env` (WSL configuration paths are not auto-discovered from Windows).
  - The default state directory is `~/.local/state/muse-airi-bridge` (configurable via `MUSE_BRIDGE_STATE_DIR`).

---

## AIRI Configuration

| Setting | Value |
|---|---|
| Provider | `OpenAI Compatible` |
| API protocol | `Chat Completions` |
| Base URL | `http://127.0.0.1:8000/v1/` |
| API key | `MUSE_BRIDGE_API_KEY` configured in `.env` |
| Model | `muse-airi` |

### Usage Rules in AIRI

- **Speech & Vision**: Configure speech-to-text (STT) and text-to-speech (TTS) on the AIRI side. This bridge handles transcribed text only and does not provide audio or image generation APIs.
- **No Auxiliary Sub-Agents**: Keep normal conversation history enabled, and do not point AIRI's auxiliary features (Autonomous Artist, Vision, background recipes, Spark notifications, etc.) to this bridge. Known auxiliary requests are rejected by the bridge.
- **Function Tools**: AIRI function tools are not executed. Tool definitions with `tool_choice` set to `auto` or `none` are ignored, while required/forced tool calls are rejected with an error. Muse itself decides when and how to search or run tasks.
- **Connection Probe (`ping`)**: AIRI's `ping` validation probe is answered locally with `pong` without posting to Muse. A passing probe does not imply Muse connectivity; use `doctor` or an authenticated `GET /readyz` request to verify the actual Muse connection.

---

## Muse Soul Addendum & Emotion Sync

Before first use, append the full text of the [Soul Addendum Prompt](docs/muse-soul-addendum.md) to your Muse's Soul.

- This project never edits your Muse Soul automatically.
- `docs/muse-soul-addendum.md` contains the actual invisible header and all 9 invisible footers. Copy and paste it directly without passing it through editors or converters that strip zero-width characters.

### How the Invisible Protocol Works

1. **Prepending the Input Header**
   - The bridge prepends a 40-character invisible header encoding the ASCII string `AIRI1` to the extracted user text before posting to Muse.
   - Each byte is encoded MSB-first into 8 characters using `U+200B = 0` and `U+200C = 1`.
2. **Appending the Emotion Footer by Muse**
   - Only when the current user message begins with this invisible header, Muse selects one primary emotion for its response and appends `AE1:` + a 1-character emotion code (5 bytes = 40 invisible characters) at the very end of the reply.

| Code | AIRI Emotion |
|---|---|
| `N` / `H` / `S` / `A` | `neutral` / `happy` / `sad` / `angry` |
| `T` / `U` / `W` | `think` / `surprised` / `awkward` |
| `Q` / `C` | `question` / `curious` |

3. **Collecting the Reply & Converting to an `ACT` Marker**
   - The bridge waits for Muse's first completed reply before returning it via SSE (or JSON); it does not stream tokens live during generation (default timeout: 300s, history poll interval: 1s).
   - It reads the reply from `payload.content`, strips the dedicated trailing footer, and prepends a single `<|ACT {"emotion":{"name":"happy","intensity":0.6}}|>` marker (`intensity` is `1` for `neutral` and `0.6` for all other emotions).
   - It never infers emotion from body text or emoji, makes no extra LLM repair calls, and never reposts messages.

### Fallback Behavior for Malformed Footers

- **Missing Footer**: Treated as `neutral`.
- **Unknown or Corrupted Emotion Code**: If the trailing 40 characters start with the valid 32-character `AE1:` signature, an unknown or corrupted code byte is stripped and treated as `neutral`.
- **Duplicate Footers**: If multiple dedicated footers appear contiguously at the end, the entire trailing footer sequence is stripped and treated as `neutral`.
- **Damaged Signature, Truncated Code, or Trailing Whitespace**: If the signature itself is damaged, the code is truncated, or whitespace/newlines follow the footer, it is not recognized as a footer; the original string is preserved intact and treated as `neutral`. Invisible characters inside the body or inside quotes are never bulk-deleted.
- **Disabling Emotion Markers**: Setting `MUSE_BRIDGE_EMOTIONS=false` in `.env` disables only the insertion of the `<|ACT ...|>` marker (the invisible input header is still sent and recognizable reply footers are still stripped). Visual expression in AIRI depends on the motions supported by your avatar model.

---

## API Specification

| Endpoint | Description |
|---|---|
| `GET /healthz` | Process liveness check (does not check Muse) |
| `GET /readyz` | Muse connectivity check via history read (never posts) |
| `GET /v1/models` | List local bridge models |
| `POST /v1/chat/completions` | Chat endpoint (supports both JSON and SSE streaming) |

### Authentication & Networking

- All endpoints except `/healthz` require an `Authorization: Bearer <local-key>` header.
- CORS preflight (`OPTIONS`) requests respond without authentication. CORS allows all origins (`*`), uses no cookies, and requires a Bearer key. By default, the server binds to loopback (`127.0.0.1`) only.

### Supported Parameters & Request Routing

- Implements an **AIRI-specific subset** of Chat Completions, not a general-purpose stateless LLM API.
- **Input Envelope Verification**: Normal chat inputs must begin with AIRI v0.14.1's datetime prefix (`[YYYY-MM-DD HH:MM] `). When `system` or `developer` messages are present, the bridge verifies that they match AIRI's standard chat prompt format. There is no fallback that guesses and forwards undecorated plain user text.
- **Accepted Parameters**: Supports string or text-part array `messages`, `stream`, and `n=1`. Accepts `temperature`, `top_p`, `max_tokens`, and `max_completion_tokens` for compatibility, but ignores them because they cannot be applied to Muse. Token `usage` is omitted.
- **Unsupported Features**: `stop`, structured output (`response_format`), tool-result continuations, image/audio binary/file inputs, and the Responses API are rejected.
- **Local Probe Detection**: Identified by the exact verified shape: a single `user` message `"ping"`, token limit `16`, non-streaming, with no extra instructions or tools. A `"ping"` typed in normal chat carries a datetime prefix and is sent to Muse as usual. The `X-Muse-Bridge-Response` header distinguishes `local-probe` from `muse`.

---

## Retries & Recovery from Interruption

### Serialized Turns & Conflict Prevention

- Only one new turn is processed at a time. Do not post simultaneously from the Muse Web UI or another client; if another user message appears in history while waiting, the bridge raises a conflict error (`409 / muse_interference`).
- New posts are also blocked if an unfinished user turn is already visible in Muse's main chat, preventing a late reply from an earlier turn from being mistaken for the current answer.

### SQLite Journal & Data Privacy

- The bridge records the request hash, original text hash, outbound header+text hash (`wire_hash`), protocol version, pre-send history sequence (`baseline`), job state, and collected reply in SQLite.
- Retries of an identical request share the in-flight job or replay a completed result cached within 10 minutes.
- Raw user inputs and credentials are never stored, but **reply text (including the invisible footer) is stored in the state database**. Treat the state directory (and any pre-migration backup) as sensitive conversation data.

### Automatic Database Migration from `0.1.0`

- A `0.1.0` database is migrated automatically on startup after creating a backup at `journal.pre-v2.sqlite3` in the state directory.
- Legacy jobs are recorded as `raw-v0` and new jobs as `invisible-v1`:
  - **Legacy Pending Jobs**: Block new posts until reconciled (`reconcile`) or explicitly cleared (`clear-pending`), and are never upgraded or reposted with the new header protocol.
  - **Legacy Completed Cache**: Replayed as `neutral` within the 10-minute TTL without re-interpreting invisible footers.
  - **Expired Legacy Cache & Failed Jobs**: Retained as cleared tombstone records (without reply text) to prevent accidental reposting under the new protocol. Send a new message in AIRI rather than retrying the old request.
- Under `invisible-v1`, if `payload.content` is missing from a Muse event, the bridge raises `muse_content_missing` rather than guessing from display fields (if already posted, the job enters `uncertain` and is not auto-reposted). Only legacy `raw-v0` reconciliation preserves the `0.1.0` display-field fallback order.

### Client Disconnections & Managing Pending Jobs

- Disconnecting an HTTP client does not cancel Muse's work; once posted, the background worker continues polling to collect the reply.
- After a timeout, an uncertain send error, or a process restart during an active turn, the job is held as `uncertain` rather than automatically resent.

```powershell
# Safe to run even while the server is running (prints job IDs and states only, never conversation text):
uv run --env-file .env muse-airi-bridge pending

# Stop the server before running the commands below.
# Match an uncertain job against recent Muse history to recover its completed reply (never posts):
uv run --env-file .env muse-airi-bridge reconcile JOB_ID

# Abandon an uncertain job only after confirming Muse may still be working:
uv run --env-file .env muse-airi-bridge clear-pending JOB_ID --acknowledge-muse-may-still-be-working
```

- Clearing a pending job (`clear-pending`) does not stop Muse on the remote side. After clearing, create a new turn in AIRI instead of resending the cleared request (immediate retries of a cleared request are rejected).
- Starting multiple bridge processes against the same state directory is also blocked by a file lock.

---

## V1 Limitations

- Proactive notifications or follow-up messages arriving after the first completed reply are not pushed to AIRI.
- Because AIRI does not include message IDs in API requests, sending the exact same text within the same minute with conversation history length set to `0` cannot be distinguished.
- Collisions where a user manually types internal delimiter strings such as `\n[Context]\n` are not handled in v1.
- Request classification relies on AIRI v0.14.1's prompt structure. If an AIRI update changes its prompt templates, update the extraction rules and rejection tests before use.
- `reconcile` inspects the most recent 100 history events; reconciliation may fail if more than 100 events occurred since the turn was posted.

---

## Development & Repository Management

```powershell
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
git status
```

- `pyproject.toml`, `uv.lock`, and `.python-version` are tracked in Git. `.venv`, credentials (`.env`), the state database, and build artifacts (`dist/`) are gitignored.
- The test suite includes black-box HTTP/SSE contract tests.
- **Final Verification on Your Environment**:
  1. Append [docs/muse-soul-addendum.md](docs/muse-soul-addendum.md) to your Muse's Soul.
  2. Send a short message from AIRI.
  3. Confirm that a single "invisible header + raw input" message appears in Muse history, and that the trailing emotion footer is converted into an `ACT` marker driving AIRI's text display, TTS, lip-sync, and avatar expression.
- See [docs/design.md](docs/design.md) for architecture and source references, and [docs/verification.md](docs/verification.md) for completed verification results and remaining manual checks.

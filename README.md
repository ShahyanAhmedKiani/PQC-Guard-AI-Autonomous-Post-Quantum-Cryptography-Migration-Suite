# PQC-Guard AI

An autonomous, multi-agent pipeline that scans legacy Python codebases for
vulnerable cryptography (RSA/ECC), refactors it to NIST Post-Quantum
Cryptography (ML-KEM-512, via `liboqs-python`) using an LLM, and verifies the
result by executing it in an isolated sandbox — with automatic retry on
failure.

## Architecture

```
┌───────────────┐      ┌────────────────┐      ┌───────────────┐
│  Scanner       │ ───▶ │  Refactor       │ ───▶ │  Tester        │
│  (static scan) │      │  (LLM rewrite)  │      │  (sandbox exec)│
└───────────────┘      └────────────────┘      └───────┬────────┘
                              ▲                          │ fail
                              └──────── retry (max 2) ───┘
```

- **Backend**: FastAPI (`backend/main.py`, `backend/agents.py`), streaming
  agent events over a WebSocket (`/ws/pipeline`).
- **Frontend**: React + Vite + Tailwind (`frontend/src/App.jsx`) — a
  split-screen dashboard: file upload / before-after code on the left, a
  live "Agent Terminal" streaming agent logs on the right.
- **LLM**: a fallback chain — **Groq -> OpenRouter -> Gemini** by default,
  configured via `LLM_PROVIDER_CHAIN` in `.env`. All three are called through
  the OpenAI SDK against their OpenAI-compatible endpoints. If a provider's
  call errors out, times out, or returns a suspiciously weak/empty/refusal
  response, the Refactor agent automatically retries the same request on the
  next provider in the chain — and logs each attempt to the Agent Terminal.
- **PQC primitive**: `ML-KEM-512` via `liboqs-python`.

## Quick start (Docker)

1. Copy the env template and add your API key(s):
   ```bash
   cp backend/.env.example backend/.env
   # edit backend/.env — fill in as many as you have, in any combination:
   #   GROQ_API_KEY        (console.groq.com/keys)        — tried first
   #   OPENROUTER_API_KEY  (openrouter.ai/keys)            — first fallback
   #   GEMINI_API_KEY      (aistudio.google.com/apikey)    — final fallback
   # You only need ONE key to run the app; more keys = more resilient fallback.
   ```
2. Build and run:
   ```bash
   docker compose up --build
   ```
3. Open the dashboard at **http://localhost:5173**
   (backend API at http://localhost:8000, docs at `/docs`).

## Running without Docker

**Backend:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit it
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

## API surface

| Method | Path              | Description                                   |
|--------|-------------------|------------------------------------------------|
| GET    | `/api/health`     | Health check                                   |
| POST   | `/api/upload`     | Upload a `.py` file, get back its text          |
| POST   | `/api/scan`       | Run only the Scanner agent (no LLM call)        |
| WS     | `/ws/pipeline`    | Full Scanner → Refactor → Tester pipeline for ONE file, streamed |
| WS     | `/ws/batch-pipeline` | Full pipeline run sequentially over MULTIPLE files, streamed |
| WS     | `/ws/github-scan` | Scan every `.py` file in a public GitHub repo (Scanner agent only), streamed |

## GitHub repo scan feature

Paste a public GitHub repo URL (e.g. `https://github.com/owner/repo`, optionally
with `/tree/<branch>`) into the "Or scan a GitHub repository" box on the
dashboard. This:

1. Fetches the repo's file tree via GitHub's REST API (2 API calls per scan,
   regardless of repo size — everything else comes from
   `raw.githubusercontent.com`, which has its own, much higher limit).
2. Filters to `.py` files, skipping `venv/`, `node_modules/`, `__pycache__/`,
   `.git/`, `build/`, `dist/`, and `migrations/` directories.
3. Runs the static-analysis Scanner agent (no LLM call, no cost) on each
   file, streaming per-file progress into the Agent Terminal.
4. Shows a results list with a risk badge per file and a checkbox next to
   each vulnerable one. Click **Migrate** on a single file to load it into
   the editor and run it through the full pipeline, or select multiple and
   use **batch migration** (below) to run all of them at once.

It intentionally does **not** auto-refactor every vulnerable file it finds —
that would be slow and could burn a lot of LLM quota on a large repo.

Configuration (`backend/.env`):
- `GITHUB_TOKEN` (optional) — raises GitHub's API rate limit from 60/hr to
  5,000/hr. Not required for occasional use on public repos.
- `GITHUB_MAX_FILES` (default 40) — max `.py` files scanned per repo.
- `GITHUB_MAX_FILE_SIZE` (default 307200 = 300 KB) — files larger than this
  are skipped.

## Batch migration feature

After a GitHub repo scan, check the box next to any flagged files (or click
**Select all vulnerable**) and click **Migrate Selected (N)**. This runs the
full Scanner → Refactor → Tester pipeline over every selected file, **one
file at a time** (not in parallel, to stay within LLM provider rate limits).

- The Agent Terminal tags every log line with a small file-path badge so you
  can tell which file each Scanner/Refactor/Tester message belongs to.
- A progress indicator on the button shows which file (`i/N`) is currently
  migrating.
- When the batch finishes, a results list shows each file's outcome
  (**Verified** or **Failed**) with a per-file download button for any
  successfully migrated code.

Configuration (`backend/.env`):
- `MAX_BATCH_FILES` (default 10) — max files processed in a single batch
  request; extras beyond this are dropped with a warning in the terminal.

## Diff view

Above the code area, toggle between **Panels** (the default two-panel
before/after view) and **Diff** — a GitHub-style side-by-side diff computed
client-side with the [`diff`](https://www.npmjs.com/package/diff) (jsdiff)
library:

- Removed lines (only in the original) are highlighted red and appear only
  on the left, with the right side blank on that row.
- Added lines (only in the refactored code) are highlighted green and appear
  only on the right, with the left side blank.
- Unchanged lines appear on both sides at the same row, so context lines
  stay aligned even when the surrounding code shifts.
- A `+N`/`-N` counter in the diff header shows how many lines were added vs.
  removed at a glance.

Both sides render inside a single CSS grid (not two independently-stacked
columns), which keeps left/right rows vertically aligned even if a long line
wraps to multiple visual lines.

## Notes on `liboqs-python`

`liboqs-python` on PyPI (pinned here to `0.16.0`, the version actually
published — earlier docs sometimes reference `0.10.0`, which no longer
resolves) is a **pure Python wrapper**. It does not ship a precompiled
`liboqs`; instead, the native C library is downloaded and built from source
the first time `import oqs` runs. That build takes several minutes — far
longer than the Tester agent's sandbox timeout — so the backend `Dockerfile`
forces this build to happen once, at **image build time**, by running a
throwaway ML-KEM-512 keypair/encapsulation as its final setup step. If that
step fails during `docker compose up --build`, it means liboqs itself failed
to compile (check the build log for a missing toolchain component), not a
problem with the pipeline code.

First build may take several minutes for this reason — that's expected.

## Retry logic

There are two independent layers of resilience:

1. **Provider fallback** (within a single Refactor attempt): if Groq errors
   out or returns a weak response, OpenRouter is tried next, then Gemini.
   See `call_llm_with_fallback()` in `agents.py`.
2. **Test-fix retry loop** (across attempts): if the Tester agent's sandboxed
   execution fails, its stack trace is fed back into the *next* Refactor
   attempt (which again runs the full provider fallback chain) so it can fix
   the specific error. This repeats up to `MAX_RETRIES` (default: 2) times
   before the pipeline reports a final `failed` status.

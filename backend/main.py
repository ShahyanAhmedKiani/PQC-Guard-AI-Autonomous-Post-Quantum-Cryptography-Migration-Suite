"""
main.py
-------
FastAPI entrypoint for PQC-Guard AI.

Exposes:
  - POST /api/scan          : quick synchronous scan only (no LLM call)
  - POST /api/upload        : accepts a .py file upload, returns its raw text
  - WS   /ws/pipeline       : runs the full Scanner -> Refactor -> Tester
                              pipeline for ONE file, streaming structured
                              JSON log events to the frontend Agent Terminal.
  - WS   /ws/batch-pipeline : runs the full pipeline sequentially over
                              MULTIPLE files (e.g. several flagged files
                              from a GitHub repo scan), streaming per-file
                              progress and a final aggregate summary.
  - WS   /ws/github-scan    : scans every .py file in a public GitHub repo
                              with the Scanner agent (no LLM call), streaming
                              per-file progress and a final results list.
  - GET  /api/health        : simple health check
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents import PipelineOrchestrator, ScannerAgent
from github_scanner import scan_github_repo

MAX_BATCH_FILES = int(os.getenv("MAX_BATCH_FILES", "10"))

app = FastAPI(
    title="PQC-Guard AI",
    description="Autonomous multi-agent pipeline for migrating legacy crypto to post-quantum standards.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # relaxed for hackathon demo; restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScanRequest(BaseModel):
    code: str


class ScanResponse(BaseModel):
    is_vulnerable: bool
    overall_risk: str
    summary: str
    findings: list


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pqc-guard-ai-backend"}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """Accepts a .py file upload and returns its decoded text content."""
    if not file.filename.endswith(".py"):
        raise HTTPException(status_code=400, detail="Only .py files are supported.")

    contents = await file.read()
    try:
        code = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text.")

    return {"filename": file.filename, "code": code}


@app.post("/api/scan", response_model=ScanResponse)
async def scan_only(request: ScanRequest) -> ScanResponse:
    """Runs only the Scanner agent synchronously (used for quick pre-checks)."""
    scanner = ScannerAgent()
    result: dict = {}
    async for event in scanner.run(request.code):
        if event.get("result") is not None:
            result = event["result"]
    if not result:
        raise HTTPException(status_code=500, detail="Scanner failed to produce a result.")
    return ScanResponse(**result)


@app.websocket("/ws/pipeline")
async def pipeline_ws(websocket: WebSocket) -> None:
    """
    Streams the full multi-agent pipeline over a WebSocket.

    Client sends: {"code": "<python source>"}
    Server sends a sequence of JSON log events, e.g.:
        {"agent": "Scanner", "level": "info", "message": "...", ...}
    ending with an Orchestrator event containing "final_status".
    """
    await websocket.accept()
    orchestrator = PipelineOrchestrator()

    try:
        raw = await websocket.receive_text()
        payload = json.loads(raw)
        code = payload.get("code", "")

        if not code.strip():
            await websocket.send_json(
                {"agent": "Orchestrator", "level": "error", "message": "No code provided."}
            )
            await websocket.close()
            return

        async for event in orchestrator.run(code):
            await websocket.send_json(event)

        await websocket.close()

    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        await websocket.send_json(
            {"agent": "Orchestrator", "level": "error", "message": "Invalid JSON payload."}
        )
        await websocket.close()
    except Exception as e:  # noqa: BLE001 - surface unexpected errors to the client
        try:
            await websocket.send_json(
                {"agent": "Orchestrator", "level": "error", "message": f"Unexpected server error: {e}"}
            )
            await websocket.close()
        except RuntimeError:
            pass


@app.websocket("/ws/batch-pipeline")
async def batch_pipeline_ws(websocket: WebSocket) -> None:
    """
    Runs the full Scanner -> Refactor -> Tester pipeline sequentially over
    MULTIPLE files — e.g. several files a user selected from a GitHub repo
    scan's results.

    Client sends: {"files": [{"path": "a.py", "code": "..."}, ...]}
      (capped at MAX_BATCH_FILES; extras beyond the cap are dropped)

    Server sends the same per-agent log events as /ws/pipeline, each one
    additionally tagged with a "file_path" key so the frontend can attribute
    log lines to the right file. Before each file, an Orchestrator event
    carries a "batch_progress": {"index", "total", "path"} key. After all
    files finish, a final event carries "batch_complete": true and
    "results": [{"path", "final_status", "final_code"}, ...].

    Files are processed one at a time (not in parallel) to stay within
    LLM provider rate limits and keep the terminal log readable.
    """
    await websocket.accept()

    try:
        raw = await websocket.receive_text()
        payload = json.loads(raw)
        files = payload.get("files", [])

        if not files:
            await websocket.send_json(
                {"agent": "Orchestrator", "level": "error", "message": "No files provided for batch migration."}
            )
            await websocket.close()
            return

        if len(files) > MAX_BATCH_FILES:
            await websocket.send_json(
                {
                    "agent": "Orchestrator",
                    "level": "warn",
                    "message": f"Batch capped at {MAX_BATCH_FILES} files ({len(files)} were selected); extras will be skipped.",
                }
            )
            files = files[:MAX_BATCH_FILES]

        results = []

        for i, f in enumerate(files, start=1):
            path = f.get("path") or f"file_{i}.py"
            code = f.get("code", "")

            await websocket.send_json(
                {
                    "agent": "Orchestrator",
                    "level": "info",
                    "message": f"[{i}/{len(files)}] Starting migration of {path}...",
                    "batch_progress": {"index": i, "total": len(files), "path": path},
                }
            )

            if not code.strip():
                await websocket.send_json(
                    {"agent": "Orchestrator", "level": "warn", "message": f"{path} has no content — skipping.", "file_path": path}
                )
                results.append({"path": path, "final_status": "failed", "final_code": None})
                continue

            orchestrator = PipelineOrchestrator()
            final_status = None
            final_code = None

            async for event in orchestrator.run(code):
                event["file_path"] = path
                await websocket.send_json(event)
                if event.get("final_status"):
                    final_status = event["final_status"]
                if event.get("final_code"):
                    final_code = event["final_code"]

            results.append({"path": path, "final_status": final_status or "failed", "final_code": final_code})

        verified_count = sum(1 for r in results if r["final_status"] == "verified")
        await websocket.send_json(
            {
                "agent": "Orchestrator",
                "level": "success" if verified_count == len(results) else "warn",
                "message": f"Batch migration complete — {verified_count}/{len(results)} file(s) verified.",
                "batch_complete": True,
                "results": results,
            }
        )
        await websocket.close()

    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        await websocket.send_json(
            {"agent": "Orchestrator", "level": "error", "message": "Invalid JSON payload."}
        )
        await websocket.close()
    except Exception as e:  # noqa: BLE001 - surface unexpected errors to the client
        try:
            await websocket.send_json(
                {"agent": "Orchestrator", "level": "error", "message": f"Unexpected server error: {e}"}
            )
            await websocket.close()
        except RuntimeError:
            pass
async def github_scan_ws(websocket: WebSocket) -> None:
    """
    Streams a GitHub repository scan over a WebSocket.

    Client sends: {"repo_url": "https://github.com/owner/repo"}
    Server sends a sequence of JSON log events, ending with a
    "GithubScanner" event containing a "files" key: a list of per-file
    scan summaries, or null/[] if the scan failed or found nothing.
    """
    await websocket.accept()

    try:
        raw = await websocket.receive_text()
        payload = json.loads(raw)
        repo_url = payload.get("repo_url", "")

        if not repo_url.strip():
            await websocket.send_json(
                {"agent": "GithubScanner", "level": "error", "message": "No repo URL provided."}
            )
            await websocket.close()
            return

        async for event in scan_github_repo(repo_url):
            await websocket.send_json(event)

        await websocket.close()

    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        await websocket.send_json(
            {"agent": "GithubScanner", "level": "error", "message": "Invalid JSON payload."}
        )
        await websocket.close()
    except Exception as e:  # noqa: BLE001 - surface unexpected errors to the client
        try:
            await websocket.send_json(
                {"agent": "GithubScanner", "level": "error", "message": f"Unexpected server error: {e}"}
            )
            await websocket.close()
        except RuntimeError:
            pass


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("BACKEND_PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)

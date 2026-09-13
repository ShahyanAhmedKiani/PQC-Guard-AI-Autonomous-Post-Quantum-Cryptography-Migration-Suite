"""
github_scanner.py
------------------
"Scan GitHub Repo" feature for PQC-Guard AI.

Given a public GitHub repository URL, this module:
  1. Resolves the repo's default branch (if none is specified in the URL).
  2. Fetches the full file tree via GitHub's REST API.
  3. Filters it down to `.py` files (skipping vendored/virtualenv/build dirs).
  4. Fetches each file's raw content and runs the existing ScannerAgent
     (static analysis, no LLM call) over it.
  5. Streams progress events + a final per-file results list.

This intentionally does NOT run the Refactor/Tester agents automatically —
that would be slow and could burn a lot of LLM quota on a large repo. The
frontend lets the user pick one flagged file to migrate through the existing
full pipeline (/ws/pipeline), the same as a manual single-file upload.

Note on GitHub API rate limits: only two `api.github.com` calls are made per
repo scan (repo metadata + file tree), regardless of repo size — everything
else is fetched from `raw.githubusercontent.com`, which isn't subject to the
same core API rate limit. Unauthenticated access (60 req/hr) is therefore
enough for casual use; set GITHUB_TOKEN in .env for heavier use (5,000/hr).
"""

from __future__ import annotations

import os
import re
from typing import Any, AsyncGenerator, Optional

import httpx

from agents import ScannerAgent, make_event

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Safety caps so a huge/malicious repo URL can't make the scan run forever
# or blow up memory with giant files.
MAX_FILES_TO_SCAN = int(os.getenv("GITHUB_MAX_FILES", "40"))
MAX_FILE_SIZE_BYTES = int(os.getenv("GITHUB_MAX_FILE_SIZE", str(300 * 1024)))  # 300 KB

IGNORED_PATH_SEGMENTS = (
    "/venv/", "/.venv/", "/env/", "/node_modules/", "/__pycache__/",
    "/site-packages/", "/.git/", "/dist/", "/build/", "/migrations/",
)

# Matches:
#   https://github.com/owner/repo
#   https://github.com/owner/repo.git
#   https://github.com/owner/repo/tree/some-branch
#   github.com/owner/repo
REPO_URL_RE = re.compile(
    r"github\.com[:/]+(?P<owner>[^/\s]+)/(?P<repo>[^/.\s]+)(?:\.git)?(?:/tree/(?P<branch>[^/\s]+))?"
)


def parse_repo_url(url: str) -> Optional[dict]:
    """Extract {owner, repo, branch} from a GitHub URL. `branch` may be None."""
    match = REPO_URL_RE.search(url.strip())
    if not match:
        return None
    return {
        "owner": match.group("owner"),
        "repo": match.group("repo"),
        "branch": match.group("branch"),
    }


def _auth_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _is_ignored_path(path: str) -> bool:
    wrapped = f"/{path}/"
    return any(seg in wrapped for seg in IGNORED_PATH_SEGMENTS)


async def scan_github_repo(repo_url: str) -> AsyncGenerator[dict, Any]:
    """
    Fetches a repo's `.py` files and runs ScannerAgent over each, yielding
    progress events. The final event carries `files`: a list of per-file
    scan summaries — {path, code, is_vulnerable, overall_risk, summary,
    findings} — or `files: None`/`files: []` if the scan couldn't run or
    found nothing.
    """
    agent_name = "GithubScanner"

    parsed = parse_repo_url(repo_url)
    if not parsed:
        yield make_event(agent_name, "error", f"Could not parse a GitHub repo URL from: {repo_url}")
        yield make_event(agent_name, "error", "Repo scan aborted.", files=None)
        return

    owner, repo, branch = parsed["owner"], parsed["repo"], parsed["branch"]
    yield make_event(
        agent_name,
        "info",
        f"Resolved repo: {owner}/{repo}" + (f" (branch: {branch})" if branch else ""),
    )

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        # --- Resolve default branch if the URL didn't specify one ---
        if not branch:
            try:
                resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=_auth_headers())
                resp.raise_for_status()
                branch = resp.json().get("default_branch", "main")
                yield make_event(agent_name, "info", f"Using default branch: {branch}")
            except httpx.HTTPStatusError as e:
                reason = "repo not found or private" if e.response.status_code == 404 else f"HTTP {e.response.status_code}"
                yield make_event(agent_name, "error", f"Could not fetch repo metadata ({reason}).")
                yield make_event(agent_name, "error", "Repo scan aborted — check the URL is a public GitHub repo.", files=None)
                return
            except Exception as e:
                yield make_event(agent_name, "error", f"Could not fetch repo metadata: {e}")
                yield make_event(agent_name, "error", "Repo scan aborted.", files=None)
                return

        # --- Fetch the full file tree ---
        yield make_event(agent_name, "info", "Fetching repository file tree...")
        try:
            tree_resp = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}",
                params={"recursive": "1"},
                headers=_auth_headers(),
            )
            tree_resp.raise_for_status()
            tree_json = tree_resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                yield make_event(
                    agent_name,
                    "error",
                    "GitHub API rate limit hit. Set GITHUB_TOKEN in .env for a higher limit.",
                )
            else:
                yield make_event(agent_name, "error", f"Could not fetch file tree: HTTP {e.response.status_code}")
            yield make_event(agent_name, "error", "Repo scan aborted.", files=None)
            return
        except Exception as e:
            yield make_event(agent_name, "error", f"Could not fetch file tree: {e}")
            yield make_event(agent_name, "error", "Repo scan aborted.", files=None)
            return

        if tree_json.get("truncated"):
            yield make_event(
                agent_name,
                "warn",
                "Repository tree was truncated by GitHub's API (very large repo) — some files may be missed.",
            )

        py_blobs = [
            item
            for item in tree_json.get("tree", [])
            if item.get("type") == "blob"
            and item.get("path", "").endswith(".py")
            and not _is_ignored_path(item.get("path", ""))
            and item.get("size", 0) <= MAX_FILE_SIZE_BYTES
        ]

        if not py_blobs:
            yield make_event(agent_name, "warn", "No scannable .py files found in this repository.")
            yield make_event(agent_name, "success", "Repo scan complete — nothing to scan.", files=[])
            return

        capped = len(py_blobs) > MAX_FILES_TO_SCAN
        py_blobs = py_blobs[:MAX_FILES_TO_SCAN]

        yield make_event(
            agent_name,
            "info",
            f"Found {len(py_blobs)} Python file(s) to scan"
            + (f" (capped at {MAX_FILES_TO_SCAN}; repo has more)" if capped else "")
            + ".",
        )

        scanner = ScannerAgent()
        results: list[dict] = []

        for i, blob in enumerate(py_blobs, start=1):
            path = blob["path"]
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

            try:
                file_resp = await client.get(raw_url)
                file_resp.raise_for_status()
                code = file_resp.text
            except Exception as e:
                yield make_event(agent_name, "warn", f"[{i}/{len(py_blobs)}] Could not fetch {path}: {e} — skipping.")
                continue

            # Run the Scanner agent but only surface its FINAL summary event
            # to the terminal — forwarding every per-line finding for every
            # file in a repo would flood the log on large repos.
            scan_result: dict = {}
            async for event in scanner.run(code):
                if event.get("result") is not None:
                    scan_result = event["result"]

            if scan_result.get("is_vulnerable"):
                yield make_event(agent_name, "warn", f"[{i}/{len(py_blobs)}] {path}: {scan_result['summary']}")
            else:
                yield make_event(agent_name, "info", f"[{i}/{len(py_blobs)}] {path}: clean.")

            results.append(
                {
                    "path": path,
                    "code": code,
                    "is_vulnerable": scan_result.get("is_vulnerable", False),
                    "overall_risk": scan_result.get("overall_risk", "none"),
                    "summary": scan_result.get("summary", ""),
                    "findings": scan_result.get("findings", []),
                }
            )

        vulnerable_count = sum(1 for r in results if r["is_vulnerable"])
        yield make_event(
            agent_name,
            "success" if vulnerable_count == 0 else "warn",
            f"Repo scan complete — {vulnerable_count}/{len(results)} file(s) flagged as vulnerable.",
        )
        yield make_event(agent_name, "success", "Scan results ready.", files=results)

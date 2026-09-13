"""
agents.py
---------
Core multi-agent pipeline for PQC-Guard AI.

Three agents run sequentially, each yielding structured log events that the
FastAPI layer streams to the frontend "Agent Terminal":

    1. ScannerAgent   -> static analysis for vulnerable crypto imports/usages
    2. RefactorAgent  -> LLM-driven rewrite to post-quantum (ML-KEM-512) code
    3. TesterAgent    -> isolated subprocess execution + retry feedback loop

The agents communicate via plain dataclasses / dicts so the pipeline is easy
to reason about and to stream over WebSockets/SSE.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

from dotenv import load_dotenv

load_dotenv()

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))

# Minimum length (chars) for an LLM response to be considered a real,
# confident answer rather than an empty/truncated/low-confidence one.
MIN_CONFIDENT_RESPONSE_LEN = 80

# --------------------------------------------------------------------------
# LLM client bootstrap — multi-provider fallback chain.
#
# Groq, OpenRouter, and Gemini all expose an OpenAI-compatible
# `chat.completions.create()` surface, so a single `openai` SDK client
# handles all three — only `base_url` / `api_key` / model differ:
#   - Groq:       https://api.groq.com/openai/v1
#   - OpenRouter: https://openrouter.ai/api/v1
#   - Gemini:     https://generativelanguage.googleapis.com/v1beta/openai/
#
# Order of attempt is controlled by LLM_PROVIDER_CHAIN (default:
# "groq,openrouter,gemini"). For each provider in the chain, we:
#   1. Skip it if no API key is configured for it.
#   2. Call it. If the call raises (auth error, rate limit, timeout,
#      network failure, etc.) -> fall back to the next provider.
#   3. If it returns a response but the response looks low-confidence
#      (empty, too short, or refuses/truncates) -> also fall back.
#   4. First provider to return a solid response wins; its output is used.
# --------------------------------------------------------------------------
PROVIDER_CONFIGS: dict[str, dict] = {
    "groq": {
        "api_key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model_env": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
        "display_name": "Groq",
    },
    "openrouter": {
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "model_env": "OPENROUTER_MODEL",
        "default_model": "openai/gpt-oss-120b",
        "display_name": "OpenRouter",
        "extra_headers": {
            # Optional headers OpenRouter uses for its public app-ranking
            # listings; harmless to include for a hackathon demo.
            "HTTP-Referer": "https://pqc-guard.ai",
            "X-Title": "PQC-Guard AI",
        },
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.0-flash",
        "display_name": "Gemini",
    },
}

LLM_PROVIDER_CHAIN = [
    p.strip().lower()
    for p in os.getenv("LLM_PROVIDER_CHAIN", "groq,openrouter,gemini").split(",")
    if p.strip()
]

_client_cache: dict[str, tuple] = {}


def _get_client_for(provider: str):
    """Lazily build (and cache) an OpenAI-SDK client for one provider.

    Returns (client, model, display_name) or None if that provider has no
    API key configured (so it's silently skipped in the fallback chain).
    """
    if provider in _client_cache:
        return _client_cache[provider]

    cfg = PROVIDER_CONFIGS.get(provider)
    if not cfg:
        return None

    api_key = os.getenv(cfg["api_key_env"], "")
    if not api_key:
        return None

    from openai import OpenAI

    kwargs = {"api_key": api_key, "base_url": cfg["base_url"]}
    if "extra_headers" in cfg:
        kwargs["default_headers"] = cfg["extra_headers"]

    client = OpenAI(**kwargs)
    model = os.getenv(cfg["model_env"], cfg["default_model"])
    result = (client, model, cfg["display_name"])
    _client_cache[provider] = result
    return result


def _looks_low_confidence(text: str) -> Optional[str]:
    """Heuristic check for a weak/low-confidence LLM response.

    Returns a human-readable reason string if the response looks weak,
    or None if it looks like a solid, usable response.
    """
    if not text or not text.strip():
        return "empty response"
    stripped = text.strip()
    if len(stripped) < MIN_CONFIDENT_RESPONSE_LEN:
        return f"response too short ({len(stripped)} chars) to be a real code rewrite"
    refusal_markers = ("i can't", "i cannot", "i'm not able to", "as an ai")
    if any(stripped.lower().startswith(m) for m in refusal_markers):
        return "response looks like a refusal, not code"
    return None


async def call_llm_with_fallback(
    agent_name: str, system_prompt: str, user_prompt: str
) -> AsyncGenerator[dict, Any]:
    """
    Try each provider in LLM_PROVIDER_CHAIN in order (default: Groq ->
    OpenRouter -> Gemini). Yields log events for each attempt, and a final
    event carrying either `llm_output`/`llm_provider` on success, or
    `llm_output=None` if every provider in the chain failed.
    """
    available = [(p, _get_client_for(p)) for p in LLM_PROVIDER_CHAIN]
    available = [(p, c) for p, c in available if c is not None]

    if not available:
        yield make_event(
            agent_name,
            "error",
            "No LLM provider is configured. Set at least one of "
            "GROQ_API_KEY, OPENROUTER_API_KEY, or GEMINI_API_KEY in .env.",
        )
        yield make_event(agent_name, "error", "Refactor aborted — no LLM configured.", llm_output=None)
        return

    for provider, (client, model, display_name) in available:
        yield make_event(agent_name, "info", f"Trying {display_name} ({model})...")

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=2048,
            )
            raw_output = response.choices[0].message.content or ""
        except Exception as e:
            yield make_event(
                agent_name,
                "warn",
                f"{display_name} call failed ({e}) — falling back to next provider.",
            )
            continue

        weak_reason = _looks_low_confidence(raw_output)
        if weak_reason:
            yield make_event(
                agent_name,
                "warn",
                f"{display_name} response looked low-confidence ({weak_reason}) — falling back to next provider.",
            )
            continue

        yield make_event(agent_name, "success", f"{display_name} returned a confident response.")
        yield make_event(
            agent_name,
            "info",
            f"Using {display_name} output for this attempt.",
            llm_output=raw_output,
            llm_provider=display_name,
        )
        return

    yield make_event(
        agent_name,
        "error",
        f"All configured providers ({', '.join(d for _, (_, _, d) in available)}) "
        "failed or returned low-confidence responses.",
    )
    yield make_event(agent_name, "error", "Refactor could not complete this attempt.", llm_output=None)


# --------------------------------------------------------------------------
# Shared event schema for streaming to the frontend terminal
# --------------------------------------------------------------------------
def make_event(agent: str, level: str, message: str, **extra: Any) -> dict:
    """Build a single structured log event for the streaming terminal."""
    return {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "agent": agent,
        "level": level,  # info | warn | error | success | thought
        "message": message,
        **extra,
    }


# --------------------------------------------------------------------------
# 1. SCANNER AGENT
# --------------------------------------------------------------------------
# Known vulnerable (pre-quantum) crypto primitives we flag. Pattern-matching
# on imports + usage is fast, deterministic, and doesn't require an LLM call,
# which keeps the scanner cheap and auditable.
VULNERABLE_PATTERNS = [
    {
        "pattern": r"from\s+cryptography\.hazmat\.primitives\.asymmetric\s+import\s+rsa",
        "algorithm": "RSA",
        "risk": "critical",
        "reason": "RSA key exchange/signatures are broken by Shor's algorithm on a sufficiently large quantum computer.",
    },
    {
        "pattern": r"from\s+cryptography\.hazmat\.primitives\.asymmetric\s+import\s+ec",
        "algorithm": "ECC (Elliptic Curve)",
        "risk": "critical",
        "reason": "Elliptic curve cryptography (ECDH/ECDSA) is vulnerable to Shor's algorithm.",
    },
    {
        "pattern": r"from\s+Crypto\.PublicKey\s+import\s+RSA",
        "algorithm": "RSA (PyCryptodome)",
        "risk": "critical",
        "reason": "RSA via PyCryptodome is not quantum-resistant.",
    },
    {
        "pattern": r"rsa\.generate_private_key",
        "algorithm": "RSA key generation",
        "risk": "critical",
        "reason": "Generates an RSA keypair vulnerable to quantum attacks.",
    },
    {
        "pattern": r"ec\.generate_private_key",
        "algorithm": "ECC key generation",
        "risk": "critical",
        "reason": "Generates an elliptic-curve keypair vulnerable to quantum attacks.",
    },
    {
        "pattern": r"from\s+cryptography\.hazmat\.primitives\.asymmetric\s+import\s+dh",
        "algorithm": "Diffie-Hellman (classic)",
        "risk": "high",
        "reason": "Classic finite-field Diffie-Hellman key exchange is vulnerable to Shor's algorithm.",
    },
    {
        "pattern": r"paramiko\.RSAKey",
        "algorithm": "RSA (Paramiko/SSH)",
        "risk": "high",
        "reason": "SSH RSA host/user keys are vulnerable to future quantum attacks.",
    },
]


@dataclass
class ScanFinding:
    line_number: int
    line_text: str
    algorithm: str
    risk: str
    reason: str


@dataclass
class ScanResult:
    findings: list[ScanFinding] = field(default_factory=list)
    is_vulnerable: bool = False
    overall_risk: str = "none"
    summary: str = ""


class ScannerAgent:
    """Static-analysis agent that detects vulnerable cryptographic usage."""

    name = "Scanner"

    async def run(self, code: str) -> AsyncGenerator[dict, Any]:
        yield make_event(self.name, "info", "Booting static analysis engine...")
        yield make_event(self.name, "thought", "Parsing source into AST to validate syntax before pattern scan.")

        try:
            ast.parse(code)
            yield make_event(self.name, "info", "AST parse successful. Source is syntactically valid Python.")
        except SyntaxError as e:
            yield make_event(self.name, "error", f"Syntax error in uploaded file: {e}")
            result = ScanResult(summary="Upload contains invalid Python syntax.")
            yield make_event(self.name, "error", "Aborting scan — cannot proceed with invalid syntax.", result=self._serialize(result))
            return

        findings: list[ScanFinding] = []
        lines = code.splitlines()

        yield make_event(self.name, "info", f"Scanning {len(lines)} lines against {len(VULNERABLE_PATTERNS)} known pre-quantum signatures...")

        for idx, line in enumerate(lines, start=1):
            for sig in VULNERABLE_PATTERNS:
                if re.search(sig["pattern"], line):
                    finding = ScanFinding(
                        line_number=idx,
                        line_text=line.strip(),
                        algorithm=sig["algorithm"],
                        risk=sig["risk"],
                        reason=sig["reason"],
                    )
                    findings.append(finding)
                    yield make_event(
                        self.name,
                        "warn",
                        f"Line {idx}: detected {sig['algorithm']} usage ({sig['risk'].upper()} risk).",
                        finding={
                            "line_number": idx,
                            "algorithm": sig["algorithm"],
                            "risk": sig["risk"],
                        },
                    )

        is_vulnerable = len(findings) > 0
        overall_risk = "none"
        if any(f.risk == "critical" for f in findings):
            overall_risk = "critical"
        elif any(f.risk == "high" for f in findings):
            overall_risk = "high"
        elif findings:
            overall_risk = "medium"

        summary = (
            f"Found {len(findings)} vulnerable cryptographic usage(s). Overall risk: {overall_risk.upper()}."
            if is_vulnerable
            else "No known pre-quantum vulnerable cryptography detected."
        )

        result = ScanResult(
            findings=findings,
            is_vulnerable=is_vulnerable,
            overall_risk=overall_risk,
            summary=summary,
        )

        yield make_event(
            self.name,
            "success" if is_vulnerable else "info",
            summary,
            result=self._serialize(result),
        )

    @staticmethod
    def _serialize(result: ScanResult) -> dict:
        return {
            "is_vulnerable": result.is_vulnerable,
            "overall_risk": result.overall_risk,
            "summary": result.summary,
            "findings": [
                {
                    "line_number": f.line_number,
                    "line_text": f.line_text,
                    "algorithm": f.algorithm,
                    "risk": f.risk,
                    "reason": f.reason,
                }
                for f in result.findings
            ],
        }


# --------------------------------------------------------------------------
# 2. REFACTOR AGENT
# --------------------------------------------------------------------------
# This exact snippet is injected into the system prompt verbatim to anchor
# the LLM on the correct liboqs-python API and prevent hallucinated methods.
CANONICAL_PQC_PATTERN = '''\
import oqs

def generate_pq_keys():
    with oqs.KeyEncapsulation('ML-KEM-512') as kem:
        public_key = kem.generate_keypair()
        private_key = kem.export_secret_key()
        ciphertext, shared_secret_sender = kem.encap_secret(public_key)
    return public_key, private_key
'''

REFACTOR_SYSTEM_PROMPT = f"""You are an expert cryptography engineer specializing in migrating legacy
Python code from classical (pre-quantum) public-key cryptography to NIST
Post-Quantum Cryptography (PQC) standards.

Your task: rewrite the provided vulnerable Python code so that all RSA/ECC/DH
key exchange and key generation is replaced with the NIST-standardized
ML-KEM-512 (Module-Lattice-Based Key-Encapsulation Mechanism) algorithm,
implemented via the `liboqs-python` library (the `oqs` module).

You MUST use this exact, verified implementation pattern as your foundation
(do not invent different method names or a different API surface):

```python
{CANONICAL_PQC_PATTERN}```

Rules:
1. Use `oqs.KeyEncapsulation('ML-KEM-512')` for all key exchange / key generation.
2. Preserve the original code's overall structure, function names, and any
   non-cryptographic business logic wherever reasonably possible.
3. Replace RSA/ECC signing use-cases with a comment noting that a PQC
   signature scheme (e.g., ML-DSA / Dilithium) would be used in production,
   since ML-KEM is a KEM, not a signature scheme — but still fully migrate
   any key-exchange/encapsulation logic to ML-KEM-512.
4. The output MUST be a single, complete, runnable Python script.
5. Include a short `if __name__ == "__main__":` block that exercises the new
   PQC code path so it can be verified by automated testing.
6. Do NOT include markdown fences, prose, or explanations in your response —
   output ONLY the raw Python source code.
7. If you are given a previous failed attempt and its stack trace, fix the
   root cause of that specific error while keeping everything else intact.
"""


class RefactorAgent:
    """LLM-driven agent that rewrites vulnerable code into PQC-safe code."""

    name = "Refactor"

    async def run(
        self,
        original_code: str,
        scan_result: dict,
        previous_attempt: Optional[str] = None,
        previous_error: Optional[str] = None,
        attempt_number: int = 1,
    ) -> AsyncGenerator[dict, Any]:
        yield make_event(
            self.name,
            "info",
            f"Refactor attempt {attempt_number}/{MAX_RETRIES + 1} — invoking LLM chain "
            f"({' -> '.join(p.capitalize() for p in LLM_PROVIDER_CHAIN)})...",
        )

        vulnerable_algos = sorted({f["algorithm"] for f in scan_result.get("findings", [])})
        if vulnerable_algos:
            yield make_event(
                self.name,
                "thought",
                f"Targeting migration of: {', '.join(vulnerable_algos)} -> ML-KEM-512.",
            )

        user_prompt = self._build_user_prompt(
            original_code, scan_result, previous_attempt, previous_error
        )

        yield make_event(self.name, "thought", "Composing system prompt with canonical ML-KEM-512 pattern to prevent hallucination...")

        raw_output: Optional[str] = None
        async for event in call_llm_with_fallback(self.name, REFACTOR_SYSTEM_PROMPT, user_prompt):
            if event.get("llm_output") is not None:
                raw_output = event["llm_output"]
            yield event

        if not raw_output:
            yield make_event(self.name, "error", "Refactor produced no usable output from any provider.", code=None)
            return

        new_code = self._strip_markdown_fences(raw_output)

        yield make_event(
            self.name,
            "success",
            f"LLM returned {len(new_code.splitlines())} lines of refactored code.",
        )
        yield make_event(self.name, "info", "Handing refactored code to Tester agent for verification.", code=new_code)

    @staticmethod
    def _build_user_prompt(
        original_code: str,
        scan_result: dict,
        previous_attempt: Optional[str],
        previous_error: Optional[str],
    ) -> str:
        parts = [
            "## Original vulnerable code\n```python\n" + original_code + "\n```",
            "\n## Scanner findings\n" + json.dumps(scan_result, indent=2),
        ]
        if previous_attempt and previous_error:
            parts.append(
                "\n## Previous refactor attempt FAILED during testing.\n"
                "Here is the code that failed:\n```python\n" + previous_attempt + "\n```\n"
                "Here is the stack trace / error it produced:\n```\n" + previous_error + "\n```\n"
                "Fix the root cause and return a corrected, complete script."
            )
        parts.append(
            "\nRewrite this into a single, complete, runnable Python script using "
            "ML-KEM-512 via liboqs-python as instructed. Output ONLY raw Python code."
        )
        return "\n".join(parts)

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        text = text.strip()
        text = re.sub(r"^```(?:python)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return text.strip()


# --------------------------------------------------------------------------
# 3. TESTER AGENT
# --------------------------------------------------------------------------
class TesterAgent:
    """Executes generated PQC code in an isolated subprocess and verifies it."""

    name = "Tester"

    async def run(self, code: str, timeout_seconds: int = 15) -> AsyncGenerator[dict, Any]:
        yield make_event(self.name, "info", "Spinning up isolated subprocess sandbox...")
        yield make_event(self.name, "thought", "Writing candidate code to a temp file for out-of-process execution (no exec() in-process, for isolation).")

        with tempfile.TemporaryDirectory() as tmp_dir:
            script_path = os.path.join(tmp_dir, "candidate.py")
            with open(script_path, "w") as f:
                f.write(code)

            yield make_event(self.name, "info", f"Executing candidate script with a {timeout_seconds}s timeout...")

            try:
                proc = subprocess.run(
                    [sys.executable, script_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    cwd=tmp_dir,
                )
            except subprocess.TimeoutExpired:
                msg = f"Execution timed out after {timeout_seconds}s (possible infinite loop or blocking call)."
                yield make_event(self.name, "error", msg)
                yield make_event(self.name, "error", "Verification FAILED.", verified=False, error=msg)
                return
            except Exception as e:
                msg = f"Sandbox failed to launch subprocess: {e}"
                yield make_event(self.name, "error", msg)
                yield make_event(self.name, "error", "Verification FAILED.", verified=False, error=msg)
                return

            if proc.stdout:
                for line in proc.stdout.strip().splitlines():
                    yield make_event(self.name, "info", f"stdout: {line}")

            if proc.returncode == 0:
                yield make_event(self.name, "success", "Subprocess exited with code 0. No runtime errors detected.")
                yield make_event(
                    self.name,
                    "success",
                    "Verification PASSED — migration marked as Verified.",
                    verified=True,
                    stdout=proc.stdout,
                )
            else:
                error_output = proc.stderr.strip() or "Unknown error (non-zero exit, no stderr captured)."
                yield make_event(self.name, "error", f"Subprocess exited with code {proc.returncode}.")
                for line in error_output.splitlines()[-15:]:
                    yield make_event(self.name, "error", f"stderr: {line}")
                yield make_event(
                    self.name,
                    "error",
                    "Verification FAILED — feeding stack trace back to Refactor agent.",
                    verified=False,
                    error=error_output,
                )


# --------------------------------------------------------------------------
# ORCHESTRATOR — wires the three agents together with the retry loop
# --------------------------------------------------------------------------
class PipelineOrchestrator:
    """
    Runs Scanner -> Refactor -> Tester sequentially, retrying the
    Refactor/Tester loop up to MAX_RETRIES times on failure.
    """

    def __init__(self):
        self.scanner = ScannerAgent()
        self.refactor = RefactorAgent()
        self.tester = TesterAgent()

    async def run(self, original_code: str) -> AsyncGenerator[dict, Any]:
        # --- Stage 1: Scan ---
        scan_result: dict = {}
        async for event in self.scanner.run(original_code):
            if event.get("result") is not None:
                scan_result = event["result"]
            yield event

        if not scan_result.get("is_vulnerable"):
            yield make_event(
                "Orchestrator",
                "success",
                "No vulnerabilities found — nothing to refactor. Pipeline complete.",
                final_status="clean",
            )
            return

        # --- Stage 2 & 3: Refactor <-> Test retry loop ---
        previous_attempt: Optional[str] = None
        previous_error: Optional[str] = None
        final_code: Optional[str] = None
        verified = False

        for attempt in range(1, MAX_RETRIES + 2):  # initial try + MAX_RETRIES retries
            candidate_code: Optional[str] = None
            async for event in self.refactor.run(
                original_code,
                scan_result,
                previous_attempt=previous_attempt,
                previous_error=previous_error,
                attempt_number=attempt,
            ):
                if event.get("code") is not None:
                    candidate_code = event["code"]
                yield event

            if not candidate_code:
                yield make_event(
                    "Orchestrator",
                    "error",
                    "Refactor agent produced no code. Aborting pipeline.",
                    final_status="failed",
                )
                return

            async for event in self.tester.run(candidate_code):
                if "verified" in event:
                    verified = event["verified"]
                    if verified:
                        final_code = candidate_code
                    else:
                        previous_attempt = candidate_code
                        previous_error = event.get("error", "Unknown failure")
                yield event

            if verified:
                break

            if attempt < MAX_RETRIES + 1:
                yield make_event(
                    "Orchestrator",
                    "warn",
                    f"Retrying refactor (attempt {attempt + 1}/{MAX_RETRIES + 1})...",
                )

        if verified and final_code:
            yield make_event(
                "Orchestrator",
                "success",
                "Pipeline complete — migration Verified and ready for review.",
                final_status="verified",
                final_code=final_code,
            )
        else:
            yield make_event(
                "Orchestrator",
                "error",
                f"Pipeline exhausted {MAX_RETRIES + 1} attempts without a verified result.",
                final_status="failed",
            )

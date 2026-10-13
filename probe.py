"""
probe.py — Phase 0: sanity probe. Pings candidate models across providers,
classifies every failure, hashes every output. The result file becomes the
SEED of the Model Registry (Phase 1).

Philosophy: this is a measurement instrument. It does NOT fail over to
another model — a failure is recorded as data, never routed around.
(Retries only for infrastructure noise: 429/5xx — and even those are counted.)

Run:  python probe.py
"""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

PROBE_PROMPT = "Reply with exactly one word: blue"

CANDIDATES = [
    {
        "provider": "gemini",
        "key_var": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"],
    },
    {
        "provider": "nvidia",
        "key_var": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models": [
            "openai/gpt-oss-20b",
            "z-ai/glm-5.3-flash",
            # intentionally included: it timed out in our earlier diagnostics —
            # the radar should CLASSIFY that, not fear it.
            "deepseek-ai/deepseek-v4.1-flash",
        ],
    },
]

RETRY_CLASSES = {"rate_limited", "server_error"}   # infra noise — retry, record
MAX_ATTEMPTS = 2
WAIT_SECONDS = 15

OUT_PATH = Path("data/probe.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_error(exc: Exception) -> str:
    """Pure function: exception -> failure class. (Unit-tested in Phase 1.)"""
    name = type(exc).__name__
    msg = str(exc)
    if name == "NotFoundError" or "404" in msg or " 410" in msg:
        return "retired_or_unavailable"
    if name == "RateLimitError" or "429" in msg:
        return "rate_limited"
    if name == "APITimeoutError" or "timed out" in msg.lower():
        return "timeout"
    if name in ("AuthenticationError", "PermissionDeniedError") or "401" in msg or "403" in msg:
        return "auth_error"
    if name == "APIConnectionError":
        return "connection"
    if name == "InternalServerError" or any(c in msg for c in (" 500", " 502", " 503")):
        return "server_error"
    return "unknown_error"


def normalize_output(text: str) -> str:
    """Lowercase + collapse whitespace + strip trailing punctuation, so hashes
    compare content-ish, not formatting accidents."""
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .!?\n\t")


def output_hash(text: str) -> str:
    """Short sha256 of the normalized output — the model's behavior fingerprint."""
    return hashlib.sha256(normalize_output(text).encode("utf-8")).hexdigest()[:16]


def probe_model(provider: dict, model: str) -> dict:
    """One probe = one deterministic prompt -> record. No failover, ever."""
    base = {
        "provider": provider["provider"], "model": model,
        "probed_at": utc_now(), "prompt": PROBE_PROMPT,
    }
    key = os.getenv(provider["key_var"])
    if not key:
        return {**base, "status": "skipped", "error_type": "no_key",
                "latency_ms": None, "output_preview": None,
                "output_hash": None, "attempts": 0}

    client = OpenAI(api_key=key, base_url=provider["base_url"], timeout=30)

    last_class, last_err = None, None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        t0 = time.perf_counter()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROBE_PROMPT}],
                temperature=0.0, max_tokens=20,
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            out = resp.choices[0].message.content or ""
            return {**base, "status": "alive", "error_type": None,
                    "latency_ms": latency_ms, "output_preview": out[:80],
                    "output_hash": output_hash(out), "attempts": attempt}
        except Exception as e:
            last_err, last_class = e, classify_error(e)
            if last_class in RETRY_CLASSES and attempt < MAX_ATTEMPTS:
                console.print(f"    [yellow]{model}: {last_class} — retrying in "
                              f"{WAIT_SECONDS}s (attempt {attempt + 1}/{MAX_ATTEMPTS})[/yellow]")
                time.sleep(WAIT_SECONDS)
            else:
                break

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {**base, "status": "down", "error_type": last_class,
            "error_detail": str(last_err)[:150], "latency_ms": latency_ms,
            "output_preview": None, "output_hash": None, "attempts": attempt}


def main() -> None:
    console.rule("[bold cyan]llm-radar — Phase 0 probe")
    records = []
    for provider in CANDIDATES:
        if not os.getenv(provider["key_var"]):
            console.print(f"[red]No {provider['key_var']} in .env — "
                          f"{provider['provider']} models will be skipped[/red]")
        for model in provider["models"]:
            console.print(f"[cyan]Probing[/cyan] {provider['provider']}/{model} ...")
            records.append(probe_model(provider, model))

    # ---- table ----
    table = Table(title="Probe results — the seed of the Model Registry",
                  show_lines=False)
    for col in ["provider", "model", "status", "error_type", "latency", "output hash", "attempts"]:
        table.add_column(col)
    for r in records:
        status_style = {"alive": "green", "down": "red", "skipped": "yellow"}[r["status"]]
        table.add_row(
            r["provider"], r["model"],
            f"[{status_style}]{r['status']}[/{status_style}]",
            r["error_type"] or "-",
            f"{r['latency_ms']} ms" if r["latency_ms"] is not None else "-",
            r["output_hash"] or "-",
            str(r["attempts"]),
        )
    console.print(table)

    alive = sum(1 for r in records if r["status"] == "alive")
    down = sum(1 for r in records if r["status"] == "down")
    console.print(f"\n[bold]{alive} alive · {down} down · "
                  f"{len(records) - alive - down} skipped[/bold]")

    # ---- persist: the registry seed ----
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(
        {"probed_at": utc_now(), "prompt": PROBE_PROMPT, "records": records},
        indent=2), encoding="utf-8")
    console.print(f"[dim]Seed written to {OUT_PATH} — Phase 1's registry.py "
                  f"will ingest this file.[/dim]")
    console.print("[dim]Note: every 'down' is DATA (retired / rate_limited / timeout) — "
                  "that's the radar working, not the script failing.[/dim]")
    console.rule("[bold green]PHASE 0 COMPLETE")


if __name__ == "__main__":
    main()
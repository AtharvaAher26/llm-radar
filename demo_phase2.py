"""Phase 2 demo — the canary runner catching a silent behavior change.

Fully offline: a fake provider plays a model that is healthy, gets its
golden baseline snapshotted (the explicit human step), then "the provider
quietly ships a worse version" — and the radar fires.

Run:  python demo_phase2.py"""
import tempfile
from pathlib import Path

from rich.console import Console
from rich.table import Table

from canary_runner import execute_run
from canary_store import CanaryStore
from migration_report import build_migration_report, render_markdown
from registry import ModelRegistry

console = Console()

# Canned outputs that PASS every deterministic check, one per canary.
CANNED = {
    "json-001": '{"color": "blue", "hex": "#0000FF"}',
    "json-002": '{"name": "Ada", "year": 1815, "skills": ["math", "poetry", "code"]}',
    "json-003": '{"city": "Tokyo", "country": "Japan"}',
    "json-004": '{"ok": true, "code": 200}',
    "cls-001": "negative",
    "cls-002": "positive",
    "cls-003": "spam",
    "cls-004": "python",
    "ins-001": "red\ngreen\nblue",
    "ins-002": "The moon orbits the Earth.\nIt reflects sunlight toward us.",
    "ins-003": "Mars\nVenus\nEarth\nJupiter",
    "ins-004": "STATUS: ok",
    "code-001": "def add(a, b):\n    return a + b",
    "code-002": "def is_even(n):\n    return n % 2 == 0",
    "code-003": "def greet(name):\n    return f'Hello, {name}!'",
    "code-004": "def max_of_three(a, b, c):\n    return max(a, b, c)",
}


def stable_call(case):
    return {"status": "alive", "output": CANNED[case.id],
            "error_type": None, "latency_ms": 120, "attempts": 1}


def drifting_call(case):
    """Same model after a silent provider update: prose instead of JSON,
    refusals instead of labels, broken code."""
    bad = {
        "json_strict": "Sure! The answer you asked for is blue.",
        "classification": "I cannot classify this text.",
        "instruction": "red",
        "code_gen": "def add(a, b)\n    return a + b",
    }
    return {"status": "alive", "output": bad[case.capability],
            "error_type": None, "latency_ms": 140, "attempts": 1}


PROVIDER = {"provider": "fake", "key_var": "FAKE_KEY", "base_url": "", "models": []}

tmp = Path(tempfile.mkdtemp(prefix="llm-radar-demo-"))
store = CanaryStore(tmp / "canaries.json")
store.ensure_seeded()
registry = ModelRegistry(tmp / "registry.json")
paths = {"runs_path": tmp / "runs.json", "alerts_path": tmp / "alerts.json"}

console.rule("[bold cyan]1 · healthy model, first run (no baseline yet)")
results, alerts = execute_run(PROVIDER, "demo-model", store, registry,
                              call_fn=stable_call, **paths)
passed = sum(1 for r in results if r.check_passed)
console.print(f"{passed}/{len(results)} checks passed · {len(alerts)} alerts "
              f"(first measurement — nothing to compare against)")

console.rule("[bold cyan]2 · HUMAN STEP: snapshot the golden baseline")
store.refresh_baseline(dict(CANNED))
console.print(f"Baseline frozen at {store.golden_count()} golden outputs. "
              "The runner will never move this on its own.")

console.rule("[bold cyan]3 · the provider silently ships a worse version")
results, alerts = execute_run(PROVIDER, "demo-model", store, registry,
                              call_fn=drifting_call, **paths)
passed = sum(1 for r in results if r.check_passed)
console.print(f"{passed}/{len(results)} checks passed")
table = Table(title="Drift alerts")
for col in ["model", "capability", "level", "detail"]:
    table.add_column(col)
for a in alerts:
    table.add_row(a.model, a.capability, f"[red]{a.level}[/red]", a.detail)
console.print(table)

console.rule("[bold cyan]4 · should we migrate demo-model -> demo-model-v2?")
execute_run(PROVIDER, "demo-model-v2", store, registry,
            call_fn=stable_call, **paths)
from canary_runner import load_runs
report = build_migration_report(
    "demo-model", "demo-model-v2", load_runs(paths["runs_path"]),
    {c.id: c.capability for c in store.cases})
console.print(render_markdown(report))
console.rule("[bold green]PHASE 2 DEMO COMPLETE — drift caught, verdict rendered")

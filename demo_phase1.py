"""Phase 1 demo — seed the canary suite + build the registry from probe.json.
Run:  python demo_phase1.py"""
from rich.console import Console
from rich.table import Table

from canary_store import CanaryStore
from registry import ModelRegistry

console = Console()

store = CanaryStore()
store.ensure_seeded()
console.print(f"[bold]Canary suite:[/bold] {len(store.cases)} cases "
              f"({store.golden_count()} with golden outputs)")

table = Table(title="Canary suite by capability")
for col in ["id", "capability", "checks configured"]:
    table.add_column(col)
for c in store.cases:
    checks = []
    if c.expected_json_keys: checks.append(f"json:{len(c.expected_json_keys)} keys")
    if c.expected_label or c.expected_labels: checks.append("label")
    if c.expected_content_fragments: checks.append(f"fragments:{len(c.expected_content_fragments)}")
    if c.min_lines is not None or c.max_lines is not None: checks.append("lines")
    if c.must_parse_python: checks.append("py-parse")
    table.add_row(c.id, c.capability, ", ".join(checks))
console.print(table)

reg = ModelRegistry()
n = reg.ingest_probe_seed()
console.print(f"\n[bold]Registry:[/bold] ingested {n} probe records")
t2 = Table(title="Model Registry (from Phase 0 seed)")
for col in ["model", "status", "last_error", "observations"]:
    t2.add_column(col)
for s in reg.summary():
    color = {"active": "green", "degraded": "yellow", "retired": "red"}.get(s["status"], "white")
    t2.add_row(s["key"], f"[{color}]{s['status']}[/{color}]",
               s["last_error_type"] or "-", str(s["observations"]))
console.print(t2)
console.rule("[bold green]PHASE 1 COMPLETE — suite + registry alive")
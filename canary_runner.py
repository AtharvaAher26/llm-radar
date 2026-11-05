"""
canary_runner.py — Phase 2: the engine. Executes the golden canary suite
against a model, persists every result, and raises DriftAlerts when the
model's behavior moves away from its baseline.

Baseline rules (deliberate):
  - The golden baseline lives in CanaryStore (golden_output per canary) and
    changes ONLY via refresh_baseline() — a human command. The runner reads
    it, never writes it: a radar that quietly re-baselines itself would
    report "no drift" forever.
  - With no golden for a capability, the previous recorded run is the
    baseline. With neither, there is nothing to compare — no alert.
  - Output hashes are compared twice over: against the golden outputs and
    against the registry's stored fingerprints from the previous run.

The provider call is injectable (call_fn) so the whole engine is testable
offline with a fake provider. The default call_fn is probe.call_model —
the same temperature-0 call path the Phase 0 probe uses.

Run:  python canary_runner.py            (all candidate models)
"""
import json
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table

import probe
from canary_store import CanaryStore
from checks import evaluate
from models import CanaryCase, DriftAlert, RunResult, utc_now
from registry import ModelRegistry

RUNS_PATH = Path("data/runs.json")
ALERTS_PATH = Path("data/alerts.json")

# A capability's pass rate must fall at least this far below baseline to
# count as drift; twice this (or a total collapse) is BROKEN.
MATERIAL_PASS_DROP = 0.25
BROKEN_PASS_DROP = 0.50

# call_fn(case) -> probe.call_model-shaped dict:
#   {status, output, error_type, latency_ms, attempts}
CallFn = Callable[[CanaryCase], dict]


def make_call_fn(provider: dict, model: str) -> CallFn:
    """Default provider: the real call path from probe.py."""
    def call(case: CanaryCase) -> dict:
        return probe.call_model(provider, model, case.prompt, max_tokens=512)
    return call


# ------------------------------ execution ------------------------------

def run_canaries(provider_name: str, model: str, cases: list[CanaryCase],
                 call_fn: CallFn) -> list[RunResult]:
    """Run every canary once. Pure execution — no persistence, no alerts.
    All results of a run share one timestamp, so a run stays identifiable
    as a batch in the append-only run history."""
    run_ts = utc_now()
    results = []
    for case in cases:
        r = call_fn(case)
        output = r.get("output")
        passed = None
        if r["status"] == "alive" and output is not None:
            passed, _ = evaluate(case, output)
        results.append(RunResult(
            canary_id=case.id, model=model, provider=provider_name,
            status=r["status"], check_passed=passed,
            error_type=r.get("error_type"),
            output=output,
            output_hash=probe.output_hash(output) if output else None,
            empty_output=(r["status"] == "alive" and not (output or "").strip()),
            latency_ms=r.get("latency_ms"), attempts=r.get("attempts", 1),
            timestamp=run_ts,
        ))
    return results


# ----------------------------- persistence -----------------------------

def load_runs(path: Path = RUNS_PATH) -> list[RunResult]:
    if not Path(path).exists():
        return []
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RunResult.model_validate(d) for d in raw]


def append_runs(results: list[RunResult], path: Path = RUNS_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    raw += [json.loads(r.model_dump_json()) for r in results]
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def load_alerts(path: Path = ALERTS_PATH) -> list[DriftAlert]:
    if not Path(path).exists():
        return []
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [DriftAlert.model_validate(d) for d in raw]


def append_alerts(alerts: list[DriftAlert], path: Path = ALERTS_PATH) -> None:
    if not alerts:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    raw += [json.loads(a.model_dump_json()) for a in alerts]
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


# --------------------------- drift comparison ---------------------------

def _pass_rate(results: list[RunResult]) -> float | None:
    evaluated = [r for r in results if r.check_passed is not None]
    if not evaluated:
        return None
    return sum(1 for r in evaluated if r.check_passed) / len(evaluated)


def _golden_baseline(store: CanaryStore, capability: str) -> tuple[float | None, dict[str, str]]:
    """Baseline from golden outputs: the pass rate the golden outputs
    themselves score against the deterministic checks, plus their hashes.
    (None, {}) when this capability has no golden snapshot yet."""
    goldens = [c for c in store.by_capability(capability) if c.golden_output is not None]
    if not goldens:
        return None, {}
    passed = sum(1 for c in goldens if evaluate(c, c.golden_output)[0])
    hashes = {c.id: probe.output_hash(c.golden_output) for c in goldens}
    return passed / len(goldens), hashes


def _previous_rate(prior_runs: list[RunResult], model: str,
                   capability: str, cases: list[CanaryCase]) -> float | None:
    """Fallback baseline: this model's pass rate in its most recent prior
    run (identified by the run's shared timestamp batch)."""
    ids = {c.id for c in cases}
    mine = [r for r in prior_runs if r.model == model and r.canary_id in ids]
    if not mine:
        return None
    latest = max(r.timestamp for r in mine)
    batch = [r for r in mine if r.timestamp == latest]
    return _pass_rate(batch)


def assess_capability(model: str, capability: str, results: list[RunResult],
                      baseline_rate: float | None, golden_hashes: dict[str, str],
                      changed_ids: set[str]) -> DriftAlert | None:
    """Compare one capability's fresh results against its baseline.
    Returns a DriftAlert for DRIFTED / BROKEN / RETIRED, else None."""
    if any(r.error_type == "retired_or_unavailable" for r in results):
        return DriftAlert(model=model, capability=capability, level="RETIRED",
                          detail="provider returned 404/410 — model retired or unavailable")
    current_rate = _pass_rate(results)
    if current_rate is None:
        if baseline_rate is not None and results:
            return DriftAlert(model=model, capability=capability, level="BROKEN",
                              detail="run produced no evaluable output "
                                     f"({len(results)} canaries, all down/skipped)")
        return None
    if baseline_rate is None and not golden_hashes:
        return None     # first measurement — nothing to compare against yet

    drop = (baseline_rate - current_rate) if baseline_rate is not None else 0.0
    # behavior changed but checks still pass: hash moved vs golden or vs
    # the fingerprint stored from the previous run
    hash_moved = sorted(
        r.canary_id for r in results
        if r.check_passed and r.output_hash and (
            (r.canary_id in golden_hashes and golden_hashes[r.canary_id] != r.output_hash)
            or r.canary_id in changed_ids))

    if drop >= BROKEN_PASS_DROP or (current_rate == 0.0 and (baseline_rate or 0) > 0):
        level = "BROKEN"
    elif drop >= MATERIAL_PASS_DROP or hash_moved:
        level = "DRIFTED"
    else:
        return None
    detail = (f"pass rate {current_rate:.0%}"
              + (f" vs baseline {baseline_rate:.0%} (drop {drop:.0%})"
                 if baseline_rate is not None else " (no golden pass baseline)")
              + (f"; output hash changed on: {', '.join(hash_moved)}" if hash_moved else ""))
    return DriftAlert(model=model, capability=capability, level=level, detail=detail)


def compare_run(results: list[RunResult], store: CanaryStore,
                prior_runs: list[RunResult], changed_ids: set[str]) -> list[DriftAlert]:
    """One DriftAlert per drifted capability in this run."""
    alerts = []
    model = results[0].model if results else ""
    for capability in sorted({c.capability for c in store.cases}):
        cases = store.by_capability(capability)
        ids = {c.id for c in cases}
        cap_results = [r for r in results if r.canary_id in ids]
        if not cap_results:
            continue
        baseline_rate, golden_hashes = _golden_baseline(store, capability)
        if baseline_rate is None:
            baseline_rate = _previous_rate(prior_runs, model, capability, cases)
        alert = assess_capability(model, capability, cap_results,
                                  baseline_rate, golden_hashes, changed_ids)
        if alert:
            alerts.append(alert)
    return alerts


# ----------------------------- orchestration -----------------------------

def execute_run(provider: dict, model: str, store: CanaryStore,
                registry: ModelRegistry, call_fn: CallFn | None = None,
                runs_path: Path = RUNS_PATH,
                alerts_path: Path = ALERTS_PATH) -> tuple[list[RunResult], list[DriftAlert]]:
    """Full pipeline for one model: run -> persist -> fingerprint ->
    compare vs baseline -> persist alerts. Never touches the golden
    baseline (human refresh only)."""
    prior_runs = load_runs(runs_path)
    results = run_canaries(provider["provider"], model, store.cases,
                           call_fn or make_call_fn(provider, model))
    changed = registry.record_run(provider["provider"], model, results)
    append_runs(results, runs_path)
    alerts = compare_run(results, store, prior_runs, set(changed))
    append_alerts(alerts, alerts_path)
    return results, alerts


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    console = Console()
    console.rule("[bold cyan]llm-radar — Phase 2 canary run")
    store = CanaryStore()
    store.ensure_seeded()
    registry = ModelRegistry()
    all_alerts: list[DriftAlert] = []
    for provider in probe.CANDIDATES:
        for model in provider["models"]:
            console.print(f"[cyan]Running canaries[/cyan] {provider['provider']}/{model} ...")
            results, alerts = execute_run(provider, model, store, registry)
            rate = _pass_rate(results)
            rate_txt = f"{rate:.0%} checks passed" if rate is not None else "no evaluable output"
            console.print(f"    {len(results)} canaries — {rate_txt}, "
                          f"{len(alerts)} drift alert(s)")
            all_alerts += alerts
    if all_alerts:
        table = Table(title="Drift alerts")
        for col in ["model", "capability", "level", "detail"]:
            table.add_column(col)
        for a in all_alerts:
            color = {"RETIRED": "red", "BROKEN": "red", "DRIFTED": "yellow"}[a.level]
            table.add_row(a.model, a.capability, f"[{color}]{a.level}[/{color}]", a.detail)
        console.print(table)
    else:
        console.print("[green]No drift detected against current baselines.[/green]")
    console.print(f"[dim]Results appended to {RUNS_PATH}; alerts to {ALERTS_PATH}. "
                  f"Golden baselines unchanged (human refresh only).[/dim]")
    console.rule("[bold green]PHASE 2 RUN COMPLETE")


if __name__ == "__main__":
    main()

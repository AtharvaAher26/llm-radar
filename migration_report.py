"""
migration_report.py — Phase 2: go/no-go migration reports.

Builds a MigrationReport from the canary runner's persisted results
(data/runs.json): the latest run of the FROM model vs the latest run of
the TO model, capability by capability, with an explicit verdict.

Verdict thresholds (constants, not vibes):
  RISKY   — TO pass rate regresses > 15 pts vs FROM, or TO's absolute
            pass rate < 80%, or a capability collapses (FROM passed,
            TO scores 0), or either model has no run data at all.
  CAUTION — regression > 5 pts, any single capability drops > 25 pts,
            or TO is > 1.5x slower on average than FROM.
  SAFE    — none of the above.

Run:  python migration_report.py FROM_MODEL TO_MODEL [--out report.md]
"""
import argparse
from pathlib import Path

from canary_runner import RUNS_PATH, _pass_rate, load_runs
from models import MigrationReport, RunResult

RISKY_REGRESSION = 0.15
CAUTION_REGRESSION = 0.05
MIN_ACCEPTABLE_RATE = 0.80
CAPABILITY_CAUTION_DROP = 0.25
CAUTION_LATENCY_RATIO = 1.5


def latest_batch(runs: list[RunResult], model: str) -> list[RunResult]:
    """A model's most recent run (results share one batch timestamp)."""
    mine = [r for r in runs if r.model == model]
    if not mine:
        return []
    latest = max(r.timestamp for r in mine)
    return [r for r in mine if r.timestamp == latest]


def _avg_latency(results: list[RunResult]) -> float | None:
    vals = [r.latency_ms for r in results if r.latency_ms is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _model_summary(results: list[RunResult]) -> dict:
    evaluated = [r for r in results if r.check_passed is not None]
    return {
        "canaries_run": len(results),
        "canaries_evaluated": len(evaluated),
        "pass_rate": _pass_rate(results),
        "avg_latency_ms": _avg_latency(results),
    }


def build_migration_report(from_model: str, to_model: str,
                           runs: list[RunResult],
                           capability_of: dict[str, str] | None = None) -> MigrationReport:
    """capability_of maps canary_id -> capability; when omitted, per-
    capability rows are skipped and only overall rates drive the verdict."""
    from_batch = latest_batch(runs, from_model)
    to_batch = latest_batch(runs, to_model)
    fs, ts = _model_summary(from_batch), _model_summary(to_batch)
    reasons: list[str] = []

    rows = []
    capability_collapse = False
    capability_caution = False
    if capability_of:
        for cap in sorted(set(capability_of.values())):
            ids = {cid for cid, c in capability_of.items() if c == cap}
            f_rate = _pass_rate([r for r in from_batch if r.canary_id in ids])
            t_rate = _pass_rate([r for r in to_batch if r.canary_id in ids])
            rows.append({
                "capability": cap,
                "from_pass_rate": f_rate, "to_pass_rate": t_rate,
                "delta": (round(t_rate - f_rate, 3)
                          if f_rate is not None and t_rate is not None else None),
                "from_avg_latency_ms": _avg_latency([r for r in from_batch if r.canary_id in ids]),
                "to_avg_latency_ms": _avg_latency([r for r in to_batch if r.canary_id in ids]),
            })
            if f_rate and t_rate == 0.0:
                capability_collapse = True
                reasons.append(f"capability collapse: {cap} goes {f_rate:.0%} -> 0%")
            elif (f_rate is not None and t_rate is not None
                  and f_rate - t_rate > CAPABILITY_CAUTION_DROP):
                capability_caution = True
                reasons.append(f"{cap} drops {f_rate:.0%} -> {t_rate:.0%}")

    f_rate, t_rate = fs["pass_rate"], ts["pass_rate"]
    if f_rate is None or t_rate is None:
        verdict = "RISKY"
        missing = from_model if f_rate is None else to_model
        reasons.insert(0, f"no canary run data for {missing} — migrating blind")
    else:
        regression = f_rate - t_rate
        if regression > RISKY_REGRESSION or t_rate < MIN_ACCEPTABLE_RATE or capability_collapse:
            verdict = "RISKY"
        elif (regression > CAUTION_REGRESSION or capability_caution
              or (fs["avg_latency_ms"] and ts["avg_latency_ms"]
                  and ts["avg_latency_ms"] > CAUTION_LATENCY_RATIO * fs["avg_latency_ms"])):
            verdict = "CAUTION"
        else:
            verdict = "SAFE"
        if regression > RISKY_REGRESSION:
            reasons.insert(0, f"pass rate regresses {f_rate:.0%} -> {t_rate:.0%}")
        if t_rate < MIN_ACCEPTABLE_RATE:
            reasons.append(f"target pass rate {t_rate:.0%} below the "
                           f"{MIN_ACCEPTABLE_RATE:.0%} floor")
        if verdict == "SAFE":
            reasons.append(f"pass rate {f_rate:.0%} -> {t_rate:.0%}, "
                           "no capability collapse, latency comparable")

    return MigrationReport(
        from_model=from_model, to_model=to_model, rows=rows,
        summary={"from": fs, "to": ts, "reasons": reasons}, verdict=verdict)


def _pct(x: float | None) -> str:
    return f"{x:.0%}" if x is not None else "—"


def _ms(x: float | None) -> str:
    return f"{x:.0f} ms" if x is not None else "—"


def render_markdown(report: MigrationReport) -> str:
    fs, ts = report.summary["from"], report.summary["to"]
    lines = [
        f"# Migration report: {report.from_model} → {report.to_model}",
        "",
        f"**Verdict: {report.verdict}** · generated {report.generated_at}",
        "",
        "## Overall",
        "",
        "| model | pass rate | avg latency | canaries evaluated |",
        "|---|---|---|---|",
        f"| {report.from_model} | {_pct(fs['pass_rate'])} | {_ms(fs['avg_latency_ms'])} "
        f"| {fs['canaries_evaluated']}/{fs['canaries_run']} |",
        f"| {report.to_model} | {_pct(ts['pass_rate'])} | {_ms(ts['avg_latency_ms'])} "
        f"| {ts['canaries_evaluated']}/{ts['canaries_run']} |",
        "",
    ]
    if report.rows:
        lines += ["## By capability", "",
                  "| capability | from | to | delta | from latency | to latency |",
                  "|---|---|---|---|---|---|"]
        for r in report.rows:
            delta = f"{r['delta']:+.0%}" if r["delta"] is not None else "—"
            lines.append(f"| {r['capability']} | {_pct(r['from_pass_rate'])} "
                         f"| {_pct(r['to_pass_rate'])} | {delta} "
                         f"| {_ms(r['from_avg_latency_ms'])} | {_ms(r['to_avg_latency_ms'])} |")
        lines.append("")
    lines += ["## Why", ""] + [f"- {reason}" for reason in report.summary["reasons"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a migration report from canary runs.")
    ap.add_argument("from_model")
    ap.add_argument("to_model")
    ap.add_argument("--runs", type=Path, default=RUNS_PATH)
    ap.add_argument("--out", type=Path, default=None,
                    help="also write the markdown report to this file")
    args = ap.parse_args()

    from canary_store import CanaryStore
    store = CanaryStore()
    store.ensure_seeded()
    capability_of = {c.id: c.capability for c in store.cases}
    report = build_migration_report(args.from_model, args.to_model,
                                    load_runs(args.runs), capability_of)
    md = render_markdown(report)
    print(md)
    if args.out:
        args.out.write_text(md, encoding="utf-8")
        print(f"(written to {args.out})")


if __name__ == "__main__":
    main()

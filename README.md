# llm-radar

A radar that continuously probes LLM models with a golden canary suite — catching silent provider behavior changes and model retirements automatically, and generating go/no-go migration reports before you switch models.

Providers silently change the models behind an API name: outputs drift, formats break, models get retired. llm-radar measures that instead of discovering it in production. It runs a fixed suite of 16 deterministic prompts ("canaries") against candidate models, checks every output with deterministic rules (no LLM judge), fingerprints each output with a hash, and compares runs against a golden baseline. When a model's pass rate collapses, its output hashes move, or the provider starts returning 404/410, the radar raises a drift alert — and when you're considering a switch, it renders a migration report with an explicit SAFE / CAUTION / RISKY verdict.

## Status — what works today

**Working (Phases 0–2):**

- **Probe** — pings every candidate model with a fixed prompt, classifies *every* failure (`retired_or_unavailable`, `rate_limited`, `timeout`, `auth_error`, …), hashes every output. A failure is recorded as data, never routed around: no failover, ever.
- **Canary suite** — 16 cases across 4 capabilities (`json_strict`, `classification`, `instruction`, `code_gen`), all scored by deterministic checks: JSON keys, exact labels, content fragments, line counts, and Python parseability.
- **Canary runner** — executes the whole suite against a model, persists every result, and raises `DriftAlert`s per capability: `RETIRED` (404/410), `BROKEN` (pass rate drops ≥ 50 pts, or collapses to 0), `DRIFTED` (drop ≥ 25 pts, or the output hash changes on a canary that still passes its checks).
- **Model registry** — tracks each model's status over time and stores its behavior fingerprints (probe hash + per-canary hashes) so changes are compared across runs, not discarded.
- **Migration reports** — latest run vs latest run, capability by capability, rendered to markdown with the verdict thresholds stated in code.
- **Scheduled runs** — a GitHub Actions workflow runs the probe and the canary suite daily and uploads the radar data as an artifact.
- **28 tests, all offline** — including a fake-provider suite that plays a healthy model, a silently-drifting model, and a retired model.

**Design principles that are load-bearing:**

- **The golden baseline never moves itself.** Baseline outputs are snapshotted only by the explicit `refresh_baseline()` command. A radar that quietly re-baselines would report "no drift" forever.
- **Timeouts are not retirements.** Only a 404/410 retires a model in the registry; a timeout marks it degraded.
- **No keys, no crash.** Without API keys every model is recorded as `skipped`/`no_key` and the tooling completes normally — that's also how the CI workflow behaves before secrets are configured.

**Not built yet:** semantic/embedding-based checks (the deterministic layer is Signal 1 by design), alerting integrations (Slack/webhook), and a historical trend UI. Data is plain JSON files under `data/`.

## Architecture

```
probe.py           Phase 0 probe + the single shared call path (call_model):
                   temperature-0 OpenAI-SDK calls, failure classification,
                   output hashing. Used by the probe AND the runner.
checks.py          Deterministic checks. Pure functions, fully offline.
canary_store.py    The 16-case suite + golden baseline lifecycle
                   (set_golden / refresh_baseline — explicit, human-only).
canary_runner.py   The engine: run suite -> persist results -> fingerprint
                   -> compare vs baseline -> persist DriftAlerts.
                   The provider call is injectable, so tests run offline.
registry.py        Model status over time + behavior fingerprints,
                   compared across runs.
migration_report.py  SAFE / CAUTION / RISKY reports from persisted runs.
models.py          Pydantic v2 schemas: CanaryCase, RunResult,
                   DriftAlert, MigrationReport.
data/              JSON state (gitignored): probe.json, canaries.json,
                   registry.json, runs.json, alerts.json
```

## Quick start

```bash
pip install -r requirements.txt
```

API keys (either or both; missing keys only skip that provider's models) — put them in a `.env` file or export them:

- `GEMINI_API_KEY` — Google AI Studio key, for the Gemini candidates
- `NVIDIA_API_KEY` — NVIDIA NIM key, for the NIM candidates

```bash
python probe.py                       # Phase 0: probe + registry seed
python demo_phase1.py                 # Phase 1: suite + registry overview
python canary_runner.py               # Phase 2: full canary run + drift check
python migration_report.py gemini-3.5-flash gemini-3.8-flash --out report.md
python demo_phase2.py                 # offline demo: a fake model drifts, radar fires
pytest                                # 28 tests, no network, no keys needed
```

To snapshot or refresh the golden baseline (deliberately a manual step — do it when you've reviewed a model's outputs and trust them):

```python
from canary_store import CanaryStore
store = CanaryStore(); store.ensure_seeded()
store.refresh_baseline({"json-001": '{"color": "blue", "hex": "#0000FF"}', ...})
```

With no baseline for a capability, the runner falls back to comparing against the model's previous recorded run; with neither, it records the run and raises no alert — a first measurement can't drift from itself.

## What the data looks like

One canary result (`data/runs.json`):

```json
{
  "canary_id": "json-001",
  "model": "demo-model",
  "provider": "fake",
  "status": "alive",
  "check_passed": true,
  "error_type": null,
  "output": "{\"color\": \"blue\", \"hex\": \"#0000FF\"}",
  "output_hash": "2e16e57dc9fb7a58",
  "empty_output": false,
  "latency_ms": 120,
  "attempts": 1,
  "timestamp": "2026-11-05T14:09:50.872942+00:00"
}
```

One drift alert (`data/alerts.json`):

```json
{
  "model": "demo-model",
  "capability": "classification",
  "level": "BROKEN",
  "detail": "pass rate 0% vs baseline 100% (drop 100%)",
  "timestamp": "2026-11-05T14:09:50.881399+00:00"
}
```

A migration report renders as markdown: an overall table (pass rate, average latency, canaries evaluated per model), a per-capability table with deltas, the verdict, and the reasons behind it — run `python demo_phase2.py` to see one end to end.

## Verdict thresholds (migration reports)

- **RISKY** — target's pass rate regresses > 15 pts vs the current model, or falls below 80% absolute, or a capability collapses (was passing, now 0%), or either model has no run data.
- **CAUTION** — regression > 5 pts, any single capability drops > 25 pts, or the target is > 1.5× slower on average.
- **SAFE** — none of the above.

## License

MIT — see [LICENSE](LICENSE).

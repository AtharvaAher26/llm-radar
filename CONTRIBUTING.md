# Contributing to llm-radar

llm-radar continuously probes LLM models with a golden canary suite —
catching silent provider behaviour changes and model retirements
automatically, and generating go/no-go migration reports before you
switch models. It runs a fixed suite of 16 deterministic prompts
("canaries") against candidate models, checks every output with
deterministic rules (no LLM judge), fingerprints each output with a
hash, and compares runs against a golden baseline.

## Setup

Requires Python 3.12.

```bash
git clone https://github.com/AtharvaAher26/llm-radar.git
cd llm-radar
pip install -r requirements.txt
```

**Keys (either or both):** put them in a `.env` file or export them.

- `GEMINI_API_KEY` — Google AI Studio key, for the Gemini candidates
- `NVIDIA_API_KEY` — NVIDIA NIM key, for the NIM candidates

There is **no Groq provider** in this repo — don't add one expecting
an existing key path. Missing keys only skip that provider's models;
see "No keys, no crash" below.

## Tests

```bash
pytest test_phase1.py test_phase2.py
```

28 tests, all offline — including a fake-provider suite that plays a
healthy model, a silently-drifting model, and a retired model. No
network calls, no keys needed. Run the full suite before opening a
pull request. New behaviour needs a test; a fix needs a regression
test that fails without it.

The daily probe runs in CI (`.github/workflows/probe.yml`, 06:17 UTC)
and degrades gracefully without repository secrets — every model is
recorded as `skipped`/`no_key` and the run still succeeds. Repository
secrets live in Settings → Secrets → Actions (see the README's
"Repository secrets" note).

## Ground rules

These principles are load-bearing — a change that breaks one is a
regression, however convenient it looks:

- **The golden baseline never moves itself.** Baseline outputs are
  snapshotted only by the explicit `refresh_baseline()` command. A
  radar that quietly re-baselines would report "no drift" forever.
- **Timeouts are not retirements.** Only a 404/410 retires a model in
  the registry; a timeout marks it degraded.
- **No failover, ever.** A failure is recorded as data, never routed
  around. The probe and the runner share one call path (`call_model`
  in `probe.py`) — keep it that way.
- **No keys, no crash.** Without API keys every model is recorded as
  `skipped`/`no_key` and the tooling completes normally. Don't add a
  hard dependency on a key being present.
- **Deterministic checks only.** Canary outputs are scored by
  deterministic rules (JSON keys, exact labels, content fragments,
  line counts, Python parseability) — no LLM judge. Keep the checks in
  `checks.py` pure functions, fully offline.

## Pull requests

- One focused change per PR, with a clear title and a short description
  of what changed and why.
- Keep the README's status section honest: if a feature lands, say so;
  if something is deferred, say so.
- Don't commit secrets, API keys, or real probe data — `data/` is
  gitignored for a reason. Fixtures and the fake-provider suite are the
  way to show behaviour.

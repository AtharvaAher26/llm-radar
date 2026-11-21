---
name: Bug report
about: Something in llm-radar didn't work as expected
title: "[Bug] "
labels: bug
---

**What happened**
A clear description of the bug.

**What you expected**
What llm-radar should have done instead.

**Steps to reproduce**
1. Command you ran (e.g. `pytest test_phase1.py test_phase2.py`, the daily probe, `refresh_baseline()`)
2. Provider / model involved (Gemini or NVIDIA NIM — there is no Groq provider)
3. Where it failed

**Environment**
- OS:
- Python version (`python --version`):
- Keys present? (GEMINI_API_KEY / NVIDIA_API_KEY — say which, never paste the key itself)

**Probe / canary details**
- Canary or check that misbehaved (if any):
- Was the golden baseline involved? (baseline drift, unexpected re-baseline, timeout vs retirement)
- Did a model get marked retired, degraded, or skipped — and was that correct?

**Sample output**
Paste the relevant output, report excerpt, or log here — remove API
keys and anything private first. If a canary verdict looks wrong,
paste the canary prompt and the output hash / verdict.

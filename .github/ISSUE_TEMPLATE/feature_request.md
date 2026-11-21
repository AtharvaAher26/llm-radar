---
name: Feature request
about: Suggest an idea for llm-radar
title: "[Feature] "
labels: enhancement
---

**Problem**
What gap does this solve? (A provider behaviour you can't catch
today, a model you can't probe, a report the migration workflow
needs.)

**Proposed change**
What should llm-radar do differently? Be concrete: a new canary, a
new provider, a new check in `checks.py`, a report field.

**Scope note**
llm-radar scores canaries with deterministic rules only — no LLM
judge. If your idea needs a judge, say so; it likely doesn't fit.
And remember the load-bearing rules (see CONTRIBUTING.md): the
golden baseline never moves itself, timeouts are not retirements,
no failover ever, no keys no crash. A feature that breaks one of
those is a regression, not an improvement.

**Alternatives considered**
Anything else you tried, or a simpler version of the same idea.

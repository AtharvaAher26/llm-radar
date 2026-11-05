"""Phase 2 tests — canary runner, drift alerts, fingerprints, migration
reports. Zero network: every provider is a fake returning canned outputs.
Run:  pytest test_phase2.py -v"""
import json

from canary_runner import (append_runs, assess_capability, execute_run,
                           load_alerts, load_runs, run_canaries)
from canary_store import CanaryStore, seed_canaries
from migration_report import build_migration_report, render_markdown
from models import RunResult
from registry import ModelRegistry

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

PROVIDER = {"provider": "fake", "key_var": "FAKE_KEY", "base_url": "", "models": []}


def stable_call(case):
    return {"status": "alive", "output": CANNED[case.id],
            "error_type": None, "latency_ms": 100, "attempts": 1}


def drifting_call(case):
    bad = {
        "json_strict": "Sure! The answer you asked for is blue.",
        "classification": "I cannot classify this text.",
        "instruction": "red",
        "code_gen": "def add(a, b)\n    return a + b",
    }
    return {"status": "alive", "output": bad[case.capability],
            "error_type": None, "latency_ms": 100, "attempts": 1}


def hash_moved_call(case):
    """Outputs still pass every check but differ from the golden text."""
    moved = dict(CANNED)
    moved["json-001"] = '{"color": "blue", "hex": "#00F"}'
    moved["ins-001"] = "blue\ngreen\nred"
    return {"status": "alive", "output": moved[case.id],
            "error_type": None, "latency_ms": 100, "attempts": 1}


def retired_call(case):
    return {"status": "down", "output": None,
            "error_type": "retired_or_unavailable", "latency_ms": 30, "attempts": 1}


def _env(tmp_path):
    store = CanaryStore(tmp_path / "canaries.json")
    store.ensure_seeded()
    reg = ModelRegistry(tmp_path / "registry.json")
    paths = {"runs_path": tmp_path / "runs.json",
             "alerts_path": tmp_path / "alerts.json"}
    return store, reg, paths


# ---------- canary_runner.py ----------

def test_runner_executes_suite_and_persists(tmp_path):
    store, reg, paths = _env(tmp_path)
    results, alerts = execute_run(PROVIDER, "m1", store, reg,
                                  call_fn=stable_call, **paths)
    assert len(results) == 16
    assert all(r.check_passed for r in results)
    assert all(r.output_hash for r in results)
    assert alerts == []                       # no baseline yet -> nothing to compare
    assert store.golden_count() == 0          # runner NEVER writes the baseline
    assert len(load_runs(paths["runs_path"])) == 16

def test_runner_detects_broken_drift_after_baseline(tmp_path):
    store, reg, paths = _env(tmp_path)
    execute_run(PROVIDER, "m1", store, reg, call_fn=stable_call, **paths)
    store.refresh_baseline(dict(CANNED))      # the explicit human step
    _, alerts = execute_run(PROVIDER, "m1", store, reg,
                            call_fn=drifting_call, **paths)
    assert {a.capability for a in alerts} == {
        "json_strict", "classification", "instruction", "code_gen"}
    assert all(a.level == "BROKEN" for a in alerts)
    assert len(load_alerts(paths["alerts_path"])) == len(alerts)   # persisted

def test_runner_detects_hash_only_drift(tmp_path):
    store, reg, paths = _env(tmp_path)
    execute_run(PROVIDER, "m1", store, reg, call_fn=stable_call, **paths)
    store.refresh_baseline(dict(CANNED))
    results, alerts = execute_run(PROVIDER, "m1", store, reg,
                                  call_fn=hash_moved_call, **paths)
    assert all(r.check_passed for r in results)          # checks still pass...
    assert {a.capability for a in alerts} == {"json_strict", "instruction"}
    assert all(a.level == "DRIFTED" for a in alerts)     # ...but behavior moved

def test_runner_retired_model_alerts_without_baseline(tmp_path):
    store, reg, paths = _env(tmp_path)
    _, alerts = execute_run(PROVIDER, "m1", store, reg,
                            call_fn=retired_call, **paths)
    assert len(alerts) == 4
    assert all(a.level == "RETIRED" for a in alerts)
    assert reg.models["fake/m1"]["status"] == "retired"

def test_runner_second_run_compares_to_first_without_golden(tmp_path):
    store, reg, paths = _env(tmp_path)
    execute_run(PROVIDER, "m1", store, reg, call_fn=stable_call, **paths)
    _, alerts = execute_run(PROVIDER, "m1", store, reg,
                            call_fn=drifting_call, **paths)
    # no goldens: previous run is the baseline -> collapse still caught
    assert alerts and all(a.level == "BROKEN" for a in alerts)

def test_assess_no_baseline_no_alert():
    cases = [c for c in seed_canaries() if c.capability == "json_strict"]
    results = run_canaries("fake", "m1", cases, stable_call)
    alert = assess_capability("m1", "json_strict", results, None, {}, set())
    assert alert is None


# ---------- registry fingerprints ----------

def test_fingerprints_store_and_change_detection(tmp_path):
    reg = ModelRegistry(tmp_path / "r.json")
    assert reg.record_fingerprints("g", "m", {"json-001": "aaa"}) == []
    assert reg.fingerprint("g", "m", "json-001") == "aaa"
    assert reg.record_fingerprints("g", "m", {"json-001": "aaa"}) == []
    assert reg.record_fingerprints("g", "m", {"json-001": "bbb"}) == ["json-001"]
    reg2 = ModelRegistry(tmp_path / "r.json")            # survives restart
    assert reg2.fingerprint("g", "m", "json-001") == "bbb"

def test_record_run_stores_canary_fingerprints(tmp_path):
    reg = ModelRegistry(tmp_path / "r.json")
    def rr(cid, h):
        return RunResult(canary_id=cid, model="m", provider="g",
                         status="alive", check_passed=True, output_hash=h)
    assert reg.record_run("g", "m", [rr("json-001", "h1")]) == []
    assert reg.record_run("g", "m", [rr("json-001", "h2")]) == ["json-001"]
    assert reg.models["g/m"]["status"] == "active"

def test_probe_seed_stores_probe_fingerprint(tmp_path):
    p = tmp_path / "probe.json"
    p.write_text(json.dumps({"probed_at": "t", "prompt": "x", "records": [
        {"provider": "g", "model": "m", "status": "alive", "probed_at": "t",
         "error_type": None, "output_hash": "probehash1"}]}), encoding="utf-8")
    reg = ModelRegistry(tmp_path / "r.json")
    reg.ingest_probe_seed(p)
    assert reg.fingerprint("g", "m", "probe") == "probehash1"


# ---------- migration_report.py ----------

def _runs(model, ts, passed_ids, all_ids):
    return [RunResult(canary_id=cid, model=model, provider="fake",
                      status="alive", check_passed=cid in passed_ids,
                      output_hash="h", latency_ms=100, timestamp=ts)
            for cid in all_ids]

ALL_IDS = sorted(CANNED)
CAP_OF = {c.id: c.capability for c in seed_canaries()}

def test_migration_safe():
    runs = (_runs("a", "t1", set(ALL_IDS), ALL_IDS)
            + _runs("b", "t2", set(ALL_IDS), ALL_IDS))
    rep = build_migration_report("a", "b", runs, CAP_OF)
    assert rep.verdict == "SAFE"
    assert rep.summary["to"]["pass_rate"] == 1.0

def test_migration_risky_on_big_regression():
    runs = (_runs("a", "t1", set(ALL_IDS), ALL_IDS)
            + _runs("b", "t2", set(ALL_IDS[:8]), ALL_IDS))
    rep = build_migration_report("a", "b", runs, CAP_OF)
    assert rep.verdict == "RISKY"

def test_migration_caution_on_small_regression():
    runs = (_runs("a", "t1", set(ALL_IDS), ALL_IDS)
            + _runs("b", "t2", set(ALL_IDS) - {"code-001"}, ALL_IDS))
    rep = build_migration_report("a", "b", runs, CAP_OF)
    assert rep.verdict == "CAUTION"      # 100% -> 93.75%: >5pt, <15pt

def test_migration_risky_without_data():
    rep = build_migration_report("a", "ghost", _runs("a", "t1", set(ALL_IDS), ALL_IDS),
                                 CAP_OF)
    assert rep.verdict == "RISKY"

def test_migration_markdown_render():
    runs = (_runs("a", "t1", set(ALL_IDS), ALL_IDS)
            + _runs("b", "t2", set(ALL_IDS), ALL_IDS))
    md = render_markdown(build_migration_report("a", "b", runs, CAP_OF))
    assert "**Verdict: SAFE**" in md
    assert "a → b" in md and "json_strict" in md

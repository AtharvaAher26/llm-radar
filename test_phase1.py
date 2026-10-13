"""Phase 1 tests — models, checks, stores, registry. Zero network.
Run:  pytest test_phase1.py -v"""
import json

import pytest

from canary_store import CanaryStore, seed_canaries
from checks import (check_json_keys, check_label, check_line_count,
                    check_python_parse, evaluate)
from models import CanaryCase
from registry import ModelRegistry


# ---------- checks.py ----------

def test_json_keys_pass():
    case = CanaryCase(id="t", capability="json_strict", prompt="x",
                      expected_json_keys=["a", "b"])
    ok, detail = check_json_keys(case, '{"a": 1, "b": 2}')
    assert ok

def test_json_keys_with_fence():
    case = CanaryCase(id="t", capability="json_strict", prompt="x",
                      expected_json_keys=["a"])
    ok, _ = check_json_keys(case, 'Here you go:\n```json\n{"a": 1}\n```')
    assert ok

def test_json_keys_fail():
    case = CanaryCase(id="t", capability="json_strict", prompt="x",
                      expected_json_keys=["a", "b"])
    ok, detail = check_json_keys(case, '{"a": 1}')
    assert not ok and "b" in detail

def test_label_exact():
    case = CanaryCase(id="t", capability="classification", prompt="x",
                      expected_label="negative")
    ok, _ = check_label(case, "Negative")
    assert ok

def test_label_in_set():
    case = CanaryCase(id="t", capability="classification", prompt="x",
                      expected_labels=["spam", "not_spam"])
    ok, _ = check_label(case, "The answer is: spam")
    assert ok

def test_line_count_exact():
    case = CanaryCase(id="t", capability="instruction", prompt="x",
                      min_lines=3, max_lines=3)
    ok, _ = check_line_count(case, "red\ngreen\nblue")
    assert ok
    ok, detail = check_line_count(case, "red\ngreen\nblue\nyellow")
    assert not ok

def test_python_parse_ok_and_fail():
    case = CanaryCase(id="t", capability="code_gen", prompt="x", must_parse_python=True)
    ok, _ = check_python_parse(case, "def add(a, b):\n    return a + b")
    assert ok
    ok, detail = check_python_parse(case, "def add(a, b)\n    return a + b")
    assert not ok and "SyntaxError" in detail

def test_evaluate_runs_all_configured():
    case = CanaryCase(id="t", capability="code_gen", prompt="x",
                      must_parse_python=True, expected_content_fragments=["def add"])
    ok, failures = evaluate(case, "def add(a, b):\n    return a + b")
    assert ok and failures == []


# ---------- canary_store.py ----------

def test_seed_suite_shape():
    cases = seed_canaries()
    assert len(cases) == 16
    caps = {c.capability for c in cases}
    assert caps == {"json_strict", "classification", "instruction", "code_gen"}
    assert len({c.id for c in cases}) == 16      # unique ids

def test_store_seeds_once_and_persists(tmp_path):
    p = tmp_path / "canaries.json"
    s1 = CanaryStore(p)
    s1.ensure_seeded()
    assert s1.golden_count() == 0                # no golden outputs yet
    s1.set_golden("json-001", '{"color": "blue"}')
    s2 = CanaryStore(p)                          # simulated restart
    assert s2.golden_count() == 1
    s2.ensure_seeded()                           # must NOT overwrite existing store
    assert s2.golden_count() == 1

def test_refresh_baseline(tmp_path):
    s = CanaryStore(tmp_path / "c.json")
    s.ensure_seeded()
    s.refresh_baseline({"json-001": '{"color":"blue"}', "cls-001": "negative"})
    assert s.golden_count() == 2


# ---------- registry.py ----------

def _write_probe(tmp_path, records):
    p = tmp_path / "probe.json"
    p.write_text(json.dumps({"probed_at": "now", "prompt": "x", "records": records}),
                 encoding="utf-8")
    return p

def test_registry_ingest_alive_retired_timeout(tmp_path):
    p = _write_probe(tmp_path, [
        {"provider": "gemini", "model": "m1", "status": "alive",
         "probed_at": "t1", "error_type": None},
        {"provider": "nvidia", "model": "m2", "status": "down",
         "probed_at": "t2", "error_type": "retired_or_unavailable"},
        {"provider": "nvidia", "model": "m3", "status": "down",
         "probed_at": "t3", "error_type": "timeout"},
    ])
    reg = ModelRegistry(tmp_path / "reg.json")
    n = reg.ingest_probe_seed(p)
    assert n == 3
    assert reg.models["gemini/m1"]["status"] == "active"
    assert reg.models["nvidia/m2"]["status"] == "retired"    # 404/410 retires
    assert reg.models["nvidia/m3"]["status"] == "degraded"   # timeout != retired

def test_registry_ingest_is_idempotent(tmp_path):
    p = _write_probe(tmp_path, [
        {"provider": "g", "model": "m", "status": "alive", "probed_at": "t",
         "error_type": None}])
    reg = ModelRegistry(tmp_path / "r.json")
    reg.ingest_probe_seed(p)
    reg.ingest_probe_seed(p)
    assert reg.models["g/m"]["observations"] == 2
    assert reg.models["g/m"]["status"] == "active"

def test_active_models_filter(tmp_path):
    p = _write_probe(tmp_path, [
        {"provider": "g", "model": "a", "status": "alive", "probed_at": "t", "error_type": None},
        {"provider": "g", "model": "b", "status": "down", "probed_at": "t",
         "error_type": "retired_or_unavailable"},
    ])
    reg = ModelRegistry(tmp_path / "r.json")
    reg.ingest_probe_seed(p)
    assert reg.active_models() == ["g/a"]
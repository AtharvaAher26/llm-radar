"""
checks.py — Deterministic canary checks. Pure functions, fully offline.
Each returns (passed: bool, detail: str). Cheap objective Signal-1 layer;
semantic/embedding checks come in Phase 3.
"""
import ast
import json
import re

from models import CanaryCase


def _extract_json(text: str):
    """Tolerant: survives ```json fences and prose around the object."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def check_json_keys(case: CanaryCase, output: str) -> tuple[bool, str]:
    if not case.expected_json_keys:
        return True, "n/a"
    try:
        data = _extract_json(output)
    except (ValueError, json.JSONDecodeError) as e:
        return False, f"invalid JSON: {e}"
    missing = [k for k in case.expected_json_keys if k not in data]
    return (not missing), ("all keys present" if not missing else f"missing: {missing}")


def check_label(case: CanaryCase, output: str) -> tuple[bool, str]:
    """Classification: expected_label exact (normalized) OR within allowed set."""
    if not case.expected_label and not case.expected_labels:
        return True, "n/a"
    out = re.sub(r"\s+", " ", output.strip().lower())
    if case.expected_label:
        ok = case.expected_label.lower() in out
        return ok, f"expected '{case.expected_label}'" + ("" if ok else f", got '{out[:60]}'")
    allowed = {l.lower() for l in case.expected_labels}
    found = next((l for l in allowed if l in out), None)
    return (found is not None), f"allowed={sorted(allowed)}, found={found}"


def check_fragments(case: CanaryCase, output: str) -> tuple[bool, str]:
    if not case.expected_content_fragments:
        return True, "n/a"
    low = output.lower()
    missing = [f for f in case.expected_content_fragments if f.lower() not in low]
    return (not missing), ("all fragments present" if not missing
                           else f"missing fragments: {missing}")


def check_line_count(case: CanaryCase, output: str) -> tuple[bool, str]:
    lines = [l for l in output.strip().splitlines() if l.strip()]
    if case.max_lines is not None and len(lines) > case.max_lines:
        return False, f"{len(lines)} lines > max {case.max_lines}"
    if case.min_lines is not None and len(lines) < case.min_lines:
        return False, f"{len(lines)} lines < min {case.min_lines}"
    return True, f"{len(lines)} lines (ok)"


def check_python_parse(case: CanaryCase, output: str) -> tuple[bool, str]:
    if not case.must_parse_python:
        return True, "n/a"
    text = output.strip()
    if "```" in text:
        parts = re.findall(r"```(?:python)?\n(.*?)```", text, flags=re.DOTALL)
        if parts:
            text = parts[0]
    try:
        ast.parse(text)
        return True, "parses"
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"


def evaluate(case: CanaryCase, output: str) -> tuple[bool, list[str]]:
    """Run every configured check. Returns (all_passed, failure details)."""
    results = [
        check_json_keys(case, output),
        check_label(case, output),
        check_fragments(case, output),
        check_line_count(case, output),
        check_python_parse(case, output),
    ]
    failures = [detail for ok, detail in results if not ok]
    return (len(failures) == 0), failures
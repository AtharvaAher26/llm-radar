"""
models.py — Data models for llm-radar (Pydantic v2).
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CanaryCase(BaseModel):
    """One golden probe in the canary suite."""
    id: str                                   # e.g. "json-001"
    capability: str                           # json_strict | classification | instruction | code_gen
    prompt: str
    description: str = ""
    # Deterministic check configuration (all optional; run all that are set)
    expected_json_keys: list[str] = Field(default_factory=list)
    expected_labels: list[str] = Field(default_factory=list)   # classification sets
    expected_label: Optional[str] = None                       # exact match case
    expected_content_fragments: list[str] = Field(default_factory=list)
    max_lines: Optional[int] = None           # e.g. "exactly 3 bullets"
    min_lines: Optional[int] = None
    must_parse_python: bool = False           # code_gen: output must parse via ast
    # Behavioral reference (filled when snapshot taken)
    golden_output: Optional[str] = None


class RunResult(BaseModel):
    """Outcome of running one canary against one model."""
    canary_id: str
    model: str
    provider: str
    status: str                              # alive | down
    check_passed: Optional[bool] = None      # None = couldn't evaluate (no output)
    error_type: Optional[str] = None         # retired_or_unavailable | rate_limited | timeout | ...
    output: Optional[str] = None
    output_hash: Optional[str] = None
    empty_output: bool = False
    latency_ms: Optional[int] = None
    attempts: int = 1
    timestamp: str = Field(default_factory=utc_now)


class DriftAlert(BaseModel):
    """Aggregate alert for one (model, capability) pair."""
    model: str
    capability: str
    level: str                               # STABLE | DRIFTED | BROKEN | RETIRED
    detail: str
    timestamp: str = Field(default_factory=utc_now)


class MigrationReport(BaseModel):
    """Side-by-side verdict for migrating from one model to another."""
    from_model: str
    to_model: str
    rows: list[dict]
    summary: dict                            # pass rates, latency averages
    verdict: str                             # SAFE | CAUTION | RISKY
    generated_at: str = Field(default_factory=utc_now)
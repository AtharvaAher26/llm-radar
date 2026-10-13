"""
canary_store.py — The golden canary suite + baseline snapshots.

Principles carried over from the integrity layer:
  - the suite is code-defined (seed_canaries), versioned in git
  - golden snapshots change ONLY via explicit refresh_baseline() —
    the baseline is the reference point, so it must never drift silently.
"""
import json
from pathlib import Path

from models import CanaryCase

STORE_PATH = Path("data/canaries.json")


def seed_canaries() -> list[CanaryCase]:
    """16 canaries across 4 capabilities, demanding deterministic outputs."""
    return [
        # ---------- json_strict (4) ----------
        CanaryCase(id="json-001", capability="json_strict",
                   prompt='Return ONLY a JSON object (no prose) with keys "color" and "hex" '
                          'where color is "blue" and hex is any valid hex code.',
                   expected_json_keys=["color", "hex"]),
        CanaryCase(id="json-002", capability="json_strict",
                   prompt='Return ONLY a JSON object with keys "name": "Ada", '
                          '"year": 1815, "skills" as a list of exactly 3 strings.',
                   expected_json_keys=["name", "year", "skills"]),
        CanaryCase(id="json-003", capability="json_strict",
                   prompt='Return ONLY a JSON object with keys "city" and "country" '
                          'for the capital of Japan.',
                   expected_json_keys=["city", "country"]),
        CanaryCase(id="json-004", capability="json_strict",
                   prompt='Return ONLY a JSON object with keys "ok" (boolean true) and '
                          '"code" (integer 200).',
                   expected_json_keys=["ok", "code"]),
        # ---------- classification (4) ----------
        CanaryCase(id="cls-001", capability="classification",
                   prompt="Classify the sentiment of this review as exactly one word — "
                          "positive or negative:\n\"The flight was delayed 6 hours and my "
                          "bag was lost.\"",
                   expected_label="negative"),
        CanaryCase(id="cls-002", capability="classification",
                   prompt="Classify as exactly one word — positive or negative:\n"
                          "\"Best purchase I've made all year, works flawlessly.\"",
                   expected_label="positive"),
        CanaryCase(id="cls-003", capability="classification",
                   prompt="Spam or not_spam? Answer with exactly one word:\n"
                          "\"WINNER!! Click here to claim your $10,000,000 prize now!!!\"",
                   expected_label="spam"),
        CanaryCase(id="cls-004", capability="classification",
                   prompt="Is this Python or JavaScript? Answer with exactly one word:\n"
                          "def add(a, b):\n    return a + b",
                   expected_label="python"),
        # ---------- instruction (4) ----------
        CanaryCase(id="ins-001", capability="instruction",
                   prompt="List exactly 3 colors, one per line, nothing else.",
                   min_lines=3, max_lines=3),
        CanaryCase(id="ins-002", capability="instruction",
                   prompt="Write exactly 2 sentences about the moon.",
                   min_lines=2, max_lines=2),
        CanaryCase(id="ins-003", capability="instruction",
                   prompt="Name exactly 4 planets, one per line, nothing else.",
                   min_lines=4, max_lines=4),
        CanaryCase(id="ins-004", capability="instruction",
                   prompt="Reply with a single line in this exact format: "
                          "STATUS: <one of ok/fail/unknown>",
                   expected_content_fragments=["status:"]),
        # ---------- code_gen (4) ----------
        CanaryCase(id="code-001", capability="code_gen",
                   prompt="Write a Python function add(a, b) that returns the sum. "
                          "Reply with only the code.",
                   must_parse_python=True, expected_content_fragments=["def add"]),
        CanaryCase(id="code-002", capability="code_gen",
                   prompt="Write a Python function is_even(n) returning True if n is even. "
                          "Reply with only the code.",
                   must_parse_python=True, expected_content_fragments=["def is_even"]),
        CanaryCase(id="code-003", capability="code_gen",
                   prompt="Write a Python function greet(name) returning 'Hello, <name>!'. "
                          "Reply with only the code.",
                   must_parse_python=True, expected_content_fragments=["def greet"]),
        CanaryCase(id="code-004", capability="code_gen",
                   prompt="Write a Python function max_of_three(a, b, c). "
                          "Reply with only the code.",
                   must_parse_python=True, expected_content_fragments=["def max_of_three"]),
    ]


class CanaryStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cases: list[CanaryCase] = []
        self.load()

    def load(self) -> None:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.cases = [CanaryCase.model_validate(d) for d in raw]

    def save(self) -> None:
        data = [json.loads(c.model_dump_json()) for c in self.cases]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def ensure_seeded(self) -> None:
        if not self.cases:
            self.cases = seed_canaries()
            self.save()

    def by_capability(self, capability: str) -> list[CanaryCase]:
        return [c for c in self.cases if c.capability == capability]

    # -------- golden snapshot lifecycle (explicit, human-triggered) --------

    def set_golden(self, canary_id: str, output: str) -> None:
        for c in self.cases:
            if c.id == canary_id:
                c.golden_output = output
                self.save()
                return
        raise KeyError(f"unknown canary id: {canary_id}")

    def refresh_baseline(self, outputs: dict[str, str]) -> None:
        """Explicit command only — never automatic. {canary_id: output}."""
        for cid, out in outputs.items():
            self.set_golden(cid, out)

    def golden_count(self) -> int:
        return sum(1 for c in self.cases if c.golden_output is not None)
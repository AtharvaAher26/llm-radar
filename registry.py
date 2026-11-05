"""
registry.py — Model Registry: which models exist, their status over time,
and behavior fingerprints. Ingests Phase 0's data/probe.json as its seed.
"""
import json
from pathlib import Path

from models import RunResult, utc_now

REGISTRY_PATH = Path("data/registry.json")
PROBE_SEED = Path("data/probe.json")


class ModelRegistry:
    def __init__(self, path: Path = REGISTRY_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.models: dict[str, dict] = {}   # "provider/model" -> record
        self._load()

    @staticmethod
    def key(provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def _load(self) -> None:
        if self.path.exists():
            self.models = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.models, indent=2), encoding="utf-8")

    def ingest_probe_seed(self, probe_path: Path = PROBE_SEED) -> int:
        """Phase 0 bridge: probe.json records -> registry entries."""
        if not Path(probe_path).exists():
            return 0
        data = json.loads(Path(probe_path).read_text(encoding="utf-8"))
        n = 0
        for rec in data.get("records", []):
            k = self.key(rec["provider"], rec["model"])
            entry = self.models.setdefault(k, {
                "provider": rec["provider"], "model": rec["model"],
                "first_seen": None, "last_seen": None,
                "status": "unknown", "last_error_type": None,
                "observations": 0, "fingerprints": {},
            })
            if rec["status"] == "alive":
                entry["status"] = "active"
                entry["last_seen"] = rec["probed_at"]
                entry["first_seen"] = entry["first_seen"] or rec["probed_at"]
                if rec.get("output_hash"):
                    # the probe prompt is fixed, so its hash IS comparable
                    # across runs — store it instead of discarding it.
                    self._set_fingerprint(entry, "probe", rec["output_hash"])
            elif rec["status"] == "down":
                # timeouts are NOT retirements — only 404/410 retires a model
                if rec.get("error_type") == "retired_or_unavailable":
                    entry["status"] = "retired"
                elif entry["status"] in ("unknown", "active"):
                    entry["status"] = "degraded"
                entry["last_error_type"] = rec.get("error_type")
            entry["observations"] += 1
            n += 1
        self._save()
        return n

    # -------- behavior fingerprints (output hashes, compared over time) --------

    @staticmethod
    def _set_fingerprint(entry: dict, source: str, new_hash: str) -> bool:
        """Store the latest hash for a source. Returns True if it CHANGED
        (a previous hash existed and differs) — first sighting is not drift."""
        fps = entry.setdefault("fingerprints", {})
        old = fps.get(source, {}).get("hash")
        fps[source] = {"hash": new_hash, "updated_at": utc_now()}
        return old is not None and old != new_hash

    def record_fingerprints(self, provider: str, model: str,
                            hashes: dict[str, str | None]) -> list[str]:
        """Store latest output hashes {source: hash}; return the sources
        whose fingerprint changed vs the previously stored one."""
        k = self.key(provider, model)
        entry = self.models.setdefault(k, {
            "provider": provider, "model": model,
            "first_seen": None, "last_seen": None,
            "status": "unknown", "last_error_type": None,
            "observations": 0, "fingerprints": {},
        })
        changed = [src for src, h in hashes.items()
                   if h and self._set_fingerprint(entry, src, h)]
        self._save()
        return changed

    def fingerprint(self, provider: str, model: str, source: str) -> str | None:
        entry = self.models.get(self.key(provider, model), {})
        return entry.get("fingerprints", {}).get(source, {}).get("hash")

    def record_run(self, provider: str, model: str,
                   results: list[RunResult]) -> list[str]:
        """Ingest one full canary run: roll the model's status up from the
        results and store every output hash as a fingerprint.
        Returns the canary ids whose fingerprint changed since last run."""
        alive = any(r.status == "alive" for r in results)
        errors = [r.error_type for r in results if r.error_type]
        if alive:
            self.record_result(provider, model, "alive")
        elif results and all(r.status == "skipped" for r in results):
            pass    # never measured (no API key) — not a status change
        elif "retired_or_unavailable" in errors:
            self.record_result(provider, model, "down", "retired_or_unavailable")
        elif results:
            self.record_result(provider, model, "down", errors[0] if errors else None)
        return self.record_fingerprints(
            provider, model, {r.canary_id: r.output_hash for r in results})

    def record_result(self, provider: str, model: str, status: str,
                      error_type: str | None = None) -> None:
        k = self.key(provider, model)
        entry = self.models.setdefault(k, {
            "provider": provider, "model": model,
            "first_seen": None, "last_seen": None,
            "status": "unknown", "last_error_type": None, "observations": 0,
            "fingerprints": {},
        })
        if status == "alive":
            entry["status"] = "active"
            entry["last_seen"] = utc_now()
            entry["first_seen"] = entry["first_seen"] or entry["last_seen"]
        elif status == "down":
            if error_type == "retired_or_unavailable":
                entry["status"] = "retired"
            elif entry["status"] in ("unknown", "active"):
                entry["status"] = "degraded"
            entry["last_error_type"] = error_type
        entry["observations"] += 1
        self._save()

    def active_models(self) -> list[str]:
        return [k for k, v in self.models.items() if v["status"] == "active"]

    def summary(self) -> list[dict]:
        return [{"key": k, **v} for k, v in self.models.items()]
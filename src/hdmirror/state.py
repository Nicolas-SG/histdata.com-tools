"""Per-month download state, persisted as JSON next to the archives."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OK = "ok"
ABSENT = "absent"
FAILED = "failed"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class State:
    """``{pair: {month: {status, size, sha256, remote_updated_at, ...}}}``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.months: dict[str, dict[str, dict[str, Any]]] = {}
        if path.exists():
            self.months = json.loads(path.read_text()).get("months", {})

    def get(self, pair: str, month: str) -> dict[str, Any]:
        return self.months.get(pair, {}).get(month, {})

    def update(self, pair: str, month: str, **fields: Any) -> dict[str, Any]:
        entry = self.months.setdefault(pair, {}).setdefault(month, {})
        entry.update(fields)
        return entry

    def record_file(self, pair: str, month: str, path: Path, **fields: Any) -> None:
        """Mark ``month`` as present, with the size and hash of ``path``."""
        self.update(
            pair,
            month,
            status=OK,
            size=path.stat().st_size,
            sha256=sha256(path),
            error=None,
            **fields,
        )

    def seed(self, inventory: dict[str, dict[str, Path]]) -> int:
        """Record archives found on disk that the state does not know yet."""
        added = 0
        for pair, found in inventory.items():
            for month, path in found.items():
                entry = self.get(pair, month)
                if entry.get("status") == OK and entry.get("size") == path.stat().st_size:
                    continue
                self.record_file(pair, month, path, seeded_at=now_iso())
                added += 1
        return added

    def save(self) -> None:
        temp = self.path.with_name(f".{self.path.name}.tmp")
        temp.write_text(json.dumps({"version": 1, "months": self.months}, indent=1, sort_keys=True))
        os.replace(temp, self.path)

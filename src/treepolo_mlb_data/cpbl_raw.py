from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .raw import Snapshot


@dataclass(slots=True)
class CPBLRawRecord:
    kind: str
    key: str
    snapshot: Snapshot


class CPBLRawArchive:
    """Lossless CPBL JSON archive, physically separate from MLB Savant CSV raw data."""

    def __init__(self, root: Path):
        # `root` is already the selected CPBL dataset root (`data/cpbl`). Keep
        # provider raw data at `data/cpbl/raw/...`, never inside the MLB raw tree.
        self.root = Path(root) / "raw"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _json_bytes(payload: Any) -> bytes:
        return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

    def save_schedule(self, day: date, payload: Any) -> CPBLRawRecord:
        return self._save("schedule", day.isoformat(), day, payload)

    def save_game(self, game_id: str, game_date: date, payload: Any) -> CPBLRawRecord:
        return self._save("games", game_id, game_date, payload)

    def _save(self, kind: str, key: str, day: date, payload: Any) -> CPBLRawRecord:
        raw = self._json_bytes(payload)
        digest = hashlib.sha256(raw).hexdigest()
        folder = self.root / kind / str(day.year) / f"{day.month:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        safe_key = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in key)
        pattern = f"{safe_key}_*_{digest[:12]}.json.gz"
        for existing in folder.glob(pattern):
            snapshot = self.load_metadata(existing)
            if snapshot.sha256 == digest:
                return CPBLRawRecord(kind, key, snapshot)
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
        snapshot_id = f"cpbl_{kind}_{safe_key}_{stamp}_{digest[:12]}"
        path = folder / f"{safe_key}_{stamp}_{digest[:12]}.json.gz"
        tmp = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(tmp, "wb", compresslevel=6) as handle:
            handle.write(raw)
        tmp.replace(path)
        snapshot = Snapshot(
            snapshot_id=snapshot_id,
            start_date=day.isoformat(),
            end_date=day.isoformat(),
            fetched_at=now.isoformat(),
            sha256=digest,
            bytes_uncompressed=len(raw),
            path=str(path),
        )
        self._manifest(path).write_text(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return CPBLRawRecord(kind, key, snapshot)

    @staticmethod
    def _manifest(path: Path) -> Path:
        return path.with_suffix(".manifest.json")

    def load_metadata(self, path: Path) -> Snapshot:
        return Snapshot(**json.loads(self._manifest(path).read_text(encoding="utf-8")))

    def read_verified(self, path: Path) -> tuple[Snapshot, Any]:
        snapshot = self.load_metadata(path)
        with gzip.open(path, "rb") as handle:
            raw = handle.read()
        if hashlib.sha256(raw).hexdigest() != snapshot.sha256 or len(raw) != snapshot.bytes_uncompressed:
            raise ValueError(f"CPBL raw snapshot verification failed: {path}")
        return snapshot, json.loads(raw.decode("utf-8"))

    def iter_games(self) -> list[Path]:
        games_root = self.root / "games"
        return sorted(games_root.glob("**/*.json.gz")) if games_root.exists() else []

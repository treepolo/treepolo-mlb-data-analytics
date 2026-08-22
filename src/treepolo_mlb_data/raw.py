from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class Snapshot:
    snapshot_id: str
    start_date: str
    end_date: str
    fetched_at: str
    sha256: str
    bytes_uncompressed: int
    path: str


class RawArchive:
    def __init__(self, root: Path):
        self.root = root / "raw"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, start: date, end: date, payload: bytes) -> Snapshot:
        digest = hashlib.sha256(payload).hexdigest()
        existing = self._find_identical(start, end, digest)
        if existing:
            return existing
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
        snapshot_id = f"{start.isoformat()}_{end.isoformat()}_{stamp}_{digest[:12]}"
        folder = self.root / str(start.year) / f"{start.month:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        gz_path = folder / f"{snapshot_id}.csv.gz"
        tmp = gz_path.with_suffix(gz_path.suffix + ".tmp")
        with gzip.open(tmp, "wb", compresslevel=6) as handle:
            handle.write(payload)
        tmp.replace(gz_path)
        snapshot = Snapshot(snapshot_id, start.isoformat(), end.isoformat(), now.isoformat(), digest, len(payload), str(gz_path))
        self._manifest_path(gz_path).write_text(json.dumps(asdict(snapshot), indent=2) + "\n", encoding="utf-8")
        return snapshot

    def _find_identical(self, start: date, end: date, digest: str) -> Snapshot | None:
        pattern = f"{start.isoformat()}_{end.isoformat()}_*_{digest[:12]}.csv.gz"
        for path in self.root.glob(f"**/{pattern}"):
            snapshot = self.load_metadata(path)
            if snapshot.sha256 == digest:
                return snapshot
        return None

    @staticmethod
    def _manifest_path(gz_path: Path) -> Path:
        return gz_path.with_suffix(".json")

    def load_metadata(self, path: Path) -> Snapshot:
        return Snapshot(**json.loads(self._manifest_path(path).read_text(encoding="utf-8")))

    def read_verified(self, path: Path) -> tuple[Snapshot, bytes]:
        snapshot = self.load_metadata(path)
        with gzip.open(path, "rb") as handle:
            payload = handle.read()
        if hashlib.sha256(payload).hexdigest() != snapshot.sha256:
            raise ValueError(f"Raw snapshot checksum mismatch: {path}")
        if len(payload) != snapshot.bytes_uncompressed:
            raise ValueError(f"Raw snapshot byte count mismatch: {path}")
        return snapshot, payload

    def iter_snapshots(self) -> list[Path]:
        return sorted(self.root.glob("**/*.csv.gz"))

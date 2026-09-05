"""Fetch the referenced Three.js baseball research asset without vendoring it.

The upstream repository did not expose an explicit root LICENSE during the
research pass, so this helper preserves provenance while keeping third-party
payloads out of this repository by default.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "upstream_manifest.json"
OUTPUT_DIR = HERE / "upstream"
USER_AGENT = "treepolo-mlb-data-analytics research asset fetcher"


def git_blob_sha1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, spec in manifest["files"].items():
        request = Request(spec["raw_url"], headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=30) as response:
            payload = response.read()

        expected_size = int(spec["size_bytes"])
        expected_sha = str(spec["git_blob_sha1"])
        actual_size = len(payload)
        actual_sha = git_blob_sha1(payload)

        if actual_size != expected_size:
            raise RuntimeError(
                f"{name}: size mismatch: expected {expected_size}, got {actual_size}"
            )
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"{name}: Git blob SHA mismatch: expected {expected_sha}, got {actual_sha}"
            )

        target = OUTPUT_DIR / name
        target.write_bytes(payload)
        print(f"fetched {name}: {actual_size} bytes, git blob {actual_sha}")


if __name__ == "__main__":
    main()

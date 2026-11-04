"""Download 20 real SearchQA rows for a small, disjoint workflow smoke run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data/searchqa_sub2api_smoke"
PARAMS = {
    "dataset": "lucadiliello/searchqa", "config": "default",
    "split": "validation", "offset": 0, "length": 20,
}
URL = "https://datasets-server.huggingface.co/rows?" + urlencode(PARAMS)


def main() -> None:
    with urlopen(URL, timeout=60) as response:
        payload = json.load(response)
    rows = sorted(payload["rows"], key=lambda entry: entry["row_idx"])
    if [entry["row_idx"] for entry in rows] != list(range(20)):
        raise ValueError("Expected source rows 0 through 19")
    if any(entry.get("truncated_cells") for entry in rows):
        raise ValueError("Source API returned truncated data")
    items = [
        {"id": entry["row"]["key"], **{
            key: entry["row"][key] for key in ("question", "context", "answers")
        }} for entry in rows
    ]
    if len({item["id"] for item in items}) != 20:
        raise ValueError("Duplicate source IDs")
    for item in items:
        if not all(item.get(key) for key in ("id", "question", "context", "answers")):
            raise ValueError("Missing required SearchQA field")
    manifest = {
        "purpose": "workflow_smoke_only_not_paper_split",
        "source_url": URL,
        "selection": "validation rows [0:8] train, [8:12] val, [12:20] test",
        "counts": {}, "ids": {}, "sha256": {},
    }
    for name, subset in (("train", items[:8]), ("val", items[8:12]), ("test", items[12:20])):
        path = OUTPUT / name / "items.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (json.dumps(subset, ensure_ascii=False, indent=2) + "\n").encode()
        path.write_bytes(content)
        manifest["counts"][name] = len(subset)
        manifest["ids"][name] = [item["id"] for item in subset]
        manifest["sha256"][name] = hashlib.sha256(content).hexdigest()
    manifest_path = OUTPUT / "split_manifest.json"
    # Refuse to silently change the reference data if the upstream dataset changes.
    reference = Path(__file__).with_name("data_manifest.json")
    if reference.exists() and json.loads(reference.read_text()) != manifest:
        raise ValueError("Source payload changed relative to data_manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Prepared {manifest['counts']} in {OUTPUT}")


if __name__ == "__main__":
    main()

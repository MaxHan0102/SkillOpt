"""Materialize the exact released SearchQA and LiveMath ID splits."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "reproduction/data"
BENCHMARKS = ("searchqa", "livemathematicianbench")
SEARCH_REVISION = "c1a979068ba118d85467179b704031d113d689cc"
SEARCH_FILES = {
    "train-00000-of-00001-55e7116bea868a35.parquet":
        "effe1d14befa8a77d294d17dd10f34d20c47f3a27530c7d6d3429502411e2beb",
    "validation-00000-of-00001-2092e81367c2ca98.parquet":
        "45b85051c127c6ad17dbb10fc454b00c776b86201aa734a6c7772494689e82a1",
}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(2**20), b""):
            result.update(chunk)
    return result.hexdigest()


def download(url: str, path: Path, expected_sha256: str = "") -> str:
    if path.exists() and (not expected_sha256 or digest(path) == expected_sha256):
        return digest(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    for attempt in range(3):
        try:
            # A fresh query avoids expired signed URLs cached by HTTP proxies.
            with urlopen(url + f"?download=true&nonce={time.time_ns()}", timeout=120) as response:
                with partial.open("wb") as output:
                    total = 0
                    next_progress = 32 * 2**20
                    while chunk := response.read(2**20):
                        output.write(chunk)
                        total += len(chunk)
                        if total >= next_progress:
                            print(f"Downloading {path.name}: {total // 2**20} MiB", flush=True)
                            next_progress += 32 * 2**20
            actual = digest(partial)
            if expected_sha256 and actual != expected_sha256:
                raise ValueError(f"Source checksum mismatch: {path.name}")
            partial.replace(path)
            print(f"Downloaded {path.name}: {path.stat().st_size} bytes", flush=True)
            return actual
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_items(benchmark: str) -> dict[str, list[dict]]:
    source = ROOT / "data" / f"{benchmark}_id_split"
    result = {split: load_json(source / split / "items.json") for split in ("train", "val", "test")}
    ids = [str(item["id"]) for items in result.values() for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Official manifest contains duplicate IDs")
    return result


def verify_materialized(benchmark: str) -> dict:
    directory = DATA / f"{benchmark}_official"
    record = load_json(directory / "provenance.json")
    expected = manifest_items(benchmark)
    manifest_path = ROOT / "data" / f"{benchmark}_id_split/split_manifest.json"
    if record["official_manifest_sha256"] != digest(manifest_path):
        raise ValueError("Official manifest changed")
    for split, lookup in expected.items():
        path = directory / split / "items.json"
        rows = load_json(path)
        if [str(item["id"]) for item in rows] != [str(item["id"]) for item in lookup]:
            raise ValueError(f"ID/order mismatch in {benchmark}/{split}")
        if digest(path) != record["split_sha256"][split]:
            raise ValueError(f"Payload checksum mismatch in {benchmark}/{split}")
        if benchmark == "searchqa":
            valid = all(row.get("question") and row.get("context") and row.get("answers") for row in rows)
        else:
            valid = all(row.get("question") and row.get("choices") and row.get("correct_choice", {}).get("label") for row in rows)
        if not valid:
            raise ValueError(f"Missing runnable fields in {benchmark}/{split}")
    return record


def prepare(benchmark: str) -> dict:
    directory = DATA / f"{benchmark}_official"
    if (directory / "provenance.json").exists():
        record = verify_materialized(benchmark)
        print(f"Verified existing {benchmark}: {record['counts']}", flush=True)
        return record
    source_dir = ROOT / "data" / f"{benchmark}_id_split"
    manifest = load_json(source_dir / "split_manifest.json")
    lookup = manifest_items(benchmark)
    wanted = {str(item["id"]) for rows in lookup.values() for item in rows}
    raw = DATA / "raw" / benchmark
    source_hashes = {}
    if benchmark == "searchqa":
        import pyarrow.parquet as pq

        from scripts.materialize_searchqa import materialize_searchqa_splits

        revision = SEARCH_REVISION
        selected = []
        for filename, expected_hash in SEARCH_FILES.items():
            path = raw / filename
            url = f"https://huggingface.co/datasets/lucadiliello/searchqa/resolve/{revision}/data/{filename}"
            source_hashes[filename] = download(url, path, expected_hash)
            for batch in pq.ParquetFile(path).iter_batches(
                batch_size=2048, columns=["key", "question", "context", "answers"],
            ):
                selected.extend(row for row in batch.to_pylist() if str(row["key"]) in wanted)
        materialize_searchqa_splits(source_dir, directory, {"selected": selected}, dataset_name="lucadiliello/searchqa")
    else:
        from skillopt.envs.livemathematicianbench.dataloader import _normalize_item

        revision = manifest["source_revision"]
        selected = {}
        for filename in manifest["source_files"]:
            path = raw / filename
            url = f"https://huggingface.co/datasets/{manifest['source_repo']}/resolve/{revision}/{filename}"
            source_hashes[filename] = download(url, path)
            for index, item in enumerate(load_json(path)):
                normalized = _normalize_item(item, index, str(path.relative_to(ROOT)))
                key = normalized["id"]
                if key not in wanted:
                    continue
                if key in selected:
                    raise ValueError(f"Duplicate source item: {key}")
                selected[key] = normalized
        missing = wanted - selected.keys()
        if missing:
            raise ValueError(f"Missing official IDs: {sorted(missing)}")
        for split, items in lookup.items():
            path = directory / split / "items.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([selected[str(item["id"])] for item in items], ensure_ascii=False, indent=2) + "\n")
    record = {
        "benchmark": benchmark,
        "source_repo": manifest["source_repo"],
        "source_revision": revision,
        "source_file_sha256": source_hashes,
        "official_manifest_sha256": digest(source_dir / "split_manifest.json"),
        "counts": {split: len(rows) for split, rows in lookup.items()},
        "split_sha256": {split: digest(directory / split / "items.json") for split in lookup},
    }
    (directory / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
    verify_materialized(benchmark)
    print(f"Prepared {benchmark}: {record['counts']}", flush=True)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=(*BENCHMARKS, "all"), default="all", nargs="?")
    args = parser.parse_args()
    for benchmark in BENCHMARKS if args.benchmark == "all" else [args.benchmark]:
        prepare(benchmark)


if __name__ == "__main__":
    main()

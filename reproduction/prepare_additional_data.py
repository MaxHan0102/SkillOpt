"""Materialize official data for the four additional benchmarks."""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_official_data import digest, download, load_json, manifest_items  # noqa: E402

DATA = ROOT / "reproduction" / "data"
BENCHMARKS = ("docvqa", "spreadsheetbench", "alfworld", "officeqa")


def spreadsheet_filename_fix() -> list[dict]:
    # Verified 400 contains one typo: task 42930's gold workbook is named 43930.
    # Preserve the source file and add the name expected by the official loader.
    folder = DATA / "spreadsheetbench_verified_400" / "spreadsheet" / "42930"
    source = folder / "1_43930_golden.xlsx"
    target = folder / "1_42930_golden.xlsx"
    if not source.is_file():
        raise FileNotFoundError("Missing original golden workbook for 42930")
    if not target.exists():
        shutil.copyfile(source, target)
    if digest(source) != digest(target):
        raise ValueError("42930 alias differs from original workbook")
    return [{"task_id": "42930", "reason": "upstream golden filename typo",
             "source": str(source.relative_to(ROOT)), "alias": str(target.relative_to(ROOT)),
             "sha256": digest(source)}]


def prepare(benchmark: str) -> dict:
    if benchmark == "officeqa":
        from reproduction.prepare_officeqa_data import prepare as prepare_officeqa

        return prepare_officeqa()
    if benchmark == "alfworld":
        record = load_json(DATA / "alfworld" / "provenance.json")
        counts = {}
        for split in ("train", "val", "test"):
            rows = load_json(ROOT / "data" / "alfworld_path_split" / split / "items.json")
            if any(not (DATA / "alfworld" / row["gamefile"]).is_file() for row in rows):
                raise FileNotFoundError("Missing official ALFWorld gamefiles")
            counts[split] = len(rows)
        return {**record, "benchmark": benchmark, "counts": counts}

    from huggingface_hub import HfApi

    manifest_path = ROOT / "data" / f"{benchmark}_id_split" / "split_manifest.json"
    manifest = load_json(manifest_path)
    lookup = manifest_items(benchmark)
    wanted = {str(row["id"]) for rows in lookup.values() for row in rows}
    output = DATA / f"{benchmark}_official"
    if (output / "provenance.json").exists():
        if benchmark == "spreadsheetbench":
            record = load_json(output / "provenance.json")
            record["filename_aliases"] = spreadsheet_filename_fix()
            for alias in record["filename_aliases"]:
                record["workbook_sha256"][alias["alias"]] = alias["sha256"]
            (output / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
        return verify(benchmark)
    info = HfApi().dataset_info(manifest["source_repo"], revision=manifest["source_revision"], files_metadata=True)
    source_hashes = {}
    selected = {}
    image_hashes = {}
    if benchmark == "docvqa":
        import pyarrow.parquet as pq
        from PIL import Image

        files = [f for f in info.siblings if f.rfilename.startswith("DocVQA/validation-") and f.rfilename.endswith(".parquet")]
        for source in sorted(files, key=lambda f: f.rfilename):
            path = DATA / "raw" / benchmark / source.rfilename
            url = f"https://huggingface.co/datasets/{manifest['source_repo']}/resolve/{manifest['source_revision']}/{source.rfilename}"
            source_hashes[source.rfilename] = download(url, path, source.lfs.sha256)
            for batch in pq.ParquetFile(path).iter_batches(batch_size=32):
                for row in batch.to_pylist():
                    key = str(row["questionId"])
                    if key not in wanted:
                        continue
                    if key in selected:
                        raise ValueError(f"Duplicate source ID: {key}")
                    image = DATA / "docvqa_images" / f"q{key}_d{row['docId']}.png"
                    image.parent.mkdir(parents=True, exist_ok=True)
                    with Image.open(io.BytesIO(row["image"]["bytes"])) as decoded:
                        decoded.save(image, format="PNG")
                    relative = str(image.relative_to(ROOT))
                    image_hashes[relative] = digest(image)
                    selected[key] = {
                        "id": key, "questionId": key, "docId": str(row["docId"]),
                        "question": row["question"], "answer": json.dumps(row["answers"], ensure_ascii=False),
                        "image_path": relative, "source_split": "validation",
                        "ucsf_document_id": row["ucsf_document_id"],
                        "ucsf_document_page_no": row["ucsf_document_page_no"],
                    }
            print(f"DocVQA: found {len(selected)}/{len(wanted)} official examples", flush=True)
    else:
        source = next(f for f in info.siblings if f.rfilename == manifest["source_file"])
        path = DATA / "raw" / benchmark / source.rfilename
        url = f"https://huggingface.co/datasets/{manifest['source_repo']}/resolve/{manifest['source_revision']}/{source.rfilename}"
        source_hashes[source.rfilename] = download(url, path, source.lfs.sha256)
        data_root = DATA / "spreadsheetbench_verified_400"
        if not (data_root / "dataset.json").exists():
            with tarfile.open(path) as archive:
                archive.extractall(DATA, filter="data")
        for row in load_json(data_root / "dataset.json"):
            key = str(row["id"])
            if key not in wanted:
                continue
            if key in selected:
                raise ValueError(f"Duplicate source ID: {key}")
            selected[key] = row
        spreadsheet_filename_fix()

    if wanted != selected.keys():
        raise ValueError(f"Missing {len(wanted - selected.keys())} official IDs")
    split_hashes = {}
    for split, rows in lookup.items():
        folder = output / split
        folder.mkdir(parents=True, exist_ok=True)
        chosen = [selected[str(row["id"])] for row in rows]
        if benchmark == "docvqa":
            payload = folder / "items.csv"
            with payload.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(chosen[0]))
                writer.writeheader()
                writer.writerows(chosen)
        else:
            payload = folder / "items.json"
            payload.write_text(json.dumps(chosen, ensure_ascii=False, indent=2) + "\n")
        split_hashes[split] = digest(payload)
    record = {
        "benchmark": benchmark, "source_repo": manifest["source_repo"],
        "source_revision": manifest["source_revision"], "source_file_sha256": source_hashes,
        "official_manifest_sha256": digest(manifest_path),
        "counts": {split: len(rows) for split, rows in lookup.items()}, "split_sha256": split_hashes,
    }
    if image_hashes:
        record["image_sha256"] = image_hashes
    else:
        record["workbook_sha256"] = {str(p.relative_to(ROOT)): digest(p) for p in sorted(data_root.rglob("*.xlsx"))}
        record["filename_aliases"] = spreadsheet_filename_fix()
    (output / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
    return verify(benchmark)


def verify(benchmark: str) -> dict:
    from skillopt.envs.docvqa.dataloader import DocVQADataLoader
    from skillopt.envs.spreadsheetbench.dataloader import SpreadsheetBenchDataLoader
    from skillopt.envs.spreadsheetbench.rollout import _find_test_cases

    output = DATA / f"{benchmark}_official"
    record = load_json(output / "provenance.json")
    manifest = ROOT / "data" / f"{benchmark}_id_split" / "split_manifest.json"
    if digest(manifest) != record["official_manifest_sha256"]:
        raise ValueError("Official manifest changed")
    loader_cls = DocVQADataLoader if benchmark == "docvqa" else SpreadsheetBenchDataLoader
    loader = loader_cls(split_dir=str(output), split_mode="split_dir")
    for split, expected in manifest_items(benchmark).items():
        folder = output / split
        rows = loader.load_split_items(str(folder))
        if [str(r["id"]) for r in rows] != [str(r["id"]) for r in expected]:
            raise ValueError(f"Official ID/order mismatch: {split}")
        file = folder / ("items.csv" if benchmark == "docvqa" else "items.json")
        if digest(file) != record["split_sha256"][split]:
            raise ValueError(f"Split checksum mismatch: {split}")
        for row in rows:
            if benchmark == "docvqa":
                if not row["question"] or not row["answers"] or not (ROOT / row["image_path"]).is_file():
                    raise ValueError("Incomplete DocVQA example")
            else:
                folder = DATA / "spreadsheetbench_verified_400" / row["spreadsheet_path"]
                if not row.get("instruction") or not row.get("answer_position"):
                    raise ValueError("Incomplete spreadsheet instruction/scoring metadata")
                if not _find_test_cases(str(folder)):
                    raise FileNotFoundError(f"Missing spreadsheet files: {row['id']}")
    for key in ("image_sha256", "workbook_sha256"):
        for name, expected in record.get(key, {}).items():
            if digest(ROOT / name) != expected:
                raise ValueError("Asset checksum mismatch")
    print(f"Verified {benchmark}: {record['counts']}", flush=True)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=(*BENCHMARKS, "all"), default="all", nargs="?")
    args = parser.parse_args()
    for benchmark in BENCHMARKS if args.benchmark == "all" else [args.benchmark]:
        prepare(benchmark)


if __name__ == "__main__":
    main()

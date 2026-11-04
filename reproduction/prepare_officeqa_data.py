"""Download authorized OfficeQA data directly from Hugging Face, with exact splits.

An explicit official endpoint prevents inherited HF_ENDPOINT mirror settings from
receiving gated-data credentials. Credentials are read by the HF SDK, never logged.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_official_data import digest, load_json, manifest_items  # noqa: E402

ENDPOINT = "https://huggingface.co"
DATA = ROOT / "reproduction/data"
PAYLOAD = DATA / "officeqa_docs_official"
CORPUS = PAYLOAD / "treasury_bulletins_parsed"
OUTPUT = DATA / "officeqa_official"
MANIFEST = ROOT / "data/officeqa_id_split/split_manifest.json"


def verify() -> dict:
    from skillopt.envs.officeqa.dataloader import OfficeQADataLoader

    record = load_json(OUTPUT / "provenance.json")
    if digest(MANIFEST) != record["official_manifest_sha256"]:
        raise ValueError("OfficeQA official manifest changed")
    loader = OfficeQADataLoader(split_dir=str(OUTPUT), split_mode="split_dir")
    referenced = set()
    for split, expected in manifest_items("officeqa").items():
        path = OUTPUT / split / "items.json"
        if digest(path) != record["split_sha256"][split]:
            raise ValueError(f"OfficeQA split checksum mismatch: {split}")
        rows = loader.load_split_items(str(path.parent))
        if [row["id"] for row in rows] != [str(row["id"]) for row in expected]:
            raise ValueError(f"OfficeQA ID/order mismatch: {split}")
        for row in rows:
            if not row["question"] or not row["ground_truth"] or not row["source_files"]:
                raise ValueError(f"Incomplete OfficeQA example: {row['id']}")
            for source_file in row["source_files"]:
                if Path(source_file).name != source_file:
                    raise ValueError("OfficeQA source filename is not a basename")
                text = CORPUS / "transformed" / source_file
                parsed = CORPUS / "jsons" / (Path(source_file).stem + ".json")
                if not text.is_file() or not parsed.is_file():
                    raise FileNotFoundError(f"Missing OfficeQA source assets: {source_file}")
                referenced.add(source_file)
    for name, sha256 in record["source_file_sha256"].items():
        if digest(PAYLOAD / name) != sha256:
            raise ValueError(f"OfficeQA asset checksum mismatch: {name}")
    if len(referenced) != record["referenced_text_documents"]:
        raise ValueError("OfficeQA referenced-document count changed")
    print(f"Verified officeqa: {record['counts']}; {len(referenced)} referenced documents", flush=True)
    return record


def prepare() -> dict:
    if (OUTPUT / "provenance.json").exists():
        return verify()

    from huggingface_hub import HfApi, snapshot_download

    manifest = load_json(MANIFEST)
    repo, revision = manifest["source_repo"], manifest["source_revision"]
    patterns = [manifest["source_file"], "treasury_bulletins_parsed/jsons/*.json",
                "treasury_bulletins_parsed/transformed/*.txt"]
    api = HfApi(endpoint=ENDPOINT, token=True)
    info = api.dataset_info(repo, revision=revision, files_metadata=True)
    sources = [f for f in info.siblings if f.rfilename == manifest["source_file"]
               or (f.rfilename.startswith("treasury_bulletins_parsed/jsons/") and f.rfilename.endswith(".json"))
               or (f.rfilename.startswith("treasury_bulletins_parsed/transformed/") and f.rfilename.endswith(".txt"))]
    print(f"Downloading {len(sources)} official OfficeQA files from {ENDPOINT}", flush=True)
    snapshot_download(repo_id=repo, repo_type="dataset", revision=revision, endpoint=ENDPOINT,
                      token=True, allow_patterns=patterns, local_dir=PAYLOAD, max_workers=8)
    source_hashes = {}
    for source in sources:
        path = PAYLOAD / source.rfilename
        actual = digest(path)
        if source.lfs:
            if actual != source.lfs.sha256:
                raise ValueError(f"LFS checksum mismatch: {source.rfilename}")
        else:
            blob = path.read_bytes()
            git_sha1 = hashlib.sha1(f"blob {len(blob)}\0".encode() + blob).hexdigest()
            if git_sha1 != source.blob_id:
                raise ValueError(f"Git blob checksum mismatch: {source.rfilename}")
        source_hashes[source.rfilename] = actual

    with (PAYLOAD / manifest["source_file"]).open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    selected = {row["uid"]: row for row in raw}
    if len(selected) != len(raw):
        raise ValueError("Duplicate OfficeQA source IDs")
    lookup = manifest_items("officeqa")
    wanted = {str(row["id"]) for rows in lookup.values() for row in rows}
    if wanted != selected.keys():
        raise ValueError("OfficeQA source IDs differ from the official full split")
    hashes = {}
    referenced = set()
    from skillopt.envs.officeqa.dataloader import _parse_list_field

    for split, rows in lookup.items():
        payload = []
        for row in rows:
            source = selected[str(row["id"])]
            payload.append({**source, "id": str(row["id"]), "category": row["category"]})
            referenced.update(_parse_list_field(source["source_files"]))
        path = OUTPUT / split / "items.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        hashes[split] = digest(path)
    record = {
        "benchmark": "officeqa", "source_repo": repo, "source_revision": revision,
        "download_endpoint": ENDPOINT, "authenticated": True,
        "official_manifest_sha256": digest(MANIFEST), "counts": {split: len(rows) for split, rows in lookup.items()},
        "split_sha256": hashes, "source_file_sha256": source_hashes,
        "parsed_json_files": sum(name.endswith(".json") for name in source_hashes),
        "text_files": sum(name.endswith(".txt") for name in source_hashes),
        "referenced_text_documents": len(referenced),
        "integrity": "Each downloaded file matches the pinned Hugging Face LFS SHA-256 or Git blob SHA-1.",
        "evidence_mode": "Official offline adapter: parsed source pages plus local document tools.",
    }
    (OUTPUT / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
    return verify()


if __name__ == "__main__":
    prepare()

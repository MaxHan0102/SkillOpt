"""Materialize the official text-game data used by SkillOpt's ALFWorld env.

Uses the URLs from the installed official alfworld-download command, omitting
visual detector weights and pretrained BUTLER agents that AlfredTWEnv does not use.
"""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
REPRO = ROOT / "reproduction"


def main() -> None:
    from alfworld.info import ALFRED_PDDL_PATH, ALFRED_TWL2_PATH

    official = runpy.run_path(str(Path(sys.executable).with_name("alfworld-download")))
    data_root = Path(os.environ.get("ALFWORLD_DATA", str(REPRO / "data" / "alfworld"))).resolve()
    downloads = REPRO / ".cache" / "downloads" / "alfworld"
    downloads.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    records = []
    for key in ("JSON_FILES_URL", "PDDL_FILES_URL", "TW_PDDL_FILES_URL"):
        url = official[key]
        archive = downloads / url.rsplit("/", 1)[1]
        if not archive.is_file():
            partial = archive.with_suffix(archive.suffix + ".part")
            print(f"Downloading {archive.name}", flush=True)
            with requests.get(url, stream=True, timeout=(20, 120)) as response:
                response.raise_for_status()
                with partial.open("wb") as target:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        target.write(chunk)
            partial.replace(archive)
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        print(f"Extracting {archive.name}", flush=True)
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                destination = (data_root / member.filename).resolve()
                if not destination.is_relative_to(data_root):
                    raise ValueError("Archive contains a path outside the data root")
            bad = zipped.testzip()
            if bad:
                raise ValueError(f"Corrupt archive member: {bad}")
            zipped.extractall(data_root)
        records.append({"url": url, "sha256": digest, "size_bytes": archive.stat().st_size})
    logic = data_root / "logic"
    logic.mkdir(exist_ok=True)
    shutil.copyfile(ALFRED_PDDL_PATH, logic / "alfred.pddl")
    shutil.copyfile(ALFRED_TWL2_PATH, logic / "alfred.twl2")
    counts = {}
    for split in ("train", "val", "test"):
        rows = json.loads((ROOT / "data" / "alfworld_path_split" / split / "items.json").read_text())
        missing = [row["id"] for row in rows if not (data_root / row["gamefile"]).is_file()]
        if missing:
            raise FileNotFoundError(f"{len(missing)} official {split} gamefiles are missing")
        counts[split] = len(rows)
    (data_root / "provenance.json").write_text(json.dumps({
        "source": "official alfworld-download text-game assets", "archives": records,
        "official_skillopt_split_counts": counts,
    }, indent=2) + "\n")
    print(f"Ready: {sum(counts.values())} official split gamefiles, plus game logic.")


if __name__ == "__main__":
    main()

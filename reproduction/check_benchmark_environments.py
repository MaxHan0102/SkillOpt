"""Offline functional checks of benchmark dependencies; makes no model requests.

Synthetic documents/images/workbooks check local runtime functionality, not
benchmark performance. ALFWorld checks real official games when downloaded.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPRO = ROOT / "reproduction"


def check_tools() -> dict:
    import datasets  # noqa: F401
    import pandas as pd
    from PIL import Image, ImageDraw

    from skillopt.envs.docvqa.adapter import DocVQAAdapter  # noqa: F401
    from skillopt.envs.docvqa.evaluator import evaluate as doc_evaluate
    from skillopt.envs.docvqa.rollout import _build_messages
    from skillopt.envs.officeqa.adapter import OfficeQAAdapter  # noqa: F401
    from skillopt.envs.officeqa.tool_runtime import resolve_docs_roots, run_tool
    from skillopt.envs.spreadsheetbench.adapter import SpreadsheetBenchAdapter  # noqa: F401
    from skillopt.envs.spreadsheetbench.evaluator import compare_workbooks
    from skillopt.envs.spreadsheetbench.executor import run_generated_code

    with tempfile.TemporaryDirectory(prefix="benchmark-smoke-", dir=REPRO / ".cache") as tmp:
        scratch = Path(tmp)
        image_path = scratch / "document.png"
        image = Image.new("RGB", (320, 80), "white")
        ImageDraw.Draw(image).text((10, 10), "Invoice total: 42", fill="black")
        image.save(image_path)
        messages, _, _ = _build_messages(
            {"question": "What is the invoice total?", "image_path": str(image_path)},
            "Read the document carefully.",
            "auto",
        )
        assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert doc_evaluate("<answer>42</answer>", ["42"])["anls"] == 1

        doc_path = scratch / "bulletin.txt"
        doc_path.write_text("Treasury bulletin\nRevenue: 42 million\n", encoding="utf-8")
        roots = resolve_docs_roots([str(scratch)])
        _, found = run_tool("grep", {"pattern": "Revenue", "path": str(doc_path)},
                            allowed_roots=roots, allowed_files=[doc_path.name])
        _, read = run_tool("read", {"path": str(doc_path)},
                           allowed_roots=roots, allowed_files=[doc_path.name])
        assert "42 million" in found and "Treasury bulletin" in read

        input_path, output_path, gold_path = [scratch / name for name in ("input.xlsx", "output.xlsx", "gold.xlsx")]
        pd.DataFrame({"value": [2, 3]}).to_excel(input_path, index=False)
        pd.DataFrame({"value": [4, 6]}).to_excel(gold_path, index=False)
        ok, error = run_generated_code(
            "import pandas as pd\ndf = pd.read_excel(INPUT_PATH)\ndf['value'] *= 2\ndf.to_excel(OUTPUT_PATH, index=False)",
            str(input_path), str(output_path), timeout=30,
        )
        assert ok, error
        same, error = compare_workbooks(str(gold_path), str(output_path), "Sheet1!A1:A3")
        assert same, error

    return {
        "docvqa": {"status": "passed", "checks": ["adapter_import", "image_encoding", "multimodal_messages", "answer_scoring"]},
        "officeqa": {"status": "passed", "checks": ["adapter_import", "local_document_read", "local_document_search"]},
        "spreadsheetbench": {"status": "passed", "checks": ["adapter_import", "pandas_xlsx_io", "official_code_executor", "official_workbook_comparison"]},
    }


def check_alfworld() -> dict:
    from alfworld.agents.environment import get_environment
    from omegaconf import OmegaConf  # noqa: F401

    from skillopt.envs.alfworld.rollout import build_alfworld_env

    get_environment("AlfredTWEnv")
    data_root = Path(os.environ["ALFWORLD_DATA"])
    samples = []
    total = 0
    missing = []
    for split in ("train", "val", "test"):
        rows = json.loads((ROOT / "data" / "alfworld_path_split" / split / "items.json").read_text())
        total += len(rows)
        missing.extend(row["id"] for row in rows if not (data_root / row["gamefile"]).is_file())
        samples.append((split, rows[0]))
    if missing:
        raise FileNotFoundError(f"ALFWorld data missing for {len(missing)}/{total} official games. Prepare text game data first.")

    checked = []
    for split, sample in samples:
        manager = build_alfworld_env(
            env_num=1, seed=42, is_train=split == "train",
            eval_dataset="eval_out_of_distribution",
            specific_gamefiles=[sample["gamefile"]],
        )
        try:
            observation, _ = manager.reset({})
            assert observation["text"] and "Your task is to" in observation["text"][0]
            observation, _, _, _ = manager.step(["look"])
            assert observation["text"][0]
            checked.append({"split": split, "id": sample["id"], "reset": True, "step": True})
        finally:
            manager.envs.close()

    return {"alfworld": {"status": "passed", "official_gamefiles_present": total,
                         "checks": ["official_environment_builder", "multiprocess_worker", "reset", "step", "close"],
                         "sample_games": checked}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=("tools", "alfworld"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    (REPRO / ".cache").mkdir(parents=True, exist_ok=True)
    results = check_tools() if args.profile == "tools" else check_alfworld()
    packages = ("skillopt", "openai", "numpy", "pandas", "Pillow", "datasets") if args.profile == "tools" else (
        "skillopt", "openai", "numpy", "alfworld", "textworld", "gymnasium", "omegaconf")
    report = {
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "profile": args.profile,
        "environment": Path(sys.prefix).name,
        "python": platform.python_version(),
        "model_api_calls": 0,
        "packages": {name: importlib.metadata.version(name) for name in packages},
        "results": results,
    }
    output = args.output or REPRO / ".cache" / f"environment_check_{args.profile}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Selection and replacement safeguards for one-shot execution-error reruns."""
import json

import pytest

from reproduction import retry_spreadsheet_errors as retry
from reproduction.retry_spreadsheet_errors import merge_occurrence, select_errors, selected_items


def row(task_id, hard=0, *, execution_ok=True):
    return {"id": task_id, "hard": hard, "soft": float(hard), "execution_ok": execution_ok,
            "failure_flags": [] if execution_ok else ["llm_ok"]}


def test_selects_execution_errors_including_partial_success_but_not_wrong_answers():
    rows = [row("wrong"), row("connection", execution_ok=False), row("partial", 1, execution_ok=False)]
    rows[1]["fail_reason"] = "llm-call-failed: Connection error."
    selection = select_errors(rows, list(reversed(rows)), ["wrong", "connection", "partial"])
    assert [r["id"] for r in selection] == ["connection", "partial"]
    assert selection[0]["failure_kind"] == "connection"


def test_subset_keeps_original_order_and_rejects_changed_full_split():
    rows = [row("a"), row("b"), row("c")]
    assert selected_items(rows, ["a", "b", "c"], ["c", "a"]) == [rows[0], rows[2]]
    with pytest.raises(ValueError, match="IDs/order"):
        selected_items(list(reversed(rows)), ["a", "b", "c"], ["a"])
    for selected in (["foreign"], ["a", "a"], []):
        with pytest.raises(ValueError, match="selected task IDs"):
            selected_items(rows, ["a", "b", "c"], selected)


def test_merge_replaces_only_the_selected_occurrence_and_never_takes_best_score():
    expected = ["wrong", "error"]
    original = [row("wrong", 0), row("error", 1, execution_ok=False)]
    failed_rerun = [row("error", 0, execution_ok=False)]
    merged = merge_occurrence(original, failed_rerun, ["error"], expected)
    assert merged == [original[0], failed_rerun[0]]
    assert original[1]["hard"] == 1
    # Same ID in a different round/variant keeps that occurrence's own result.
    other_cell = [row("wrong", 0), row("error", 0)]
    assert merge_occurrence(other_cell, [], [], expected) == other_cell


@pytest.mark.parametrize("rerun", [[], [row("error"), row("error")], [row("wrong")]])
def test_merge_rejects_missing_duplicate_and_unselected_results(rerun):
    with pytest.raises(ValueError, match="expected unique test IDs"):
        merge_occurrence([row("wrong"), row("error")], rerun, ["error"], ["wrong", "error"])


def test_selection_rejects_scores_that_disagree_with_saved_fingerprints():
    with pytest.raises(ValueError, match="disagree"):
        select_errors([row("a", 1)], [row("a", 0, execution_ok=False)], ["a"])


def test_campaign_report_keeps_originals_and_joins_exact_repeat_and_variant(tmp_path, monkeypatch):
    source, root = tmp_path / "original", tmp_path / "rerun"
    expected = ["wrong", "error"]
    inputs = source / "inputs" / retry.BENCHMARK
    inputs.mkdir(parents=True)
    (inputs / "config.json").write_text("{}")
    monkeypatch.setattr(retry, "verify_inputs", lambda *_: {"test_ids": expected})
    benchmark = source / retry.BENCHMARK
    benchmark.mkdir()
    original_summary = {"variants": {variant: {"avg_at_5_hard": 0.1 if variant == "final" else 0.0}
                                     for variant in retry.VARIANTS}}
    retry.write_json(benchmark / "status.json", {"phase": "completed_with_errors"})
    retry.write_json(benchmark / "stability_summary.json", original_summary)
    for repeat, variant in retry.schedule():
        directory = retry.cell_path(benchmark, repeat, variant)
        failed = (repeat, variant) == (5, "final")
        rows = [row("wrong"), row("error", int(failed), execution_ok=not failed)]
        retry.write_jsonl(directory / "results.jsonl", rows)
        retry.write_jsonl(directory / "item_fingerprints.jsonl", rows)
        retry.write_json(directory / "trial_metrics.json", {})
    before = (benchmark / "stability_summary.json").read_bytes()
    manifest = retry.prepare(source, root)
    assert manifest["selected_executions"] == manifest["unique_task_ids"] == 1
    assert retry.read(root / "rerun_summary.json")["variants"] == {}
    directory = retry.cell_path(root, 5, "final")
    failed_again = [row("error", 0, execution_ok=False)]
    retry.write_jsonl(directory / "results.jsonl", failed_again)
    retry.write_jsonl(directory / "item_fingerprints.jsonl", failed_again)
    retry.write_json(directory / "trial_metrics.json", {})
    summary = retry.report(root)
    assert summary["complete"]
    assert summary["variants"]["final"]["composite_mean_hard"] == 0.0
    assert summary["variants"]["final"]["remaining_execution_failures"] == 1
    assert summary["variants"]["none"]["replaced_occurrences"] == 0
    merged = retry.jsonl(root / "composite/repeat_05/final/results.jsonl")
    assert [r["provenance"]["kind"] for r in merged] == ["original", "targeted_rerun"]
    assert (benchmark / "stability_summary.json").read_bytes() == before
    assert json.loads((root / "original_summary.json").read_text()) == original_summary


def test_api_bypass_preserves_other_proxy_settings(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:27891")
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    assert retry.bypass_api_proxy("https://example.test/v1") == "example.test"
    import os

    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == "localhost,127.0.0.1,example.test"
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:27891"

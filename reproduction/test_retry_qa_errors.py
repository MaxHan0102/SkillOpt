"""QA-specific error classification, benchmark routing and score provenance."""
import pytest

from reproduction import retry_qa_errors as qa


@pytest.mark.parametrize(("reason", "kind"), [
    ("error: Error code: 502 - Upstream service temporarily unavailable", "api_upstream_502"),
    ("error: Connection error.", "api_connection"),
    ("error: Request timed out.", "timeout"),
    ("Model neither produced a tool request nor a final answer", "model_incomplete_response"),
])
def test_model_output_failure_is_distinguished_from_infrastructure(reason, kind):
    assert qa.failure_kind(reason) == kind


@pytest.mark.parametrize("benchmark", qa.BENCHMARKS)
def test_subprocess_uses_selected_benchmark_and_qa_entry_point(benchmark, monkeypatch):
    qa.configure(benchmark, setter=monkeypatch.setattr)
    command = qa.engine.command("trial", "--run-root", "/tmp/example", "--repeat", "3", "--variant", "final")
    assert command[2] == benchmark
    assert command[3].endswith("/retry_qa_errors.py")
    assert command[-2:] == ["--benchmark", benchmark]
    assert f"skillopt/envs/{benchmark}/adapter.py" in qa.engine.SOURCE_FILES
    assert not any("/spreadsheetbench/" in name for name in qa.engine.SOURCE_FILES)


@pytest.mark.parametrize("benchmark", qa.BENCHMARKS)
def test_subset_report_uses_exact_cell_replacement_and_preserves_hard_and_soft(benchmark, tmp_path, monkeypatch):
    qa.configure(benchmark, setter=monkeypatch.setattr)
    engine = qa.engine
    expected = ["ordinary_wrong", "model_failure", "api_failure"]
    source, root = tmp_path / "source", tmp_path / "retry"
    inputs = source / "inputs" / benchmark
    inputs.mkdir(parents=True)
    (inputs / "config.json").write_text("{}")
    monkeypatch.setattr(engine, "verify_inputs", lambda *_: {"test_ids": expected})
    folder = source / benchmark
    folder.mkdir()
    engine.write_json(folder / "status.json", {"phase": "completed_with_errors"})
    summary = {"variants": {variant: {"avg_at_5_hard": 0.0, "avg_at_5_soft": 0.0}
                            for variant in engine.VARIANTS}}
    engine.write_json(folder / "stability_summary.json", summary)
    for repeat, variant in engine.schedule():
        directory = engine.cell_path(folder, repeat, variant)
        rows = []
        for task_id in expected:
            failed = (repeat, variant) == (5, "final") and task_id != "ordinary_wrong"
            reason = "Model neither produced a tool request nor a final answer" if task_id == "model_failure" else "error: Connection error."
            rows.append({"id": task_id, "hard": 0, "soft": 0.0, "execution_ok": not failed,
                         "failure_flags": ["agent_ok"] if failed else [], "fail_reason": reason if failed else "wrong answer"})
        engine.write_jsonl(directory / "results.jsonl", rows)
        engine.write_jsonl(directory / "item_fingerprints.jsonl", rows)
        engine.write_json(directory / "trial_metrics.json", {})
    original_bytes = (folder / "stability_summary.json").read_bytes()
    manifest = engine.prepare(source, root)
    assert manifest["benchmark"] == benchmark
    assert manifest["selected_executions"] == 2
    assert manifest["failure_kinds"] == {"model_incomplete_response": 1, "api_connection": 1}
    assert manifest["cells"][0]["repeat"] == 5
    assert engine.read(root / "rerun_summary.json")["variants"] == {}
    rerun = engine.cell_path(root, 5, "final")
    rows = [{"id": "model_failure", "hard": 0, "soft": 0.0, "execution_ok": False},
            {"id": "api_failure", "hard": 0, "soft": 0.75, "execution_ok": True}]
    engine.write_jsonl(rerun / "results.jsonl", rows)
    engine.write_jsonl(rerun / "item_fingerprints.jsonl", rows)
    engine.write_json(rerun / "trial_metrics.json", {})
    report = engine.report(root)
    assert report["complete"]
    assert report["variants"]["final"]["composite_mean_hard"] == 0.0
    assert report["variants"]["final"]["composite_mean_soft"] == pytest.approx(0.75 / 15)
    assert report["variants"]["final"]["remaining_execution_failures"] == 1
    assert report["variants"]["none"]["replaced_occurrences"] == 0
    assert report["impact_assessment"]["final"]["maximum_possible_hard_gain_percentage_points"] == pytest.approx(200 / 15)
    assert (folder / "stability_summary.json").read_bytes() == original_bytes
    assert (root / "REPORT.md").read_text().startswith(f"# {qa.LABELS[benchmark]}")

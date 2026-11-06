"""Timeout-only selection and preservation of previous valid outcomes."""
import pytest

from reproduction.retry_docvqa_timeouts import replace_selected, select_timeouts


def test_selection_excludes_wrong_answers_non_timeout_failures_and_successes():
    rows = [
        {"id": "timeout", "agent_ok": False, "phase": "timeout", "hard": 0},
        {"id": "legacy_timeout", "agent_ok": False, "fail_reason": "task-timeout-660s", "hard": 0},
        {"id": "wrong", "agent_ok": True, "hard": 0},
        {"id": "correct", "agent_ok": True, "hard": 1},
        {"id": "api_error", "agent_ok": False, "phase": "error", "fail_reason": "502", "hard": 0},
    ]
    assert select_timeouts(rows) == ["timeout", "legacy_timeout"]


@pytest.mark.parametrize("new_outcome", [
    {"hard": 1, "agent_ok": True},
    {"hard": 0, "agent_ok": True},
    {"hard": 0, "agent_ok": False, "phase": "timeout"},
])
def test_replacement_keeps_every_new_outcome_and_untouched_records(new_outcome):
    previous = [{"id": "success", "hard": 1, "soft": 1.0, "provenance": "first_retest"},
                {"id": "timeout", "hard": 0, "soft": 0.0, "agent_ok": False},
                {"id": "wrong", "hard": 0, "soft": 0.5}]
    fresh = [{"id": "timeout", "soft": float(new_outcome["hard"]), **new_outcome}]
    merged = replace_selected(previous, fresh, ["timeout"], ["success", "timeout", "wrong"])
    assert merged[0] == previous[0]
    assert merged[2] == previous[2]
    assert merged[1]["hard"] == new_outcome["hard"]
    assert previous[1]["agent_ok"] is False
    with pytest.raises(ValueError):
        replace_selected(previous, fresh, ["timeout", "success"], ["success", "timeout", "wrong"])


def test_empty_retry_preserves_completed_final_skill():
    records = [{"id": "first", "hard": 1, "soft": 1.0}, {"id": "second", "hard": 0, "soft": 0.5}]
    assert replace_selected(records, [], [], ["first", "second"]) == records

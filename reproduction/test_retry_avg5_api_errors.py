"""Verify API-only selection and the actual audited proxy request path."""
import base64
import hashlib
import importlib
import json

import httpx
import pytest
from openai import DefaultHttpxClient, InternalServerError, OpenAI

from reproduction import retry_avg5_api_errors as runner
from reproduction import run_experiments
from skillopt.model import azure_openai


def test_only_service_errors_are_replaced_and_all_task_failures_stay_scored():
    reasons = ["error: LLM message call failed after 5 retries: Connection error.",
               "error: LLM message call failed after 5 retries: Request timed out.",
               "error: LLM message call failed after 5 retries: Error code: 502",
               "task-timeout-660s", "Model neither produced a tool request nor a final answer",
               "unexpected: ValueError: invalid coordinate", "predicted 'Connection error' but expected '3'", ""]
    rows = [{"id": str(i), "hard": int(i == 7), "soft": float(i == 7), "fail_reason": reason}
            for i, reason in enumerate(reasons)]
    items = [{**row, "execution_ok": i >= 6, "failure_flags": ["agent_ok"] if i < 6 else []}
             for i, row in enumerate(rows)]
    expected = [row["id"] for row in rows]
    selection = runner.select_errors(rows, items, expected)
    assert [row["id"] for row in selection] == ["0", "1", "2", "3"]
    fresh = [{**rows[i], "fail_reason": "Model neither produced a tool request nor a final answer"}
             for i in range(4)]
    merged = runner.engine.merge_occurrence(rows, fresh, [row["id"] for row in selection], expected)
    assert len(merged) == len(rows)
    assert merged[4:] == rows[4:]
    assert sum(row["hard"] for row in merged) == 1
    assert not any(runner.api_failure_kind(row) for row in merged)


@pytest.mark.parametrize("benchmark", ["docvqa", "officeqa"])
def test_proxy_uses_frozen_images_and_retains_key_model_audit(tmp_path, monkeypatch, benchmark):
    api_base, key = "https://api.invalid/v1", "unit-api-secret"
    proxy_url = "http://user:unit-proxy-password@proxy.invalid:3128"
    monkeypatch.setenv("https_proxy", proxy_url)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("SKILLOPT_CREDENTIAL_SOURCE", "env_file")
    monkeypatch.setattr(run_experiments, "credentials", lambda: (api_base, key))
    monkeypatch.setattr(azure_openai, "_make_client", lambda _: OpenAI(base_url=api_base, api_key=key, max_retries=0))
    with DefaultHttpxClient(proxy=proxy_url, trust_env=False) as probe:
        transport_class = type(probe._transport_for_url(httpx.URL(api_base)))
    sdk_httpx = importlib.import_module(transport_class.__module__.split(".")[0])

    def fake_transport(self, request):
        assert request.headers["authorization"] == "Bearer " + key
        return sdk_httpx.Response(502, headers={"x-request-id": "test-request", "authorization": key},
                                 json={"error": {"message": f"upstream failed: {key} {proxy_url}"}})

    monkeypatch.setattr(transport_class, "handle_request", fake_transport)
    image = b"frozen-image-bytes"
    images = {"task1": {"sha256": hashlib.sha256(image).hexdigest()}} if benchmark == "docvqa" else {}
    runner.install_proxy_client(api_base, key, [{"id": "task1", "question": "What value?"}], images, tmp_path)
    runner.engine.install_request_audit(tmp_path / "request_metadata.jsonl")
    client = azure_openai._make_client("target")
    content = [{"type": "text", "text": "What value?\n\nReturn answer."},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}}]
    if benchmark == "officeqa":
        content = "## Question\nWhat value?\n\n## Candidate Files\nlocal.txt"
    with pytest.raises(InternalServerError):
        client.chat.completions.create(model="gpt-6.1-terra", reasoning_effort="high",
                                       messages=[{"role": "user", "content": content}])
    audit = json.loads((tmp_path / "request_metadata.jsonl").read_text())
    assert audit["credential_source"] == "reproduction/.env"
    assert audit["model"] == "gpt-6.1-terra" and audit["reasoning_effort"] == "high"
    route = json.loads((tmp_path / "proxy_route.json").read_text())
    assert route["actual_transport_verified"] and route["trust_env"] is False
    outgoing = json.loads((tmp_path / "proxy_requests.jsonl").read_text())
    assert outgoing["task_id"] == "task1" and outgoing["proxy_transport"] == "HTTPProxy"
    response = json.loads((tmp_path / "http_responses.jsonl").read_text())
    assert response["attempt_id"] == outgoing["attempt_id"]
    assert response["response_headers"] == {"x-request-id": "test-request"}
    for f in tmp_path.glob("*.json*"):
        saved = f.read_text()
        assert key not in saved and proxy_url not in saved and "unit-proxy-password" not in saved
    client.close()


def test_office_task_stays_identifiable_across_tool_turns():
    payload = {"messages": [{"role": "user", "content": "## Question\nWhat value?\n\nSources..."},
                            {"role": "assistant", "content": "Checking."},
                            {"role": "tool", "content": "42"}]}
    assert runner.task_for_request(payload, [{"id": "a", "question": "What value?"}]) == "a"
    with pytest.raises(ValueError, match="uniquely identify"):
        runner.task_for_request(payload, [{"id": "b", "question": "A different question?"}])

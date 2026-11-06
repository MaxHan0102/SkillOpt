"""Verify explicit proxy routing and full, redacted error diagnostics offline."""
import importlib
import json

import httpx
import pytest
from openai import APIConnectionError, DefaultHttpxClient, InternalServerError, OpenAI

from reproduction import retry_docvqa_proxy as proxy_runner
from skillopt.model import azure_openai


def test_missing_proxy_refuses_direct_fallback(monkeypatch):
    for name in ["https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="refusing a direct API request"):
        proxy_runner.configured_proxy("https://api.invalid/v1")


@pytest.mark.parametrize("failure", ["http", "connection"])
def test_explicit_proxy_overrides_no_proxy_and_records_redacted_errors(tmp_path, monkeypatch, failure):
    key = "unit-api-secret"
    proxy = "http://test-user:unit-proxy-password@proxy.invalid:3128"
    monkeypatch.setenv("https_proxy", proxy)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setattr(azure_openai, "_make_client", lambda _: OpenAI(
        base_url="https://api.invalid/v1", api_key=key, max_retries=0))

    with DefaultHttpxClient(proxy=proxy, trust_env=False) as probe:
        transport_class = type(probe._transport_for_url(httpx.URL("https://api.invalid/v1")))
    sdk_httpx = importlib.import_module(transport_class.__module__.split(".")[0])

    def fake_transport(self, request):
        if failure == "connection":
            raise sdk_httpx.ConnectError(f"proxy failed: {key} {proxy}")
        return sdk_httpx.Response(502, headers={"x-request-id": "req-unit-test", "Authorization": "private-header"},
                                 json={"error": {"message": f"upstream failed: {key} {proxy}"}})

    monkeypatch.setattr(transport_class, "handle_request", fake_transport)
    proxy_runner.install_proxy_client("https://api.invalid/v1", key,
                                      [{"id": "45837", "question": "What value?"}], tmp_path)
    client = azure_openai._make_client("target")
    assert client._client.trust_env is False
    selected = client._client._transport_for_url(httpx.URL("https://api.invalid/v1/chat/completions"))
    assert proxy_runner.is_proxy_transport(selected)
    assert type(selected._pool).__name__ == "HTTPProxy"
    error_type = InternalServerError if failure == "http" else APIConnectionError
    with pytest.raises(error_type):
        client.chat.completions.create(model="gpt-6.1-terra", reasoning_effort="high", messages=[
            {"role": "user", "content": [{"type": "text", "text": "What value?\n\nReturn answer."}]}])
    assert json.loads((tmp_path / "proxy_route.json").read_text())["actual_transport_verified"] is True
    outgoing = json.loads((tmp_path / "proxy_requests.jsonl").read_text())
    assert outgoing["task_id"] == "45837" and outgoing["proxy_transport"] == "HTTPProxy"
    diagnostic_file = "http_responses.jsonl" if failure == "http" else "transport_errors.jsonl"
    text = (tmp_path / diagnostic_file).read_text()
    assert key not in text and proxy not in text and "unit-proxy-password" not in text
    record = json.loads(text)
    assert record["attempt_id"] == outgoing["attempt_id"]
    if failure == "http":
        assert record["status_code"] == 502
        assert record["response_headers"]["x-request-id"] == "req-unit-test"
        assert "Authorization" not in record["response_headers"]
        assert "upstream failed" in record["error_body"]
    else:
        assert record["error_type"] == "ConnectError"
        assert "proxy failed" in record["error_message"]
    client.close()


def test_direct_transport_is_rejected():
    with httpx.Client(trust_env=False) as client:
        transport = client._transport_for_url(httpx.URL("https://api.invalid"))
        assert proxy_runner.is_proxy_transport(transport) is False

"""Continue avg@5 API-error reruns through a proxy, preserving task failures."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction import retry_docvqa_proxy as proxy  # noqa: E402
from reproduction import retry_qa_errors as qa  # noqa: E402
from reproduction.retest_docvqa_images import resize_image  # noqa: E402

engine = qa.engine
NATIVE_VERIFY = engine.verify
NATIVE_WORKER = engine.worker


def api_failure_kind(row: dict) -> str | None:
    reason = str(row.get("fail_reason") or "").casefold()
    if reason.startswith("task-timeout"):
        return "task_timeout"
    if not reason.startswith("error: llm message call failed"):
        return None
    if "connection error" in reason or "connection refused" in reason:
        return "api_connection"
    if "timed out" in reason or "timeout" in reason:
        return "api_timeout"
    match = re.search(r"error code:\s*([45]\d\d)", reason)
    return f"api_http_{match[1]}" if match else None


def select_errors(rows: list[dict], items: list[dict], expected: list[str]) -> list[dict]:
    candidates = qa.select_errors(rows, items, expected)
    raw = {str(row["id"]): row for row in rows}
    return [{**item, "failure_kind": kind} for item in candidates
            if (kind := api_failure_kind(raw[item["id"]])) is not None]


def verify(root: Path) -> dict:
    manifest = NATIVE_VERIFY(root)
    for name, expected in manifest.get("extra_input_sha256", {}).items():
        if engine.digest(Path(name)) != expected:
            raise ValueError(f"Frozen rerun input changed: {name}")
    return manifest


def full_items(root: Path) -> list[dict]:
    from scripts.eval_only import get_adapter

    cfg = engine.read(root / "inputs" / engine.BENCHMARK / "config.json")
    adapter = get_adapter(cfg)
    adapter.setup(cfg)
    return adapter.build_eval_env(0, "valid_unseen", 42)


def prepare(source: Path, root: Path) -> dict:
    manifest = engine.prepare(source, root)
    manifest.update(
        selection_rule="Only original API HTTP/connection/request timeout failures or task timeouts; "
                       "model-output and local task failures remain scored as wrong",
        network_change="Explicit configured proxy, trust_env=False; verify actual proxy transport",
        credential_source="reproduction/.env",
        protocol="One new execution per selected original (repeat, skill, task), preserving all outcomes; "
                 "original task/model failures and all earlier campaigns are retained; no best-of selection",
        images={}, extra_input_sha256={},
    )
    selected = {item["id"] for cell in manifest["cells"] for item in cell["selection"]}
    items = engine.selected_items(full_items(root), manifest["test_ids"], sorted(selected))
    if engine.BENCHMARK == "docvqa":
        image_dir = root / "inputs" / "images"
        image_dir.mkdir()
        for item in items:
            info = resize_image(Path(item["image_path"]), image_dir / f"{item['id']}.png")
            manifest["images"][str(item["id"])] = info
            manifest["original_files_sha256"][info["source"]] = info["source_sha256"]
            manifest["extra_input_sha256"][info["path"]] = info["sha256"]
        manifest["preprocessing"] = (
            "Same as ordinary DocVQA retest: if estimated 32px patches exceed 30000, "
            "multiply both original dimensions by 0.85 until <=28000; floor; RGB PNG/LANCZOS; no crop"
        )
    engine.write_json(root / "manifest.json", manifest)
    (root / "manifest.sha256").write_text(engine.digest(root / "manifest.json") + "\n")
    report(root)
    return {key: manifest[key] for key in ("benchmark", "selected_executions", "unique_task_ids", "failure_kinds")}


def task_for_request(payload: dict, items: list[dict]) -> str:
    texts = []
    for message in payload["messages"]:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(part.get("text", "") for part in content if part.get("type") == "text")
    matches = [str(item["id"]) for item in items if any(
        text.startswith(item["question"] + "\n\n") or text.startswith("## Question\n" + item["question"] + "\n\n")
        for text in texts)]
    if len(matches) != 1:
        raise ValueError("Cannot uniquely identify the selected task in an outgoing request")
    return matches[0]


def install_proxy_client(api_base: str, key: str, items: list[dict], images: dict, directory: Path) -> None:
    import httpx
    from openai import DefaultHttpxClient

    from skillopt.model import azure_openai

    name, url = proxy.configured_proxy(api_base)
    parts = urlsplit(url)
    secrets = [key, url, parts.password or "", unquote(parts.password or "")]
    factory = azure_openai._make_client
    lock = threading.Lock()

    def record(filename, data):
        def scrub(value):
            if isinstance(value, str):
                return proxy.redact(value, secrets)
            if isinstance(value, dict):
                return {k: scrub(v) for k, v in value.items()}
            if isinstance(value, list):
                return [scrub(v) for v in value]
            return value
        with lock, (directory / filename).open("a") as stream:
            stream.write(json.dumps(scrub(data), ensure_ascii=False) + "\n")

    def proxy_factory(role):
        if role != "target":
            raise RuntimeError("Optimizer requests forbidden during avg@5 reruns")
        client = factory(role)
        client._client.close()
        client._client = DefaultHttpxClient(proxy=url, trust_env=False)
        transport = client._client._transport_for_url(httpx.URL(api_base))
        if not proxy.is_proxy_transport(transport):
            client.close()
            raise RuntimeError("Refusing API request outside the configured proxy")

        def request_hook(request):
            selected_transport = client._client._transport_for_url(request.url)
            if not proxy.is_proxy_transport(selected_transport):
                raise RuntimeError("Outgoing request bypasses proxy")
            payload = json.loads(request.content)
            task_id = task_for_request(payload, items)
            if images:
                hashes = [hashlib.sha256(base64.b64decode(part["image_url"]["url"].split(",", 1)[1], validate=True)).hexdigest()
                          for message in payload["messages"] if isinstance(message.get("content"), list)
                          for part in message["content"] if part.get("type") == "image_url"]
                if hashes != [images[task_id]["sha256"]]:
                    raise ValueError("Outgoing image differs from this task's frozen resized input")
            request.extensions.update(avg5_task_id=task_id, avg5_attempt_id=uuid.uuid4().hex, avg5_started=time.monotonic())
            record("proxy_requests.jsonl", {"time": engine.now(), "task_id": task_id,
                   "attempt_id": request.extensions["avg5_attempt_id"], "proxy_source": name,
                   "proxy_transport": type(selected_transport._pool).__name__, "explicit_proxy": True})

        def response_hook(response):
            request = response.request
            data = {"time": engine.now(), "task_id": request.extensions["avg5_task_id"],
                    "attempt_id": request.extensions["avg5_attempt_id"], "status_code": response.status_code,
                    "elapsed_seconds": round(time.monotonic() - request.extensions["avg5_started"], 3),
                    "response_headers": {k: response.headers[k] for k in
                        ("x-request-id", "request-id", "x-correlation-id", "traceparent", "cf-ray", "x-trace-id")
                        if k in response.headers}}
            if response.status_code >= 400:
                try:
                    response.read()
                    data["error_body"] = response.text
                except Exception as error:
                    data["error_body_read_error"] = f"{type(error).__name__}: {error}"
            record("http_responses.jsonl", data)

        original_handle = transport.handle_request

        def handle(request):
            try:
                return original_handle(request)
            except Exception as error:
                record("transport_errors.jsonl", {"time": engine.now(), "task_id": request.extensions.get("avg5_task_id"),
                       "attempt_id": request.extensions.get("avg5_attempt_id"), "error_type": type(error).__name__,
                       "error_message": str(error)})
                raise

        transport.handle_request = handle
        client._client.event_hooks["request"].append(request_hook)
        client._client.event_hooks["response"].append(response_hook)
        with lock:
            engine.write_json(directory / "proxy_route.json", {"checked_at": engine.now(), "proxy_source": name,
                              "pool_class": type(transport._pool).__name__, "actual_transport_verified": True,
                              "explicit_proxy": True, "trust_env": False})
        return client

    azure_openai._make_client = proxy_factory


def trial(root: Path, repeat: int, variant: str) -> None:
    manifest = verify(root)
    cell = next(cell for cell in manifest["cells"] if (cell["repeat"], cell["variant"]) == (repeat, variant))
    selected = [item["id"] for item in cell["selection"]]
    items = engine.selected_items(full_items(root), manifest["test_ids"], selected)
    if manifest["images"]:
        items = [{**item, "image_path": manifest["images"][str(item["id"])]["path"],
                  "image_paths": [manifest["images"][str(item["id"])]["path"]]} for item in items]
    directory = engine.cell_path(root, repeat, variant)
    directory.mkdir(parents=True, exist_ok=False)
    state = {"phase": "running", "benchmark": engine.BENCHMARK, "repeat": repeat, "variant": variant,
             "started_at": engine.now(), "selected_count": len(selected)}
    engine.write_json(directory / "status.json", state)
    try:
        os.environ["SKILLOPT_CREDENTIAL_SOURCE"] = "env_file"
        api_base, key = engine.credentials()
        from skillopt.model import azure_openai
        azure_openai.configure_azure_openai(endpoint=api_base, api_key=key, auth_mode="openai_compatible")
        engine.install_tool_reasoning()
        install_proxy_client(api_base, key, items, manifest["images"], directory)
        # Install AFTER the replacement HTTP client, so Key/model/High checks remain attached.
        engine.install_request_audit(directory / "request_metadata.jsonl")
        from scripts import eval_only
        native_adapter = eval_only.get_adapter

        def filtered_adapter(cfg):
            adapter = native_adapter(cfg)
            native_build = adapter.build_eval_env

            def build(env_num, split, seed, **kwargs):
                if (env_num, split, seed) != (0, "valid_unseen", 42):
                    raise ValueError("Changed evaluation settings")
                engine.selected_items(native_build(env_num, split, seed, **kwargs), manifest["test_ids"], selected)
                return items
            adapter.build_eval_env = build
            return adapter

        eval_only.get_adapter = filtered_adapter
        inputs = root / "inputs" / engine.BENCHMARK
        engine.write_json(directory / "run_metadata.json", {**state, "expected_test_ids": selected,
                          "source_directory": cell["source_directory"], "manifest_sha256": engine.digest(root / "manifest.json"),
                          "model": engine.MODEL, "reasoning_effort": "high", "seed": 42,
                          "credential_source": "reproduction/.env", "explicit_proxy": True, "optimizer_calls_allowed": False})
        sys.argv = ["scripts/eval_only.py", "--config", str(inputs / "config.json"), "--skill", str(inputs / f"{variant}.md"),
                    "--split", "valid_unseen", "--out_root", str(directory)]
        eval_only.main()
        engine.write_json(directory / "token_usage.json", azure_openai.tracker.summary())
        metrics = engine.collect_trial(directory, engine.BENCHMARK, selected)
        metrics["api_failures"] = sum(api_failure_kind(row) is not None for row in engine.jsonl(directory / "results.jsonl"))
        engine.write_json(directory / "trial_metrics.json", metrics)
        state.update(phase="completed_with_errors" if metrics["api_failures"] else "completed", finished_at=engine.now())
    except BaseException as error:
        state.update(phase="failed", error_type=type(error).__name__, finished_at=engine.now())
        raise
    finally:
        engine.write_json(directory / "status.json", state)


def report(root: Path) -> dict:
    summary = qa.NATIVE_REPORT(root)
    manifest = engine.read(root / "manifest.json")
    original = engine.read(root / "original_summary.json")
    summary["benchmark"] = manifest["benchmark"]
    for variant, values in summary["variants"].items():
        rows = [row for repeat in range(1, 6) for row in engine.jsonl(
            root / "composite" / f"repeat_{repeat:02d}" / variant / "results.jsonl")]
        values["remaining_api_failures"] = sum(api_failure_kind(row) is not None for row in rows)
        values["non_api_execution_failures_counted_as_wrong"] = (
            values["remaining_execution_failures"] - values["remaining_api_failures"])
        values["composite_mean_soft"] = sum(float(row["soft"]) for row in rows) / len(rows)
    engine.write_json(root / "rerun_summary.json", summary)
    lines = [f"# {qa.LABELS[manifest['benchmark']]} avg@5：API 异常补测", "", manifest["protocol"], "",
             f"已核验补测 {summary['verified_rerun_executions']}/{summary['selected_executions']} 次执行。", "",
             "只替换原始 API／超时失败；模型输出和本地测试失败保留为答错。所有原始记录及旧补测保留。",
             "使用显式代理；DocVQA 沿用普通实验的超限图片缩放规则。结果为补测合并均值。", "",
             "| Skill | 原始 avg@5 | 补测合并均值 | 剩余 API/超时失败 | 非 API 执行失败（计错） |",
             "|---|---:|---:|---:|---:|"]
    for variant in engine.VARIANTS:
        before = original["variants"][variant]["avg_at_5_hard"]
        after = summary["variants"].get(variant)
        result = (f"{after['composite_mean_hard']:.2%} | {after['remaining_api_failures']} | "
                  f"{after['non_api_execution_failures_counted_as_wrong']}" if after else "待完成 | 待统计 | 保留原始计分")
        lines.append(f"| {variant} | {before:.2%} | {result} |")
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def configure(benchmark: str, setter=setattr) -> None:
    qa.configure(benchmark, setter=setter)

    def command(*arguments):
        return ["bash", str(ROOT / "reproduction/benchmark_python.sh"), benchmark, str(Path(__file__).resolve()),
                *arguments, "--benchmark", benchmark]

    sources = (*engine.SOURCE_FILES, str(Path(__file__).resolve().relative_to(ROOT)),
               "reproduction/retest_docvqa_images.py", "reproduction/retry_docvqa_proxy.py",
               "reproduction/retry_docvqa_timeouts.py")
    for key, value in {"command": command, "select_errors": select_errors, "verify": verify,
                       "report": report, "SOURCE_FILES": sources}.items():
        setter(engine, key, value)


def worker(root: Path) -> None:
    NATIVE_WORKER(root)
    summary = engine.read(root / "rerun_summary.json")
    if summary["complete"]:
        state = engine.read(root / "status.json")
        failures = sum(item["remaining_api_failures"] for item in summary["variants"].values())
        state.update(phase="completed_with_api_errors" if failures else "completed", remaining_api_failures=failures,
                     non_api_failures_counted_as_wrong=True)
        engine.write_json(root / "status.json", state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "start", "worker", "trial", "report", "status"))
    parser.add_argument("--benchmark", choices=qa.BENCHMARKS, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--repeat", type=int, choices=range(1, 6))
    parser.add_argument("--variant", choices=engine.VARIANTS)
    args = parser.parse_args()
    os.chdir(ROOT)
    configure(args.benchmark)
    root = args.run_root.resolve()
    if args.action == "prepare":
        if args.source_root is None:
            parser.error("prepare requires --source-root")
        result = prepare(args.source_root.resolve(), root)
    elif args.action == "trial":
        if args.repeat is None or args.variant is None:
            parser.error("trial requires --repeat and --variant")
        code = 0
        try:
            trial(root, args.repeat, args.variant)
        except BaseException:
            code = 1
            traceback.print_exc()
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            # Native timeout results are durable; do not wait for obsolete API threads.
            os._exit(code)
    else:
        result = {"check": qa.check, "start": engine.start, "worker": worker,
                  "report": report, "status": qa.status}[args.action](root)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

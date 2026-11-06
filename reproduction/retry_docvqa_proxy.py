"""Retry remaining ordinary DocVQA failures through an explicit HTTP proxy."""
from __future__ import annotations

import argparse
import copy
import fcntl
import importlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction import retry_docvqa_timeouts as previous_runner  # noqa: E402

base = previous_runner.base


def configured_proxy(api_base: str) -> tuple[str, str]:
    scheme = urlsplit(api_base).scheme
    names = [f"{scheme}_proxy", f"{scheme.upper()}_PROXY", "all_proxy", "ALL_PROXY"]
    for name in names:
        if os.environ.get(name):
            return name, os.environ[name]
    raise ValueError("No configured proxy available; refusing a direct API request")


def redact(text: str, secrets: list[str]) -> str:
    for secret in sorted({value for value in secrets if value}, key=len, reverse=True):
        text = text.replace(secret, "<REDACTED>")
    return text


def is_proxy_transport(transport) -> bool:
    pool = getattr(transport, "_pool", None)
    provider = type(pool).__module__.split(".")[0]
    if provider not in {"httpcore", "httpcore2"}:
        return False
    module = importlib.import_module(provider)
    proxy_types = tuple(value for name in ("HTTPProxy", "SOCKSProxy")
                        if isinstance(value := getattr(module, name, None), type))
    return isinstance(pool, proxy_types)


def install_proxy_client(api_base: str, key: str, items: list[dict], directory: Path) -> None:
    import httpx
    from openai import DefaultHttpxClient

    from skillopt.model import azure_openai

    name, proxy = configured_proxy(api_base)
    parts = urlsplit(proxy)
    secrets = [key, proxy, parts.password or "", unquote(parts.password or "")]
    factory = azure_openai._make_client
    lock = threading.Lock()

    def record(filename: str, data: dict):
        def scrub(value):
            if isinstance(value, str):
                return redact(value, secrets)
            if isinstance(value, dict):
                return {field: scrub(item) for field, item in value.items()}
            if isinstance(value, list):
                return [scrub(item) for item in value]
            return value

        encoded = json.dumps(scrub(data), ensure_ascii=False)
        with lock, (directory / filename).open("a") as stream:
            stream.write(encoded + "\n")

    def proxy_factory(role):
        if role != "target":
            raise RuntimeError("Optimizer calls forbidden during targeted proxy evaluation")
        client = factory(role)
        client._client.close()
        # Explicit proxy + trust_env=False deliberately ignores the inherited
        # NO_PROXY values, including the bypass added by the historical runner.
        client._client = DefaultHttpxClient(proxy=proxy, trust_env=False)
        transport = client._client._transport_for_url(httpx.URL(api_base))
        if not is_proxy_transport(transport):
            client.close()
            raise RuntimeError("Selected HTTP transport is not a proxy; refusing API request")

        def request_hook(request):
            selected = client._client._transport_for_url(request.url)
            if not is_proxy_transport(selected):
                raise RuntimeError("Outgoing request bypasses proxy")
            payload = json.loads(request.content)
            texts = [part.get("text", "") for message in payload["messages"]
                     if isinstance(message.get("content"), list) for part in message["content"]
                     if part.get("type") == "text"]
            matches = [str(item["id"]) for item in items
                       if any(text.startswith(item["question"] + "\n\n") for text in texts)]
            if len(matches) != 1:
                raise ValueError("Cannot identify the outgoing DocVQA task")
            request.extensions["docvqa_attempt_id"] = uuid.uuid4().hex
            request.extensions["docvqa_task_id"] = matches[0]
            request.extensions["docvqa_started"] = time.monotonic()
            record("proxy_requests.jsonl", {
                "time": base.now(), "task_id": matches[0], "attempt_id": request.extensions["docvqa_attempt_id"],
                "path": request.url.path, "proxy_source": name, "proxy_scheme": parts.scheme,
                "proxy_transport": type(selected._pool).__name__, "explicit_proxy": True, "trust_env": False,
            })

        def response_hook(response):
            request = response.request
            data = {"time": base.now(), "task_id": request.extensions["docvqa_task_id"],
                    "attempt_id": request.extensions["docvqa_attempt_id"], "status_code": response.status_code,
                    "elapsed_seconds": round(time.monotonic() - request.extensions["docvqa_started"], 3),
                    "path": request.url.path}
            header_names = ["x-request-id", "request-id", "x-correlation-id", "traceparent", "cf-ray",
                            "x-amzn-trace-id", "x-trace-id", "server", "via", "date", "content-type"]
            data["response_headers"] = {header: response.headers[header] for header in header_names if header in response.headers}
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
                record("transport_errors.jsonl", {
                    "time": base.now(), "task_id": request.extensions.get("docvqa_task_id"),
                    "attempt_id": request.extensions.get("docvqa_attempt_id"),
                    "error_type": type(error).__name__, "error_message": str(error),
                })
                raise

        transport.handle_request = handle
        client._client.event_hooks.setdefault("request", []).append(request_hook)
        client._client.event_hooks.setdefault("response", []).append(response_hook)
        base.write_json(directory / "proxy_route.json", {
            "checked_at": base.now(), "proxy_source": name, "proxy_scheme": parts.scheme,
            "explicit_proxy": True, "trust_env": False, "pool_class": type(transport._pool).__name__,
            "actual_transport_verified": True, "no_proxy_bypass_effective": False,
        })
        return client

    azure_openai._make_client = proxy_factory


def prepare(previous: Path, root: Path) -> dict:
    old = base.verify(previous)
    if not base.read(previous / "retry_summary.json")["complete"]:
        raise ValueError("Previous retry campaign is incomplete")
    selections = {variant: previous_runner.select_timeouts(base.rows(previous / "composite" / variant / "results.jsonl"))
                  for variant in base.VARIANTS}
    selections = {variant: ids for variant, ids in selections.items() if ids}
    if not selections:
        raise ValueError("No remaining timeout records")
    root.mkdir(parents=True, exist_ok=False)
    manifest = copy.deepcopy(old)
    for file in [previous / "manifest.json", previous / "manifest.sha256", previous / "retry_summary.json",
                 *[previous / "composite" / variant / "results.jsonl" for variant in base.VARIANTS]]:
        manifest["original_sha256"][str(file)] = base.digest(file)
    manifest["code_sha256"][str(Path(__file__).resolve().relative_to(ROOT))] = base.digest(Path(__file__))
    manifest.update(previous_root=str(previous), created_at=base.now(), selections=selections,
                    selected_executions=sum(map(len, selections.values())),
                    protocol="User-requested explicit-proxy rerun of only the two remaining timeout occurrences; "
                             "one evaluation per selected task, keep every outcome; same images, skills, Terra/High, seed and timeout; "
                             "retain all 374 test tasks and preserve previous results; no evolution",
                    network_change="Explicit proxy from https_proxy/HTTPS_PROXY or ALL_PROXY; trust_env=False; "
                                   "verify actual HTTPProxy/SOCKSProxy transport before sending; ignore NO_PROXY bypass")
    for variant, selected in selections.items():
        inputs = root / "cells" / variant / "inputs"
        inputs.mkdir(parents=True)
        source = previous / "cells" / variant / "inputs"
        for name in ("config.json", f"{variant}.md"):
            (inputs / name).write_bytes((source / name).read_bytes())
        items = [row for row in base.read(source / "items.json") if str(row["id"]) in selected]
        if len(items) != len(selected) or {str(row["id"]) for row in items} != set(selected):
            raise ValueError("Frozen selected inputs are incomplete")
        base.write_json(inputs / "items.json", items)
        cell_manifest = copy.deepcopy(manifest)
        cell_manifest.update(selected_ids=selected, selected_executions=len(selected), variants=[variant])
        cell_manifest["input_sha256"].update({str(file): base.digest(file) for file in inputs.iterdir()})
        previous_runner.save_manifest(inputs.parent, cell_manifest)
        manifest["original_sha256"][str(inputs.parent / "manifest.json")] = base.digest(inputs.parent / "manifest.json")
    previous_runner.save_manifest(root, manifest)
    base.write_json(root / "status.json", {"phase": "prepared", "updated_at": base.now(), "selections": selections})
    return manifest


def trial(root: Path, variant: str) -> None:
    manifest = base.verify(root)
    cell = root / "cells" / variant
    base.IDS = tuple(manifest["selections"][variant])
    os.environ["SKILLOPT_CREDENTIAL_SOURCE"] = "env_file"
    endpoint, key = base.credentials()
    install_proxy_client(endpoint, key, base.read(cell / "inputs/items.json"), cell / "evaluations" / variant)
    base.trial(cell, variant)


def report(root: Path) -> dict:
    manifest = base.verify(root)
    previous = Path(manifest["previous_root"])
    before = base.read(previous / "retry_summary.json")
    summary = {"updated_at": base.now(), "protocol": manifest["protocol"], "test_count": len(manifest["test_ids"]),
               "selected_executions": manifest["selected_executions"], "verified_executions": 0,
               "complete": True, "variants": {}}
    lines = ["# DocVQA：通过显式代理重测剩余超时记录", "", manifest["protocol"], "",
             "仅重测最优 Skill 仍超时的两题。已有成功记录与历史结果保留；结果属于异常补测合并，并非独立全量评测或 avg@5。",
             "显式代理是本轮唯一有意改变的运行设置，分母保持374，再次异常或答错均保留。", "",
             "| Skill | 本轮题数 | 答对 | 执行异常 | 前次合并 hard | 本次合并 hard | 本次合并 ANLS |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for variant in base.VARIANTS:
        selected = manifest["selections"].get(variant, [])
        directory = root / "cells" / variant / "evaluations" / variant
        if selected and not (directory / "trial_metrics.json").exists():
            summary["complete"] = False
            continue
        old = base.rows(previous / "composite" / variant / "results.jsonl")
        fresh = base.rows(directory / "results.jsonl") if selected else []
        merged = previous_runner.replace_selected(old, fresh, selected, manifest["test_ids"])
        for row in merged:
            if str(row["id"]) in selected:
                row["provenance"] = "user_requested_explicit_proxy_retry"
        destination = root / "composite" / variant
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged))
        errors = base.read(directory / "trial_metrics.json")["execution_failures"] if selected else 0
        result = {"selected_ids": selected, "rerun_count": len(fresh), "rerun_correct": sum(r["hard"] for r in fresh),
                  "rerun_execution_failures": errors, "prior_composite_hard": before["variants"][variant]["composite_hard"],
                  "composite_hard": sum(r["hard"] for r in merged) / len(merged),
                  "composite_soft": sum(r["soft"] for r in merged) / len(merged), "denominator": len(merged)}
        summary["variants"][variant] = result
        summary["verified_executions"] += len(fresh)
        lines.append(f"| {variant} | {len(fresh)} | {result['rerun_correct']} | {errors} | "
                     f"{result['prior_composite_hard']:.2%} | {result['composite_hard']:.2%} | {result['composite_soft']:.2%} |")
    base.write_json(root / "retry_summary.json", summary)
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def run(root: Path) -> None:
    with (root / "worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = base.verify(root)
        state = {"phase": "running", "started_at": base.now(), "worker_pid": os.getpid(), "network_mode": "explicit_proxy"}
        try:
            for variant, selected in manifest["selections"].items():
                state.update(phase="evaluating", variant=variant, selected_ids=selected, updated_at=base.now())
                base.write_json(root / "status.json", state)
                with (root / f"{variant}.log").open("x") as log:
                    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "trial", "--run-root", str(root),
                                             "--variant", variant], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                summary = report(root)
                print(f"[{base.now()}] {variant} returncode={result.returncode} verified={summary['verified_executions']}/"
                      f"{manifest['selected_executions']}", flush=True)
                if result.returncode:
                    raise RuntimeError("Proxy trial child failed; evidence preserved")
            errors = sum(r["rerun_execution_failures"] for r in summary["variants"].values())
            state.update(phase="completed_with_errors" if errors else "completed", finished_at=base.now())
        except BaseException as error:
            state.update(phase="failed", error_type=type(error).__name__, finished_at=base.now())
            raise
        finally:
            base.write_json(root / "status.json", state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "run", "trial", "report"))
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--previous-root", type=Path, default=ROOT / "reproduction/outputs/docvqa_timeout_retry_20261106")
    parser.add_argument("--variant", choices=base.VARIANTS)
    args = parser.parse_args()
    os.chdir(ROOT)
    root = args.run_root.resolve()
    if not root.is_relative_to(ROOT / "reproduction/outputs"):
        parser.error("Keep outputs under reproduction/outputs")
    if args.action == "prepare":
        manifest = prepare(args.previous_root.resolve(), root)
        print(json.dumps({"selections": manifest["selections"], "selected_executions": manifest["selected_executions"]}))
    elif args.action == "check":
        manifest = base.verify(root)
        for variant in manifest["selections"]:
            base.verify(root / "cells" / variant)
        os.environ["SKILLOPT_CREDENTIAL_SOURCE"] = "env_file"
        endpoint, _ = base.credentials()
        name, _ = configured_proxy(endpoint)
        print(json.dumps({"frozen_inputs_verified": True, "proxy_source": name, "selections": manifest["selections"]}))
    elif args.action == "trial":
        if not args.variant:
            parser.error("trial requires --variant")
        code = 0
        try:
            trial(root, args.variant)
        except BaseException:
            code = 1
            traceback.print_exc()
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(code)
    elif args.action == "run":
        run(root)
    else:
        print(json.dumps(report(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

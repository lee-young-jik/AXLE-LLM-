#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib import error as urlerror
from urllib import request as urlrequest

from openai import AsyncOpenAI
from axle import AxleClient

from autoprove import (
    SYSTEM_PROMPT,
    Problem,
    build_user_prompt,
    call_llm,
    extract_errors,
    get_content,
    get_okay,
    parse_csv,
    summarize_first_change,
    to_dict,
)

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT
if os.getenv("WEB_PROVER_OUTPUT_DIR", "").strip():
    OUTPUT_DIR = Path(os.getenv("WEB_PROVER_OUTPUT_DIR", "").strip())
elif os.getenv("VERCEL", "").strip():
    OUTPUT_DIR = Path("/tmp") / "webapp"
else:
    OUTPUT_DIR = ROOT / "outputs" / "webapp"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class RunRequest:
    axle_api_key: str
    problem_id: str
    formal_statement: str
    use_mathlib: bool
    hint: str
    environment: str
    llm_provider: str
    llm_api_key: str
    llm_model: str
    max_attempts: int
    temperature: float
    max_tokens: int
    use_repair: bool
    repairs: str
    terminal_tactics: str
    fallback_tactics: str
    fallback_after_attempt: int
    feedback_lines: int


jobs_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def model_default(provider: str) -> str:
    if provider == "openrouter":
        return "openai/gpt-4.1-mini"
    if provider == "openai":
        return "gpt-4.1-mini"
    return "gpt-4.1-mini"


def is_likely_axle_key(key: str) -> bool:
    key = key.strip()
    if not key.startswith("pk_"):
        return False
    return len(key) >= len("pk_") + 12


def provider_api_key(provider: str) -> str:
    if provider == "openrouter":
        return os.getenv("OPENROUTER_API_KEY", "").strip()
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY", "").strip()
    return ""


def is_likely_openrouter_key(key: str) -> bool:
    key = key.strip()
    if not key.startswith("sk-or-v1-"):
        return False
    return len(key) >= len("sk-or-v1-") + 16


def config_payload() -> dict[str, Any]:
    providers = [
        {
            "id": "openrouter",
            "label": "OpenRouter",
            "default_model": os.getenv("OPENROUTER_MODEL", model_default("openrouter")),
            "manual_key_input": True,
            "key_from_server_env": False,
        }
    ]
    if provider_api_key("openai"):
        providers.append(
            {
                "id": "openai",
                "label": "OpenAI",
                "default_model": os.getenv("OPENAI_MODEL", model_default("openai")),
                "manual_key_input": False,
                "key_from_server_env": True,
            }
        )
    return {
        "providers": providers,
        "default_provider": "openrouter",
        "axle_ready": True,
        "axle_manual_key_input": True,
        "axle_key_from_server_env": False,
    }


def fetch_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    req = urlrequest.Request(url, headers=headers or {})
    with urlrequest.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def fetch_models(provider: str, llm_api_key: str = "") -> list[dict[str, str]]:
    provider = provider.strip().lower()
    key = llm_api_key.strip() or provider_api_key(provider)

    if provider == "openrouter":
        if not llm_api_key.strip():
            raise ValueError("OpenRouter 모델 조회에는 API key 직접 입력이 필요합니다.")
        key = llm_api_key.strip()
        if not is_likely_openrouter_key(key):
            raise ValueError("OpenRouter API key 형식이 올바르지 않습니다. (sk-or-v1-...)")
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = fetch_json("https://openrouter.ai/api/v1/models", headers=headers)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        models: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id", "")).strip()
            if not model_id:
                continue
            name = str(row.get("name", "")).strip() or model_id
            models.append({"id": model_id, "name": name})
        models.sort(key=lambda x: x["id"])
        return models

    if provider == "openai":
        if not key:
            raise ValueError("OPENAI_API_KEY is not configured on the server")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }
        payload = fetch_json(f"{base_url}/models", headers=headers)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        models: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id", "")).strip()
            if not model_id:
                continue
            models.append({"id": model_id, "name": model_id})
        models.sort(key=lambda x: x["id"])
        return models

    raise ValueError("llm_provider must be one of: openrouter, openai")


def build_llm_client(cfg: RunRequest) -> AsyncOpenAI:
    provider = cfg.llm_provider
    if provider == "openrouter":
        api_key = cfg.llm_api_key.strip()
    else:
        api_key = cfg.llm_api_key.strip() or provider_api_key(provider)
    if not api_key:
        raise RuntimeError(f"{provider} API key is not configured on the server")
    if provider == "openrouter":
        return AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


def safe_request_view(cfg: RunRequest) -> dict[str, Any]:
    data = asdict(cfg)
    data["axle_api_key"] = "<hidden>" if cfg.axle_api_key.strip() else ""
    data["llm_api_key"] = "<hidden>" if cfg.llm_api_key.strip() else ""
    return data


def normalize_mathlib_import(statement: str, use_mathlib: bool) -> str:
    lines = [line for line in statement.strip().splitlines()]
    if not lines:
        return ""

    filtered = [line for line in lines if line.strip() != "import Mathlib"]
    body = "\n".join(filtered).strip()
    if not body:
        return ""

    if use_mathlib:
        return f"import Mathlib\n\n{body}\n"
    return body + "\n"


def add_event(job_id: str, event_type: str, **payload: Any) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job["updated_at"] = utc_now()
        job["events"].append(
            {
                "type": event_type,
                "timestamp": job["updated_at"],
                **payload,
            }
        )


def set_job_state(job_id: str, **updates: Any) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.update(updates)
        job["updated_at"] = utc_now()


def create_job(job_id: str, cfg: RunRequest) -> None:
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "events": [],
            "result": None,
            "request": safe_request_view(cfg),
        }


def load_request(data: dict[str, Any]) -> RunRequest:
    raw_formal_statement = str(data.get("formal_statement", "")).strip()
    if not raw_formal_statement:
        raise ValueError("formal_statement is required")
    use_mathlib = bool(data.get("use_mathlib", True))
    formal_statement = normalize_mathlib_import(raw_formal_statement, use_mathlib)
    if not formal_statement:
        raise ValueError("formal_statement is empty after Mathlib import normalization")

    axle_api_key = str(data.get("axle_api_key", "")).strip()
    if not axle_api_key:
        raise ValueError("AXLE 실행에는 API key 직접 입력이 필요합니다.")
    if not is_likely_axle_key(axle_api_key):
        raise ValueError("AXLE API key 형식이 올바르지 않습니다. (pk_...)")

    llm_provider = str(data.get("llm_provider", "openrouter")).strip().lower()
    if llm_provider not in {"openrouter", "openai"}:
        raise ValueError("llm_provider must be one of: openrouter, openai")
    llm_api_key = str(data.get("llm_api_key", "")).strip()
    if llm_provider == "openrouter":
        if not llm_api_key:
            raise ValueError("OpenRouter 실행에는 API key 직접 입력이 필요합니다.")
        if not is_likely_openrouter_key(llm_api_key):
            raise ValueError("OpenRouter API key 형식이 올바르지 않습니다. (sk-or-v1-...)")
    elif not (llm_api_key or provider_api_key(llm_provider)):
        raise ValueError(f"{llm_provider} API key is not configured on the server")
    return RunRequest(
        axle_api_key=axle_api_key,
        problem_id=str(data.get("problem_id", "web_problem")).strip() or "web_problem",
        formal_statement=formal_statement,
        use_mathlib=use_mathlib,
        hint=str(data.get("hint", "")).strip(),
        environment=str(data.get("environment", "lean-4.28.0")).strip() or "lean-4.28.0",
        llm_provider=llm_provider,
        llm_api_key=llm_api_key,
        llm_model=str(data.get("llm_model", "")).strip() or model_default(llm_provider),
        max_attempts=max(1, int(data.get("max_attempts", 6))),
        temperature=float(data.get("temperature", 0.2)),
        max_tokens=max(200, int(data.get("max_tokens", 1200))),
        use_repair=bool(data.get("use_repair", True)),
        repairs=str(data.get("repairs", "apply_terminal_tactics,remove_extraneous_tactics")).strip(),
        terminal_tactics=str(data.get("terminal_tactics", "aesop,simp,rfl")).strip(),
        fallback_tactics=str(data.get("fallback_tactics", "")).strip(),
        fallback_after_attempt=max(1, int(data.get("fallback_after_attempt", 1))),
        feedback_lines=max(1, int(data.get("feedback_lines", 10))),
    )


async def run_job(job_id: str, cfg: RunRequest) -> None:
    set_job_state(job_id, status="running")
    add_event(job_id, "job_started", message="Proof loop started.")

    llm = build_llm_client(cfg)
    problem = Problem(
        problem_id=cfg.problem_id,
        formal_statement=cfg.formal_statement,
        environment=cfg.environment,
        hint=cfg.hint,
    )
    attempts: list[dict[str, Any]] = []
    feedback = ""

    try:
        async with AxleClient(api_key=cfg.axle_api_key.strip()) as axle:
            for attempt in range(1, cfg.max_attempts + 1):
                prompt = build_user_prompt(
                    problem=problem,
                    attempt=attempt,
                    max_attempts=cfg.max_attempts,
                    last_feedback=feedback,
                    solved_memory=[],
                    max_statement_chars=700,
                )
                add_event(job_id, "attempt_started", attempt=attempt, prompt=prompt)

                candidate = await call_llm(
                    client=llm,
                    model=cfg.llm_model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                )
                add_event(job_id, "candidate", attempt=attempt, candidate=candidate)

                verify = await axle.verify_proof(
                    formal_statement=problem.formal_statement,
                    content=candidate,
                    environment=cfg.environment,
                    ignore_imports=True,
                )
                verify_ok = get_okay(verify)
                verify_errors = extract_errors(verify)
                attempt_record: dict[str, Any] = {
                    "attempt": attempt,
                    "prompt": prompt,
                    "candidate": candidate,
                    "verify": to_dict(verify),
                    "verify_ok": verify_ok,
                }
                add_event(
                    job_id,
                    "verify_result",
                    attempt=attempt,
                    ok=verify_ok,
                    errors=verify_errors,
                )

                if verify_ok:
                    result = {
                        "problem_id": problem.problem_id,
                        "status": "solved",
                        "environment": cfg.environment,
                        "attempts_used": attempt,
                        "final_proof": get_content(verify) or candidate,
                        "attempt_records": attempts + [attempt_record],
                    }
                    save_result(job_id, cfg, result)
                    add_event(job_id, "job_finished", status="solved", final_proof=result["final_proof"])
                    set_job_state(job_id, status="solved", result=result)
                    return

                fallback_errors: list[str] = []
                fallback_tactics = parse_csv(cfg.fallback_tactics) if attempt >= cfg.fallback_after_attempt else []
                if fallback_tactics:
                    fallback_records: list[dict[str, Any]] = []
                    for tactic in fallback_tactics:
                        fallback_candidate = problem.formal_statement.replace("sorry", tactic, 1)
                        fallback_verify = await axle.verify_proof(
                            formal_statement=problem.formal_statement,
                            content=fallback_candidate,
                            environment=cfg.environment,
                            ignore_imports=True,
                        )
                        fallback_ok = get_okay(fallback_verify)
                        fallback_verify_errors = extract_errors(fallback_verify)
                        fallback_records.append(
                            {
                                "tactic": tactic,
                                "candidate": fallback_candidate,
                                "verify_ok": fallback_ok,
                                "verify": to_dict(fallback_verify),
                            }
                        )
                        add_event(
                            job_id,
                            "fallback_result",
                            attempt=attempt,
                            tactic=tactic,
                            ok=fallback_ok,
                            errors=fallback_verify_errors,
                        )
                        if fallback_ok:
                            attempt_record["fallback_attempts"] = fallback_records
                            result = {
                                "problem_id": problem.problem_id,
                                "status": "solved",
                                "environment": cfg.environment,
                                "attempts_used": attempt,
                                "solved_via": f"fallback_tactic:{tactic}",
                                "final_proof": get_content(fallback_verify) or fallback_candidate,
                                "attempt_records": attempts + [attempt_record],
                            }
                            save_result(job_id, cfg, result)
                            add_event(job_id, "job_finished", status="solved", final_proof=result["final_proof"])
                            set_job_state(job_id, status="solved", result=result)
                            return
                        fallback_errors.extend(fallback_verify_errors)
                    attempt_record["fallback_attempts"] = fallback_records

                repair_errors: list[str] = []
                repaired_ok = False
                if cfg.use_repair:
                    repair_kwargs: dict[str, Any] = {
                        "content": candidate,
                        "environment": cfg.environment,
                        "ignore_imports": True,
                    }
                    repairs = parse_csv(cfg.repairs)
                    terminal_tactics = parse_csv(cfg.terminal_tactics)
                    if repairs:
                        repair_kwargs["repairs"] = repairs
                    if terminal_tactics:
                        repair_kwargs["terminal_tactics"] = terminal_tactics

                    repair_obj = await axle.repair_proofs(**repair_kwargs)
                    repaired_content = get_content(repair_obj) or candidate
                    repair_verify = await axle.verify_proof(
                        formal_statement=problem.formal_statement,
                        content=repaired_content,
                        environment=cfg.environment,
                        ignore_imports=True,
                    )
                    repaired_ok = get_okay(repair_verify)
                    repair_errors = extract_errors(repair_verify) or extract_errors(repair_obj)
                    changed = repaired_content.strip() != candidate.strip()

                    attempt_record["repair"] = to_dict(repair_obj)
                    attempt_record["repair_verify"] = to_dict(repair_verify)
                    attempt_record["repair_ok"] = repaired_ok

                    add_event(
                        job_id,
                        "repair_result",
                        attempt=attempt,
                        changed=changed,
                        ok=repaired_ok,
                        first_change=summarize_first_change(candidate, repaired_content) if changed else "",
                        repaired_content=repaired_content,
                        errors=repair_errors,
                    )

                    if repaired_ok:
                        result = {
                            "problem_id": problem.problem_id,
                            "status": "solved",
                            "environment": cfg.environment,
                            "attempts_used": attempt,
                            "final_proof": get_content(repair_verify) or repaired_content,
                            "attempt_records": attempts + [attempt_record],
                        }
                        save_result(job_id, cfg, result)
                        add_event(job_id, "job_finished", status="solved", final_proof=result["final_proof"])
                        set_job_state(job_id, status="solved", result=result)
                        return

                attempts.append(attempt_record)
                merged_errors = verify_errors + fallback_errors + repair_errors
                feedback = "\n".join(merged_errors[: cfg.feedback_lines]) if merged_errors else "Proof failed without explicit error details."
                add_event(job_id, "feedback_ready", attempt=attempt, feedback=feedback)

        result = {
            "problem_id": problem.problem_id,
            "status": "failed",
            "environment": cfg.environment,
            "attempts_used": cfg.max_attempts,
            "final_proof": "",
            "attempt_records": attempts,
            "last_feedback": feedback,
        }
        save_result(job_id, cfg, result)
        add_event(job_id, "job_finished", status="failed", final_proof="")
        set_job_state(job_id, status="failed", result=result)
    except Exception as exc:  # pragma: no cover - defensive path
        add_event(job_id, "job_error", message=str(exc))
        set_job_state(job_id, status="error", error=str(exc))


def save_result(job_id: str, cfg: RunRequest, result: dict[str, Any]) -> None:
    payload = {
        "job_id": job_id,
        "saved_at": utc_now(),
        "request": safe_request_view(cfg),
        "result": result,
    }
    out = OUTPUT_DIR / f"run-{job_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _axle_key_check_once(axle_api_key: str, environment: str) -> tuple[bool, list[str]]:
    formal_statement = "theorem axle_key_check : True := by\n  sorry\n"
    content = "theorem axle_key_check : True := by\n  trivial\n"
    async with AxleClient(api_key=axle_api_key) as axle:
        verify = await axle.verify_proof(
            formal_statement=formal_statement,
            content=content,
            environment=environment,
            ignore_imports=True,
        )
    return get_okay(verify), extract_errors(verify)


def check_axle_key(axle_api_key: str, environment: str = "lean-4.28.0") -> tuple[bool, list[str]]:
    key = axle_api_key.strip()
    if not key:
        raise ValueError("AXLE API key를 입력해 주세요.")
    if not is_likely_axle_key(key):
        raise ValueError("AXLE API key 형식이 올바르지 않습니다. (pk_...)")
    env = environment.strip() or "lean-4.28.0"
    try:
        return asyncio.run(_axle_key_check_once(key, env))
    except Exception as exc:
        return False, [str(exc)]


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/axle-check":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
                axle_api_key = str(data.get("axle_api_key", "")).strip()
                environment = str(data.get("environment", "lean-4.28.0")).strip() or "lean-4.28.0"
                ok, errors = check_axle_key(axle_api_key=axle_api_key, environment=environment)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except Exception as exc:
                self._send_json({"ok": False, "error": f"AXLE key 검증 실패: {exc}"}, status=502)
                return
            if ok:
                self._send_json({"ok": True, "message": "AXLE key 인증 완료"})
                return
            self._send_json(
                {
                    "ok": False,
                    "error": "AXLE key 인증 실패",
                    "details": errors[:3],
                },
                status=401,
            )
            return

        if parsed.path == "/api/models":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
                provider = str(data.get("llm_provider", "openrouter")).strip().lower()
                llm_api_key = str(data.get("llm_api_key", "")).strip()
                models = fetch_models(provider, llm_api_key=llm_api_key)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except urlerror.HTTPError as exc:
                self._send_json({"error": f"모델 목록 조회 실패: HTTP {exc.code}"}, status=502)
                return
            except Exception as exc:
                self._send_json({"error": f"모델 목록 조회 실패: {exc}"}, status=502)
                return

            self._send_json({"llm_provider": provider, "models": models, "count": len(models)})
            return

        if parsed.path == "/api/prove":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
                cfg = load_request(data)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            job_id = uuid.uuid4().hex[:10]
            create_job(job_id, cfg)
            asyncio.run(run_job(job_id, cfg))
            with jobs_lock:
                job = jobs.get(job_id)
            self._send_json(job or {"error": "proof run failed"}, status=200 if job else 500)
            return

        if parsed.path == "/api/runs":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
                cfg = load_request(data)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            job_id = uuid.uuid4().hex[:10]
            create_job(job_id, cfg)

            thread = threading.Thread(target=lambda: asyncio.run(run_job(job_id, cfg)), daemon=True)
            thread.start()
            self._send_json({"job_id": job_id, "status": "queued"}, status=202)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"okay": True, "time": utc_now()})
            return
        if parsed.path == "/api/config":
            self._send_json(config_payload())
            return
        if parsed.path.startswith("/api/runs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with jobs_lock:
                job = jobs.get(job_id)
            if not job:
                self._send_json({"error": "Job not found"}, status=404)
                return
            self._send_json(job)
            return
        if parsed.path == "/" or parsed.path == "":
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[web]", fmt % args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local AXLE + LLM web playground")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Serving AXLE playground on http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

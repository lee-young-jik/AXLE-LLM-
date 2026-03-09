from __future__ import annotations

import asyncio
import json
import uuid
from http.server import BaseHTTPRequestHandler
from urllib import error as urlerror
from urllib.parse import urlparse

from web_prover import (
    check_axle_key,
    config_payload,
    create_job,
    fetch_models,
    jobs,
    jobs_lock,
    load_request,
    run_job,
    utc_now,
)


class handler(BaseHTTPRequestHandler):  # noqa: N801
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"okay": True, "time": utc_now()})
            return
        if parsed.path == "/api/config":
            self._send_json(config_payload())
            return
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)

        if parsed.path == "/api/axle-check":
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

        self._send_json({"error": "Not found"}, status=404)

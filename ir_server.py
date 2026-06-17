#!/usr/bin/env python3
"""`ir_send` 를 LAN HTTP API 로 노출한다 — 봇/앱이 에어컨을 제어하도록(의존성 0, stdlib만).

엔드포인트:
  GET  /health  → {"ok": true}
  GET  /list    → {"configs": [{"label","confidence","synthetic"}, ...]}
  POST /send    → 본문 {"mode","temp","power"} 또는 {"label":"냉방_25_on"}
                  → 전송 후 {"ok": true, "label", "source", "segs"}

- 전송은 IR LED(단일 자원)이므로 락으로 직렬화한다.
- 인증: `config.HTTP_TOKEN` 이 설정돼 있으면 `Authorization: Bearer <token>` 헤더 필요(빈값=무인증).
- 바이트 위치·파라미터는 하드코딩하지 않는다 — 라벨은 `model.json`의 파라미터 순서로 조립.

사용:
  python3 ir_server.py                 # config.HTTP_HOST:HTTP_PORT 로 listen
  curl -X POST http://<pi>:8000/send -H 'Content-Type: application/json' \
       -d '{"mode":"냉방","temp":25,"power":"on"}'
"""
import sys
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import ir_send

_send_lock = threading.Lock()   # IR LED 단일 자원 — 전송 직렬화


def _list_configs():
    out = []
    if config.DATASET_DIR.exists():
        for f in sorted(config.DATASET_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({"label": f.stem,
                        "confidence": d.get("confidence"),
                        "synthetic": bool(d.get("synthetic"))})
    return out


def _model_params():
    try:
        return json.loads(config.MODEL_FILE.read_text(encoding="utf-8"))["params"]
    except Exception:
        return None


def resolve_label(body):
    """요청 본문 → (label, parts). {"label"} 우선, 아니면 model.json 파라미터 순서로 조립."""
    if not isinstance(body, dict):
        raise ValueError("JSON 객체 본문이 필요합니다")
    if body.get("label"):
        label = str(body["label"])
        return label, label.split("_")
    params = _model_params()
    if not params:
        raise ValueError('model.json 이 없습니다 — "label" 을 직접 지정하세요')
    missing = [p for p in params if p not in body]
    if missing:
        raise ValueError(f"파라미터 누락: {missing} (필요: {params})")
    parts = [str(body[p]) for p in params]
    return "_".join(parts), parts


class Handler(BaseHTTPRequestHandler):
    server_version = "ir_server/1.0"

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        if not config.HTTP_TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {config.HTTP_TOKEN}"

    def do_GET(self):
        if not self._authed():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        if self.path == "/health":
            return self._json(200, {"ok": True})
        if self.path == "/list":
            return self._json(200, {"configs": _list_configs()})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        if self.path != "/send":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})
        try:
            label, parts = resolve_label(body)
        except ValueError as e:
            return self._json(400, {"ok": False, "error": str(e)})
        try:
            with _send_lock:
                res = ir_send.send(label, parts)
        except FileNotFoundError as e:
            return self._json(404, {"ok": False, "error": str(e)})
        except SystemExit as e:                      # pigpiod 미연결/모델 없음/합성 불가
            return self._json(503, {"ok": False, "error": str(e)})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})
        return self._json(200, {"ok": True, **res})

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main():
    srv = ThreadingHTTPServer((config.HTTP_HOST, config.HTTP_PORT), Handler)
    auth = "토큰 인증 ON" if config.HTTP_TOKEN else "무인증(LAN 전용 권장)"
    print(f"IR HTTP 서버 시작: http://{config.HTTP_HOST}:{config.HTTP_PORT}  [{auth}]")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()

"""ir_server — HTTP API 라우팅·인증·라벨 해석(하드웨어/전송은 모킹)."""
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import pytest

import config
import ir_send
import ir_server


@pytest.fixture
def server(monkeypatch):
    """ir_send.send 를 모킹한 실제 HTTP 서버를 임시 포트로 띄우고 base_url 반환."""
    sent = {}

    def fake_send(label, parts=None, gpio=None):
        sent["label"], sent["parts"] = label, parts
        return {"label": label, "source": "test", "segs": 3}

    monkeypatch.setattr(ir_send, "send", fake_send)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ir_server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address
    yield f"http://{host}:{port}", sent
    srv.shutdown()
    srv.server_close()


def _req(url, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health(server):
    base, _ = server
    assert _req(f"{base}/health") == (200, {"ok": True})


def test_send_by_label(server):
    base, sent = server
    status, obj = _req(f"{base}/send", "POST", {"label": "냉방_25_on"})
    assert status == 200 and obj["ok"] and obj["label"] == "냉방_25_on"
    assert sent["label"] == "냉방_25_on" and sent["parts"] == ["냉방", "25", "on"]


def test_send_by_fields_uses_model_order(server, monkeypatch):
    base, sent = server
    monkeypatch.setattr(ir_server, "_model_params", lambda: ["mode", "temp", "power"])
    status, obj = _req(f"{base}/send", "POST", {"mode": "냉방", "temp": 25, "power": "on"})
    assert status == 200 and sent["label"] == "냉방_25_on"


def test_send_off_without_temp_matches_collected(server, monkeypatch):
    """끌 때 temp 생략 → 같은 off 수집본을 매칭(온도 무관)."""
    base, sent = server
    monkeypatch.setattr(ir_server, "_model_params", lambda: ["mode", "temp", "power"])
    monkeypatch.setattr(ir_server, "_dataset_params", lambda: [
        ("냉방_18_off", {"mode": "냉방", "temp": 18, "power": "off"}),
        ("냉방_25_on", {"mode": "냉방", "temp": 25, "power": "on"}),
    ])
    status, obj = _req(f"{base}/send", "POST", {"mode": "냉방", "power": "off"})
    assert status == 200 and sent["label"] == "냉방_18_off"


def test_partial_no_match_400(server, monkeypatch):
    base, _ = server
    monkeypatch.setattr(ir_server, "_model_params", lambda: ["mode", "temp", "power"])
    monkeypatch.setattr(ir_server, "_dataset_params", lambda: [])
    status, obj = _req(f"{base}/send", "POST", {"mode": "냉방"})
    assert status == 400 and "맞는 수집본이 없습니다" in obj["error"]


def test_unknown_path_404(server):
    base, _ = server
    assert _req(f"{base}/nope")[0] == 404


def test_auth_required(server, monkeypatch):
    base, _ = server
    monkeypatch.setattr(config, "HTTP_TOKEN", "s3cret")
    assert _req(f"{base}/send", "POST", {"label": "냉방_25_on"})[0] == 401
    status, obj = _req(f"{base}/send", "POST", {"label": "냉방_25_on"}, token="s3cret")
    assert status == 200 and obj["ok"]

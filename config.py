"""환경변수 기반 설정 중앙 관리.

배포/하드웨어에 따라 달라지는 값(핀 번호, pigpio 데몬 주소, 데이터 경로 등)을
환경변수로 분리한다. 값이 없으면 아래 기본값을 사용한다.

프로젝트 루트에 `.env` 파일이 있으면 자동으로 읽어들인다(의존성 없이 자체 파싱).
환경변수 목록은 `.env.example` 참고.
"""
import os
from pathlib import Path

# ── .env 자동 로드 (있으면) ──────────────────────────────
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())


def _int(name, default):
    return int(os.getenv(name, default))


# ── pigpio 데몬 ──────────────────────────────────────────
# 원격 라즈베리파이의 pigpiod에 붙으려면 PIGPIO_HOST 지정
PIGPIO_HOST = os.getenv("PIGPIO_HOST", "localhost")
PIGPIO_PORT = _int("PIGPIO_PORT", 8888)

# ── GPIO 핀 (BCM) ────────────────────────────────────────
IR_RX_GPIO = _int("IR_RX_GPIO", 13)   # KY-022 수신기 OUT
IR_TX_GPIO = _int("IR_TX_GPIO", 18)   # IR LED (송신)

# ── IR 캐리어 / 타이밍 ───────────────────────────────────
CARRIER_HZ = _int("IR_CARRIER_HZ", 38000)
GLITCH_US = _int("IR_GLITCH_US", 150)
FRAME_GAP_US = _int("IR_FRAME_GAP_US", 30000)

# ── 데이터 경로 ──────────────────────────────────────────
DATASET_DIR = Path(os.getenv("IR_DATASET_DIR", "dataset"))   # ir_collect.py 출력
MODEL_FILE = Path(os.getenv("IR_MODEL_FILE", "model.json"))  # ir_learn.py 출력


def connect():
    """설정된 호스트/포트로 pigpio 데몬에 연결해 pi 객체 반환."""
    import pigpio
    pi = pigpio.pi(PIGPIO_HOST, PIGPIO_PORT)
    if not pi.connected:
        raise SystemExit(
            f"pigpiod 연결 실패 ({PIGPIO_HOST}:{PIGPIO_PORT}) "
            "— sudo systemctl start pigpiod"
        )
    return pi

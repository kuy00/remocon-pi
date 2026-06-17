# remocon-pi

라즈베리파이 + [`pigpio`](https://abyz.me.uk/rpi/pigpio/)로 **에어컨 IR 리모컨 신호를 녹화 → 분석 → 재생**하여 에어컨을 제어하는 프로젝트.

> 핵심: 프로토콜을 완전히 해독하지 않아도, **녹화한 원시 펄스를 그대로 되쏘면(replay) 에어컨이 동작한다.**

## 빠른 시작

```bash
# 1. 라즈베리파이 환경 준비
sudo apt install pigpio python3-pigpio
sudo systemctl start pigpiod          # 모든 스크립트의 전제

pip install -r requirements.txt       # (시스템 패키지로 설치했다면 생략 가능)

# 2. (선택) 설정 — 기본값과 다르면 .env 로 덮어쓰기
cp .env.example .env                  # 핀/호스트 등 필요한 값만 수정

# 3. 수집 → 학습 (리모컨에 맞게 sweep.json 편집 후)
python3 ir_collect.py                 # 설정별 8회 반복 수집 → dataset/
python3 ir_learn.py                   # dataset/ 자동 분석 → model.json

# 4. 제어
python3 ir_send.py --list
python3 ir_send.py 냉방 21 on            # 수집됐으면 재생(replay)
python3 ir_send.py 냉방 25 on            # 미수집이면 model.json으로 자동 합성 송신
python3 ir_synth.py 냉방 25 on --dry     # (선택) 합성 결과만 확인 — 송신 안 함
python3 ir_synth.py 냉방 25 on --dry --save # (선택) 합성본을 dataset/에 저장

# 5. (선택) LAN에서 봇/앱으로 제어 — HTTP API
python3 ir_server.py                     # 기본 0.0.0.0:8000
curl -X POST http://<pi-ip>:8000/send \
     -H 'Content-Type: application/json' -d '{"mode":"냉방","temp":25,"power":"on"}'
```

> 부팅 시 자동 실행은 `deploy/ir-server.service`(systemd) 사용 — [docs/usage.md](docs/usage.md#부팅-시-자동-실행-systemd) 참고.

## 설정

핀 번호·pigpiod 호스트·데이터 경로 등은 환경변수(또는 `.env`)로 바꿀 수 있다.
기본값으로도 라즈베리파이 단독 실행은 바로 동작한다. 전체 목록은 [docs/hardware.md](docs/hardware.md#설정--환경변수-configpy) 참고.

```bash
# 예: 노트북에서 원격 Pi 제어 / 송신 핀 임시 변경
PIGPIO_HOST=192.168.0.50 python3 ir_send.py 냉방 21 on
IR_TX_GPIO=17 python3 ir_send.py 난방 30 off
```

## 하드웨어

| 기능 | 부품 | GPIO (BCM) |
|------|------|------------|
| 수신 | KY-022 IR 수신기 OUT | 13 |
| 송신 | IR LED (트랜지스터 구동) | 18 |

> IR LED는 GPIO에 직결하지 말고 트랜지스터로 구동할 것. 자세한 배선은 [docs/hardware.md](docs/hardware.md).

## 스크립트

**범용 파이프라인 (수집 → 학습 → 합성·송신)** — 리모컨 무관, 하드코딩 없음:

| 파일 | 역할 |
|------|------|
| `ir_collect.py` | 수집 — `sweep.json` 스윕 × 8회 반복 × 신뢰도 게이트 → `dataset/` 저장 |
| `ir_learn.py` | 학습 — `dataset/` 자동 분석(필드 발견·체크섬 탐색) → `model.json` |
| `ir_synth.py` | 합성 — `model.json` 규칙 + 가장 가까운 수집본 템플릿으로 미수집 조합 신호 생성·저장·송신 (위치 하드코딩 없음) |
| `ir_send.py` | 송신 — 수집본 재생(replay), 미수집이면 `model.json`으로 자동 합성 송신 |
| `ir_server.py` | HTTP API — LAN에서 봇/앱이 `ir_send`를 호출하도록 노출 (stdlib, 의존성 0) |
| `ir_codec.py` | 공통 — raw 펄스 ↔ 비트/바이트, 신뢰도 계산 (하드웨어 무관·순수 로직) |
| `ir_io.py` | 공통 — pigpio 저수준 I/O: 수신 collector + 송신 캐리어/파형 |
| `ir_monitor.py` | 모니터 — 실시간 디코딩 + 대조 검증 |
| `config.py` | 설정 중앙 관리 (환경변수/`.env`) |


## 문서

| 문서 | 내용 |
|------|------|
| [docs/overview.md](docs/overview.md) | 프로젝트 개요, 파이프라인, 동작 원리 |
| [docs/hardware.md](docs/hardware.md) | 핀 배치, 배선, pigpiod 설정, 주요 상수 |
| [docs/usage.md](docs/usage.md) | 스크립트별 실행법, 데이터 형식 |
| [docs/data-format.md](docs/data-format.md) | raw 펄스 형식, 프레임/바이트 구조, 프로토콜 분석 |

## 데이터

- `dataset/` — `ir_collect.py` 출력. 설정별 `params` + 반복 raw 펄스 + 신뢰도 (합성 저장본은 `synthetic: true`, gitignore 권장)
- `model.json` — `ir_learn.py` 출력. 학습된 프로토콜 규칙

## 테스트

순수 로직(코덱·학습·합성)은 하드웨어 없이 검증된다. `pigpio` 불필요.

```bash
pip install -r requirements-dev.txt   # pytest
pytest                                # tests/ 실행
```

## 요구 사항

- 라즈베리파이 (GPIO)
- `pigpiod` 데몬 실행
- Python 3, `pigpio` 패키지
- (개발) `pytest` — `requirements-dev.txt`

# 하드웨어 / 환경 설정

## 핀 배치 (BCM 기준)

| 기능 | 부품 | GPIO |
|------|------|------|
| 수신 | KY-022 IR 수신기 OUT | **13** |
| 송신 | IR LED (트랜지스터 구동) | **18** |

- 수신 핀은 코드에서 `PUD_UP`(내부 풀업) + 글리치 필터 150µs로 설정된다.
- 송신은 `pigpio` wave API로 38kHz 캐리어를 소프트웨어 생성한다(하드웨어 PWM 불필요, 임의 GPIO 사용 가능).

## 배선 주의

- **IR LED를 GPIO에 직결하지 말 것.** LED 구동 전류가 핀 허용치를 넘으므로 트랜지스터(예: 2N2222)와 저항으로 구동한다.
- 송신 시 IR LED를 에어컨 수신부 방향으로 향하게 하고 1m 이내에서 테스트한다.

## 소프트웨어 환경

```bash
sudo apt install pigpio python3-pigpio   # 미설치 시
sudo systemctl start pigpiod             # 데몬 실행 (모든 스크립트의 전제)
sudo systemctl enable pigpiod            # 부팅 시 자동 실행(선택)
```

모든 스크립트는 `pigpiod`가 떠 있어야 동작한다. 미실행/연결 실패 시
`pigpiod 연결 실패 (...)` 오류로 종료된다.

## 설정 — 환경변수 (`config.py`)

배포/하드웨어에 따라 달라지는 값은 [`config.py`](../config.py)에서 환경변수로
읽는다. 값이 없으면 아래 기본값을 쓴다. 프로젝트 루트에 `.env` 파일이 있으면
자동 로드된다(`.env.example` 복사해서 사용).

| 환경변수 | 기본값 | 의미 |
|----------|--------|------|
| `PIGPIO_HOST` | `localhost` | pigpiod 호스트. **원격 라즈베리파이 제어 시 그 IP 지정** |
| `PIGPIO_PORT` | `8888` | pigpiod 포트 |
| `IR_RX_GPIO` | `13` | 수신 핀 (BCM) |
| `IR_TX_GPIO` | `18` | 송신 핀 (BCM, `ir_send.py --gpio`로도 변경 가능) |
| `IR_CARRIER_HZ` | `38000` | IR 캐리어 주파수 |
| `IR_GLITCH_US` | `150` | 수신 글리치 필터 |
| `IR_FRAME_GAP_US` | `30000` | 프레임 종료로 보는 무신호 갭 |
| `IR_DATASET_DIR` | `dataset` | 수집 데이터 경로 (`ir_collect.py` 출력) |
| `IR_MODEL_FILE` | `model.json` | 학습 모델 경로 (`ir_learn.py` 출력) |
| `IR_REPEATS` | `8` | 설정당 반복 수집 횟수 |
| `IR_MIN_AGREE` | `0.75` | 수집 신뢰도 하한(미달 시 재촬영) |

`config.connect()`가 `PIGPIO_HOST:PIGPIO_PORT`로 데몬에 연결한다.

### 예시
```bash
# 노트북에서 원격 Pi(192.168.0.50) 제어
PIGPIO_HOST=192.168.0.50 python3 ir_send.py 냉방 21 on
# 송신 핀만 임시 변경
IR_TX_GPIO=17 python3 ir_send.py 난방 30 off
```

## 프로토콜 상수 (스크립트 내 고정 — 하드웨어 무관)

수신기/리모컨 프로토콜에 종속된 값이라 환경변수로 빼지 않았다.

| 상수 | 값 | 의미 |
|------|-----|------|
| 헤더 mark/space | ~6800 / ~3300µs | 프레임 시작 감지 기준 |
| `BIT_THRESHOLD` | 800 | space 길이로 0/1 판정하는 경계 |
| `MIN_SEGS_TO_SAVE` | 60 | 노이즈로 보지 않을 최소 세그먼트 수 |

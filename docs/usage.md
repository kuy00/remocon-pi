# 사용법

> 전제: 라즈베리파이에서 `sudo systemctl start pigpiod` 실행 상태. 핀 배치는 [hardware.md](hardware.md) 참고.
>
> 핀/데몬 호스트/경로 등 설정은 환경변수 또는 `.env`로 바꾼다(기본값은 단독 실행 기준). 전체 목록은 [hardware.md의 환경변수 표](hardware.md#설정--환경변수-configpy) 참고.

## 1. 수집 — `ir_collect.py`

`sweep.json`에 정의한 파라미터 조합을 돌며 설정당 8회 반복 수집해 `dataset/`에 저장한다.

```bash
# 먼저 sweep.json 을 리모컨에 맞게 편집 (axes: mode/temp/power ...)
python3 ir_collect.py                     # 전체 스윕 수집 (마지막 축 교차)
python3 ir_collect.py --sweep my.json --out dataset
python3 ir_collect.py --no-interleave     # 교차 끄기 — 설정당 연속 8회(구방식)
```

- 헤더 타이밍 하드코딩 없음 — 긴 무신호 갭으로만 전송 1회를 구분
- 설정당 8회 반복 후 **반복 일치율(신뢰도)** 계산, 기준(기본 **90%**) 미달이면 **통과할 때까지 재촬영**
- **교차 수집(기본)** — `sweep.json` `order`의 **마지막 축**(기본 `[mode, temp, power]`의 `power`)을
  같은 그룹(예: `냉방_21_on` / `냉방_21_off`) 안에서 **라운드마다 번갈아** 캡처한다
  (`on→off→on→off…×8`). 전원이 토글식인 리모컨은 on을 다시 찍으려면 어차피 off를 거쳐야 하므로
  이 순서가 자연스럽다. 8라운드 후 멤버별 신뢰도 검사, **미달 멤버만** 교차로 재촬영
  (미달 화면에서 `Enter`=재촬영, `s`=현재본 저장하고 진행). `--no-interleave`면 설정당 연속 8회(구방식).
- 저장: `dataset/{라벨}.json` — `params` + `repeats`(raw 펄스 N회) + `confidence` (교차 여부와 무관하게 라벨당 1파일)
- 반복 횟수/기준은 `config.REPEATS`/`config.MIN_AGREE`(환경변수 `IR_REPEATS`, `IR_MIN_AGREE`)로 조정

## 2. 학습 — `ir_learn.py`

`dataset/`를 자동 분석해 프로토콜 규칙 모델(`model.json`)을 만든다(하드웨어 불필요).

```bash
python3 ir_learn.py
```

- 반복본 다수결로 노이즈 제거, 신뢰도 낮은 설정 경고
- 바이트별 자동 분류: 상수 / 필드(파라미터·선형/룩업) / 체크섬 / 미해독(complex)
- 미해독 바이트가 0이면 완전 합성 가능, 남으면 그 프레임은 replay 필요

## 3. 모니터 — `ir_monitor.py`

버튼을 누르면 실시간으로 디코딩해 바이트로 표시한다(하드코딩 없는 QC 도구).

```bash
python3 ir_monitor.py
```

출력 예: `segs` 수 + 추정 임계값 + `F1`/`F2` 바이트 헥스. 신호가 제대로 들어오는지, 바이트가 안정적인지 빠르게 확인할 때 사용.

## 4. 송신(제어) — `ir_send.py`

설정을 송신해 에어컨을 제어한다. **수집본이 있으면 재생(replay), 없으면 `model.json`으로
자동 합성**해 보낸다(아래 `ir_synth` 로직 재사용).

```bash
python3 ir_send.py --list              # 수집된 설정 목록(+신뢰도)
python3 ir_send.py 냉방 21 on          # 수집됐으면 dataset/냉방_21_on.json 재생
python3 ir_send.py 냉방 25 on          # 미수집이면 자동 합성 송신 (콘솔에 "합성" 표시)
python3 ir_send.py --label 냉방_21_on  # 라벨 직접 지정
python3 ir_send.py 냉방 21 on --gpio 17 # 송신 핀 변경
```

- 위치인자를 `_`로 결합해 `dataset/{라벨}.json`을 찾는다(수집 시 `sweep.json` order 순서와 맞춰 입력)
- 수집본 있음 → 저장된 `level 0 → 38kHz 캐리어 ON(mark)`, `level 1 → OFF(space)`로 변환해 송신
- 수집본 없음 → `model.json` + 가장 가까운 수집본으로 합성(`ir_synth.synthesize`). 콘솔에 `replay`/`합성` 명시
- 모델이 없거나 그 그룹이 합성 불가면 → "수집/학습 필요" 안내 후 종료

## 5. 합성 송신 — `ir_synth.py`

수집하지 **않은** 조합을 **`model.json`(ir_learn 산출) 규칙만으로** 합성해 송신한다.
바이트 위치·온도식·체크섬을 코드에 박지 않고 모델에서 읽으므로 **어떤 리모컨이든** 동작한다.
같은 범주형 그룹의 **가장 가까운 수집본**을 타이밍 템플릿으로 삼아, 헤더/타이밍은 실측
그대로 두고 값이 바뀌는 비트의 space 길이만 교체한다(서지컬 합성).

> 선행: `python3 ir_learn.py [--dataset ...]` 로 `model.json` 을 먼저 만든다.

```bash
python3 ir_synth.py 냉방 25 on                       # model.json 규칙으로 합성 후 송신
python3 ir_synth.py 냉방 25 on --dry                 # 송신 없이 합성 바이트 + 자가검증
python3 ir_synth.py 냉방 25 on --template 냉방_24_on  # 템플릿 라벨 지정
python3 ir_synth.py 냉방 25 on --dataset dataset_cool --model model.json
```

- 파라미터는 모델의 `params` 순서대로 입력(예: `mode temp power`). 숫자는 자동 인식.
- 모델 규칙별 처리: `const`→고정, `field linear`→계산(외삽), `field lookup`→표/없으면 템플릿,
  `checksum frame_sum_pair`→그룹 합 상수를 만족하도록 멤버 보정, `complex`→템플릿값(그 바이트는 replay)
- `--dry`는 합성 결과를 다시 디코딩해 목표 바이트와 일치하는지 자가검증(송신 없음, 하드웨어 불필요)
- **주의**: 체크섬 분할이 실측과 다를 수 있다(전체 합은 보존). 에어컨이 전체합만 검증하면 그대로
  동작하고, 멤버를 개별 검증하면 인접 템플릿일수록 안전하다. 처음엔 `--dry`로 확인 후 송신 권장.

## 데이터 형식 한눈에

```jsonc
// dataset/냉방_21_on.json  (ir_collect.py 출력)
{
  "params": { "mode": "냉방", "temp": 21, "power": "on" },
  "frame_gap_us": 30000, "glitch_us": 150,
  "n_repeats": 8, "confidence": 1.0,
  "repeats": [ [[0,6800],[1,3330], ...], ... ]   // 8회분 raw 펄스
}
```

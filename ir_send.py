#!/usr/bin/env python3
"""수집된 IR 데이터로 에어컨을 제어한다 — 수집본이 있으면 재생, 없으면 자동 합성.

- 수집본 존재: 녹화된 raw 펄스를 38kHz 캐리어로 그대로 재생(replay).
- 수집본 없음: `model.json` 규칙 + 가장 가까운 수집본으로 합성해 송신(ir_synth 활용).
  모델이 없거나 그 그룹이 합성 불가면 안내 후 종료.
- 저장 데이터의 level 0 = mark(캐리어 ON), level 1 = space(캐리어 OFF)

사용 예:
  python3 ir_send.py 냉방 21 on        # 수집됐으면 재생
  python3 ir_send.py 냉방 25 on        # 미수집이면 자동 합성 송신
  python3 ir_send.py --label 냉방_21_on # 라벨 직접 지정
  python3 ir_send.py --list             # 수집된 설정 목록
"""
import sys
import json
import argparse

import pigpio

import config
from ir_io import transmit_segs, CARRIER_HZ

TX_GPIO = config.IR_TX_GPIO      # IR LED (BCM)
DATASET_DIR = config.DATASET_DIR


def load_segs(label):
    """dataset/{label}.json 의 첫 반복본 raw segs 반환 (replay용)."""
    fpath = DATASET_DIR / f"{label}.json"
    if not fpath.exists():
        raise FileNotFoundError(f"{fpath} 없음 — '--list'로 수집된 설정 확인")
    data = json.loads(fpath.read_text(encoding="utf-8"))
    reps = data.get("repeats")
    if not reps:
        raise ValueError(f"{fpath} 에 repeats 데이터 없음")
    return reps[0], data.get("confidence")


def synth_fallback(label, values):
    """수집본이 없을 때 model.json 으로 합성. (segs, 설명문) 반환 or SystemExit."""
    model_file = config.MODEL_FILE
    if not model_file.exists():
        raise SystemExit(
            f"'{label}' 수집본 없음 + 모델({model_file}) 없음 — "
            f"먼저 'ir_collect.py'로 수집하거나 'ir_learn.py'로 학습, '--list'로 확인")
    import ir_synth
    model = json.loads(model_file.read_text(encoding="utf-8"))
    if not values:
        values = label.split("_")
    tparams = ir_synth.parse_params(model["params"], values)
    res = ir_synth.synthesize(model, str(DATASET_DIR), tparams)
    info = f"합성 (템플릿 {res['template'].name}, 신뢰도 {res['conf']:.0%})"
    for n in res["notes"]:
        info += f"\n  · {n}"
    return res["segs"], info


def list_available():
    print(f"=== {DATASET_DIR}/ 수집된 설정 ===")
    if not DATASET_DIR.exists():
        print("  (없음) — 먼저 ir_collect.py 로 수집")
        return
    for f in sorted(DATASET_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            conf = d.get("confidence")
            tag = f" (신뢰도 {conf:.0%})" if isinstance(conf, (int, float)) else ""
        except Exception:
            tag = ""
        print(f"  {f.stem}{tag}")


def main():
    ap = argparse.ArgumentParser(description="수집된 IR 데이터로 에어컨 제어(replay)")
    ap.add_argument("parts", nargs="*", help="설정 값 (예: 냉방 21 on) → 라벨 냉방_21_on")
    ap.add_argument("--label", help="dataset 라벨 직접 지정 (예: 냉방_21_on)")
    ap.add_argument("--list", action="store_true", help="수집된 설정 목록")
    ap.add_argument("--gpio", type=int, default=TX_GPIO, help=f"송신 GPIO (기본 {TX_GPIO})")
    args = ap.parse_args()

    if args.list:
        list_available()
        return

    gpio = args.gpio

    # 라벨 결정: --label 우선, 아니면 위치인자를 '_'로 결합
    if args.label:
        label = args.label
    elif args.parts:
        label = "_".join(args.parts)
    else:
        ap.print_help()
        sys.exit(1)

    # 수집본 있으면 재생, 없으면 model.json 으로 합성
    try:
        segs, conf = load_segs(label)
        source = f"replay, 신뢰도 {conf:.0%}" if isinstance(conf, (int, float)) else "replay"
    except FileNotFoundError:
        segs, source = synth_fallback(label, args.parts)

    pi = config.connect()
    try:
        pi.set_mode(gpio, pigpio.OUTPUT)
        pi.write(gpio, 0)
        pi.wave_clear()
        print(f"송신: {label}  (GPIO {gpio}, {CARRIER_HZ}Hz, segs={len(segs)}) — {source}")
        transmit_segs(pi, gpio, segs)
        print("완료.")
    finally:
        pi.write(gpio, 0)
        pi.stop()


if __name__ == "__main__":
    main()

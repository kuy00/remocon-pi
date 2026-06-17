#!/usr/bin/env python3
"""범용 IR 수집기 — 설정 파일(sweep.json) 스윕 × 고반복 × 신뢰도 게이트.

- 헤더 타이밍 하드코딩 없음: 긴 무신호 갭으로만 프레임(전송 1회)을 구분한다.
- 설정 하나당 N회(기본 8) 반복 캡처 후, 반복 일치율(신뢰도)을 계산한다.
- 신뢰도가 기준 미만이면 그 설정을 자동으로 다시 촬영한다.
- 모든 반복본의 raw segs를 저장 → 학습기가 다수결/비트신뢰도 계산.
- 교차 수집(기본): `order`의 마지막 축(예: power on/off)을 한 라운드에 한 번씩
  번갈아 캡처(on→off→on→off…×N). 전원 토글식 리모컨은 어차피 on/off를 오가야
  하므로 이 순서가 자연스럽다. `--no-interleave`로 설정당 연속 N회 방식으로 끌 수 있다.
- 재촬영도 그룹 전체를 교차로 누른다 — 통과한 멤버는 토글 유지용으로 누르되 캡처는
  버리고(저장 안 함), 미달 멤버만 새로 채운다. on/off 교차 순서를 재촬영에서도 유지.

사용: python3 ir_collect.py            # sweep.json 전체 수집 (마지막 축 교차)
      python3 ir_collect.py --sweep my.json --out dataset
      python3 ir_collect.py --no-interleave   # 설정당 연속 N회 (구방식)
"""
import sys
import json
import time
import argparse
from pathlib import Path
from itertools import product

import config
import ir_codec
from ir_io import GapFramedCollector

sys.stdout.reconfigure(line_buffering=True)

N_REPEATS = config.REPEATS
MIN_AGREE = config.MIN_AGREE     # 합의 신뢰도 하한 — 미달 시 계속 재촬영


def load_sweep(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    axes = cfg["axes"]
    order = cfg.get("order", list(axes.keys()))
    combos = [dict(zip(order, vals))
              for vals in product(*[axes[k] for k in order])]
    return order, combos


def label(params, order):
    return "_".join(str(params[k]) for k in order)


def group_combos(combos, order, interleave=True):
    """교차 수집 단위로 combos를 묶는다.

    interleave=True: `order`의 마지막 축(예: power)만 교차 그룹으로 묶는다.
      마지막 축을 제외한 값이 같은 combo끼리 한 그룹(예: 냉방_21_on / 냉방_21_off).
    interleave=False 또는 축이 1개뿐이면: 설정마다 1개짜리 그룹(구방식).
    product() 순서상 같은 그룹은 연속하므로 입력 순서가 보존된다.
    """
    if not interleave or len(order) < 2:
        return [[c] for c in combos]
    key_axes = order[:-1]
    groups, index = [], {}
    for c in combos:
        key = tuple(c[k] for k in key_axes)
        if key not in index:
            index[key] = len(groups)
            groups.append([])
        groups[index[key]].append(c)
    return groups


def capture_group(rx, members, order):
    """한 교차 그룹(예: on/off)을 라운드 단위로 번갈아 N회씩 캡처.

    각 라운드마다 멤버를 한 번씩(on→off→…) 캡처한다. N라운드 후 멤버별로
    신뢰도를 계산하고, 기준 미달 멤버만 다시 채운다.

    재촬영도 **그룹 전체를 교차로** 누른다 — 전원 토글식 리모컨은 off를 다시 찍으려면
    on을 거쳐야 하므로, 통과한 멤버도 토글 유지용으로 누르되 그 캡처는 버리고(저장 안 함)
    미달 멤버의 캡처만 새로 저장한다. 화면에서 Enter=재촬영, 's'=현재본 저장하고 진행.
    반환: [(params, repeats, conf), ...] (members 순서 유지).
    """
    names = [label(m, order) for m in members]
    collected = {n: [] for n in names}
    title = " / ".join(names)
    all_targets = list(zip(names, members))   # 라운드마다 누를 전체 순서(on→off→…)

    def run_rounds(record, note=""):
        # record: 이번에 '저장'할 멤버 이름 집합. 그 외 멤버는 토글 유지용으로
        # 눌러서 받되 저장하지 않는다(전원 토글 리모컨의 on/off 교차 유지).
        input(f"\n>>> [{title}] 교차 수집{note} — 안내대로 버튼을 누르세요. 준비되면 Enter: ")
        for r in range(1, N_REPEATS + 1):
            for name, _ in all_targets:
                keep = name in record
                tag = f" ({len(collected[name])+1}/{N_REPEATS})" if keep else " (토글용·저장 안 함)"
                print(f"    라운드 {r}/{N_REPEATS} — [{name}] 버튼을 누르세요{tag}...")
                rx.clear()
                segs = rx.wait_one()
                if keep:
                    collected[name].append(segs)
                print(f"      수집 (segs={len(segs)})")
                time.sleep(0.3)

    run_rounds(set(names))
    while True:
        failing = []
        for m, name in zip(members, names):
            _, conf = ir_codec.consensus(collected[name])
            mark = "✅" if conf >= MIN_AGREE else "⚠️"
            print(f"    {name}: 신뢰도 {conf:.0%} {mark}")
            if conf < MIN_AGREE:
                failing.append(name)
        if not failing:
            break
        ans = input(f"\n>>> [{', '.join(failing)}] 기준({MIN_AGREE:.0%}) 미달 — "
                    f"Enter=재촬영(그룹 전체 교차), s=현재본 저장하고 진행: ").strip().lower()
        if ans == "s":
            print("    수동 진행 — 현재본 저장")
            break
        for name in failing:
            collected[name] = []
        run_rounds(set(failing), note=" 재촬영")

    return [(m, collected[n], ir_codec.consensus(collected[n])[1])
            for m, n in zip(members, names)]


def save(out_dir, params, order, repeats, conf):
    out_dir.mkdir(parents=True, exist_ok=True)
    fpath = out_dir / f"{label(params, order)}.json"
    fpath.write_text(json.dumps({
        "params": params,
        "frame_gap_us": config.FRAME_GAP_US,
        "glitch_us": config.GLITCH_US,
        "n_repeats": len(repeats),
        "confidence": round(conf, 4),
        "repeats": repeats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return fpath


def main():
    ap = argparse.ArgumentParser(description="범용 IR 수집기 (스윕×고반복×신뢰도)")
    ap.add_argument("--sweep", default="sweep.json")
    ap.add_argument("--out", default="dataset")
    ap.add_argument("--gpio", type=int, default=config.IR_RX_GPIO)
    ap.add_argument("--no-interleave", action="store_true",
                    help="교차 수집 끄기 — 설정당 연속 N회 (구방식)")
    args = ap.parse_args()

    order, combos = load_sweep(args.sweep)
    groups = group_combos(combos, order, interleave=not args.no_interleave)
    out_dir = Path(args.out)
    cross = order[-1] if len(order) >= 2 and not args.no_interleave else None
    print("=" * 60)
    print("  범용 IR 수집기")
    print(f"  축: {order}")
    print(f"  총 {len(combos)}개 설정 × {N_REPEATS}회 반복")
    if cross:
        print(f"  교차 축: '{cross}' (그룹당 멤버 번갈아 수집) — 그룹 {len(groups)}개")
    print(f"  신뢰도 기준 {MIN_AGREE:.0%} (미달 시 자동 재촬영)")
    print("=" * 60)

    pi = config.connect()
    rx = GapFramedCollector(pi, args.gpio)
    rx.start()
    done = 0
    try:
        for gi, members in enumerate(groups, 1):
            gtitle = " / ".join(label(m, order) for m in members)
            print(f"\n[그룹 {gi}/{len(groups)}] {gtitle}")
            for params, repeats, conf in capture_group(rx, members, order):
                fpath = save(out_dir, params, order, repeats, conf)
                done += 1
                print(f"    저장: {fpath} (신뢰도 {conf:.0%})")
        print(f"\n완료 — {out_dir}/ 에 {done}개 설정 저장")
    except KeyboardInterrupt:
        print("\n중단됨.")
    finally:
        rx.stop()
        pi.stop()


if __name__ == "__main__":
    main()

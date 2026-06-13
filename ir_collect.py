#!/usr/bin/env python3
"""범용 IR 수집기 — 설정 파일(sweep.json) 스윕 × 고반복 × 신뢰도 게이트.

- 헤더 타이밍 하드코딩 없음: 긴 무신호 갭으로만 프레임(전송 1회)을 구분한다.
- 설정 하나당 N회(기본 8) 반복 캡처 후, 반복 일치율(신뢰도)을 계산한다.
- 신뢰도가 기준 미만이면 그 설정을 자동으로 다시 촬영한다.
- 모든 반복본의 raw segs를 저장 → 학습기가 다수결/비트신뢰도 계산.

사용: python3 ir_collect.py            # sweep.json 전체 수집
      python3 ir_collect.py --sweep my.json --out dataset
"""
import os
import sys
import json
import time
import argparse
from pathlib import Path
from collections import deque
from itertools import product

import pigpio

import config
import ir_codec

sys.stdout.reconfigure(line_buffering=True)

N_REPEATS = int(os.getenv("IR_REPEATS", "8"))
MIN_AGREE = float(os.getenv("IR_MIN_AGREE", "0.75"))  # 합의 신뢰도 하한
MAX_ATTEMPTS = 3        # 설정당 재촬영 최대 횟수
MIN_EDGES = 40          # 노이즈로 보지 않을 최소 엣지 수
FRAME_GAP_US = config.FRAME_GAP_US


class GapFramedCollector:
    """헤더 무관 — 조용한 갭으로 전송 1회(=프레임 묶음)를 구분해 수집."""

    def __init__(self, pi, gpio):
        self.pi = pi
        self.gpio = gpio
        self.edges = deque()
        self.last_edge_mono = time.time()
        self.frames = deque(maxlen=50)
        self.cb = None

    def start(self):
        self.pi.set_mode(self.gpio, pigpio.INPUT)
        self.pi.set_pull_up_down(self.gpio, pigpio.PUD_UP)
        self.pi.set_glitch_filter(self.gpio, config.GLITCH_US)
        self.cb = self.pi.callback(self.gpio, pigpio.EITHER_EDGE, self._cb)

    def stop(self):
        if self.cb:
            self.cb.cancel()
            self.cb = None

    def clear(self):
        self.edges.clear()
        self.frames.clear()

    def _cb(self, gpio, level, tick):
        self.edges.append((level, tick))
        self.last_edge_mono = time.time()

    def poll(self):
        """주기적으로 호출 — 조용한 갭이 지나면 누적 엣지를 프레임으로 확정."""
        if len(self.edges) < 2:
            return
        quiet_us = (time.time() - self.last_edge_mono) * 1_000_000
        if quiet_us < FRAME_GAP_US:
            return
        local = list(self.edges)
        self.edges.clear()
        if len(local) < MIN_EDGES:
            return
        segs = []
        for i in range(1, len(local)):
            lvl, t0 = local[i - 1]
            _, t1 = local[i]
            segs.append((lvl, pigpio.tickDiff(t0, t1)))
        self.frames.append(segs)

    def wait_one(self, timeout=None):
        t_end = time.time() + timeout if timeout else None
        while not self.frames:
            self.poll()
            if t_end and time.time() > t_end:
                return None
            time.sleep(0.005)
        return self.frames.popleft()


def load_sweep(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    axes = cfg["axes"]
    order = cfg.get("order", list(axes.keys()))
    combos = [dict(zip(order, vals))
              for vals in product(*[axes[k] for k in order])]
    return order, combos


def label(params, order):
    return "_".join(str(params[k]) for k in order)


def capture_setting(rx, params, order):
    """한 설정을 N회 반복 캡처. 신뢰도 미달 시 재촬영."""
    name = label(params, order)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n>>> [{name}] 설정으로 리모컨을 맞춘 뒤 Enter "
              f"(시도 {attempt}/{MAX_ATTEMPTS})")
        input()
        rx.clear()
        repeats = []
        while len(repeats) < N_REPEATS:
            print(f"    [{len(repeats)+1}/{N_REPEATS}] 버튼을 누르세요...")
            segs = rx.wait_one()
            repeats.append(segs)
            print(f"      수집 (segs={len(segs)})")
            time.sleep(0.3)
            rx.clear()
        _, conf = ir_codec.consensus(repeats)
        print(f"    신뢰도 {conf:.0%}", end=" ")
        if conf >= MIN_AGREE:
            print("→ 통과 ✅")
            return repeats, conf
        print(f"→ 기준({MIN_AGREE:.0%}) 미달, 재촬영 ⚠️")
    print(f"    경고: {MAX_ATTEMPTS}회 시도 후에도 신뢰도 낮음 — 마지막 결과 저장")
    return repeats, conf


def save(out_dir, params, order, repeats, conf):
    out_dir.mkdir(parents=True, exist_ok=True)
    fpath = out_dir / f"{label(params, order)}.json"
    fpath.write_text(json.dumps({
        "params": params,
        "frame_gap_us": FRAME_GAP_US,
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
    args = ap.parse_args()

    order, combos = load_sweep(args.sweep)
    out_dir = Path(args.out)
    print("=" * 60)
    print("  범용 IR 수집기")
    print(f"  축: {order}")
    print(f"  총 {len(combos)}개 설정 × {N_REPEATS}회 반복")
    print(f"  신뢰도 기준 {MIN_AGREE:.0%} (미달 시 자동 재촬영)")
    print("=" * 60)

    pi = config.connect()
    rx = GapFramedCollector(pi, args.gpio)
    rx.start()
    try:
        for idx, params in enumerate(combos, 1):
            print(f"\n[{idx}/{len(combos)}] {label(params, order)}")
            repeats, conf = capture_setting(rx, params, order)
            fpath = save(out_dir, params, order, repeats, conf)
            print(f"    저장: {fpath} (신뢰도 {conf:.0%})")
        print(f"\n완료 — {out_dir}/ 에 {len(combos)}개 설정 저장")
    except KeyboardInterrupt:
        print("\n중단됨.")
    finally:
        rx.stop()
        pi.stop()


if __name__ == "__main__":
    main()

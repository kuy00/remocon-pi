#!/usr/bin/env python3
"""[일회성 진단] 미해독(complex) 바이트의 체크섬 공식을 실측 데이터로 추적.

model.json에서 kind=='complex'인 바이트 위치를 자동으로 읽어 대상으로 삼고,
dataset/의 고신뢰 샘플로 다음 가설들을 전수 검증한다(하드코딩 없음):

  1) 단일바이트: sum/xor/2의보수 × {전체 payload, 프레임별, 연속구간} + 자동 오프셋
     - 핵심: '두 대상 바이트를 모두 제외한 전체'(비연속) payload — 학습기가 못 보던 케이스
  2) 16비트 합 분할: 두 대상이 (하위, 상위) 바이트쌍인지 (정/역순, 2의보수 포함)

찾으면 정확한 공식을 출력 → 이후 ir_learn.find_checksum 확장의 근거로 사용.

사용: python3 diag_checksum.py            # dataset/ + model.json
      python3 diag_checksum.py --dataset dataset_cool --model model.json
"""
import sys
import json
import glob
import argparse
from pathlib import Path
from itertools import combinations

import ir_codec

sys.stdout.reconfigure(line_buffering=True)


def load_samples(data_dir):
    samples = []
    for f in sorted(glob.glob(str(Path(data_dir) / "*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        reps = d.get("repeats", [])
        if not reps:
            continue
        frames, conf = ir_codec.consensus(reps)
        if frames:
            samples.append({"params": d["params"], "frames": frames, "conf": conf})
    return samples


def complex_targets(model):
    """model.json에서 kind=='complex' 바이트 위치 [(fi,bi),...] 반환."""
    out = []
    for fi, fr in enumerate(model["frames"]):
        for b in fr["bytes"]:
            if b["kind"] == "complex":
                out.append((fi, b["index"]))
    return out


def _xor(xs):
    r = 0
    for x in xs:
        r ^= x
    return r


SUM_SCHEMES = {
    "sum":     lambda xs: sum(xs) & 0xFF,
    "sum_neg": lambda xs: (-sum(xs)) & 0xFF,
    "sum_inv": lambda xs: (~sum(xs)) & 0xFF,
}


def byte(s, pos):
    return s["frames"][pos[0]][pos[1]]


def fmt(positions):
    return " ".join(f"F{f+1}B{b}" for f, b in positions)


def test_single(samples, target, payload_sets):
    """target(fi,bi)가 어떤 payload+스킴+오프셋으로 설명되는지 전수 검증."""
    hits = []
    t0 = byte(samples[0], target)
    for label, rng in payload_sets:
        if not rng:
            continue
        # sum 계열
        for name, fn in SUM_SCHEMES.items():
            off = (t0 - fn([byte(samples[0], p) for p in rng])) & 0xFF
            if all((fn([byte(s, p) for p in rng]) + off) & 0xFF == byte(s, target)
                   for s in samples):
                hits.append(f"{name}(+0x{off:02X}) of [{label}]")
        # xor 계열
        off = t0 ^ _xor([byte(samples[0], p) for p in rng])
        if all(_xor([byte(s, p) for p in rng]) ^ off == byte(s, target) for s in samples):
            hits.append(f"xor(^0x{off:02X}) of [{label}]")
    return hits


def test_pair16(samples, t_lo, t_hi, payload_sets):
    """두 대상이 16비트 합의 (하위,상위) 바이트쌍인지 검증 (정/역순·2의보수)."""
    hits = []
    variants = {
        "sum16":     lambda xs: sum(xs) & 0xFFFF,
        "sum16_neg": lambda xs: (-sum(xs)) & 0xFFFF,
        "sum16_inv": lambda xs: (~sum(xs)) & 0xFFFF,
    }
    for label, rng in payload_sets:
        if not rng:
            continue
        for name, fn in variants.items():
            s16_0 = fn([byte(samples[0], p) for p in rng])
            for lo_t, hi_t, order in ((t_lo, t_hi, "정순"), (t_hi, t_lo, "역순")):
                off_lo = (byte(samples[0], lo_t) - (s16_0 & 0xFF)) & 0xFF
                off_hi = (byte(samples[0], hi_t) - (s16_0 >> 8)) & 0xFF
                ok = True
                for s in samples:
                    v = fn([byte(s, p) for p in rng])
                    if (byte(s, lo_t) != ((v & 0xFF) + off_lo) & 0xFF or
                            byte(s, hi_t) != ((v >> 8) + off_hi) & 0xFF):
                        ok = False
                        break
                if ok:
                    hits.append(f"{name} of [{label}] → 하위+0x{off_lo:02X}/상위+0x{off_hi:02X} ({order})")
    return hits


def main():
    ap = argparse.ArgumentParser(description="[진단] complex 바이트 체크섬 공식 추적")
    ap.add_argument("--dataset", default="dataset")
    ap.add_argument("--model", default="model.json")
    ap.add_argument("--rows", type=int, default=12, help="원시 바이트 표 출력 행 수")
    args = ap.parse_args()

    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    shape = tuple(model["shape"])
    targets = complex_targets(model)
    if not targets:
        print("model.json에 complex 바이트 없음 — 추적할 대상이 없습니다.")
        return

    samples = load_samples(args.dataset)
    samples = [s for s in samples if tuple(len(f) for f in s["frames"]) == shape]
    hi = [s for s in samples if s["conf"] >= 0.9]
    use = hi if len(hi) >= 3 else samples
    print(f"샘플 {len(samples)}개 (고신뢰 {len(hi)}개 사용), 프레임 {shape}")
    print(f"대상(complex) 바이트: {fmt(targets)}\n")

    all_pos = [(fi, bi) for fi, L in enumerate(shape) for bi in range(L)]
    target_set = set(targets)

    # payload 후보 집합 정의
    full = [p for p in all_pos if p not in target_set]                 # 둘 다 제외(비연속)
    frame_sets = []
    for fi, L in enumerate(shape):
        fr = [(fi, bi) for bi in range(L) if (fi, bi) not in target_set]
        if fr:
            frame_sets.append((f"F{fi+1}전체", fr))
    payload_sets = [("전체(대상제외)", full)] + frame_sets

    # ── 원시 바이트 표 (수동 확인용) ──
    print("[원시 바이트] params | " + fmt(all_pos))
    tmark = " ".join(("vv" if p in target_set else "  ") for p in all_pos)
    print(f"  {'(대상표시)':16s} {tmark}")
    for s in use[:args.rows]:
        lab = "/".join(str(v) for v in s["params"].values())
        row = " ".join(f"{byte(s, p):02X}" for p in all_pos)
        print(f"  {lab:16s} {row}")
    print()

    # ── 1) 단일바이트 가설 ──
    print("[1] 단일바이트 체크섬 가설")
    for t in targets:
        hits = test_single(use, t, payload_sets)
        print(f"  {fmt([t])}: " + (" | ".join(hits) if hits else "매칭 없음"))
    print()

    # ── 2) 16비트 합 분할 가설 (대상이 정확히 2개일 때) ──
    if len(targets) == 2:
        print("[2] 16비트 합 분할 가설 (두 대상 = 하위/상위 바이트쌍)")
        hits = test_pair16(use, targets[0], targets[1], payload_sets)
        print("  " + (" | ".join(hits) if hits else "매칭 없음"))
    else:
        print(f"[2] 16비트 쌍 가설 생략 — 대상이 {len(targets)}개 (2개일 때만)")

    print("\n끝. 매칭이 나오면 그 공식을 ir_learn.find_checksum 확장 근거로 알려주세요.")


if __name__ == "__main__":
    main()

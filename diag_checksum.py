#!/usr/bin/env python3
"""[일회성 진단 · 분석 끝나면 삭제할 것] 미해독(complex) 바이트의 체크섬 공식 추적.

model.json에서 kind=='complex'인 바이트 위치를 자동으로 읽어 대상으로 삼고,
dataset/의 고신뢰 샘플로 다음 가설들을 전수 검증한다(하드코딩 없음):

  0) [선형성 게이트] 범주형 축(power/mode 등)을 토글했을 때 대상 바이트의
     변화량(Δ)이 일정한지 검사. Δ가 일정하지 않으면 — 그 축에서 입력 변화가
     동일한데 출력 변화가 다르면 — 어떤 sum/xor/CRC(선형·가산) 체크섬으로도
     설명 불가임이 '증명'된다. 공식 탐색 이전에 가능 여부부터 가른다.
  1) 단일바이트: sum/xor/2의보수 × {전체, 프레임별, 연속구간, 자기만-제외} + 자동 오프셋
     - 자기만-제외: 다른 complex 바이트를 입력으로 허용 (학습기·구버전 diag가 못 보던 케이스)
  2) CRC-8 전수 스윕: poly·init·refin·refout·xorout 전 조합 × payload 구간
  3) 16비트 합 분할: 두 대상이 (하위, 상위) 바이트쌍인지 (정/역순, 2의보수 포함)

찾으면 정확한 공식을 출력 → ir_learn.find_checksum 확장 근거. 못 찾고 게이트가
'Δ 불일치'를 보고하면 → 해당 바이트는 합성 불가(replay 필요)가 확정된다.

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


# ── [0] 의존 파라미터 분석: 이 바이트를 결정하는 '최소 파라미터 집합' ──
def min_determining_subset(samples, target, params):
    """target 바이트를 일관되게 결정하는 가장 작은 파라미터 부분집합을 찾는다.

    크기 1 → N 순으로, 그 부분집합 값이 같으면 바이트도 항상 같은지(결정성) 검사.
    찾으면 (subset, 그 표) 반환. 전체 파라미터로도 결정 안 되면 (None, 충돌예시).
    → 합성에 필요한 '최소 수집 차원'을 데이터로 도출(하드코딩 없음).
    """
    for size in range(1, len(params) + 1):
        for combo in combinations(params, size):
            table, ok, clash = {}, True, None
            for s in samples:
                k = tuple(s["params"][p] for p in combo)
                v = byte(s, target)
                if k in table and table[k] != v:
                    ok, clash = False, (combo, k, table[k], v)
                    break
                table[k] = v
            if ok and len(set(table.values())) > 1:  # 실제로 변해야 의미
                return combo, table, None
    # 전체로도 결정 안 됨 → 마지막 충돌 사례 보고
    table, clash = {}, None
    for s in samples:
        k = tuple(s["params"][p] for p in params)
        v = byte(s, target)
        if k in table and table[k] != v:
            clash = (tuple(params), k, table[k], v)
            break
        table[k] = v
    return None, table, clash


def linearity_gate(samples, target, params):
    """범주형 축을 토글했을 때 Δ(출력변화)가 일정한지 — sum/xor/CRC 가능성 게이트.

    한 범주형 파라미터만 두 값 사이에서 바뀌고 나머지가 같은 쌍들을 모아,
    Δ(=대상 바이트 차이)가 쌍마다 일정한지 본다. 일정하지 않으면(입력변화는
    같은데 출력변화가 다르면) 어떤 가산/선형 체크섬으로도 설명 불가가 '증명'된다.
    """
    cat = [p for p in params
           if any(not isinstance(s["params"][p], (int, float)) for s in samples)]
    out = []
    for p in cat:
        vals = sorted({s["params"][p] for s in samples}, key=str)
        if len(vals) < 2:
            continue
        a, b = vals[0], vals[1]
        others = [q for q in params if q != p]
        # 나머지 파라미터가 같은 a/b 쌍 매칭
        idx = {}
        for s in samples:
            key = (tuple(s["params"][q] for q in others), s["params"][p])
            idx[key] = s
        subs, xors = set(), set()
        for (okey, pv), s in list(idx.items()):
            if pv != a:
                continue
            sb = idx.get((okey, b))
            if sb:
                subs.add((byte(sb, target) - byte(s, target)) & 0xFF)
                xors.add(byte(sb, target) ^ byte(s, target))
        if subs:
            const = len(subs) == 1 and len(xors) == 1
            out.append((p, a, b, sorted(subs), sorted(xors), const))
    return out


def _bitrev(b):
    r = 0
    for i in range(8):
        r |= ((b >> i) & 1) << (7 - i)
    return r


def _crc8(data, poly, init, refin, refout, xorout):
    crc = init
    for x in data:
        if refin:
            x = _bitrev(x)
        crc ^= x
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    if refout:
        crc = _bitrev(crc)
    return crc ^ xorout


def crc_sweep(samples, target, payload_sets):
    """poly·init·refin·refout·xorout 전 조합 × payload 구간 CRC-8 전수 검증."""
    tgt = [byte(s, target) for s in samples]
    for label, rng in payload_sets:
        if not rng:
            continue
        pays = [[byte(s, p) for p in rng] for s in samples]
        for poly in range(256):
            for refin in (0, 1):
                for refout in (0, 1):
                    for init in range(256):
                        xorout = _crc8(pays[0], poly, init, refin, refout, 0) ^ tgt[0]
                        if all(_crc8(p, poly, init, refin, refout, xorout) == t
                               for p, t in zip(pays, tgt)):
                            return (f"CRC8 poly=0x{poly:02X} init=0x{init:02X} "
                                    f"refin={refin} refout={refout} xorout=0x{xorout:02X} "
                                    f"of [{label}]")
    return None


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

    params = model["params"]
    all_pos = [(fi, bi) for fi, L in enumerate(shape) for bi in range(L)]
    target_set = set(targets)

    # payload 후보 집합 정의 (대상별로 '자기만 제외'는 아래에서 추가)
    full = [p for p in all_pos if p not in target_set]                 # 둘 다 제외(비연속)
    frame_sets = []
    for fi, L in enumerate(shape):
        fr = [(fi, bi) for bi in range(L) if (fi, bi) not in target_set]
        if fr:
            frame_sets.append((f"F{fi+1}전체", fr))
    base_payloads = [("전체(대상제외)", full)] + frame_sets

    def payloads_for(t):
        """대상 t 기준 payload 집합 — '자기만 제외'(다른 complex를 입력 허용) 추가."""
        self_only = [p for p in all_pos if p != t]
        return base_payloads + [("자기만제외(교차허용)", self_only)]

    # ── 원시 바이트 표 (수동 확인용) ──
    print("[원시 바이트] params | " + fmt(all_pos))
    tmark = " ".join(("vv" if p in target_set else "  ") for p in all_pos)
    print(f"  {'(대상표시)':16s} {tmark}")
    for s in use[:args.rows]:
        lab = "/".join(str(v) for v in s["params"].values())
        row = " ".join(f"{byte(s, p):02X}" for p in all_pos)
        print(f"  {lab:16s} {row}")
    print()

    # ── [0] 의존 파라미터 분석 + 선형성 게이트 (가장 먼저 — 가능 여부부터 가른다) ──
    print("[0] 의존 파라미터 분석 (= 합성에 필요한 최소 수집 차원)")
    for t in targets:
        combo, table, clash = min_determining_subset(use, t, params)
        if combo:
            ncell = len(table)
            print(f"  {fmt([t])}: 결정 파라미터 = <{'+'.join(combo)}>  "
                  f"(룩업 {ncell}칸 → 그 조합만 1개씩 수집하면 합성 가능)")
        else:
            print(f"  {fmt([t])}: 전체 파라미터로도 결정 안 됨 ⚠ "
                  f"(숨은 상태/노이즈 — 충돌 예: {clash})")
    print()
    print("    [선형성 게이트] 범주형 축 토글 시 Δ가 일정한가? (sum/xor/CRC 가능성)")
    for t in targets:
        gate = linearity_gate(use, t, params)
        if not gate:
            print(f"  {fmt([t])}: 범주형 토글 쌍 없음")
            continue
        for (p, a, b, subs, xors, const) in gate:
            verdict = "일정 → 가산/선형 가능성 있음" if const \
                else "불일치 → sum/xor/CRC 전부 불가 증명됨"
            print(f"  {fmt([t])} <{p}:{a}↔{b}>: "
                  f"Δsub={['0x%02X'%x for x in subs]} Δxor={['0x%02X'%x for x in xors]} → {verdict}")
    print()

    # ── 1) 단일바이트 가설 (자기만제외 교차 payload 포함) ──
    print("[1] 단일바이트 체크섬 가설 (sum/xor/2의보수)")
    for t in targets:
        hits = test_single(use, t, payloads_for(t))
        print(f"  {fmt([t])}: " + (" | ".join(hits) if hits else "매칭 없음"))
    print()

    # ── 2) CRC-8 전수 스윕 ──
    print("[2] CRC-8 전수 스윕 (poly·init·refin·refout·xorout)")
    for t in targets:
        hit = crc_sweep(use, t, payloads_for(t))
        print(f"  {fmt([t])}: " + (hit if hit else "매칭 없음"))
    print()

    # ── 3) 16비트 합 분할 가설 (대상이 정확히 2개일 때) ──
    if len(targets) == 2:
        print("[3] 16비트 합 분할 가설 (두 대상 = 하위/상위 바이트쌍)")
        hits = test_pair16(use, targets[0], targets[1], base_payloads)
        print("  " + (" | ".join(hits) if hits else "매칭 없음"))
    else:
        print(f"[3] 16비트 쌍 가설 생략 — 대상이 {len(targets)}개 (2개일 때만)")

    print("\n끝. [0]의 '결정 파라미터'가 핵심 — 그게 최소 수집 차원이고,")
    print("    [1][2]에서 매칭이 나오면 그 공식이 ir_learn.find_checksum 확장 근거입니다.")


if __name__ == "__main__":
    main()

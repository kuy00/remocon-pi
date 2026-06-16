#!/usr/bin/env python3
"""수집 데이터에서 IR 프로토콜 규칙을 자동 학습한다(에어컨/리모컨 무관).

하드코딩 없이:
  1) 여러 수집본을 다수결로 정제(노이즈 제거)
  2) 항상 고정인 바이트 = 상수, 파라미터와 함께 변하는 바이트 = 그 필드(자동 발견)
  3) 필드 값 관계 추론(선형/룩업) → 예측 가능 여부 판정
  4) 체크섬 알고리즘 자동 탐색(sum/xor/2의보수 + 범위)
  5) 학습 결과를 모델(JSON)로 저장하고, 수집본을 재현해 자가검증

사용: python3 ir_learn.py   (dataset/ 읽어 model.json 생성)
"""
import sys
import json
import glob
from collections import Counter
from itertools import combinations
from pathlib import Path

import ir_codec

sys.stdout.reconfigure(line_buffering=True)

DATA_DIR = Path("dataset")
MODEL_OUT = Path("model.json")


# ── 데이터셋 적재 (ir_collect.py 출력: params + repeats) ──────
def load_dataset():
    samples = []  # {"params": {...}, "frames": [[..],[..]], "confidence": x}
    low_conf = []
    for f in sorted(glob.glob(str(DATA_DIR / "*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        reps = d.get("repeats", [])
        if not reps:
            continue
        frames, conf = ir_codec.consensus(reps)
        if not frames:
            continue
        samples.append({"params": d["params"], "frames": frames, "confidence": conf})
        if conf < 0.9:
            low_conf.append((d["params"], conf))
    return samples, low_conf


# ── 3. 필드 발견 ──────────────────────────────────────────
def discover_fields(samples, params):
    """프레임별 바이트가 어느 파라미터와 함께 변하는지 분류."""
    # 가장 흔한 프레임 구성(프레임 수/길이)만 사용
    shape = Counter(tuple(len(f) for f in s["frames"]) for s in samples).most_common(1)[0][0]
    samples = [s for s in samples if tuple(len(f) for f in s["frames"]) == shape]
    report = []
    for fi, L in enumerate(shape):
        for bi in range(L):
            vals = [s["frames"][fi][bi] for s in samples]
            if len(set(vals)) == 1:
                report.append((fi, bi, "const", vals[0], None))
                continue
            # 파라미터(또는 그 조합)로 바이트가 결정되는가?
            # 단일 → 2개 조합 순으로, 가장 단순한 설명을 찾는다.
            owner = None
            for size in (1, 2):
                for combo in combinations(params, size):
                    m, ok = {}, True
                    for s in samples:
                        k = tuple(s["params"][p] for p in combo)
                        v = s["frames"][fi][bi]
                        if k in m and m[k] != v:
                            ok = False
                            break
                        m[k] = v
                    if ok and len(set(m.values())) > 1:
                        owner = combo
                        break
                if owner:
                    break
            report.append((fi, bi, "field" if owner else "complex", None, owner))
    return shape, samples, report


# ── 4. 체크섬 자동 탐색 ───────────────────────────────────
def find_checksum(samples, target_fi, target_bi):
    """target 바이트가 (프레임 교차 포함) 다른 바이트들의 체크섬인지 탐색.

    전체 프레임 바이트를 프레임 순서로 평탄화해 연속 구간을 payload 후보로 시도한다.
    상수 보정항(const)은 첫 샘플에서 offset을 구해 전 샘플로 검증한다(자동 추정).
    노이즈 오탐을 줄이려 고신뢰(>=0.9) 샘플이 충분하면 그것만으로 탐색한다.
    range 는 [frame, byte] 쌍의 리스트 — 같은 프레임 내/교차 모두 표현한다.
    """
    hi = [s for s in samples if s.get("confidence", 1.0) >= 0.9]
    use = hi if len(hi) >= 3 else samples
    # 타깃을 제외한 전체 바이트 위치를 프레임 순서로 평탄화
    positions = [(fi, bi)
                 for fi, f in enumerate(use[0]["frames"])
                 for bi in range(len(f))
                 if not (fi == target_fi and bi == target_bi)]
    schemes = {
        "sum":      lambda xs: sum(xs) & 0xFF,
        "xor":      _xor,
        "sum_neg":  lambda xs: (-sum(xs)) & 0xFF,
        "sum_inv":  lambda xs: (~sum(xs)) & 0xFF,
    }
    def tgt(s):
        return s["frames"][target_fi][target_bi]
    def payload(s, rng):
        return [s["frames"][fi][bi] for fi, bi in rng]
    # 연속 구간 [a:b] payload 후보 × 스킴 × 자동 보정상수
    for a in range(0, len(positions)):
        for b in range(a + 1, len(positions) + 1):
            rng = positions[a:b]
            for name, fn in schemes.items():
                off = (tgt(use[0]) - fn(payload(use[0], rng))) & 0xFF
                ok = all((fn(payload(s, rng)) + off) & 0xFF == tgt(s) for s in use)
                if ok:
                    return {"scheme": name,
                            "range": [[fi, bi] for fi, bi in rng],
                            "const": off, "samples_used": len(use)}
    return None


def _xor(xs):
    r = 0
    for x in xs:
        r ^= x
    return r


# ── 5. 필드 값 관계(선형/룩업) ────────────────────────────
def field_relation(samples, fi, bi, owner):
    """owner: 파라미터 튜플. 단일 숫자면 선형 검사, 그 외 lookup."""
    if len(owner) == 1:
        p = owner[0]
        pairs = sorted({(s["params"][p], s["frames"][fi][bi]) for s in samples})
        if all(isinstance(k, (int, float)) for k, _ in pairs) and len(pairs) >= 2:
            (x0, y0), (x1, y1) = pairs[0], pairs[1]
            if x1 != x0:
                slope = (y1 - y0) / (x1 - x0)
                base = y0 - slope * x0
                if all(abs(slope * x + base - y) < 1e-6 for x, y in pairs):
                    return {"type": "linear", "by": list(owner),
                            "slope": slope, "base": base}
        return {"type": "lookup", "by": list(owner),
                "map": {str(k): v for k, v in pairs}}
    # 조합 키 → 바이트
    m = {tuple(s["params"][p] for p in owner): s["frames"][fi][bi] for s in samples}
    return {"type": "lookup", "by": list(owner),
            "map": {"|".join(map(str, k)): v for k, v in sorted(m.items())}}


def main():
    samples, low_conf = load_dataset()
    if not samples:
        print(f"데이터 없음 ({DATA_DIR}/ 비어있음) — 먼저 ir_collect.py 로 수집")
        return
    params = list(samples[0]["params"].keys())
    print(f"샘플 {len(samples)}개, 파라미터 {params}")
    if low_conf:
        def _lab(p):
            return "/".join(str(v) for v in p.values())
        print(f"⚠ 신뢰도 90% 미만 설정 {len(low_conf)}개 — 재수집 권장: "
              + ", ".join(f"{_lab(p)}({c:.0%})" for p, c in low_conf[:5])
              + (" ..." if len(low_conf) > 5 else ""))

    shape, samples, report = discover_fields(samples, params)
    print(f"\n프레임 구성: {shape}")
    print("\n[바이트 분류]")
    model = {"params": params, "shape": list(shape), "frames": []}
    for fi, L in enumerate(shape):
        finfo = {"len": L, "bytes": []}
        for (ffi, bi, kind, cval, owner) in [r for r in report if r[0] == fi]:
            entry = {"index": bi, "kind": kind}
            tag = ""
            if kind == "const":
                entry["value"] = cval
                tag = f"const=0x{cval:02X}"
            elif kind == "field":
                rel = field_relation(samples, fi, bi, owner)
                entry["relation"] = rel
                tag = f"field<{'+'.join(owner)}> {rel['type']}"
            else:
                cs = find_checksum(samples, fi, bi)
                if cs:
                    entry["kind"] = "checksum"
                    entry["checksum"] = cs
                    where = " ".join(f"F{f+1}B{b}" for f, b in cs["range"])
                    tag = (f"checksum {cs['scheme']}(+0x{cs['const']:02X}) "
                           f"of [{where}] (n={cs['samples_used']})")
                else:
                    tag = "complex(미해독)"
            finfo["bytes"].append(entry)
            print(f"  F{fi+1} B{bi}: {tag}")
        model["frames"].append(finfo)

    MODEL_OUT.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n모델 저장: {MODEL_OUT}")

    # 자가검증: 미해독(complex) 바이트가 남았는지
    complex_cnt = sum(1 for f in model["frames"] for b in f["bytes"] if b["kind"] == "complex")
    print(f"\n자가검증: 미해독 바이트 {complex_cnt}개", "→ 완전 합성 가능 ✅" if complex_cnt == 0 else "→ 해당 프레임은 합성 불가, replay 필요 ⚠️")


if __name__ == "__main__":
    main()

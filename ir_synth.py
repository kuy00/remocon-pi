#!/usr/bin/env python3
"""학습된 `model.json` 규칙으로 미수집 조합의 IR 신호를 합성한다(리모컨 무관, 하드코딩 없음).

바이트 위치·온도식·체크섬을 코드에 박지 않는다. `ir_learn.py`가 데이터에서 도출해
`model.json`에 적은 규칙만 사용한다:
  - `const`                  : 고정값
  - `field` `linear`         : 수치 파라미터로 계산(미수집 값도 외삽)
  - `field` `lookup`         : 표 — 키가 있으면 사용, 없으면 템플릿값
  - `checksum frame_sum_pair`: 그룹별 '프레임 전체 합 상수'를 만족하도록 멤버 한 개를 보정
  - `complex`                : 미해독 → 템플릿값 유지(그 바이트는 replay)

합성 방식(서지컬): 같은 범주형 그룹의 **가장 가까운 수집본**을 타이밍 템플릿으로 삼아,
헤더/타이밍은 실측 펄스 그대로 두고 **값이 바뀌는 비트의 space 길이만 교체**한다.

선행: `python3 ir_learn.py [--dataset ...]` 로 `model.json` 생성.

사용:
  python3 ir_synth.py 냉방 25 on                 # 합성 후 송신 (model.json 사용)
  python3 ir_synth.py 냉방 25 on --dry           # 송신 없이 합성 바이트 + 자가검증
  python3 ir_synth.py 냉방 25 on --template 냉방_24_on
  python3 ir_synth.py 냉방 25 on --dataset dataset_cool --model model.json
"""
import sys
import json
import argparse
from pathlib import Path

import config
import ir_codec

sys.stdout.reconfigure(line_buffering=True)

DATASET_DIR = config.DATASET_DIR


# ── 데이터셋/모델 적재 ───────────────────────────────────
def list_group(dataset_dir, tparams, categorical):
    """target 과 같은 범주형 그룹(categorical 값 일치)의 수집본 (params, path) 목록."""
    out = []
    for f in sorted(Path(dataset_dir).glob("*.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))["params"]
        except Exception:
            continue
        if all(str(p.get(c)) == str(tparams[c]) for c in categorical):
            out.append((p, f))
    return out


def load_clean(fpath):
    """수집본을 다수결(consensus)로 정제 → (깨끗한 segs, 합의 바이트프레임, 신뢰도).

    깨끗한 segs = 합의와 정확히 일치하게 디코딩되는 반복본(타이밍 템플릿용). 없으면 None.
    """
    d = json.loads(Path(fpath).read_text(encoding="utf-8"))
    reps = d.get("repeats") or []
    if not reps:
        return None, [], 0.0
    frames, conf = ir_codec.consensus(reps)
    clean = next((r for r in reps if ir_codec.segs_to_byteframes(r) == frames), None)
    return clean, frames, conf


# ── 모델 규칙으로 목표 바이트 계산 (하드코딩 없음) ───────
def _lookup_key(tparams, by):
    return str(tparams[by[0]]) if len(by) == 1 else "|".join(str(tparams[p]) for p in by)


def _group_key(tparams, by):
    return "|".join(str(tparams[p]) for p in by)


def compute_target_frames(model, tpl_frames, tparams):
    """템플릿 바이트에서 출발해, model 규칙으로 목표 파라미터의 바이트를 계산한다."""
    frames = [list(f) for f in tpl_frames]
    pairs, notes = {}, []
    for fi, finfo in enumerate(model["frames"]):
        for e in finfo["bytes"]:
            bi = e["index"]
            if fi >= len(frames) or bi >= len(frames[fi]):
                continue
            kind = e["kind"]
            if kind == "field":
                rel = e["relation"]
                if rel["type"] == "linear":
                    val = rel["slope"] * tparams[rel["by"][0]] + rel["base"]
                    frames[fi][bi] = int(round(val)) & 0xFF
                else:  # lookup
                    key = _lookup_key(tparams, rel["by"])
                    if key in rel["map"]:
                        frames[fi][bi] = rel["map"][key] & 0xFF
                    else:
                        notes.append(f"F{fi+1}B{bi}: lookup 미수집 키({key}) → 템플릿값 유지")
            elif kind == "checksum" and e["checksum"].get("type") == "frame_sum_pair":
                cs = e["checksum"]
                pairs.setdefault(tuple(map(tuple, cs["members"])), cs)
            elif kind == "complex":
                notes.append(f"F{fi+1}B{bi}: complex(미해독) → 템플릿값 유지(replay)")
            # const → 템플릿값 그대로

    # 체크섬 쌍 보정: 다른 바이트가 모두 확정된 뒤, 멤버 한 개로 그룹 합을 맞춘다
    for members, cs in pairs.items():
        gkey = _group_key(tparams, cs["by"])
        if gkey not in cs["const_map"]:
            raise SystemExit(
                f"체크섬 그룹({gkey}) 상수 미학습 — 그 그룹을 수집·학습해야 합성 가능")
        K = cs["const_map"][gkey]
        mlist = [tuple(m) for m in members]
        mset = set(mlist)
        kept = sum(frames[f][b] for f, b in mlist[1:])           # 나머지 멤버는 템플릿값 유지
        others = sum(frames[f][b]
                     for f in range(len(frames)) for b in range(len(frames[f]))
                     if (f, b) not in mset)
        frames[mlist[0][0]][mlist[0][1]] = (K - others - kept) & 0xFF
    return frames, notes


# ── 서지컬 합성 (바뀐 비트의 space 만 교체) ──────────────
def space_clusters(segs, thr):
    shorts = sorted(d for lvl, d in segs[2:] if d < thr and d < ir_codec.BIG_GAP_US)
    longs = sorted(d for lvl, d in segs[2:] if thr <= d < ir_codec.BIG_GAP_US)

    def med(xs, default):
        return xs[len(xs) // 2] if xs else default
    return med(shorts, 560), med(longs, 1600)


def map_bits(segs, thr):
    """decoder 와 동일 순회로 각 비트의 (space_seg_index, bit) 매핑(프레임별)."""
    data = segs[2:]
    out, frame, i = [], [], 0
    while i + 1 < len(data):
        md, sd = data[i][1], data[i + 1][1]
        if md > ir_codec.BIG_GAP_US or sd > ir_codec.BIG_GAP_US:
            if frame:
                out.append(frame)
                frame = []
            i += 1
            continue
        frame.append((2 + i + 1, 1 if sd > thr else 0))
        i += 2
    if frame:
        out.append(frame)
    return out


def synth_segs(template_segs, target_frames):
    """템플릿 segs에서 target 바이트와 다른 비트의 space만 교체해 새 segs 생성."""
    thr = ir_codec.auto_threshold(template_segs)
    short_us, long_us = space_clusters(template_segs, thr)
    segs = [list(s) for s in template_segs]
    for fi, frame in enumerate(map_bits(template_segs, thr)):
        if fi >= len(target_frames):
            break
        tbytes = target_frames[fi]
        for pos, (space_idx, cur) in enumerate(frame):
            bi, k = pos // 8, pos % 8
            if bi >= len(tbytes):
                break
            want = (tbytes[bi] >> k) & 1   # LSB-first (codec와 동일)
            if want != cur:
                segs[space_idx][1] = long_us if want else short_us
    return segs


def _hex(frames):
    return " | ".join(" ".join(f"{b:02X}" for b in f) for f in frames)


def parse_params(pnames, values):
    if len(values) != len(pnames):
        raise SystemExit(f"파라미터 {pnames} 순서로 {len(pnames)}개 값 필요 — 받음: {values}")
    return {name: (int(v) if v.lstrip("-").isdigit() else v)
            for name, v in zip(pnames, values)}


def main():
    ap = argparse.ArgumentParser(description="model.json 규칙으로 미수집 조합 IR 합성/송신")
    ap.add_argument("params", nargs="+", help="모델 파라미터 순서대로 (예: 냉방 25 on)")
    ap.add_argument("--model", default=str(config.MODEL_FILE), help="학습 모델 경로")
    ap.add_argument("--dataset", default=str(DATASET_DIR), help="템플릿 수집 데이터 경로")
    ap.add_argument("--template", default=None, help="템플릿 라벨 직접 지정 (예: 냉방_24_on)")
    ap.add_argument("--gpio", type=int, default=config.IR_TX_GPIO)
    ap.add_argument("--dry", action="store_true", help="송신 없이 합성 바이트만 출력")
    args = ap.parse_args()

    mpath = Path(args.model)
    if not mpath.exists():
        raise SystemExit(
            f"모델 없음: {mpath} — 먼저 'python3 ir_learn.py --dataset {args.dataset}' 실행")
    model = json.loads(mpath.read_text(encoding="utf-8"))
    pnames = model["params"]
    tparams = parse_params(pnames, args.params)
    categorical = [p for p in pnames if isinstance(tparams[p], str)]
    numeric = [p for p in pnames if not isinstance(tparams[p], str)]

    group = list_group(args.dataset, tparams, categorical)
    if args.template:
        group = [(p, f) for p, f in group if f.stem == args.template]
    if not group:
        raise SystemExit("템플릿 없음 — 같은 범주형 그룹의 수집본이 필요(--dataset 확인)")

    cands = []
    for p, f in group:
        clean, frames, conf = load_clean(f)
        if clean is None or not frames:
            continue
        dist = sum(abs(p[n] - tparams[n]) for n in numeric) if numeric else 0
        cands.append((dist, conf, p, f, clean, frames))
    if not cands:
        raise SystemExit("쓸 만한 템플릿 없음 (반복본이 합의와 불일치 — 재수집 필요)")

    good = [c for c in cands if c[1] >= 0.9] or cands
    dist, conf, tp, tpath, template_segs, tpl_frames = min(good, key=lambda c: (c[0], -c[1]))

    target_frames, notes = compute_target_frames(model, tpl_frames, tparams)

    label = "_".join(str(tparams[p]) for p in pnames)
    print(f"합성: {label}")
    print(f"  모델: {mpath.name}   템플릿: {tpath.name} (신뢰도 {conf:.0%}, 거리 {dist})")
    print(f"  템플릿 바이트: {_hex(tpl_frames)}")
    print(f"  합성   바이트: {_hex(target_frames)}")
    for n in notes:
        print(f"  · {n}")
    if not good or conf < 0.9:
        print("  ⚠ 고신뢰(>=90%) 템플릿이 없어 노이즈 가능 — 해당 그룹 재수집 권장")

    new_segs = synth_segs(template_segs, target_frames)

    if args.dry:
        rt = ir_codec.segs_to_byteframes(new_segs)
        ok = rt == target_frames
        print(f"  segs={len(new_segs)}  자가검증(재디코딩 일치): "
              f"{'OK ✅' if ok else f'불일치 {_hex(rt)}'}")
        return

    import pigpio
    import ir_send
    pi = config.connect()
    try:
        pi.set_mode(args.gpio, pigpio.OUTPUT)
        pi.write(args.gpio, 0)
        pi.wave_clear()
        print(f"  송신 (GPIO {args.gpio}, segs={len(new_segs)}) ...")
        ir_send.transmit_segs(pi, args.gpio, new_segs)
        print("  완료.")
    finally:
        pi.write(args.gpio, 0)
        pi.stop()


if __name__ == "__main__":
    main()

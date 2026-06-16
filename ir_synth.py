#!/usr/bin/env python3
"""학습 규칙으로 미수집 조합의 IR 신호를 합성한다(에어컨/리모컨 무관).

해독된 규칙:
  - 온도 바이트(F1 B3): 온도에 선형(기울기 1) → 온도 차이만큼 가감
  - F2 B4·B5: 프레임 전체 바이트 합이 (mode,power) 그룹마다 상수인 2바이트 체크섬
    → 온도 바이트가 +dT 되면 B4 를 -dT 해서 전체 합을 보존(B5 유지)

합성 방식(서지컬): 같은 (mode,power)에서 **가장 가까운 수집본**을 템플릿으로 삼아,
헤더/타이밍은 실측 펄스 그대로 재사용하고 **값이 바뀌는 비트의 space 길이만 교체**한다.
하드코딩 상수 없이 템플릿 상대값(dT)만 쓴다.

사용:
  python3 ir_synth.py 냉방 25 on            # 합성 후 송신
  python3 ir_synth.py 냉방 25 on --dry      # 송신 없이 합성 바이트만 출력
  python3 ir_synth.py 냉방 25 on --template-temp 24
"""
import sys
import json
import glob
import argparse
from pathlib import Path

import config
import ir_codec

sys.stdout.reconfigure(line_buffering=True)

DATASET_DIR = config.DATASET_DIR
TEMP_BYTE = (0, 3)   # (frame, byte) — 온도 바이트 F1 B3
CK_LOW = (1, 4)      # F2 B4 (체크섬 조정 바이트)


def find_templates(dataset_dir, mode, power):
    """같은 (mode,power) 그룹의 수집본 라벨→온도 목록."""
    out = []
    for f in sorted(Path(dataset_dir).glob(f"{mode}_*_{power}.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            t = d["params"]["temp"]
        except Exception:
            continue
        out.append((int(t), f))
    return out


def load_segs(fpath):
    d = json.loads(Path(fpath).read_text(encoding="utf-8"))
    reps = d.get("repeats") or []
    if not reps:
        raise ValueError(f"{fpath}: repeats 없음")
    return reps[0]


def space_clusters(segs, thr):
    """템플릿의 short/long space 대표값(중앙값) 추정."""
    shorts = sorted(d for lvl, d in segs[2:] if d < thr and d < ir_codec.BIG_GAP_US)
    longs = sorted(d for lvl, d in segs[2:]
                   if thr <= d < ir_codec.BIG_GAP_US)
    def med(xs, default):
        return xs[len(xs) // 2] if xs else default
    return med(shorts, 560), med(longs, 1600)


def map_bits(segs, thr):
    """decoder(segs_to_byteframes)와 동일 순회로 각 비트의 (space_seg_index, bit) 매핑.

    반환: 프레임별 [(space_seg_index, bit), ...]
    """
    data = segs[2:]
    out, frame, i = [], [], 0
    while i + 1 < len(data):
        md = data[i][1]
        sd = data[i + 1][1]
        if md > ir_codec.BIG_GAP_US or sd > ir_codec.BIG_GAP_US:
            if frame:
                out.append(frame)
                frame = []
            i += 1
            continue
        frame.append((2 + i + 1, 1 if sd > thr else 0))   # space seg = data[i+1]
        i += 2
    if frame:
        out.append(frame)
    return out


def synth_segs(template_segs, target_frames):
    """템플릿 segs에서 target 바이트와 다른 비트의 space만 교체해 새 segs 생성."""
    thr = ir_codec.auto_threshold(template_segs)
    short_us, long_us = space_clusters(template_segs, thr)
    segs = [list(s) for s in template_segs]
    bmap = map_bits(template_segs, thr)
    for fi, frame in enumerate(bmap):
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


def build_target(template_segs, dT):
    """템플릿 바이트에서 온도차 dT 만큼 B3 +dT, B4 -dT (체크섬 보존)."""
    frames = ir_codec.segs_to_byteframes(template_segs)
    if len(frames) < 2 or len(frames[0]) <= TEMP_BYTE[1] or len(frames[1]) <= CK_LOW[1]:
        raise SystemExit(f"템플릿 프레임 구조가 예상과 다름: {[len(f) for f in frames]}")
    frames = [list(f) for f in frames]
    frames[TEMP_BYTE[0]][TEMP_BYTE[1]] = (frames[TEMP_BYTE[0]][TEMP_BYTE[1]] + dT) & 0xFF
    frames[CK_LOW[0]][CK_LOW[1]] = (frames[CK_LOW[0]][CK_LOW[1]] - dT) & 0xFF
    return frames


def _hex(frames):
    return " | ".join(" ".join(f"{b:02X}" for b in f) for f in frames)


def main():
    ap = argparse.ArgumentParser(description="미수집 조합 IR 신호 합성/송신")
    ap.add_argument("mode")
    ap.add_argument("temp", type=int)
    ap.add_argument("power")
    ap.add_argument("--template-temp", type=int, default=None,
                    help="템플릿으로 쓸 수집 온도 (기본: 가장 가까운 온도)")
    ap.add_argument("--dataset", default=str(DATASET_DIR))
    ap.add_argument("--gpio", type=int, default=config.IR_TX_GPIO)
    ap.add_argument("--dry", action="store_true", help="송신 없이 합성 바이트만 출력")
    args = ap.parse_args()

    templates = find_templates(args.dataset, args.mode, args.power)
    if not templates:
        raise SystemExit(f"템플릿 없음: {args.dataset}/{args.mode}_*_{args.power}.json")

    if args.template_temp is not None:
        cand = [tf for tf in templates if tf[0] == args.template_temp]
        if not cand:
            raise SystemExit(f"템플릿 온도 {args.template_temp} 수집본 없음")
        tpl_temp, tpl_path = cand[0]
    else:
        tpl_temp, tpl_path = min(templates, key=lambda tf: abs(tf[0] - args.temp))

    dT = args.temp - tpl_temp
    template_segs = load_segs(tpl_path)
    tpl_frames = ir_codec.segs_to_byteframes(template_segs)
    target_frames = build_target(template_segs, dT)

    print(f"합성: {args.mode} {args.temp} {args.power}")
    print(f"  템플릿: {Path(tpl_path).name} (온도 {tpl_temp}, dT={dT:+d})")
    print(f"  템플릿 바이트: {_hex(tpl_frames)}")
    print(f"  합성   바이트: {_hex(target_frames)}")

    if dT == 0:
        print("  (dT=0 — 템플릿과 동일, replay 와 같음)")

    new_segs = synth_segs(template_segs, target_frames)

    if args.dry:
        print(f"  segs={len(new_segs)} (--dry: 송신 안 함)")
        # 합성 결과를 다시 디코딩해 자가검증
        rt = ir_codec.segs_to_byteframes(new_segs)
        ok = rt == target_frames
        print(f"  자가검증(재디코딩 일치): {'OK ✅' if ok else f'불일치 {_hex(rt)}'}")
        return

    import ir_send
    pi = config.connect()
    try:
        import pigpio
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

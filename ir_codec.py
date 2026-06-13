"""IR raw 펄스(segs) ↔ 비트/바이트 변환 공통 모듈 (리모컨 무관).

- 헤더 타이밍을 하드코딩하지 않는다. 비트 0/1 경계는 각 캡처의 space 길이를
  스스로 군집화해 추정한다(self-clustering).
- 프레임 구분은 긴 무신호 갭(기본 20000us)으로만 한다.
"""
from collections import Counter

BIG_GAP_US = 20000   # 이보다 길면 프레임 경계로 본다


def auto_threshold(segs):
    """이 캡처의 space 길이들을 짧은무리/긴무리로 가르는 경계를 추정."""
    spaces = sorted(d for lvl, d in segs[2:] if d < BIG_GAP_US)
    if len(spaces) < 2:
        return 800
    best_gap, thr = 0, 800
    for i in range(1, len(spaces)):
        gap = spaces[i] - spaces[i - 1]
        if 200 < spaces[i] < 3000 and gap > best_gap:
            best_gap, thr = gap, (spaces[i] + spaces[i - 1]) // 2
    return thr


def segs_to_byteframes(segs, thr=None):
    """segs → 프레임별 바이트 리스트. thr 미지정 시 자동 추정.

    헤더 2개(mark,space)는 건너뛰고, 이후 (mark,space) 쌍의 space 길이로 비트 판정.
    LSB-first, 8비트 단위로 바이트 조립.
    """
    if thr is None:
        thr = auto_threshold(segs)
    data = segs[2:]
    bits, frames, i = [], [], 0
    while i + 1 < len(data):
        _, md = data[i]
        _, sd = data[i + 1]
        if md > BIG_GAP_US or sd > BIG_GAP_US:
            if bits:
                frames.append(bits)
                bits = []
            i += 1
            continue
        bits.append(1 if sd > thr else 0)
        i += 2
    if bits:
        frames.append(bits)
    return [[sum(x << k for k, x in enumerate(f[s:s + 8]))
             for s in range(0, len(f) // 8 * 8, 8)] for f in frames]


def majority_frames(decoded_list):
    """여러 수집본(각각 [f1bytes, f2bytes,...]) → 프레임/바이트별 다수결."""
    decoded_list = [d for d in decoded_list if d]
    if not decoded_list:
        return []
    shape = Counter(tuple(len(f) for f in d) for d in decoded_list).most_common(1)[0][0]
    keep = [d for d in decoded_list if tuple(len(f) for f in d) == shape]
    out = []
    for fi, L in enumerate(shape):
        out.append([Counter(d[fi][bi] for d in keep).most_common(1)[0][0]
                    for bi in range(L)])
    return out


def consensus(repeats):
    """반복 캡처(raw segs 리스트) → (합의 바이트프레임, 신뢰도 0~1).

    신뢰도 = 합의와 정확히 일치하는 반복본 비율.
    """
    decoded = [segs_to_byteframes(r) for r in repeats]
    cons = majority_frames(decoded)
    if not cons:
        return [], 0.0
    matches = sum(1 for d in decoded if d == cons)
    return cons, matches / len(decoded)

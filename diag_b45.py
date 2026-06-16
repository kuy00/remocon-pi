#!/usr/bin/env python3
"""F2 B4/B5 규칙 진단 — 데이터셋 고신뢰 샘플로 값 패턴/가설 검정.

학습기가 complex 로 남긴 F2 끝 두 바이트가 어떤 규칙인지 찾기 위한 일회성 도구.
사용: python3 diag_b45.py [데이터셋_디렉터리]   (기본 dataset)
"""
import sys
import glob
import json
from collections import defaultdict, Counter

import ir_codec

DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else 'dataset'

rows = []
for f in sorted(glob.glob(f'{DATA_DIR}/*.json')):
    d = json.load(open(f, encoding='utf-8'))
    fr, conf = ir_codec.consensus(d['repeats'])
    if len(fr) < 2 or conf < 0.9:
        continue
    p = d['params']
    rows.append((p['mode'], p['temp'], p['power'], fr[0], fr[1][4], fr[1][5], conf))

print(f"고신뢰(>=90%) 샘플 {len(rows)}개\n")

# 1) 원자료 덤프 (모드/전원/온도 순)
print("mode temp pw | F1(6 bytes)           | B4 B5  conf")
for m, t, pw, f1, b4, b5, c in sorted(rows, key=lambda r: (r[0], r[2], r[1])):
    f1h = ' '.join(f'{x:02X}' for x in f1)
    print(f"{m:>2} {t:>3} {pw:<3}| {f1h} | {b4:02X} {b5:02X}  {c:.0%}")


# 2) 자동 가설검정
def test(name, fn):
    ok = all(fn(f1, b4, b5) for _, _, _, f1, b4, b5, _ in rows)
    print(f"  [{'OK' if ok else '..'}] {name}")


print("\n[가설]")
test("B4 == sum(F1)&0xFF",       lambda f1, b4, b5: sum(f1) & 0xFF == b4)
test("B5 == sum(F1)&0xFF",       lambda f1, b4, b5: sum(f1) & 0xFF == b5)
test("B4 == (~sum(F1))&0xFF",    lambda f1, b4, b5: (~sum(f1)) & 0xFF == b4)
test("B4 xor B5 const?",         lambda f1, b4, b5: (b4 ^ b5) == (rows[0][4] ^ rows[0][5]))
test("B4+B5 == sum(F1)&0xFF",    lambda f1, b4, b5: (b4 + b5) & 0xFF == sum(f1) & 0xFF)
test("(B4<<8|B5)==sum(F1)",      lambda f1, b4, b5: (b4 << 8 | b5) == sum(f1))
test("B4 == sum(F1[2:6])&0xFF",  lambda f1, b4, b5: sum(f1[2:6]) & 0xFF == b4)

# 3) 모드/전원 고정 시 온도→B4,B5 변화 (선형/규칙성 확인)
g = defaultdict(list)
for m, t, pw, f1, b4, b5, c in rows:
    g[(m, pw)].append((t, b4, b5, f1))
print("\n[모드/전원 고정 시 온도 -> B4/B5]")
for k, v in sorted(g.items()):
    v = sorted(v)
    print(f"  {k}: " + ", ".join(f"{t}:{b4:02X}/{b5:02X}" for t, b4, b5, _ in v))

# 4) 체크섬 불변식 검증: (전체 12바이트 합) 이 그룹마다 상수인가?
#    F2 = [0x22,0x80,0xA0,0x00, B4, B5] (앞 4개는 const) 가정.
F2_CONST = [0x22, 0x80, 0xA0, 0x00]
print("\n[체크섬 불변식] 전체 바이트 합 = 그룹 상수 ?  (B4+B5 가 합을 고정)")
for k, v in sorted(g.items()):
    totals = [sum(f1) + sum(F2_CONST) + b4 + b5 for _, b4, b5, f1 in v]
    cnt = Counter(totals)
    base, n = cnt.most_common(1)[0]
    outliers = [(t, tot) for (t, *_), tot in zip(v, totals) if tot != base]
    flag = "OK 상수" if n == len(v) else f"대부분 0x{base:X} ({n}/{len(v)}), 예외 {outliers}"
    # 그룹 상수에서 const 부분을 뺀 'B4+B5+가변(F1)합' 도 같이 출력
    print(f"  {k}: 전체합=0x{base:X} ({base})  -> {flag}")


# 5) 합성 검증: B5=그룹 최빈값, B4=합 맞추기 로 재현해 실제 B4 와 비교
print("\n[합성 재현] B5=그룹최빈, B4=(그룹합 - 나머지) 로 계산 -> 실제와 일치?")
for k, v in sorted(g.items()):
    totals = [sum(f1) + sum(F2_CONST) + b4 + b5 for _, b4, b5, f1 in v]
    group_total = Counter(totals).most_common(1)[0][0]
    b5_mode = Counter(b5 for _, _, b5, _ in v).most_common(1)[0][0]
    hit = 0
    miss = []
    for t, b4, b5, f1 in v:
        b4_syn = (group_total - sum(f1) - sum(F2_CONST) - b5_mode) & 0xFF
        if b4_syn == b4 and b5_mode == b5:
            hit += 1
        else:
            miss.append(f"{t}(syn {b4_syn:02X}/{b5_mode:02X} vs {b4:02X}/{b5:02X})")
    print(f"  {k}: {hit}/{len(v)} 일치" + (f", 불일치 {miss}" if miss else ""))

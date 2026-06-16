#!/usr/bin/env python3
"""F2 B4/B5 규칙 진단 — dataset/ 고신뢰 샘플로 값 패턴/가설 검정.

학습기가 complex 로 남긴 F2 끝 두 바이트가 어떤 규칙인지 찾기 위한 일회성 도구.
사용: python3 diag_b45.py
"""
import glob
import json
from collections import defaultdict

import ir_codec

rows = []
for f in sorted(glob.glob('dataset/*.json')):
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
    g[(m, pw)].append((t, b4, b5))
print("\n[모드/전원 고정 시 온도 -> B4/B5]")
for k, v in sorted(g.items()):
    v = sorted(v)
    print(f"  {k}: " + ", ".join(f"{t}:{b4:02X}/{b5:02X}" for t, b4, b5 in v))

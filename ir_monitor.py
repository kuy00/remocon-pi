#!/usr/bin/env python3
"""범용 실시간 IR 모니터 — 버튼을 누르면 즉시 디코딩해 바이트로 표시한다.

리모컨/프로토콜 무관(하드코딩 없음). 수집·디버깅 시 신호가 제대로 들어오는지,
바이트가 안정적인지 빠르게 확인하는 QC 도구.

사용: python3 ir_monitor.py
"""
import sys

import config
import ir_codec
from ir_collect import GapFramedCollector

sys.stdout.reconfigure(line_buffering=True)


def fmt(frames):
    return "  ".join(
        f"F{i+1}[{len(f)}B]:" + " ".join(f"{b:02X}" for b in f)
        for i, f in enumerate(frames)
    )


def main():
    pi = config.connect()
    rx = GapFramedCollector(pi, config.IR_RX_GPIO)
    rx.start()

    print("=" * 56)
    print("  범용 IR 실시간 모니터")
    print(f"  수신 GPIO {config.IR_RX_GPIO} · 버튼을 누르면 디코딩 표시")
    print("  종료: Ctrl+C")
    print("=" * 56)

    count = 0
    last = None
    try:
        while True:
            segs = rx.wait_one(timeout=1.0)
            if segs is None:
                continue
            count += 1
            frames = ir_codec.segs_to_byteframes(segs)
            line = fmt(frames) if frames else f"(디코드 실패, segs={len(segs)})"
            same = " (직전과 동일)" if frames and frames == last else ""
            print(f"\n#{count} segs={len(segs)} thr={ir_codec.auto_threshold(segs)}us")
            print(f"  {line}{same}")
            last = frames
    except KeyboardInterrupt:
        print("\n종료.")
    finally:
        rx.stop()
        pi.stop()


if __name__ == "__main__":
    main()

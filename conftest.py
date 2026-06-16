"""pytest 공용 설정 — 루트에 두어 프로젝트 모듈(ir_codec 등)을 import 가능하게 하고,
바이트프레임 → raw segs 인코딩 헬퍼(디코더의 역방향)를 fixture 로 제공한다.
"""
import pytest

SHORT_US, LONG_US = 560, 1600


def _make_segs(frames, short=SHORT_US, long=LONG_US, header=(6800, 3300), gap=30000):
    """바이트프레임 리스트 → raw segs(디코더가 그대로 복원하도록). LSB-first."""
    segs = [[0, header[0]], [1, header[1]]]
    for fi, fr in enumerate(frames):
        for b in fr:
            for k in range(8):
                segs.append([0, 560])                              # mark
                segs.append([1, long if (b >> k) & 1 else short])  # space
        if fi < len(frames) - 1:
            segs.append([1, gap])                                  # 프레임 경계
    return segs


@pytest.fixture
def make_segs():
    return _make_segs

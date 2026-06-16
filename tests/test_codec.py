"""ir_codec — raw 펄스 ↔ 바이트 왕복 / 임계값 / 다수결 신뢰도."""
import ir_codec


def test_roundtrip_single_frame(make_segs):
    frames = [[0xD3, 0x21, 0x31, 0x05]]
    assert ir_codec.segs_to_byteframes(make_segs(frames)) == frames


def test_roundtrip_two_frames(make_segs):
    frames = [[0xD3, 0x21, 0x31, 0x05, 0x02, 0xC4], [0x22, 0x80, 0xA0, 0x00, 0xFC, 0x5D]]
    assert ir_codec.segs_to_byteframes(make_segs(frames)) == frames


def test_auto_threshold_between_clusters(make_segs):
    segs = make_segs([[0xAA, 0x55]])   # 0/1 섞임 → short/long 양쪽 존재
    thr = ir_codec.auto_threshold(segs)
    assert 560 < thr < 1600


def test_consensus_majority_and_confidence(make_segs):
    frames = [[0x10, 0x20]]
    good = make_segs(frames)
    bad = make_segs([[0x10, 0x21]])     # 1비트 다른 노이즈 본
    cons, conf = ir_codec.consensus([good, good, good, bad])
    assert cons == frames
    assert conf == 0.75                 # 4개 중 3개 일치

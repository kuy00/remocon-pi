"""ir_synth — model.json 규칙으로 미수집 조합 합성(위치 하드코딩 없음)."""
import ir_codec
import ir_synth


def _model():
    """온도=idx2(linear), 체크섬 쌍=idx4,5 인 모델(기본 위치와 다르게 둬 일반성 확인)."""
    cs = {"type": "frame_sum_pair", "members": [[0, 4], [0, 5]],
          "payload": [[0, 0], [0, 1], [0, 2], [0, 3]],
          "by": ["mode", "power"], "const_map": {"A|x": 0x200}}
    return {"params": ["mode", "temp", "power"], "shape": [6], "frames": [{"len": 6, "bytes": [
        {"index": 0, "kind": "const", "value": 0xAA},
        {"index": 1, "kind": "field",
         "relation": {"type": "lookup", "by": ["mode", "power"], "map": {"A|x": 0x11}}},
        {"index": 2, "kind": "field",
         "relation": {"type": "linear", "by": ["temp"], "slope": 1.0, "base": -16.0}},
        {"index": 3, "kind": "const", "value": 0xBB},
        {"index": 4, "kind": "checksum", "checksum": cs},
        {"index": 5, "kind": "checksum", "checksum": cs},
    ]}]}


def test_compute_target_linear_and_checksum():
    # 템플릿: temp24 → [AA,11,08,BB,C1,C2], 전체합 0x200
    tpl = [[0xAA, 0x11, 0x08, 0xBB, 0x33, 0x4F]]
    assert sum(tpl[0]) == 0x200
    frames, notes = ir_synth.compute_target_frames(_model(), tpl, {"mode": "A", "temp": 25, "power": "x"})
    assert frames[0][2] == 25 - 16          # 온도 바이트 = linear 계산
    assert sum(frames[0]) == 0x200          # 체크섬: 전체 합 보존
    assert frames[0][5] == 0x4F             # 둘째 멤버(B5)는 템플릿값 유지
    assert notes == []


def test_uncollected_group_raises():
    tpl = [[0xAA, 0x11, 0x08, 0xBB, 0x33, 0x4F]]
    import pytest
    with pytest.raises(SystemExit):
        ir_synth.compute_target_frames(_model(), tpl, {"mode": "A", "temp": 25, "power": "y"})


def test_synth_segs_roundtrip(make_segs):
    """합성 segs 를 다시 디코딩하면 목표 바이트와 일치(서지컬 비트 교체 정확성)."""
    tpl_frames = [[0xAA, 0x11, 0x08, 0xBB, 0x33, 0x4F]]
    template_segs = make_segs(tpl_frames)
    target, _ = ir_synth.compute_target_frames(_model(), tpl_frames, {"mode": "A", "temp": 25, "power": "x"})
    new_segs = ir_synth.synth_segs(template_segs, target)
    assert ir_codec.segs_to_byteframes(new_segs) == target


def _write_sample(d, mode, temp, power, frames, make_segs):
    import json
    segs = make_segs(frames)
    (d / f"{mode}_{temp}_{power}.json").write_text(json.dumps(
        {"params": {"mode": mode, "temp": temp, "power": power},
         "confidence": 1.0, "repeats": [segs, segs, segs]}), encoding="utf-8")


def test_synthesize_end_to_end(tmp_path, make_segs):
    """디스크 수집본(18,24만) + model → 미수집 25 합성: 템플릿 선택~서지컬까지."""
    # 18,24 수집 (전체합 0x200 유지)
    _write_sample(tmp_path, "A", 18, "x", [[0xAA, 0x11, 0x02, 0xBB, 0x39, 0x4F]], make_segs)
    _write_sample(tmp_path, "A", 24, "x", [[0xAA, 0x11, 0x08, 0xBB, 0x33, 0x4F]], make_segs)
    res = ir_synth.synthesize(_model(), str(tmp_path), {"mode": "A", "temp": 25, "power": "x"})
    assert res["template"].stem == "A_24_x"          # 가장 가까운 온도 선택
    decoded = ir_codec.segs_to_byteframes(res["segs"])
    assert decoded == res["target"]
    assert decoded[0][2] == 25 - 16                  # 온도 바이트
    assert sum(decoded[0]) == 0x200                  # 체크섬 합 보존

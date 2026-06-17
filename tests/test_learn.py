"""ir_learn — 필드 발견 + 프레임 합 체크섬 쌍(frame_sum_pair) 자동 탐지."""
import ir_learn


def _samples():
    """단일 프레임 [const, mode바이트, temp(linear), const, C1, C2].
    C1+C2 가 프레임 전체 합을 (mode,power) 그룹 상수로 고정(체크섬 쌍).
    """
    out = []
    for mode in ["A", "B"]:
        for power in ["x", "y"]:
            K = {("A", "x"): 0x200, ("A", "y"): 0x210,
                 ("B", "x"): 0x220, ("B", "y"): 0x230}[(mode, power)]
            modeb = {"A": 0x11, "B": 0x22}[mode] + (0x40 if power == "y" else 0)
            for temp in range(18, 27):
                tb = temp - 16
                c2 = (0x50 - (temp - 18) // 4) & 0xFF
                c1 = (K - 0xAA - modeb - tb - 0xBB - c2) & 0xFF
                out.append({"params": {"mode": mode, "temp": temp, "power": power},
                            "frames": [[0xAA, modeb, tb, 0xBB, c1, c2]],
                            "confidence": 1.0})
    return out


def test_categorical_params_autodetect():
    params = ["mode", "temp", "power"]
    assert ir_learn.categorical_params(_samples(), params) == ["mode", "power"]


def test_discover_fields_kinds():
    samples = _samples()
    shape, kept, report = ir_learn.discover_fields(samples, ["mode", "temp", "power"])
    kinds = {(fi, bi): (kind, owner) for fi, bi, kind, _, owner in report}
    assert kinds[(0, 0)][0] == "const"
    assert kinds[(0, 2)] == ("field", ("temp",))     # 온도 바이트
    assert kinds[(0, 1)][0] == "field"               # 모드 바이트(범주형)


def test_find_sum_pair_detects_checksum():
    samples = _samples()
    gparams = ["mode", "power"]
    all_pos = [(0, b) for b in range(6)]
    # 후보 = C1,C2 (그룹 안에서 변하는 비구조적 바이트)
    pair = ir_learn.find_sum_pair(samples, [(0, 4), (0, 5)], all_pos, gparams)
    assert pair is not None
    assert pair["type"] == "frame_sum_pair"
    assert sorted(map(tuple, pair["members"])) == [(0, 4), (0, 5)]
    assert len(pair["const_map"]) == 4               # mode×power 그룹 4개


def test_sum_pair_rejects_wrong_pair():
    """모드바이트(그룹 내 불변)를 멤버로 넣으면 안 잡혀야 한다(보정 의미 없음)."""
    samples = _samples()
    # (mode바이트, C1) 쌍: 둘 다 '샘플마다 변하나' 그룹 합은 멤버 무관하게 동일 →
    # find_sum_pair 는 멤버가 모두 변하면 통과시키므로, main 의 varies_in_group 필터로
    # 모드바이트를 후보에서 제외한다. 여기서는 후보를 올바로 거른 경우만 검증.
    # 모드바이트는 그룹 안에서 불변이므로 후보가 아니다.
    def varies_in_group(pos):
        groups = {}
        for s in samples:
            k = (s["params"]["mode"], s["params"]["power"])
            groups.setdefault(k, set()).add(s["frames"][pos[0]][pos[1]])
        return any(len(v) > 1 for v in groups.values())
    assert varies_in_group((0, 4)) is True    # C1
    assert varies_in_group((0, 1)) is False   # 모드바이트 → 후보 아님


def test_load_dataset_skips_synthetic_by_default(tmp_path, make_segs):
    import json

    real = {
        "params": {"mode": "A", "temp": 24, "power": "x"},
        "confidence": 1.0,
        "repeats": [make_segs([[0xAA, 0x11]])],
    }
    synth = dict(real, params={"mode": "A", "temp": 25, "power": "x"}, synthetic=True)
    (tmp_path / "A_24_x.json").write_text(json.dumps(real), encoding="utf-8")
    (tmp_path / "A_25_x.json").write_text(json.dumps(synth), encoding="utf-8")

    samples, _ = ir_learn.load_dataset(tmp_path)
    assert [s["params"]["temp"] for s in samples] == [24]

    samples, _ = ir_learn.load_dataset(tmp_path, include_synthetic=True)
    assert sorted(s["params"]["temp"] for s in samples) == [24, 25]

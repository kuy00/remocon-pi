#!/usr/bin/env python3
"""pigpio 기반 IR 하드웨어 I/O 공용 모듈.

수신(RX)·송신(TX)의 저수준 pigpio 코드를 한곳에 모은다. CLI 스크립트들
(`ir_collect`/`ir_monitor`=수신, `ir_send`/`ir_synth`=송신)이 여기서 가져다 쓴다.
순수 로직 모듈(`ir_codec` 등)은 이 모듈을 import 하지 않으므로 pigpio 없이 테스트된다.
"""
import time
from collections import deque

import pigpio

import config

# ── 공통 상수 ────────────────────────────────────────────
FRAME_GAP_US = config.FRAME_GAP_US   # 이보다 긴 무신호 = 전송 1회(프레임 묶음) 경계
MIN_EDGES = 40                       # 노이즈로 보지 않을 최소 엣지 수
CARRIER_HZ = config.CARRIER_HZ       # IR 캐리어 주파수(38kHz)
DUTY = 0.5                           # 캐리어 듀티비


# ── 수신(RX): 갭 기반 프레임 수집기 ─────────────────────
class GapFramedCollector:
    """헤더 무관 — 조용한 갭으로 전송 1회(=프레임 묶음)를 구분해 수집."""

    def __init__(self, pi, gpio):
        self.pi = pi
        self.gpio = gpio
        self.edges = deque()
        self.last_edge_mono = time.time()
        self.frames = deque(maxlen=50)
        self.cb = None

    def start(self):
        self.pi.set_mode(self.gpio, pigpio.INPUT)
        self.pi.set_pull_up_down(self.gpio, pigpio.PUD_UP)
        self.pi.set_glitch_filter(self.gpio, config.GLITCH_US)
        self.cb = self.pi.callback(self.gpio, pigpio.EITHER_EDGE, self._cb)

    def stop(self):
        if self.cb:
            self.cb.cancel()
            self.cb = None

    def clear(self):
        self.edges.clear()
        self.frames.clear()

    def _cb(self, gpio, level, tick):
        self.edges.append((level, tick))
        self.last_edge_mono = time.time()

    def poll(self):
        """주기적으로 호출 — 조용한 갭이 지나면 누적 엣지를 프레임으로 확정."""
        if len(self.edges) < 2:
            return
        quiet_us = (time.time() - self.last_edge_mono) * 1_000_000
        if quiet_us < FRAME_GAP_US:
            return
        local = list(self.edges)
        self.edges.clear()
        if len(local) < MIN_EDGES:
            return
        segs = []
        for i in range(1, len(local)):
            lvl, t0 = local[i - 1]
            _, t1 = local[i]
            segs.append((lvl, pigpio.tickDiff(t0, t1)))
        self.frames.append(segs)

    def wait_one(self, timeout=None):
        t_end = time.time() + timeout if timeout else None
        while not self.frames:
            self.poll()
            if t_end and time.time() > t_end:
                return None
            time.sleep(0.005)
        return self.frames.popleft()


# ── 송신(TX): segs → 38kHz 파형 ─────────────────────────
def build_carrier(gpio, micros):
    """micros 동안 지속되는 38kHz 캐리어 버스트(mark)용 펄스 리스트 생성."""
    wf = []
    cycle_us = 1_000_000.0 / CARRIER_HZ          # 약 26.3us
    on_us = int(round(cycle_us * DUTY))
    cycles = int(round(micros / cycle_us))
    sofar = 0
    for c in range(cycles):
        target = int(round((c + 1) * cycle_us))
        off_us = target - sofar - on_us
        sofar = target
        wf.append(pigpio.pulse(1 << gpio, 0, on_us))      # 핀 HIGH
        wf.append(pigpio.pulse(0, 1 << gpio, off_us))     # 핀 LOW
    return wf


def build_space(gpio, micros):
    """micros 동안 캐리어 OFF(space)."""
    return [pigpio.pulse(0, 1 << gpio, int(micros))]


def segs_to_wave(pi, gpio, segs):
    """[ [level,us], ... ] → pigpio 파형 생성, wave id 반환."""
    pulses = []
    for level, us in segs:
        if us <= 0:
            continue
        if level == 0:                 # 저장 level 0 = mark = 캐리어 ON
            pulses += build_carrier(gpio, us)
        else:                          # level 1 = space = 캐리어 OFF
            pulses += build_space(gpio, us)
    pi.wave_add_generic(pulses)
    return pi.wave_create()


def transmit_segs(pi, gpio, segs):
    """단일 segs(프레임 묶음)를 송신."""
    wid = segs_to_wave(pi, gpio, segs)
    pi.wave_send_once(wid)
    while pi.wave_tx_busy():
        time.sleep(0.002)
    pi.wave_delete(wid)

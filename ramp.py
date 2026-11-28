import array
import math
import os

from pywayland.client import Display
from pywayland.protocol.wayland import WlOutput
from pywayland.protocol.wlr_gamma_control_unstable_v1 import ZwlrGammaControlManagerV1

from display import ANCHORS


def gain_at(level, curve):
    for i, right in enumerate(ANCHORS[1:]):
        if level <= right:
            left = ANCHORS[i]
            t = max(0, (level - left) / (right - left))
            return curve[i] + (curve[i + 1] - curve[i]) * t
    return curve[-1]


def brightness_gain(level, curve, output):
    low, middle, high = ANCHORS
    if not output.source_curve:
        return gain_at(level, curve)
    if level <= low:
        return curve[0] * output.panel_luminance(level) / output.panel_luminance(low)
    if level >= middle or not 0 < curve[0] < curve[1]:
        return gain_at(level, curve)

    # Interpolate log gain so a slider step makes a proportional change in light.
    # Match the slopes of the panel curve at low and the linear segment at middle.
    span = middle - low
    start, end = math.log(curve[0]), math.log(curve[1])
    slope_low = (output.ceiling - output.floor) / (100 * output.panel_luminance(low))
    slope_middle = (curve[2] - curve[1]) / ((high - middle) * curve[1])
    # Ordered Bezier controls keep even steep custom calibrations monotone.
    half_range = (end - start) / 2
    first = start + min(half_range, max(0, slope_low * span / 3))
    second = end - min(half_range, max(0, slope_middle * span / 3))
    t = (level - low) / span
    u = 1 - t
    return math.exp(u**3 * start + 3 * u**2 * t * first + 3 * u * t**2 * second + t**3 * end)


def pq_table(size, gain, peak):
    # Invert ST 2084, apply gain in light, then encode to PQ again.
    # Above paper white, ease a positive boost back to the native peak.
    m1, m2 = 2610 / 16384, 2523 / 32
    c1, c2, c3 = 3424 / 4096, 2413 / 128, 2392 / 128
    white = 203
    gain = min(gain, max(1, peak * .98 / white))
    table = array.array("H")
    for i in range(size):
        v = (i / (size - 1)) ** (1 / m2)
        light = 10000 * (max(v - c1, 0) / (c2 - c3 * v)) ** (1 / m1)
        mapped = light * gain
        if gain > 1 and light > white and peak > white:
            x = min(1, (light - white) / (peak - white))
            slope = gain * (peak - white) / (peak - white * gain)
            mapped = white * gain + (peak - white * gain) * slope * x / (1 + (slope - 1) * x)
            mapped += max(0, light - peak)
        y = (mapped / 10000) ** m1
        table.append(round(65535 * min(1, ((c1 + c2 * y) / (1 + c3 * y)) ** m2)))
    return table.tobytes() * 3


class Ramp:
    def __init__(self, name):
        self.display = Display()
        self.display.connect()
        self.registry = self.display.get_registry()
        self.outputs = {}
        self.manager = None
        self.registry.dispatcher["global"] = self.bind
        self.display.roundtrip()
        self.display.roundtrip()
        if not self.manager or name not in self.outputs:
            raise RuntimeError("Output color control unavailable")
        self.control = self.manager.get_gamma_control(self.outputs[name])
        self.size = 0
        self.control.dispatcher["gamma_size"] = self.ready
        self.control.dispatcher["failed"] = lambda *_: self.ready(None, 0)
        self.display.roundtrip()
        if self.size < 2:
            raise RuntimeError("Display color table is unavailable or in use")

    def bind(self, registry, index, iface, version):
        if iface == WlOutput.name and version >= 4:
            out = registry.bind(index, WlOutput, 4)
            self.outputs[index] = out
            out.dispatcher["name"] = lambda obj, name: self.outputs.update({name: obj})
        if iface == ZwlrGammaControlManagerV1.name:
            self.manager = registry.bind(index, ZwlrGammaControlManagerV1, 1)

    def ready(self, _, size):
        self.size = size

    def set(self, data):
        with os.fdopen(os.memfd_create("omarchy-hdr"), "wb") as f:
            f.write(data)
            f.flush()
            f.seek(0)
            self.control.set_gamma(f.fileno())
            self.display.roundtrip()
        if not self.size:
            raise RuntimeError("Display rejected the color table")

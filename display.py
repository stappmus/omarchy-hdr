import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/hdr"
RUNTIME = Path(os.environ["XDG_RUNTIME_DIR"]) / "omarchy-hdr"
ANCHORS = (5, 50, 100)


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=12).strip()


def monitors():
    return json.loads(run("hyprctl", "-j", "monitors"))


class Output:
    def __init__(self, name):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("Invalid display name")
        self.name = name
        edid = next((p for p in Path("/sys/class/drm").glob(f"card*-{name}/edid") if p.read_bytes()), None)
        if not edid:
            raise ValueError("Display identification unavailable")
        try:
            info = run("edid-decode", str(edid))
        except FileNotFoundError:
            raise RuntimeError("Install v4l-utils to detect HDR displays.") from None
        if not re.search(r"BT.2020/SMPTE ST 2084|SMPTE ST\s*2084", info):
            raise ValueError("This display does not advertise HDR")
        self.edid = edid
        self.fingerprint = hashlib.sha256(edid.read_bytes()).hexdigest()
        # Omarchy assigns #2, #3, etc. when identical monitors omit their serial.
        identities = STATE.parent / "monitors/workspaces"
        suffix = ""
        if identities.exists():
            for record in identities.read_text().splitlines():
                fields = record.split("\t", 2)
                if len(fields) == 3 and fields[1] == name:
                    duplicate = re.search(r" #(\d+)$", fields[2])
                    if duplicate: suffix = "-" + duplicate[1]
                    break
        self.file = STATE / (self.fingerprint + suffix + ".json")
        self.socket = RUNTIME / name
        self.white_cache = RUNTIME / (self.file.stem + ".white")
        self.backlight = next((d for d in Path("/sys/class/backlight").glob("*")
                               if (d / "device").resolve() == edid.parent.resolve()), None)

        def nits(pattern):
            m = re.search(pattern, info)
            return float(m[1]) if m else None

        self.peak = nits(r"Native Maximum Luminance \(10% Rectangular Coverage\): ([\d.]+)") or nits(r"Desired content max luminance:.*?\(([\d.]+)")
        self.full = nits(r"Native Maximum Luminance \(Full Coverage\): ([\d.]+)") or nits(r"Desired content max frame-average luminance:.*?\(([\d.]+)")
        self.floor = nits(r"Minimum SDR Luminance \(Full Coverage\): ([\d.]+)") or 5
        self.ceiling = nits(r"Maximum Suggested SDR Luminance \(Full Coverage\): ([\d.]+)") or min(self.full or 203, 203)
        # The Dell OLED advertises source-controlled luminance in DisplayID.
        # These are starting estimates; the visual match overrides each point.
        self.source_curve = self.backlight is not None and "Luminance Information: Guidance for the Source device" in info

    def panel_luminance(self, level):
        return self.floor + (self.ceiling - self.floor) * level / 100

    def settings(self):
        settings = json.loads(self.file.read_text()) if self.file.exists() else {"on": False}
        if self.source_curve:
            original = [self.panel_luminance(level) / 203 for level in ANCHORS]
            # Apply the dimmer floor to new and previously unmodified panel curves.
            if settings.get("curve", original) == original:
                settings["curve"] = [original[0] / 8, *original[1:]]
        else:
            settings.setdefault("curve", [1.0] * len(ANCHORS))
        return settings

    def save(self, settings):
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(settings) + "\n")
        tmp.replace(self.file)

    def connected(self):
        try:
            return hashlib.sha256(self.edid.read_bytes()).hexdigest() == self.fingerprint
        except OSError:
            return False

    def brightness(self):
        if self.backlight:
            level = int((self.backlight / "brightness").read_text())
            return round(100 * level / int((self.backlight / "max_brightness").read_text()))
        return int(run("omarchy-brightness-display", "--monitor", self.name))

    def set_brightness(self, value):
        run("omarchy-brightness-display", "--no-osd", "--monitor", self.name, f"{value}%")

    def set_sdr_white(self, nits):
        reply = run("hyprctl", "eval", "hl.monitor({output=" + json.dumps(self.name) +
                    ",sdr_max_luminance=" + str(nits) + "})")
        if reply not in ("", "ok", "nil"):
            raise RuntimeError(reply)
        tmp = self.white_cache.with_suffix(".tmp")
        tmp.write_text(str(nits))
        tmp.replace(self.white_cache)

    def mode(self, hdr, white=203):
        args = (json.dumps(self.name), "true" if hdr else "false",
                str(round(self.peak or 0)), str(round(self.full or 0)), str(white))
        reply = run("hyprctl", "eval", "o.hdr_mode(" + ",".join(args) + ")")
        if reply not in ("", "ok", "nil"):
            raise RuntimeError(reply)

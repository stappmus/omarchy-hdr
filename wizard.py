import json
import math
import os
import subprocess
import socket
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QColorSpace, QPainter, QRasterWindow, QSurfaceFormat
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QMessageBox, QSlider, QVBoxLayout, QWidget)
from display import ANCHORS, monitors, run


class GrayCard(QRasterWindow):
    """A native Wayland subsurface with its own declared color space."""
    def __init__(self, hdr):
        super().__init__()
        fmt = QSurfaceFormat()
        if hdr:
            fmt.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.Bt2100Pq))
        else:
            fmt.setColorSpace(QColorSpace(QColorSpace.Primaries.SRgb, QColorSpace.TransferFunction.Gamma, 2.2))
        self.setFormat(fmt)
        self.shades = []
        for reflectance in (1.0, .50, .18, .04):
            if hdr:
                light = (203 * reflectance / 10000) ** (2610 / 16384)
                code = ((3424 / 4096 + 2413 / 128 * light) / (1 + 2392 / 128 * light)) ** (2523 / 32)
            else:
                code = reflectance ** (1 / 2.2)
            self.shades.append(QColor.fromRgbF(code, code, code))

    def paintEvent(self, event):
        painter = QPainter(self)
        for i, color in enumerate(self.shades):
            top = i * self.height() // len(self.shades)
            bottom = (i + 1) * self.height() // len(self.shades)
            painter.fillRect(0, top, self.width(), bottom - top, color)
        painter.end()


def calibrate(out):
    app = QApplication([])
    if app.platformName() != "wayland":
        raise RuntimeError("Open calibration in the native Wayland session.")
    try:
        out.brightness()
        fixed = False
    except (ValueError, subprocess.SubprocessError):
        fixed = True
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as sock:
        sock.settimeout(12)
        sock.connect(str(out.socket))
        state = json.loads(sock.recv(1024))
        if not isinstance(state, dict):
            raise RuntimeError("The HDR service needs to be updated. Run the HDR installer again.")
        if "error" in state:
            raise RuntimeError(state["error"])
        white = state["sdr_white"]
        dlg = QDialog()
        dlg.setWindowTitle("Calibrate HDR")
        dlg.setMinimumWidth(420)
        screen = next((s for s in app.screens() if s.name() == out.name), None)
        if screen: dlg.setScreen(screen)
        layout = QVBoxLayout(dlg)
        heading = QLabel()
        layout.addWidget(heading)
        layout.addWidget(QLabel("Adjust the SDR side until the bands match the HDR reference."))
        pair = QHBoxLayout()
        pair.setSpacing(0)
        cards = (GrayCard(False), GrayCard(True))
        for surface, title in zip(cards, ("SDR · adjustable", "HDR · reference")):
            column = QVBoxLayout()
            label = QLabel(title)
            label.setAlignment(Qt.AlignCenter)
            column.addWidget(label)
            card = QWidget.createWindowContainer(surface, dlg)
            card.setMinimumSize(180, 144)
            column.addWidget(card)
            pair.addLayout(column, 1)
        layout.addLayout(pair)
        slider = QSlider(Qt.Horizontal)
        slider.setAccessibleName("SDR brightness")
        slider.setRange(-200, min(150, math.floor(100 * math.log2(max(203, out.peak or 575) / 203))))
        layout.addWidget(slider)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        advance = buttons.addButton("Next", QDialogButtonBox.AcceptRole)
        layout.addWidget(buttons)
        dlg.setFixedSize(layout.sizeHint())
        index = 1 if fixed else 0
        timer = QTimer(dlg)
        timer.setSingleShot(True)

        def request(*message):
            try:
                sock.send(json.dumps(message).encode())
                error = json.loads(sock.recv(1024))
                if error: raise RuntimeError(error)
                return True
            except (ValueError, OSError, RuntimeError) as e:
                QMessageBox.warning(dlg, "HDR calibration", str(e))
                dlg.reject()
                return False

        def show_step():
            timer.stop()
            slider.blockSignals(True)
            slider.setValue(round(100 * math.log2(white[index] / 203)))
            slider.blockSignals(False)
            level = "Display brightness" if fixed else f"{('Low', 'Medium', 'High')[index]} brightness · {ANCHORS[index]}%"
            heading.setText(level)
            advance.setText("Save" if fixed or index == 2 else "Next")
            request("match", index, white[index])

        def adjust(value):
            white[index] = round(203 * 2 ** (value / 100))
            timer.start(45)

        def next_step():
            nonlocal index
            timer.stop()
            if not request("match", index, white[index]): return
            if fixed or index == 2:
                if request("save"): dlg.accept()
            else:
                index += 1
                show_step()

        def reset():
            timer.stop()
            if request("reset"): dlg.accept()

        slider.valueChanged.connect(adjust)
        timer.timeout.connect(lambda: request("match", index, white[index]))
        advance.clicked.connect(next_step)
        buttons.rejected.connect(dlg.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(reset)
        def begin():
            # A Wayland window's preferred screen is a hint. Hyprland owns placement.
            try:
                target = next(m for m in monitors() if m["name"] == out.name)
                window = next(w for w in json.loads(run("hyprctl", "-j", "clients"))
                              if w["pid"] == os.getpid() and w["mapped"])
                if window["monitor"] != target["id"]:
                    run("hyprctl", "dispatch", "hl.dsp.window.move({window=" +
                        json.dumps("pid:" + str(os.getpid())) + ",monitor=" + json.dumps(out.name) + "})")
                show_step()
            except (StopIteration, ValueError, OSError, subprocess.SubprocessError):
                QMessageBox.warning(dlg, "HDR calibration", "Could not open the reference on the selected display.")
                dlg.reject()
        QTimer.singleShot(160, begin)
        dlg.exec()

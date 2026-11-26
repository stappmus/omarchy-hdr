# HDR Display for Omarchy

Keep an HDR-capable display in HDR when switching between desktop apps, games and video. The plugin adds a **High dynamic range** switch to Omarchy’s existing Display panel. Each display gets its own setting. The regular brightness controls stay in the same place.

## Install

On Omarchy with the Lua-based Hyprland configuration and Quickshell Display panel, run:

```sh
omarchy plugin add https://github.com/stappmus/omarchy-hdr --enable
```

Open **Display** and turn on **High dynamic range** for the selected screen. The switch appears on displays that advertise PQ HDR. The plugin uses the Display widget’s existing position when enabled without an explicit placement. If you use a custom Display clone, disable that clone first.

The plugin needs Arch packages `v4l-utils` (EDID detection), `uv`, `pyside6` and `wayland`. If any are missing, install them with `sudo pacman -S --needed v4l-utils uv pyside6 wayland`. First activation downloads PyWayland 0.4.19 and its dependencies into a private Python environment under the user's data directory. Subsequent launches use that environment. A writable Wayland gamma table is required.

## Calibrate

With HDR on, select the **sliders icon** beside the switch. The display stays in HDR. Adjust the SDR card on the left until its white and gray bands match the HDR reference on the right. Choose **Next** at low and medium brightness, then **Save** at full brightness. Closing the wizard restores the starting brightness. Cancel discards the preview. A fixed-brightness screen uses one comparison.

The cards are separate native Wayland surfaces. They share a 203-nit reference white before the display’s brightness curve. Highlights can extend above ordinary white. The plugin uses panel-reported luminance when available. Actual emitted brightness depends on the display. External monitors that ignore backlight adjustments in HDR may show little change from the brightness keys with the default curve. Applications need their own HDR output support.

## Update or remove

```sh
omarchy plugin update stappmus.hdr
omarchy plugin remove stappmus.hdr
```

Disabling the plugin releases its active outputs and restores the stock Display widget. Saved preferences remain in `~/.local/state/omarchy/hdr`. The private Python environment can be removed from `~/.local/share/omarchy-hdr` after uninstall. The XDG state and data directory overrides are honored.

The Display panel is based on Omarchy’s MIT-licensed monitor widget. The gamma-control protocol retains its upstream license in the XML file.

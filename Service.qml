import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland

Item {
  Process {
    id: session
    command: [Quickshell.env("HOME") + "/.config/omarchy/plugins/stappmus.hdr/omarchy-hdr", "session"]
    stdinEnabled: true
    running: true
  }
  Connections {
    target: Hyprland
    function onRawEvent(event) {
      if (session.running && ["configreloaded", "monitoradded", "monitorremoved"].includes(event.name))
        session.write("restore\n")
    }
  }
}

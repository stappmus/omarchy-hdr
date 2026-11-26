import argparse
import contextlib
import fcntl
import json
import os
import os
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from display import ANCHORS, Output, RUNTIME, STATE, monitors, run


def serve(out):
    from ramp import Ramp, gain_at, pq_table
    ramp = Ramp(out.name)
    settings = out.settings()
    curve = settings["curve"]
    white = settings.get("sdr_white", [203] * 3)
    original = None
    client = None
    last = None
    last_white = None
    step_index = None
    fixed = False

    def apply(level):
        nonlocal last, last_white
        gain = gain_at(level, curve)
        if level < ANCHORS[0] and out.source_curve:
            gain *= (out.floor + level * (out.ceiling - out.floor) / 100) / (out.defaults()[0] * 203)
        if gain != last:
            ramp.set(pq_table(ramp.size, gain, out.peak or 1000))
            last = gain

        nits = round(gain_at(level, white))
        if nits != last_white:
            out.set_sdr_white(nits)
            last_white = nits

    def end_preview():
        nonlocal client, original, white, step_index, fixed
        if client:
            client.close()
            client = None
        if original is not None and out.connected():
            if not fixed: out.set_brightness(original)
            white = settings.get("sdr_white", [203] * 3)
            apply(original)
        original = None
        step_index = None
        fixed = False

    def stop(*_):
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
        try:
            if next(m["colorManagementPreset"] for m in monitors() if m["name"] == out.name) != "hdr":
                out.mode(True)
            for _ in range(20):
                if any(m["name"] == out.name and m["colorManagementPreset"] == "hdr"
                       and "10" in m["currentFormat"] for m in monitors()):
                    break
                time.sleep(.05)
            else:
                raise RuntimeError("Hyprland could not activate 10-bit HDR")
            # Equal anchors need no brightness read, including on TVs with locked DDC.
            apply(out.brightness() if len(set(curve)) > 1 or len(set(white)) > 1 else 50)
            out.socket.unlink(missing_ok=True)
            server.bind(str(out.socket))
            server.listen(2)
            run("hyprctl", "eval", f"o.hdr_games({json.dumps(out.name)}, true)")
            print("ready", flush=True)
            event = (out.backlight / "actual_brightness").open() if out.backlight else None
            if event: event.read()
            while ramp.size:
                tracking = not client and (len(set(curve)) > 1 or len(set(white)) > 1)
                interval = 2 if tracking and not event else None
                ready, _, changed = select.select([server, sys.stdin, ramp.display.get_fd()] + ([client] if client else []),
                                                  [], [event] if event else [], interval)
                if sys.stdin in ready:
                    break  # The shell session closed its end of the pipe.
                if changed:
                    event.seek(0)
                    event.read()
                if ramp.display.get_fd() in ready:
                    ramp.display.dispatch(block=True)
                if (changed or not ready) and tracking and ramp.size:
                    apply(out.brightness())
                if server in ready:
                    conn, _ = server.accept()
                    if client:
                        conn.send(b'{"error":"Calibration is already open"}')
                        conn.close()
                    else:
                        client = conn
                        client.send(json.dumps({"sdr_white": white}).encode())
                if client and client in ready:
                    try:
                        packet = client.recv(1024)
                    except ConnectionError:
                        packet = b""
                    if not packet:
                        end_preview()
                        continue
                    try:
                        action, *args = json.loads(packet)
                        if action == "match":
                            idx, nits = args
                            if idx not in range(3) or not isinstance(nits, int) or not 10 <= nits <= 2000:
                                raise ValueError("Invalid calibration level")
                            if original is None:
                                try:
                                    original = out.brightness()
                                except (ValueError, subprocess.SubprocessError):
                                    fixed = True
                                    original = 50
                                white = list(white)
                            if not fixed and step_index != idx:
                                out.set_brightness(ANCHORS[idx])
                            step_index = idx
                            if fixed: white[:] = [nits] * 3
                            else: white[idx] = nits
                            apply(ANCHORS[idx])
                        elif action == "save" and original is not None:
                            settings["sdr_white"] = list(white)
                            out.save({**settings, "on": True})
                        elif action == "reset":
                            settings.pop("sdr_white", None)
                            white = [203] * 3
                            out.save({**settings, "on": True})
                            apply(50 if fixed else out.brightness())
                        else:
                            raise ValueError("Unknown calibration action")
                        reply = None
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
                        reply = str(e)
                    try:
                        client.send(json.dumps(reply).encode())
                    except ConnectionError:
                        end_preview()
        finally:
            out.socket.unlink(missing_ok=True)
            if client: client.close()
            out.white_cache.unlink(missing_ok=True)
            ramp.display.disconnect()
            with contextlib.suppress(subprocess.SubprocessError):
                run("hyprctl", "eval", f"o.hdr_games({json.dumps(out.name)}, false)")
            if out.connected():
                try:
                    if original is not None and not fixed: out.set_brightness(original)
                finally:
                    out.mode(False)


def session():
    launcher = Path(__file__).with_name("omarchy-hdr")
    workers = {}

    def stop(name):
        worker = workers.pop(name, None)
        if worker is not None:
            worker.stdin.close()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
            worker.stdout.close()

    def start(out):
        lua = Path(__file__).with_name("hdr.lua")
        reply = run("hyprctl", "eval", "dofile(" + json.dumps(str(lua)) + ")")
        if reply not in ("", "ok", "nil"):
            raise RuntimeError(reply)
        worker = workers.get(out.name)
        if worker and worker.poll() is None:
            white = int(out.white_cache.read_text()) if out.white_cache.exists() else 203
            out.mode(True, white)
            run("hyprctl", "eval", f"o.hdr_games({json.dumps(out.name)}, true)")
            return
        stop(out.name)
        worker = subprocess.Popen([launcher, "serve", out.name], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        workers[out.name] = worker
        if not select.select([worker.stdout], [], [], 15)[0]:
            stop(out.name)
            raise RuntimeError("Timed out starting HDR")
        line = worker.stdout.readline().strip()
        if line != "ready":
            stop(out.name)
            raise RuntimeError(line or "Could not start HDR")

    def restore():
        if not STATE.exists() and not workers: return
        names = {m["name"] for m in monitors() if not m["disabled"]}
        for name in workers.keys() - names:
            stop(name)
        for name in names:
            try:
                out = Output(name)
                if out.settings()["on"]: start(out)
            except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as e:
                if not isinstance(e, ValueError): print(e, file=sys.stderr)

    def shutdown(*_):
        raise SystemExit

    signal.signal(signal.SIGTERM, shutdown)
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    endpoint = RUNTIME / ".session"
    with (RUNTIME / ".session.lock").open("a") as lock, socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
        fcntl.flock(lock, fcntl.LOCK_EX)
        endpoint.unlink(missing_ok=True)
        server.bind(str(endpoint))
        server.listen(4)
        try:
            restore()
            while True:
                readable, _, _ = select.select([server, sys.stdin], [], [])
                if sys.stdin in readable:
                    events = os.read(sys.stdin.fileno(), 4096)
                    if not events: break
                    restore()
                if server in readable:
                    conn, _ = server.accept()
                    with conn:
                        conn.settimeout(2)
                        try:
                            action, name = json.loads(conn.recv(1024))
                            out = Output(name)
                            if action == "on": start(out)
                            elif action == "off": stop(name)
                            else: raise ValueError("Invalid HDR action")
                            out.save({**out.settings(), "on": action == "on"})
                            if action == "off":
                                run("hyprctl", "reload")  # Restore the user's original monitor rules.
                            error = None
                        except (ValueError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as e:
                            error = str(e)
                        with contextlib.suppress(ConnectionError):
                            conn.send(json.dumps(error).encode())
        finally:
            if workers:
                for name in list(workers): stop(name)
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    run("hyprctl", "reload")
            endpoint.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["on", "off", "status", "calibrate", "serve", "session"])
    parser.add_argument("output", nargs="?")
    args = parser.parse_args()
    if args.action == "session":
        session()
        return
    out = Output(args.output or next(m["name"] for m in monitors() if m["focused"]))
    if args.action == "serve":
        serve(out)
    elif args.action == "status":
        print("on" if out.socket.exists() and any(m["name"] == out.name and m["colorManagementPreset"] == "hdr" for m in monitors()) else "off")
    elif args.action == "calibrate":
        from wizard import calibrate
        calibrate(out)
    else:
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
            conn.settimeout(25)
            conn.connect(str(RUNTIME / ".session"))
            conn.send(json.dumps([args.action, out.name]).encode())
            error = json.loads(conn.recv(4096))
            if error: raise RuntimeError(error)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(getattr(error, "stderr", None) or str(error), file=sys.stdout if sys.argv[1:2] == ["serve"] else sys.stderr)
        sys.exit(2 if isinstance(error, RuntimeError) else 1)

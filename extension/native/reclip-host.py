#!/usr/bin/env python3
"""
Cross-platform native messaging host for ClipDown extension.
Allows the Chrome extension to start/stop/check the ClipDown server.
"""
import sys
import os
import json
import struct
import socket
import platform
import subprocess
import tempfile

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_DIR = tempfile.gettempdir()
PID_FILE = os.path.join(TMP_DIR, "reclip.pid")
LOG_FILE = os.path.join(TMP_DIR, "reclip.log")

IS_WINDOWS = platform.system() == "Windows"
SERVER_PORT = 8899


def send_message(msg):
    """Send a message to the extension via stdout."""
    encoded = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def read_message():
    """Read a message from the extension via stdin."""
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    length = struct.unpack("=I", raw_length)[0]
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def is_port_in_use(port):
    """Cross-platform port check using socket."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.settimeout(0.5)
            result = s.connect_ex(("127.0.0.1", port))
            return result == 0
        except Exception:
            return False


def is_server_running():
    """Check if the server is running."""
    if is_port_in_use(SERVER_PORT):
        # Try to read PID file for info
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, "r") as f:
                    return True, f.read().strip()
            except Exception:
                pass
        return True, None
    return False, None


def start_server():
    """Start the ClipDown server."""
    running, pid = is_server_running()
    if running:
        return {"status": "already_running", "pid": pid}

    if IS_WINDOWS:
        script = os.path.join(REPO_DIR, "reclip.bat")
        if not os.path.exists(script):
            return {"status": "error", "message": f"reclip.bat not found at {script}"}
        try:
            with open(LOG_FILE, "w") as logf:
                # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS for Windows
                proc = subprocess.Popen(
                    ["cmd", "/c", script],
                    stdout=logf, stderr=logf,
                    cwd=REPO_DIR,
                    creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                )
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        script = os.path.join(REPO_DIR, "reclip.sh")
        if not os.path.exists(script):
            return {"status": "error", "message": f"reclip.sh not found at {script}"}
        try:
            with open(LOG_FILE, "w") as logf:
                proc = subprocess.Popen(
                    ["/bin/bash", script],
                    stdout=logf, stderr=logf,
                    cwd=REPO_DIR,
                    start_new_session=True,
                )
        except Exception as e:
            return {"status": "error", "message": str(e)}

    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except Exception:
        pass

    return {"status": "started", "pid": proc.pid}


def stop_server():
    """Stop the ClipDown server."""
    running, _ = is_server_running()
    if not running:
        return {"status": "not_running"}

    # Find PID by port
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5
            )
            pids = set()
            for line in result.stdout.splitlines():
                if f":{SERVER_PORT}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pids.add(parts[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid], timeout=5)
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{SERVER_PORT}"],
                capture_output=True, text=True, timeout=3
            )
            for pid in result.stdout.strip().split("\n"):
                if pid:
                    subprocess.run(["kill", "-9", pid], timeout=3)

        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
        return {"status": "stopped"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def status():
    """Report server status."""
    running, pid = is_server_running()
    return {"status": "running" if running else "stopped", "pid": pid}


def main():
    while True:
        msg = read_message()
        if msg is None:
            break

        action = msg.get("action")
        if action == "start":
            send_message(start_server())
        elif action == "stop":
            send_message(stop_server())
        elif action == "status":
            send_message(status())
        elif action == "restart":
            stop_server()
            send_message(start_server())
        else:
            send_message({"status": "error", "message": f"Unknown action: {action}"})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            send_message({"status": "error", "message": str(e)})
        except Exception:
            pass

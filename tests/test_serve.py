"""wasl.serve: SO_REUSEPORT multi-process server starts, serves, restarts a dead worker and stops cleanly."""
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get(port):
    return urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2).read()


def test_serve_workers_balance_and_restart(seeded):
    port = _free_port()
    env = {**os.environ, "WASL_SWEEP_INTERVAL_SECONDS": "0"}
    p = subprocess.Popen([sys.executable, "-m", "wasl.serve", "--port", str(port), "--workers", "2"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    try:
        for _ in range(100):
            try:
                assert b"ok" in _get(port)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("server did not start")
        kids = subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True).stdout.split()
        assert len(kids) == 2
        os.kill(int(kids[0]), signal.SIGKILL)  # a crashed worker is replaced
        time.sleep(1.5)
        kids2 = subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True).stdout.split()
        assert len(kids2) == 2 and kids[0] not in kids2
        assert b"ok" in _get(port)
    finally:
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=15)
    assert p.returncode == 0

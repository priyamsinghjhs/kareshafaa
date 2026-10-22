"""kareshafaa.serve: SO_REUSEPORT multi-process server starts, serves, restarts a dead worker and stops cleanly."""
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
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
    env = {**os.environ, "KARESHAFAA_SWEEP_INTERVAL_SECONDS": "0"}
    p = subprocess.Popen([sys.executable, "-m", "kareshafaa.serve", "--port", str(port), "--workers", "2"], env=env,
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


def test_watchdog_replaces_a_wedged_worker_and_audit_chain_stays_intact(seeded, db, tmp_path):
    """Round 22: a worker that is alive but STUCK (here: SIGSTOP, i.e. it can neither serve nor heartbeat) is
    detected by missed heartbeats, killed and replaced; requests keep being served and audited, and the audit hash
    chain verifies afterwards."""
    from kareshafaa.audit import verify_chain

    port = _free_port()
    env = {**os.environ, "KARESHAFAA_SWEEP_INTERVAL_SECONDS": "0", "KARESHAFAA_HEARTBEAT_DIR": str(tmp_path / "hb"),
           "KARESHAFAA_HEARTBEAT_INTERVAL_S": "0.3", "KARESHAFAA_HEARTBEAT_TIMEOUT_S": "3"}
    p = subprocess.Popen([sys.executable, "-m", "kareshafaa.serve", "--port", str(port), "--workers", "2"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    wedged = None
    try:
        for _ in range(100):
            try:
                assert b"ok" in _get(port)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("server did not start")
        time.sleep(1.0)  # both workers have written heartbeats
        kids = subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True).stdout.split()
        assert len(kids) == 2
        wedged = int(kids[0])
        os.kill(wedged, signal.SIGSTOP)  # alive but stuck: no heartbeat, holds its SO_REUSEPORT socket
        for _ in range(60):
            time.sleep(0.25)
            now = subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True).stdout.split()
            if str(wedged) not in now and len(now) == 2:
                break
        else:
            raise AssertionError("wedged worker was not replaced")
        wedged = None
        for i in range(20):  # audited requests across both (live) workers
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/watchdog-probe-{i}", timeout=5)
            except urllib.error.HTTPError as e:
                assert e.code in (401, 404)
    finally:
        if wedged:
            os.kill(wedged, signal.SIGKILL)
        p.send_signal(signal.SIGTERM)
        _out, err = p.communicate(timeout=20)
    assert b"watchdog" in err and b"missed heartbeats" in err
    assert p.returncode == 0
    db.expire_all()
    chain = verify_chain(db)
    assert chain["valid"], chain

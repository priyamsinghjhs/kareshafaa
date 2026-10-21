"""Multi-process server with kernel load balancing (SO_REUSEPORT).

`uvicorn --workers N` shares ONE listening socket between processes; under a burst the first process to wake
accepts every pending connection, so (measured, Iteration 7) all 10 concurrent clients landed on a single worker
while three sat idle - p95 queue latency 1.2-1.5 s. Here every worker binds its own socket with SO_REUSEPORT and
the Linux kernel spreads connections across them. The parent restarts a worker that dies and forwards SIGTERM/SIGINT.

Round 22 watchdog: a worker can also be STUCK without exiting (round 21: workers blocked on a lock kept their
SO_REUSEPORT socket, so a share of new connections hung). Each worker's event loop writes a heartbeat file every
WASL_HEARTBEAT_INTERVAL_S, but only after a no-op has run through the request thread pool, so a blocked loop and
an exhausted thread pool both stop it. The parent SIGKILLs a worker whose heartbeat is older than
WASL_HEARTBEAT_TIMEOUT_S (default 60 s), after first asking it for a thread dump (SIGUSR1 -> faulthandler, in the
log, so the cause is kept), and starts a replacement. A killed worker's open transaction is rolled back by the
database, so the audit hash chain cannot be half-written.

usage: python -m wasl.serve [--host 127.0.0.1] [--port 8000] [--workers 4]
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import tempfile
import time


def _child(host: str, port: int) -> None:
    import faulthandler

    import uvicorn

    faulthandler.register(signal.SIGUSR1, all_threads=True)  # the watchdog asks for a dump before killing

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind((host, port))
    sock.listen(2048)
    sock.set_inheritable(True)
    server = uvicorn.Server(uvicorn.Config("wasl.main:app", host=host, port=port, log_level="info"))
    server.run(sockets=[sock])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if not hasattr(socket, "SO_REUSEPORT") or a.workers <= 1:
        import uvicorn

        uvicorn.run("wasl.main:app", host=a.host, port=a.port, workers=max(1, a.workers))
        return 0
    children: dict[int, int] = {}
    stopping = False

    def spawn(slot: int) -> None:
        pid = os.fork()
        if pid == 0:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            try:
                _child(a.host, a.port)
            finally:
                os._exit(0)
        children[pid] = slot

    def stop(_sig, _frm) -> None:
        nonlocal stopping
        stopping = True
        for pid in list(children):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    hb_dir = os.environ.setdefault("WASL_HEARTBEAT_DIR", os.path.join(tempfile.gettempdir(), f"wasl-hb-{a.port}"))
    os.makedirs(hb_dir, exist_ok=True)
    hb_timeout = float(os.environ.get("WASL_HEARTBEAT_TIMEOUT_S", "60"))
    started: dict[int, float] = {}
    _orig_spawn = spawn

    def spawn(slot: int) -> None:  # noqa: F811 - record the start time for the heartbeat grace period
        _orig_spawn(slot)
        for pid, sl in children.items():
            if sl == slot and pid not in started:
                started[pid] = time.time()

    def check_heartbeats() -> None:
        now = time.time()
        for pid in list(children):
            try:
                last = os.path.getmtime(os.path.join(hb_dir, str(pid)))
            except OSError:
                last = started.get(pid, now)  # not started yet: the grace period runs from spawn
            if now - last > hb_timeout:
                print(f"wasl.serve: watchdog: worker {pid} missed heartbeats for {now - last:.0f} s; "
                      "thread dump requested, then killed and replaced", file=sys.stderr, flush=True)
                try:
                    os.kill(pid, signal.SIGUSR1)
                    time.sleep(1.0)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                started[pid] = float("inf")  # do not re-kill while it is being reaped

    for i in range(a.workers):
        spawn(i)
    while children:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        except InterruptedError:
            continue
        if pid == 0:
            if not stopping and hb_timeout > 0:
                check_heartbeats()
            time.sleep(0.5)
            continue
        started.pop(pid, None)
        try:
            os.unlink(os.path.join(hb_dir, str(pid)))
        except OSError:
            pass
        slot = children.pop(pid, None)
        if not stopping and slot is not None:
            print(f"wasl.serve: worker {pid} exited; restarting", file=sys.stderr, flush=True)
            time.sleep(0.5)
            spawn(slot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

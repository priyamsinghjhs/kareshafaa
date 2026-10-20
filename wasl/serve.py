"""Multi-process server with kernel load balancing (SO_REUSEPORT).

`uvicorn --workers N` shares ONE listening socket between processes; under a burst the first process to wake
accepts every pending connection, so (measured, Iteration 7) all 10 concurrent clients landed on a single worker
while three sat idle - p95 queue latency 1.2-1.5 s. Here every worker binds its own socket with SO_REUSEPORT and
the Linux kernel spreads connections across them. The parent restarts a worker that dies and forwards SIGTERM/SIGINT.

usage: python -m wasl.serve [--host 127.0.0.1] [--port 8000] [--workers 4]
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import time


def _child(host: str, port: int) -> None:
    import uvicorn

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
    for i in range(a.workers):
        spawn(i)
    while children:
        try:
            pid, _status = os.wait()
        except ChildProcessError:
            break
        except InterruptedError:
            continue
        slot = children.pop(pid, None)
        if not stopping and slot is not None:
            print(f"wasl.serve: worker {pid} exited; restarting", file=sys.stderr, flush=True)
            time.sleep(0.5)
            spawn(slot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

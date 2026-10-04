"""Run both services; forward termination and fail the container if either exits."""

import os
import signal
import subprocess
import sys
import time


def main():
    stopping = False

    def stop(*_args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = []
    code = 1
    try:
        children.append(subprocess.Popen(["node", "raumkernel/main.js"], start_new_session=True))
        children.append(subprocess.Popen([sys.executable, "-m", "qobuz.service"], start_new_session=True))
        while not stopping and all(p.poll() is None for p in children):
            time.sleep(0.2)
        code = 0 if stopping else 1
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        until = time.monotonic() + 8
        for child in children:
            try:
                child.wait(timeout=max(0.1, until - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    return code


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""A vcsim (VMware's vCenter simulator) for the ops-center wall: started on a free local port, in a container of its own
(a unique name), removed by that exact name - never a prune.

  python3 tests/wall/vcsim_demo.py start      prints {"url", "host", "port", "user", "password", "name"} as JSON
  python3 tests/wall/vcsim_demo.py stop NAME  removes that container (podman rm -f NAME)

From Python: start() -> (url, user, password, name); stop(name). The portal's wall reads it with
{"vcenter": {"user": user, "password": password, "hosts": ["127.0.0.1:PORT"], "verify": false}}."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time

IMAGE = "docker.io/vmware/vcsim:v0.56.0"
USER, PASSWORD = "wall-reader", "Wq7nRt2Lm"           # vcsim takes only these (its -username / -password)


def available():
    """podman and the image are here (the image is never pulled by a test)"""
    if not shutil.which("podman"):
        return False
    return subprocess.run(["podman", "image", "exists", IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start(name=None, wait=60):
    name = name or "wall-vcsim-%d-%d" % (os.getpid(), int(time.time() * 1000) % 1000000)
    port = free_port()
    subprocess.run(["podman", "run", "-d", "--name", name, "-p", "127.0.0.1:%d:8989" % port, IMAGE, "-l", "0.0.0.0:8989",
                    "-username", USER, "-password", PASSWORD], check=True, stdout=subprocess.DEVNULL)
    end = time.time() + wait
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 1).close()
            time.sleep(1)                                      # the port answers a moment before /sdk does
            return "https://127.0.0.1:%d/sdk" % port, USER, PASSWORD, name
        except OSError:
            time.sleep(0.3)
    stop(name)
    raise RuntimeError("vcsim did not answer on port %d" % port)


def stop(name):
    if name:
        subprocess.run(["podman", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "start":
        url, u, p, n = start()
        port = int(url.rsplit(":", 1)[1].split("/")[0])
        print(json.dumps({"url": url, "host": "127.0.0.1", "port": port, "user": u, "password": p, "name": n}))
    elif len(sys.argv) == 3 and sys.argv[1] == "stop":
        stop(sys.argv[2])
    else:
        sys.exit(__doc__)

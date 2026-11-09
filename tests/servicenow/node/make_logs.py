#!/usr/bin/env python3
"""Fake node logs: python3 make_logs.py LOGDIR KIND - KIND well (a few SEVERE lines) or bad (SEVERE lines, semaphore
errors, an OutOfMemoryError with its stack); every line within the last hours, plus old ones the check must skip."""
import os
import sys
import time

d, kind = sys.argv[1], sys.argv[2]
os.makedirs(d, exist_ok=True)
now = time.time()


def ts(ago):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - ago))


lines = ["%s (101) worker.1 SEVERE *** ERROR *** an old error before the window" % ts(30 * 3600)]
for i in range(3):
    lines.append("%s (2%02d) glide.scheduler.worker.%d 5F3C0D2B9A1E4C77B0A1D2E3F4A5B6C7 SEVERE *** ERROR *** "
                 "Invalid query detected, please check logs for details [Unknown field sys_id%d in table incident]" % (ts(3600 - i * 60), i, i, i))
lines.append("%s (300) http-16000-1 INFO txid=abc Semaphore set Default: 16 available" % ts(1800))
if kind == "bad":
    for i in range(140):
        lines.append("%s (4%02d) http-16000-%d SEVERE *** ERROR *** Transaction cancelled: maximum execution time exceeded "
                     "for /api/now/table/incident (user %d)" % (ts(7000 - i * 30), i % 100, i, 1000 + i))
    for i in range(4):
        lines.append("%s (5%02d) http-16000-%d WARNING *** WARNING *** Default semaphore queue depth %d exceeded, transaction "
                     "waited %d ms" % (ts(600 - i * 30), i, i, 20 + i, 30000 + i))
    lines.append("%s (600) glide.scheduler.worker.3 SEVERE *** ERROR *** Uncaught exception in worker" % ts(120))
    lines.append("java.lang.OutOfMemoryError: Java heap space")
    lines.append("\tat java.util.Arrays.copyOf(Arrays.java:3236)")
day = time.strftime("%Y-%m-%d", time.localtime(now))
with open(os.path.join(d, "localhost_log.%s.txt" % day), "w") as f:
    f.write("\n".join(lines) + "\n")
old = os.path.join(d, "localhost_log.2020-01-01.txt")
with open(old, "w") as f:
    f.write("2020-01-01 00:00:00 (1) worker.0 SEVERE OutOfMemoryError in a file the check must not read\n")
os.utime(old, (now - 40 * 86400, now - 40 * 86400))

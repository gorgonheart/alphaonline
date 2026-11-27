import os
import time

HZ = os.sysconf("SC_CLK_TCK") or 100
PAGE = os.sysconf("SC_PAGE_SIZE") or 4096
CACHE = {}


def snap():
    d = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            st = open("/proc/%s/stat" % p).read()
        except Exception:
            continue
        r = st.rfind(")")
        f = st[r + 2:].split()
        try:
            d[int(p)] = ((int(f[11]) + int(f[12])) / float(HZ), int(f[21]) * PAGE / 1048576.0)
        except Exception:
            pass
    return d


def cmd(pid):
    c = CACHE.get(pid)
    if c is None:
        try:
            c = open("/proc/%d/cmdline" % pid, "rb").read().replace(b"\x00", b" ").decode("utf-8", "ignore")[:110]
        except Exception:
            c = "?"
        CACHE[pid] = c
    return c


prev = snap()
t0 = time.time()
while time.time() - t0 < 80:
    time.sleep(2)
    cur = snap()
    rows = []
    for pid, (cpu, rss) in cur.items():
        p = prev.get(pid)
        if not p:
            continue
        dc = cpu - p[0]
        if dc > 0.12:
            rows.append((dc / 2.0, pid, rss, cmd(pid)))
    rows.sort(reverse=True)
    tot = sum(r[0] for r in rows)
    if tot > 0.8:
        st = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        print("%s  attributed_top=%.2f cores  busy_now=%.1f%%" % (
            time.strftime("%H:%M:%S"), tot, 100.0 * (sum(st) - st[3] - st[4]) / sum(st)))
        for c, pid, rss, cm in rows[:8]:
            print("    %5.2f核 %6d rss=%5.0fMB %s" % (c, pid, rss, cm))
    prev = cur

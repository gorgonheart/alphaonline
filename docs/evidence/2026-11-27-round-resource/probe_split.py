import os
import time

HZ = os.sysconf("SC_CLK_TCK") or 100
PAGE = os.sysconf("SC_PAGE_SIZE") or 4096


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
            d[int(p)] = ((int(f[11]) + int(f[12])) / float(HZ), int(f[21]) * PAGE / 1048576.0, f[0])
        except Exception:
            pass
    return d


def name(pid):
    try:
        return open("/proc/%d/comm" % pid).read().strip()
    except Exception:
        return "?"


prev = snap()
pst = [int(x) for x in open("/proc/stat").readline().split()[1:]]
t0 = time.time()
while time.time() - t0 < 45:
    time.sleep(3)
    cur = snap()
    st = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    d = [b - a for a, b in zip(pst, st)]
    tot = sum(d)
    busy = 100.0 * (tot - d[3] - d[4]) / tot
    usr = 100.0 * (d[0] + d[1]) / tot
    sysc = 100.0 * (d[2] + d[5] + d[6]) / tot
    irq = 100.0 * (d[5] + d[6]) / tot
    print("%s busy=%.1f%%  user=%.1f sys=%.1f (irq+softirq=%.1f) wait=%.1f idle=%.1f" % (
        time.strftime("%H:%M:%S"), busy, usr, sysc, irq, 100.0 * d[4] / tot, 100.0 * d[3] / tot))
    rows = []
    for pid, (cpu, rss, comm) in cur.items():
        p = prev.get(pid)
        if not p:
            continue
        dc = (cpu - p[0]) / 3.0
        if dc > 0.05:
            rows.append((dc, pid, rss, name(pid)))
    rows.sort(reverse=True)
    print("   attributed=%.2f cores" % sum(r[0] for r in rows))
    for c, pid, rss, cm in rows[:10]:
        print("     %5.2f核 pid=%7d rss=%6.0fMB %s" % (c, pid, rss, cm))
    prev = cur
    pst = st

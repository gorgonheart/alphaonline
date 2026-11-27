#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1 Hz resource sampler for a single MODEL_REPLAY round.

Emits CSV on stdout, per-process detail on stderr.
Usage: python3 -u - <duration_seconds>
"""
import os
import sys
import time

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
INT = 1.0
HZ = os.sysconf("SC_CLK_TCK") or 100
PAGE = os.sysconf("SC_PAGE_SIZE") or 4096

COMPS = ["alpha2", "alpha_legacy", "model", "trade_main", "spawn", "legacy", "other"]
_cache = {}


def read_proc_stat():
    parts = open("/proc/stat").readline().split()[1:]
    return [int(x) for x in parts]


def read_meminfo():
    m = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")[0], line.split()[1]
        m[k] = int(v)
    return m


def read_vmstat():
    keys = ("pswpin", "pswpout", "pgpgin", "pgpgout", "pgmajfault")
    out = dict.fromkeys(keys, 0)
    for line in open("/proc/vmstat"):
        p = line.split()
        if p[0] in out:
            out[p[0]] = int(p[1])
    return out


def pick_disk():
    best = None
    try:
        for line in open("/proc/diskstats"):
            p = line.split()
            name = p[2]
            if name in ("vda", "sda", "nvme0n1", "xvda"):
                return name
            try:
                tot = int(p[5]) + int(p[9])
            except Exception:
                tot = 0
            if best is None or tot > best[1]:
                best = (name, tot)
    except Exception:
        pass
    return best[0] if best else None


def read_disk(name):
    if not name:
        return None
    for line in open("/proc/diskstats"):
        p = line.split()
        if p[2] != name:
            continue
        f = [int(x) for x in p[3:14]]
        return {
            "r_ios": f[0], "r_sect": f[2], "r_ms": f[3],
            "w_ios": f[4], "w_sect": f[6], "w_ms": f[7],
            "in_flight": f[8], "io_ms": f[9], "wio_ms": f[10],
        }
    return None


def classify(pid):
    cmd = ""
    try:
        cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\x00", b" ").decode("utf-8", "ignore")
    except Exception:
        return "other"
    if not cmd.strip():
        return None                      # transient (just forked) -> retry next tick
    if "python" not in cmd and "ptw" not in cmd and "spawn" not in cmd:
        return "SKIP"                    # definitely not ours
    if "/home/ubuntu/alpha2/" in cmd:
        return "alpha2"
    if "server_robust40" in cmd:
        return "model"
    if "/home/ubuntu/alphatradetest/" in cmd:
        return "legacy"
    if "/home/ubuntu/alphatrade/" in cmd:
        return "trade_main"
    if "/home/ubuntu/alpha/" in cmd:
        return "alpha_legacy"
    if "spawn_main" in cmd or "multiprocessing" in cmd or "forkserver" in cmd:
        cwd = ""
        try:
            cwd = os.readlink("/proc/%s/cwd" % pid)
        except Exception:
            pass
        if "/home/ubuntu/alphatradetest" in cwd:
            return "legacy"
        if "/home/ubuntu/alphatrade" in cwd:
            return "trade_spawn"
        if "/home/ubuntu/alpha2" in cwd:
            return "alpha2_spawn"
        return "spawn"
    return "other"


def snap(disk):
    agg = {k: [0, 0.0, 0.0, 0.0, 0.0] for k in COMPS + ["trade_spawn", "alpha2_spawn"]}
    detail = []
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
            cpu = (int(f[11]) + int(f[12])) / float(HZ)
            rss = int(f[21]) * PAGE
        except Exception:
            continue
        comp = _cache.get(p)
        if comp is None:
            comp = classify(p)
            if comp:
                _cache[p] = comp
        if not comp or comp == "SKIP":
            continue
        rb = wb = 0.0
        try:
            io = {}
            for line in open("/proc/%s/io" % p):
                k, v = line.split(":")
                io[k] = int(v)
            rb = io.get("read_bytes", 0)
            wb = io.get("write_bytes", 0)
        except Exception:
            pass
        a = agg.setdefault(comp, [0, 0.0, 0.0, 0.0, 0.0])
        a[0] += 1
        a[1] += cpu
        a[2] += rss
        a[3] += rb
        a[4] += wb
        if rss >= 300 * 1048576:
            detail.append((int(p), comp, rss, cpu, rb, wb))
    return agg, detail


def psi(path, idx):
    try:
        lines = open(path).read().strip().split("\n")
        ln = lines[min(idx, len(lines) - 1)]
        d = dict(x.split("=") for x in ln.split()[1:])
        return float(d.get("avg10", 0))
    except Exception:
        return 0.0


def main():
    disk = pick_disk()
    print("# disk=%s dur=%.0f int=%.1f hz=%d" % (disk, DUR, INT, HZ))
    head = ["ts", "busy", "user", "sys", "wait", "idle",
            "memavail", "memfree", "cached", "dirty", "writeback",
            "swapused", "swapped_cache", "anon",
            "pswpin_ps", "pswpout_ps", "pgpgin_mbs", "pgpgout_mbs", "majflt_ps",
            "io_riops", "io_wiops", "io_rmbs", "io_wmbs", "io_util", "io_ar", "io_aw", "io_q",
            "load1", "psi_cpu", "psi_mem", "psi_io"]
    for c in COMPS + ["trade_spawn", "alpha2_spawn"]:
        head += ["%s_n" % c, "%s_cpu" % c, "%s_rss" % c, "%s_rd" % c, "%s_wr" % c]
    print(",".join(head))

    prev = None
    t0 = time.time()
    last_detail = 0.0
    while time.time() - t0 < DUR:
        now = time.time()
        ts = time.strftime("%H:%M:%S")
        st = read_proc_stat()
        m = read_meminfo()
        vm = read_vmstat()
        dk = read_disk(disk)
        agg, detail = snap(disk)
        row = dict(ts=ts)
        if prev:
            dt = now - prev["t"]
            pst = prev["st"]
            dtot = sum(st) - sum(pst)
            duser = st[0] - pst[0]
            dnice = st[1] - pst[1]
            dsys = st[2] - pst[2]
            didle = st[3] - pst[3]
            dwait = st[4] - pst[4]
            row.update(
                busy=100.0 * (dtot - didle - dwait) / dtot if dtot else 0,
                user=100.0 * duser / dtot if dtot else 0,
                sys=100.0 * (dsys + st[5] - pst[5] + st[6] - pst[6]) / dtot if dtot else 0,
                wait=100.0 * dwait / dtot if dtot else 0,
                idle=100.0 * didle / dtot if dtot else 0,
            )
            pvm = prev["vm"]
            row.update(
                pswpin_ps=(vm["pswpin"] - pvm["pswpin"]) / dt,
                pswpout_ps=(vm["pswpout"] - pvm["pswpout"]) / dt,
                pgpgin_mbs=(vm["pgpgin"] - pvm["pgpgin"]) * 4.0 / 1024 / dt,
                pgpgout_mbs=(vm["pgpgout"] - pvm["pgpgout"]) * 4.0 / 1024 / dt,
                majflt_ps=(vm["pgmajfault"] - pvm["pgmajfault"]) / dt,
            )
            pd = prev["dk"]
            if dk and pd:
                dri = dk["r_ios"] - pd["r_ios"]
                dwi = dk["w_ios"] - pd["w_ios"]
                drio = dk["r_ms"] - pd["r_ms"]
                dwio = dk["w_ms"] - pd["w_ms"]
                dtio = dk["io_ms"] - pd["io_ms"]
                row.update(
                    io_riops=dri / dt,
                    io_wiops=dwi / dt,
                    io_rmbs=(dk["r_sect"] - pd["r_sect"]) * 512.0 / 1048576 / dt,
                    io_wmbs=(dk["w_sect"] - pd["w_sect"]) * 512.0 / 1048576 / dt,
                    io_util=100.0 * dtio / (dt * 1000.0),
                    io_ar=(drio / dri) if dri else 0,
                    io_aw=(dwio / dwi) if dwi else 0,
                    io_q=float(dk["in_flight"]),
                )
            for c in agg:
                pa = prev["agg"].get(c, [0, 0.0, 0.0, 0.0, 0.0])
                a = agg[c]
                row["%s_n" % c] = a[0]
                row["%s_cpu" % c] = max(0.0, (a[1] - pa[1]) / dt)
                row["%s_rss" % c] = a[2] / 1048576.0
                row["%s_rd" % c] = max(0.0, (a[3] - pa[3]) / 1048576.0 / dt)
                row["%s_wr" % c] = max(0.0, (a[4] - pa[4]) / 1048576.0 / dt)
        row.update(
            memavail=m["MemAvailable"] / 1024.0,
            memfree=m["MemFree"] / 1024.0,
            cached=(m.get("Cached", 0) + m.get("SReclaimable", 0)) / 1024.0,
            dirty=m.get("Dirty", 0) / 1024.0,
            writeback=m.get("Writeback", 0) / 1024.0,
            swapused=(m["SwapTotal"] - m["SwapFree"]) / 1024.0,
            swapped_cache=m.get("SwapCached", 0) / 1024.0,
            anon=m.get("AnonPages", 0) / 1024.0,
            load1=os.getloadavg()[0],
            psi_cpu=psi("/proc/pressure/cpu", 0),
            psi_mem=psi("/proc/pressure/memory", 1),
            psi_io=psi("/proc/pressure/io", 1),
        )
        print(",".join(str(row.get(h, "")) for h in head))
        sys.stdout.flush()
        if now - last_detail >= 10:
            last_detail = now
            for pid, comp, rss, cpu, rb, wb in sorted(detail, key=lambda x: -x[2]):
                sys.stderr.write("D %s pid=%d %-11s rss=%5.0fMB cpu=%6.1fs rd=%8.1fMB wr=%8.1fMB\n" % (
                    ts, pid, comp, rss / 1048576.0, cpu, rb / 1048576.0, wb / 1048576.0))
            sys.stderr.flush()
        prev = {"t": now, "st": st, "vm": vm, "dk": dk, "agg": {k: list(v) for k, v in agg.items()}}
        time.sleep(INT)


if __name__ == "__main__":
    main()

import re

lines = open("/home/ubuntu/alphatrade/reports/startup.log", errors="ignore").read().split("\n")
seq = []
for ln in lines:
    if "本次循环开始" in ln:
        m = re.search(r"本次循环开始\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", ln)
        if m:
            seq.append(("LOOP", m.group(1)))
    elif "MODEL_REPLAY" in ln and '"event": "end"' in ln:
        m = re.search(r'"cutoff": "([^"]+)".*?"total_s": ([0-9.]+)', ln)
        seq.append(("END", m.group(1) if m else "?", m.group(2) if m else "?"))

print("total END=%d LOOP=%d" % (sum(1 for s in seq if s[0] == "END"), sum(1 for s in seq if s[0] == "LOOP")))
print("最近 14 轮（结束时刻取随后 loop start）:")
rows = []
for i, s in enumerate(seq):
    if s[0] == "END" and i + 1 < len(seq) and seq[i + 1][0] == "LOOP":
        rows.append((seq[i + 1][1], s[1], s[2]))
for r in rows[-14:]:
    print("  end~%s  cutoff=%s  total=%ss" % (r[0], r[1][11:], r[2]))
if len(rows) >= 3:
    print("\n轮次间隔(分钟):")
    import datetime
    prev = None
    for r in rows[-8:]:
        t = datetime.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
        if prev:
            print("  %s -> %s  间隔 %.0f min" % (prev.strftime("%H:%M"), t.strftime("%H:%M"), (t - prev).total_seconds() / 60))
        prev = t

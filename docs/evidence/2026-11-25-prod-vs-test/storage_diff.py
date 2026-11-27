"""Build an exact-byte per-directory comparison from 36/37 raw du outputs.

Reads the [F] and [J] sections of the two evidence files, aligns directories by
basename, and writes 48-storage-diff.csv plus a short conclusion to stdout.
"""
import csv

TEST = "36-test-storage-detail.txt"
PROD = "37-prod-storage-detail.txt"


def parse(path):
    sections = {}
    cur = None
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("=== ["):
            cur = line.split("]")[0].replace("=== [", "").strip()
            sections[cur] = {}
            continue
        if cur and "\t" in line and not line.startswith("==="):
            size, p = line.split("\t", 1)
            if size.strip().isdigit():
                sections[cur][p.strip().rsplit("/", 1)[-1]] = int(size.strip())
    return sections


t = parse(TEST)
p = parse(PROD)
pairs = [("F", "alpha/data (alpha vs alpha2)"), ("J", "alphatrade/data")]
rows = []
for sec, label in pairs:
    td, pd = t.get(sec, {}), p.get(sec, {})
    for name in sorted(set(td) | set(pd)):
        a, b = td.get(name), pd.get(name)
        rows.append(
            [
                label,
                name,
                a if a is not None else "",
                b if b is not None else "",
                (a - b) if a is not None and b is not None else "",
                round((a - b) / b * 100, 2) if a is not None and b not in (None, 0) else "",
                round(a / 1024**3, 3) if a is not None else "",
                round(b / 1024**3, 3) if b is not None else "",
            ]
        )
        # also emit top-level sums
for sec, label in pairs:
    td, pd = t.get(sec, {}), p.get(sec, {})
    ta, pa = sum(td.values()), sum(pd.values())
    rows.append([label, "__L1_SUM__", ta, pa, ta - pa, round((ta - pa) / pa * 100, 2),
                 round(ta / 1024**3, 3), round(pa / 1024**3, 3)])

with open("48-storage-diff.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["scope", "dir", "test_bytes", "prod_bytes", "delta_bytes", "delta_pct",
                "test_GiB", "prod_GiB"])
    w.writerows(rows)

print("scope,dir,test_GiB,prod_GiB,delta_GiB,delta_pct")
for r in rows:
    if r[1] == "__L1_SUM__" or r[6] != "" and r[7] != "" and isinstance(r[6], float) and (r[6] >= 0.05 or r[7] >= 0.05):
        d = (r[4] / 1024**3) if isinstance(r[4], (int, float)) else ""
        print("%-30s %-38s %10s %10s %12s %10s" % (
            r[0], r[1], r[6], r[7], round(d, 3) if d != "" else "", r[5]))
print("\n=== only-in-test / only-in-prod ===")
for r in rows:
    if r[2] == "" or r[3] == "":
        print(r[0], r[1], "test_bytes=", r[2], "prod_bytes=", r[3])

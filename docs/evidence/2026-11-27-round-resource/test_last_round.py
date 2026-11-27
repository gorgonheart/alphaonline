import json

rows = []
for line in open("/home/ubuntu/alphatrade/reports/startup.log", errors="ignore"):
    if "MODEL_REPLAY" not in line or '"event": "end"' not in line:
        continue
    i = line.find("MODEL_REPLAY")
    try:
        rows.append(json.loads(line[i + 12:].strip()))
    except Exception:
        pass

print("ends=%d" % len(rows))
for d in rows[-3:]:
    print("cutoff=%s mode=%s prewarm=%.2f wait=%.1f read=%.2f prep=%.2f cmp=%.2f calc=%.2f acct=%.2f flow=%.2f total=%.2f frames=%s" % (
        d.get("cutoff"), d.get("mode"), d.get("prewarm_s", 0), d.get("wait_ready_s", 0),
        d.get("input_read_s", 0), d.get("prepare_s", 0), d.get("compare_s", 0),
        d.get("calculation_s", 0), d.get("account_calculation_s", 0), d.get("account_flow_s", 0),
        d.get("total_s", 0), d.get("input_frames")))

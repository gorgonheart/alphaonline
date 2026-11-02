"""Read existing worker/PM2 logs; no imports of trading or model code."""
import argparse
from collections import defaultdict
import glob
import json
import math
from pathlib import Path
import statistics


def summarize(lines, since_stamp=0):
    attempts={};decoder=json.JSONDecoder();malformed=0
    for line in lines:
        if 'MODEL_REPLAY ' not in line:continue
        try:
            row,_=decoder.raw_decode(line.split('MODEL_REPLAY ',1)[1].lstrip())
            if row['stamp']<since_stamp:continue
            attempt=row['attempt'];event=row['event']
        except (ValueError,KeyError,TypeError):
            malformed+=1;continue
        # Identical records can occur both in framework files and PM2 stdout.
        attempts.setdefault(attempt,{})[event]=row
    groups=defaultdict(list);unfinished=[]
    for attempt,events in attempts.items():
        if 'end' not in events:
            row=events.get('calculation_committed',events.get('start',next(iter(events.values()))))
            unfinished.append(dict(account=row['account'],stamp=row['stamp'],attempt=attempt,
                                   committed=row.get('committed',False)))
            continue
        row=events['end'];groups[(row['account'],row['kind'],row['mode'])].append(row)
    summaries=[]
    for (account,kind,mode),rows in sorted(groups.items()):
        timings={}
        for key in sorted({k for r in rows for k in r if k.endswith('_s')}):
            values=sorted(r[key] for r in rows if isinstance(r.get(key),(int,float)))
            if not values:continue
            # Nearest-rank P95, explicit sample count; no fabricated missing zeros.
            timings[key]=dict(n=len(values),median=round(statistics.median(values),6),
                p95=round(values[math.ceil(.95*len(values))-1],6),max=max(values))
        summaries.append(dict(account=account,kind=kind,mode=mode,attempts=len(rows),
            publications=len({r['stamp'] for r in rows}),
            errors=sum(r['status']=='error' for r in rows),
            revision_detected=sum('revised_from' in r for r in rows),
            committed=sum(bool(r.get('committed')) for r in rows),
            skipped=sum(bool(r.get('account_skipped')) for r in rows),
            trained_quarters=sum(r.get('trained_quarters',0) for r in rows),
            reused_quarters=sum(r.get('reused_quarters',0) for r in rows),timings=timings))
    return dict(groups=summaries,unfinished=unfinished,malformed_lines=malformed)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs',nargs='+',help='Files or glob patterns, including rotated logs')
    parser.add_argument('--since-stamp',type=int,default=0,help='Inclusive publication UTC epoch seconds')
    args=parser.parse_args()
    paths=sorted({Path(p).resolve() for pattern in args.logs for p in glob.glob(pattern,recursive=True) if Path(p).is_file()})
    if not paths:parser.error('No matching log files')
    def lines():
        for path in paths:
            with path.open(encoding='utf-8',errors='replace') as stream:yield from stream
    print(json.dumps(summarize(lines(),args.since_stamp),ensure_ascii=False,indent=2))


if __name__=='__main__':main()

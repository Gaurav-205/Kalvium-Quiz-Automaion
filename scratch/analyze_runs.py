import csv

runs = [
    'logs/run_20261005_033333.csv',
    'logs/run_20261005_034653.csv',
    'logs/run_20261005_041754.csv'
]

for path in runs:
    rows = list(csv.DictReader(open(path, encoding='utf-8')))
    lb = rows[0].get('livebook') if rows else 'Unknown'
    events = {}
    for r in rows:
        events[r['event']] = events.get(r['event'], 0) + 1
    scores = [r.get('note') for r in rows if r['event'] == 'result']
    errs = [f"{r.get('lu')}: {r.get('note')}" for r in rows if r['event'] == 'error']
    print(f"=== {lb} ({path}) ===")
    print("  Events:", events)
    print("  Results count:", len(scores))
    print("  Error count:", len(errs))
    if errs:
        print("  Errors:")
        for e in errs[:10]:
            print("   -", e)
    print()

"""Print a quality report (results/quality-*.json) as a table. Usage: python scripts/quality_table.py FILE [FILE2]"""
import json
import sys

reps = [json.load(open(f)) for f in sys.argv[1:]]
print(f"{'kind':<13} {'n':>4}  " + "  ".join(f"{'brand@10':>8} {'prod@10':>7} {'mrr':>5}" for _ in reps))
for k in ["total"] + sorted(reps[0]["by_kind"]):
    cells = [(r["total"] if k == "total" else r["by_kind"][k]) for r in reps]
    print(f"{k:<13} {cells[0]['n']:>4}  " + "  ".join(f"{c['brand@10']:>8} {c['product@10']:>7} {c['mrr']:>5}" for c in cells))

import os, sys, csv, collections
sys.path[:0] = ["src", "scripts"]
from uc_sql import load_env; load_env()
from insurer_search.client import InsurerSearchClient
rows = [r for r in csv.reader(open("/tmp/wider_queries.tsv"), delimiter="\t") if len(r) == 3]
c = InsurerSearchClient.from_env()
want = collections.defaultdict(set)
for q, w, pid in rows: want[q].add(pid)
hit = 0
for q, pids in want.items():
    got = {r["payer_id"] for page in (1,) for r in c.search(q, "wider-ab", page=page)}
    hit += bool(got & pids)
print(f"{os.environ.get('LABEL')}: {hit}/{len(want)} wider-word queries find a matching brand in the top 10")

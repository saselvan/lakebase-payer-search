"""Search quality score, generated from the corpus itself (no hand-picked cases).

For a deterministic sample of payers we make the queries a front-desk user types: the exact
brand + product, one typo, a dropped word, the initials, state first, words joined. A query
scores a hit when a payer of the same brand is in the top 10 (brand@10), and a strict hit when
the same brand AND product is there (product@10). MRR = mean of 1/rank of the first brand hit.

Usage: uv run python -m insurer_search.evaluate --sample 150 [--out results/quality.json]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from insurer_search.generate import STATES, Payer, build_payers, load_seeds, product_name

STOP = {"of", "and", "the", "&", "-"}


def _typo(s: str, rng: random.Random) -> str:
    letters = [i for i, c in enumerate(s) if c.isalpha()]
    if len(letters) < 5:
        return s
    i = rng.choice(letters[1:-1])
    op = rng.choice("sdr")
    if op == "s" and i + 1 < len(s):
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if op == "d":
        return s[:i] + s[i + 1:]
    return s[:i] + rng.choice("aeioustrnl") + s[i + 1:]


def queries_for(p: Payer, rng: random.Random) -> list[tuple[str, str]]:
    brand = p.base_name
    words = [w for w in re.split(r"\s+", brand) if w]
    full = product_name(brand, p.product).lower()
    out = [("exact", full), ("typo", _typo(full, rng)), ("brand_typo", _typo(brand.lower(), rng))]
    if len(words) >= 3:
        drop = rng.choice([i for i, w in enumerate(words) if w.lower() not in STOP] or [0])
        out.append(("dropped_word", " ".join(w for i, w in enumerate(words) if i != drop).lower()))
    sig = [w for w in words if w.lower() not in STOP]
    bcbs_state = re.match(r"blue cross (?:and )?blue shield of (.+)$", brand.lower())
    if bcbs_state and p.state in STATES:
        out.append(("initials", f"bcbs{p.state.lower()}"))   # the industry form: bcbsga, bcbsmi
    elif len(sig) >= 3:   # 2-letter initials ("ac") are too short to mean one payer
        out.append(("initials", "".join(w[0] for w in sig).lower()))
    if len(sig) >= 2:
        out.append(("joined", "".join(w.lower() for w in sig[:2]) + " " + " ".join(w.lower() for w in sig[2:])))
    if p.state in STATES:
        out.append(("state_first", f"{p.state.lower()} {brand.lower()}"))
    return [(k, q.strip()) for k, q in out if 2 <= len(q.strip()) <= 100]


def run(sample: int, seed: int = 11, workers: int = 8) -> dict:
    from insurer_search.client import InsurerSearchClient

    payers = build_payers(load_seeds()[0])
    by_id = {p.payer_id: p for p in payers}
    rng = random.Random(seed)
    # One payer per brand first (so every brand is tested), then random extra payers.
    per_brand: dict[str, list[Payer]] = defaultdict(list)
    for p in payers:
        per_brand[p.base_name].append(p)
    picks = [rng.choice(v) for v in per_brand.values()]
    picks = (picks + rng.sample(payers, max(0, sample - len(picks))))[:max(sample, len(picks))]
    cases = [(p, kind, q) for p in picks for kind, q in queries_for(p, rng)]

    client = InsurerSearchClient.from_env()

    def one(case):
        p, kind, q = case
        rows = client.search(q, "eval")
        brand_rank = next((i + 1 for i, r in enumerate(rows) if by_id.get(r["payer_id"], p).base_name == p.base_name
                           and r["payer_id"] in by_id), None)
        prod_hit = any(r["payer_id"] in by_id and by_id[r["payer_id"]].base_name == p.base_name
                       and by_id[r["payer_id"]].product == p.product for r in rows)
        return kind, q, p.payer_name, brand_rank, prod_hit, rows[0]["payer_name"] if rows else None

    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(one, cases))
    by_kind: dict[str, list] = defaultdict(list)
    for r in results:
        by_kind[r[0]].append(r)

    def score(rs):
        n = len(rs)
        return {"n": n, "brand@10": round(sum(r[3] is not None for r in rs) / n, 3),
                "product@10": round(sum(r[4] for r in rs) / n, 3),
                "mrr": round(sum(1 / r[3] for r in rs if r[3]) / n, 3)}

    report = {"total": score(results), "by_kind": {k: score(v) for k, v in sorted(by_kind.items())},
              "misses": [{"kind": r[0], "q": r[1], "target": r[2], "top": r[5]}
                         for r in results if r[3] is None][:60],
              "seconds": round(time.time() - t0, 1)}
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    rep = run(a.sample)
    print(json.dumps({"total": rep["total"], "by_kind": rep["by_kind"], "seconds": rep["seconds"]}, indent=1))
    for m in rep["misses"][:25]:
        print(f"MISS {m['kind']:<12} {m['q']!r:<45} want {m['target']!r:<50} got {m['top']!r}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()

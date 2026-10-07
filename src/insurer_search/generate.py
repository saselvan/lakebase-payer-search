"""Generate the synthetic payer-alias corpus.

Every name comes from public payer lists (see docs/research/payer-search-vocabulary.md),
but every ID, plan and group number is synthetic. Output is deterministic for a given seed.

Usage: python -m insurer_search.generate --target-rows 3000000 --out data/out
"""
from __future__ import annotations

import argparse
import csv
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

# Product lines per plan type. A Payer is one (base name, product, state).
PRODUCTS = {
    "commercial": ["PPO", "HMO", "POS", "EPO", "Choice PPO", "Open Access", "Select"],
    "medicare_advantage": ["Medicare Advantage HMO", "Medicare Advantage PPO", "Dual Complete", "D-SNP"],
    "medicare": ["Part A", "Part B", "Railroad"],
    "medicaid": ["Medicaid", "Managed Medicaid", "CHIP"],
    "medicaid_mco": ["Medicaid Managed Care", "Community Plan", "Dual Choice"],
    "marketplace": ["Bronze", "Silver", "Gold"],
    "military": ["Prime", "Select", "For Life"],
    "workers_comp": ["Workers Compensation"],
    "auto": ["Auto Medical", "PIP"],
    "tpa": ["Self-Funded Plan", "Group Health"],
}

COLUMNS = ["alias_id", "payer_id", "payer_name", "alias", "alias_type", "state", "plan_type"]


@dataclass(frozen=True)
class Payer:
    payer_id: str
    payer_name: str
    base_name: str
    product: str
    state: str  # two-letter code, or "US" for a national payer
    plan_type: str


def load_seeds(seed_dir: Path = SEED_DIR) -> tuple[list[dict], list[dict]]:
    with open(seed_dir / "payers.csv", newline="") as f:
        bases = list(csv.DictReader(f))
    with open(seed_dir / "abbreviations.csv", newline="") as f:
        abbrevs = list(csv.DictReader(f))
    return bases, abbrevs


def product_name(base: str, product: str) -> str:
    """Join base and product without repeating words: "Humana Medicare Advantage" +
    "Medicare Advantage HMO" -> "Humana Medicare Advantage HMO"; "Medicaid" + "Medicaid" -> "Medicaid"."""
    bw, pw = base.split(), product.split()
    for k in range(min(len(bw), len(pw)), 0, -1):
        if [w.lower() for w in bw[-k:]] == [w.lower() for w in pw[:k]]:
            return " ".join(bw + pw[k:])
    return f"{base} {product}"


def build_payers(bases: list[dict]) -> list[Payer]:
    payers: list[Payer] = []
    seen: set[str] = set()
    for b in bases:
        raw = [s.strip().upper() for s in b["states"].split(",") if s.strip()]
        states = list(STATES) if raw == ["ALL"] else [s for s in raw if s in STATES] or ["US"]
        for product in PRODUCTS.get(b["plan_type"], ["Plan"]):
            for st in states:
                name = product_name(b["base_name"], product)
                if len(states) > 1:
                    name += f" - {st}"
                if name.lower() in seen:  # the same payer listed twice in the seeds
                    continue
                seen.add(name.lower())
                payers.append(Payer("", name, b["base_name"], product, st, b["plan_type"]))
    # Synthetic, stable IDs: sorted order, not real clearinghouse payer IDs.
    payers.sort(key=lambda p: p.payer_name)
    return [Payer(f"SYN{i:06d}", p.payer_name, p.base_name, p.product, p.state, p.plan_type)
            for i, p in enumerate(payers, start=1)]


def _abbreviation_forms(base: str, abbrevs: list[dict]) -> list[str]:
    """Short forms of a base name. Only the longest matching expansion is used, so
    "Blue Cross and Blue Shield" becomes "BCBS", not the half-form "BC and Blue Shield"."""
    forms = []
    for a in sorted(abbrevs, key=lambda a: -len(a["expansion"])):
        if forms:
            break
        exp = a["expansion"].strip()
        if len(exp) < 4:
            continue
        pat = re.compile(r"\b" + re.escape(exp).replace(r"\ and\ ", r"\ (?:and\ )?") + r"\b", re.I)
        # "Blue Cross and Blue Shield" should also match the expansion "Blue Cross Blue Shield".
        loose = re.compile(r"\b" + r"\s+(?:and\s+)?".join(map(re.escape, exp.split())) + r"\b", re.I)
        for p in (pat, loose):
            if p.search(base):
                forms.append(p.sub(a["abbrev"].strip(), base, count=1))
                break
    return sorted(set(forms))


def aliases_for(p: Payer, abbrevs: list[dict]) -> Iterator[tuple[str, str]]:
    """Yield (alias, alias_type) for one Payer. Card-text padding is added separately."""
    st_name = STATES.get(p.state, "")
    yield p.payer_name, "canonical"
    named = product_name(p.base_name, p.product)
    yield named, "brand"
    if st_name:
        if st_name.lower() not in p.base_name.lower():
            yield f"{named} {st_name}", "state_variant"
        yield f"{p.state} {named}", "state_first"
    for short in _abbreviation_forms(p.base_name, abbrevs):
        yield product_name(short, p.product) + (f" {p.state}" if st_name else ""), "abbreviation"


def generate(target_rows: int, seed: int = 7, seed_dir: Path = SEED_DIR) -> Iterator[dict]:
    bases, abbrevs = load_seeds(seed_dir)
    payers = build_payers(bases)
    rng = random.Random(seed)
    alias_id = 0
    core: list[tuple[Payer, str, str]] = []
    for p in payers:
        seen = set()
        for alias, kind in aliases_for(p, abbrevs):
            key = alias.lower()
            if key not in seen:
                seen.add(key)
                core.append((p, alias, kind))
    for p, alias, kind in core:
        alias_id += 1
        yield _row(alias_id, p, alias, kind)
    # Pad to the target with card-text aliases ("<brand> <product> Grp 123456"): the kind
    # of near-duplicate rows that make real payer-alias tables reach millions of rows.
    while alias_id < target_rows:
        p = payers[rng.randrange(len(payers))]
        alias_id += 1
        named = product_name(p.base_name, p.product)
        yield _row(alias_id, p, f"{named} Grp {rng.randrange(10**5, 10**6)}", "card_text")


def _row(alias_id: int, p: Payer, alias: str, kind: str) -> dict:
    return {"alias_id": alias_id, "payer_id": p.payer_id, "payer_name": p.payer_name,
            "alias": alias, "alias_type": kind, "state": p.state, "plan_type": p.plan_type}


def write_parquet(rows: Iterator[dict], out: Path, chunk: int = 500_000) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("part-*.parquet"):
        old.unlink()
    schema = pa.schema([("alias_id", pa.int64())] + [(c, pa.string()) for c in COLUMNS[1:]])
    buf, n, part = [], 0, 0
    for r in rows:
        buf.append(r)
        if len(buf) == chunk:
            pq.write_table(pa.Table.from_pylist(buf, schema), out / f"part-{part:04d}.parquet")
            n, part, buf = n + len(buf), part + 1, []
    if buf:
        pq.write_table(pa.Table.from_pylist(buf, schema), out / f"part-{part:04d}.parquet")
        n += len(buf)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-rows", type=int, default=3_000_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("data/out"))
    a = ap.parse_args()
    n = write_parquet(generate(a.target_rows, a.seed), a.out)
    print(f"wrote {n} rows to {a.out}")


if __name__ == "__main__":
    main()

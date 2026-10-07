"""Generate synonyms from the corpus itself, so no one has to add them one by one.

The payer universe is closed (about 120 brands), so the useful short forms can be derived:
  initials      "Independence Blue Cross"            -> ibc
  bcbs + state  "Blue Cross and Blue Shield of Georgia" -> bcbsga   (the industry's own convention)
  joined words  "Blue Cross"                         -> bluecross
Each row maps one word to one lexeme (the long form joined with "_"), which is the shape the
Lakebase tokenizer needs. The search index appends the word to every alias holding the long form.

Safety rules (each rule is a test): a generated word is dropped when two brands would share it,
when it is already a real word in the corpus, a state code, or a hand-written synonym.

Usage: python -m insurer_search.synonyms  -> data/seed/synonyms_generated.csv
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from insurer_search.generate import SEED_DIR, STATES, aliases_for, build_payers, load_seeds

STOP = {"of", "and", "the", "&", "-", "inc", "co"}


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", s.lower()) if w]


def candidates(brand: str) -> list[tuple[str, str, str]]:
    """(word, canonical, kind) proposals for one brand name."""
    words = _words(brand)
    sig = [w for w in words if w not in STOP]
    state_words = {w for n in STATES.values() for w in _words(n)}
    out = []
    m = re.match(r"blue cross (?:and )?blue shield of (.+)$", brand.lower())
    st = next((c for c, n in STATES.items() if m and n.lower() == m.group(1)), None)
    if st:   # the industry form wins over plain initials for these brands
        out.append((f"bcbs{st.lower()}", "_".join(_words(f"blue cross blue shield of {m.group(1)}")), "bcbs_state"))
    elif len(sig) >= 3:
        out.append(("".join(w[0] for w in sig), "_".join(words), "initials"))
    # Joined pairs, only inside the brand words: "blue cross" -> bluecross, never "shieldnew".
    pairs = [(a, b) for a, b in zip(sig, sig[1:]) if a not in state_words and b not in state_words]
    if ("cross", "blue") in pairs:
        pairs.remove(("cross", "blue"))
    for a, b in pairs:
        out.append((a + b, f"{a}_{b}", "joined"))
    return out


def generate(seed_dir: Path = SEED_DIR) -> list[dict]:
    bases, abbrevs = load_seeds(seed_dir)
    with open(seed_dir / "synonyms.csv", newline="") as f:
        hand = {r["word"] for r in csv.DictReader(f)}
    payers = build_payers(bases)
    vocab = {w for p in payers for alias, _ in aliases_for(p, abbrevs) for w in _words(alias)}
    proposals: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for brand in sorted({b["base_name"] for b in bases}):
        for word, canon, kind in candidates(brand):
            proposals[word].add((canon, kind))
    rows = []
    for word, options in sorted(proposals.items()):
        canons = {c for c, _ in options}
        if len(canons) > 1:            # two brands share it: ambiguous, drop
            continue
        if word in vocab or word in hand or word.upper() in STATES or len(word) < 3:
            continue
        canon, kind = sorted(options)[0]
        if word == canon:
            continue
        rows.append({"word": word, "canonical": canon, "kind": kind, "source": "generated"})
    return rows


def main() -> None:
    rows = generate()
    out = SEED_DIR / "synonyms_generated.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["word", "canonical", "kind", "source"])
        w.writeheader()
        w.writerows(rows)
    by = defaultdict(int)
    for r in rows:
        by[r["kind"]] += 1
    print(f"wrote {len(rows)} rows to {out}: {dict(by)}")


if __name__ == "__main__":
    main()

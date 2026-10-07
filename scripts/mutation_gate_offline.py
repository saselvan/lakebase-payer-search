"""Offline mutation gate: break one rule, run the tests, expect red, restore. Committed state only.
Usage: uv run python scripts/mutation_gate_offline.py
"""
import subprocess
from pathlib import Path

MUTATIONS = [
    ("payer dedup removed", "src/insurer_search/generate.py",
     "                if name.lower() in seen:  # the same payer listed twice in the seeds\n                    continue\n", ""),
    ("product_name repeats words", "src/insurer_search/generate.py",
     "    bw, pw = base.split(), product.split()\n", "    return f\"{base} {product}\"\n    bw, pw = base.split(), product.split()\n"),
    ("abbreviation not longest-first", "src/insurer_search/generate.py",
     "        if forms:\n            break\n", ""),
    ("unknown state codes kept", "src/insurer_search/generate.py",
     "[s for s in raw if s in STATES] or [\"US\"]", "[s for s in raw] or [\"US\"]"),
    ("synonym ambiguity check removed", "src/insurer_search/synonyms.py",
     "        if len(canons) > 1:            # two brands share it: ambiguous, drop\n            continue\n", ""),
    ("synonym real-word check removed", "src/insurer_search/synonyms.py",
     "word in vocab or ", ""),
    ("bcbs+state does not win", "src/insurer_search/synonyms.py",
     "    if st:   # the industry form wins over plain initials for these brands", "    if False and st:"),
    ("cross-word joins allowed", "src/insurer_search/synonyms.py",
     "if a not in state_words and b not in state_words]", "]"),
]

rows = []
for name, path, old, new in MUTATIONS:
    p = Path(path)
    src = p.read_text()
    assert old in src, f"mutation target not found: {name}"
    p.write_text(src.replace(old, new, 1))
    try:
        r = subprocess.run(["uv", "run", "pytest", "-q", "-x", "tests/test_generate.py", "tests/test_synonyms.py"],
                           capture_output=True, text=True)
        failed = [l.split("::")[-1].split(" ")[0] for l in r.stdout.splitlines() if l.startswith("FAILED")]
        last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
        rows.append((name, failed[0] if failed else "-", "RED" if r.returncode else "GREEN (bad)", last))
    finally:
        subprocess.run(["git", "checkout", "--", path], check=True)

print("| Mutation applied | Test that went red | Result line |\n|---|---|---|")
for name, test, verdict, last in rows:
    print(f"| {name} | {test} | {verdict}: {last} |")

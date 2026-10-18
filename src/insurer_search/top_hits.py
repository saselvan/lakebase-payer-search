"""The shared top-hit list: queries whose first result must name an expected brand.

One file, data/test_top_hit_cases.json, read by the live tests and by the name flywheel's
must-not-break gate, so the two never check different lists. Every reader goes through
load_top_hits(), which refuses a bad file instead of skipping rows: an empty expected brand
would match every payer name and pass for nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "test_top_hit_cases.json"


def _norm(q: str) -> str:
    return " ".join(q.lower().split())


def load_top_hits(path: Path = DEFAULT_PATH) -> list[tuple[str, str]]:
    """Return [(query, expected_brand)] in file order. Raise ValueError on any bad row."""
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: expected a non-empty JSON list")
    out, seen = [], {}
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ValueError(f"{path} row {i}: not an object")
        q, brand = r.get("query"), r.get("expected_brand")
        for name, v in (("query", q), ("expected_brand", brand)):
            if not isinstance(v, str) or not v.strip() or v != v.strip():
                raise ValueError(f"{path} row {i}: {name} must be a non-empty string with no outer spaces")
        if _norm(q) in seen:
            raise ValueError(f"{path} row {i}: duplicate query {q!r} (same as row {seen[_norm(q)]})")
        seen[_norm(q)] = i
        out.append((q, brand))
    return out

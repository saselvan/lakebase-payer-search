"""Corpus invariants. The fixture seeds hold hostile rows on purpose (see comments)."""
import csv
from pathlib import Path

import pytest

from insurer_search.generate import build_payers, generate


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    with open(tmp_path / "payers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["base_name", "parent_org", "plan_type", "states"])
        w.writerow(["Blue Cross and Blue Shield of Georgia", "Elevance", "commercial", "GA"])
        # HOSTILE: a national payer -> one Payer per state; IDs must still be unique.
        w.writerow(["UnitedHealthcare", "UHG", "commercial", "ALL"])
        # HOSTILE: a state list with an unknown code and spaces.
        w.writerow(["Kaiser Permanente", "Kaiser", "commercial", "CA, ZZ ,CO"])
        # HOSTILE: an unknown plan type must not crash.
        w.writerow(["Mystery Health", "X", "not_a_type", "TX"])
        # HOSTILE: the same payer listed twice (real seeds had this) must yield one Payer.
        w.writerow(["Blue Cross and Blue Shield of Georgia", "BCBS", "commercial", "GA"])
    with open(tmp_path / "abbreviations.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["abbrev", "expansion", "note"])
        w.writerow(["BCBS", "Blue Cross Blue Shield", ""])
        # HOSTILE: a shorter expansion that also matches; must lose to the longest one.
        w.writerow(["BC", "Blue Cross", ""])
        w.writerow(["UHC", "UnitedHealthcare", ""])
    return tmp_path


def test_alias_ids_are_unique_and_dense(seed_dir):
    rows = list(generate(target_rows=5_000, seed_dir=seed_dir))
    ids = [r["alias_id"] for r in rows]
    assert ids == list(range(1, len(rows) + 1))
    assert len(rows) == 5_000


def test_every_payer_has_exactly_one_canonical_alias(seed_dir):
    rows = list(generate(target_rows=0, seed_dir=seed_dir))
    canon = [r["payer_id"] for r in rows if r["alias_type"] == "canonical"]
    assert len(canon) == len(set(canon)) == len({r["payer_id"] for r in rows})


def test_national_payer_expands_per_state_and_unknown_codes_are_dropped(seed_dir):
    import insurer_search.generate as g
    bases = list(csv.DictReader(open(seed_dir / "payers.csv")))
    payers = build_payers(bases)
    uhc_ppo = [p for p in payers if p.base_name == "UnitedHealthcare" and p.product == "PPO"]
    assert len(uhc_ppo) == len(g.STATES) == 51
    kaiser_states = {p.state for p in payers if p.base_name == "Kaiser Permanente"}
    assert kaiser_states == {"CA", "CO"}
    assert len({p.payer_id for p in payers}) == len(payers)
    assert len({p.payer_name for p in payers}) == len(payers)


def test_abbreviation_uses_longest_expansion_and_tolerates_and(seed_dir):
    rows = list(generate(target_rows=0, seed_dir=seed_dir))
    ga = {r["alias"] for r in rows if r["payer_name"] == "Blue Cross and Blue Shield of Georgia PPO"}
    assert "BCBS of Georgia PPO GA" in ga
    assert not any(a.startswith("BC and") for a in ga)


def test_output_is_deterministic_and_clean(seed_dir):
    a = list(generate(target_rows=3_000, seed=1, seed_dir=seed_dir))
    b = list(generate(target_rows=3_000, seed=1, seed_dir=seed_dir))
    assert a == b
    # A NUL byte fails the Lakebase sync; empty aliases are useless rows.
    assert all("\x00" not in r["alias"] and r["alias"].strip() for r in a)


@pytest.mark.parametrize("base,product,expected", [
    ("Humana Medicare Advantage", "Medicare Advantage HMO", "Humana Medicare Advantage HMO"),
    ("Medicaid", "Medicaid", "Medicaid"),
    ("Aetna", "PPO", "Aetna PPO"),
    # HOSTILE: an overlap only in the middle of the base is not a repeat.
    ("Advantage Health Plan", "Advantage", "Advantage Health Plan Advantage"),
])
def test_product_name_never_repeats_the_joining_words(base, product, expected):
    from insurer_search.generate import product_name
    assert product_name(base, product) == expected


def test_payer_names_are_unique_on_the_real_seeds():
    from insurer_search.generate import load_seeds
    payers = build_payers(load_seeds()[0])
    names = [p.payer_name for p in payers]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, sorted(dupes)[:10]

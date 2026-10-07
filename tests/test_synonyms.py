"""Generated synonyms. Hostile rows: an ambiguous acronym, an acronym that is a real word."""
import csv
from pathlib import Path

import pytest

from insurer_search.synonyms import candidates, generate


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    with open(tmp_path / "payers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["base_name", "parent_org", "plan_type", "states"])
        w.writerow(["Independence Blue Cross", "IBX", "commercial", "PA"])
        w.writerow(["Blue Cross and Blue Shield of Georgia", "BCBS", "commercial", "GA"])
        # HOSTILE: shares initials "ahp" with the next row -> both must be dropped.
        w.writerow(["Affinity Health Plan", "X", "commercial", "NY"])
        w.writerow(["Allied Health Partners", "Y", "commercial", "TX"])
        # HOSTILE: initials "pos" are a real corpus word (the product POS) -> dropped.
        w.writerow(["Peoples Option Select", "Z", "commercial", "OH"])
    with open(tmp_path / "abbreviations.csv", "w", newline="") as f:
        csv.writer(f).writerow(["abbrev", "expansion", "note"])
    with open(tmp_path / "synonyms.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["word", "canonical", "kind", "source"])
        w.writerow(["bluecross", "blue_cross", "spelling_variant", "hand"])   # hand row wins
    return tmp_path


def test_candidates_for_a_bcbs_state_plan():
    got = {(w, c) for w, c, _ in candidates("Blue Cross and Blue Shield of Georgia")}
    assert ("bcbsga", "blue_cross_blue_shield_of_georgia") in got
    assert ("bluecross", "blue_cross") in got


def test_generated_rows(seed_dir):
    rows = {r["word"]: r["canonical"] for r in generate(seed_dir)}
    assert rows["ibc"] == "independence_blue_cross"
    assert rows["bcbsga"] == "blue_cross_blue_shield_of_georgia"
    assert "ahp" not in rows                 # ambiguous
    assert "pos" not in rows                 # a real word in the corpus
    assert "bluecross" not in rows           # hand-written synonym already covers it
    assert all(w == w.lower() and " " not in w and " " not in c for w, c in rows.items())


def test_two_word_state_gets_the_bcbs_state_form_and_no_cross_word_joins():
    got = {w: c for w, c, _ in candidates("Blue Cross and Blue Shield of New Hampshire")}
    assert got["bcbsnh"] == "blue_cross_blue_shield_of_new_hampshire"
    assert "shieldnew" not in got and "crossblue" not in got and "newhampshire" not in got

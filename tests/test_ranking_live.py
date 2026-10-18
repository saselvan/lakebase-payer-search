"""Live ranking tests with hostile branch fixtures (ADR 0005: rank Payers from shared Search texts).

Each fixture adds Alias rows on a THROWAWAY branch only, refreshes the index, and searches through the
Data API as a client does. Assertions are on which payer_ids come back in which order, never on SQL steps.
Run: BRANCH=t-... DATA_API_BASE=<branch url> uv run pytest tests/test_ranking_live.py
"""
import os
import threading

import pytest

from insurer_search.client import MAX_RESULTS, PAGE_SIZE

pytestmark = pytest.mark.live

T = "tenant-test"
STATES = ["TX", "CA", "NY", "FL", "OH", "IL", "PA", "AZ", "WA", "CO"]
FIRST_ALIAS_ID = 9_000_000_000_000   # far above the synced rows' ids; fixture rows are deleted at the end
TAG = "ZZZF"                         # every fixture payer_id starts with this, so clean-up cannot touch real rows


def _payers_f1():
    # HOSTILE (F1 shared text, right Payer sorts last): one invented brand text, "Zorvex Care", is a flywheel
    # Alias of 150 decoy Payers. The right Payer's own canonical name is that text, and it has the HIGHEST
    # payer_id (so the highest key id) and is inserted last. A cut that counts Search keys, or ties by id,
    # never reaches it: 151 keys tie on the text and the cut keeps 100.
    rows = []
    for i in range(1, 151):
        pid, name = f"{TAG}1D{i:03d}", f"Aaa F1 Decoy {i:03d}"
        st = STATES[i % len(STATES)]
        rows += [(pid, name, name, "canonical", st), (pid, name, "Zorvex Care", "flywheel", st)]
    rows.append((f"{TAG}1R", "Zorvex Care", "Zorvex Care", "canonical", "TX"))
    return rows


def _payers_f2():
    # HOSTILE (F2 alphabetical decoy): "AAA Decoy Health" holds "UHC", the Synonym of UnitedHealthcare, as a
    # flywheel Alias. It sorts first by name, so a tie order by name before Own-name agreement puts it first.
    return [(f"{TAG}2D", "AAA Decoy Health", "AAA Decoy Health", "canonical", "TX"),
            (f"{TAG}2D", "AAA Decoy Health", "UHC", "flywheel", "TX")]


def _payers_f3():
    # HOSTILE (F3 unique exact Alias vs fuzzy Own name): one Payer holds the exact text "Qwyntra Health" as a
    # card Alias (held by no one else); another Payer's own name is one typo away ("Qwyntra Helth").
    # An over-weighted Own-name term puts the typo Payer first.
    return [(f"{TAG}3X", "Brightwell Mutual", "Brightwell Mutual", "canonical", "OH"),
            (f"{TAG}3X", "Brightwell Mutual", "Qwyntra Health", "card_text", "OH"),
            (f"{TAG}3Y", "Qwyntra Helth", "Qwyntra Helth", "canonical", "OH")]


def _payers_f8():
    # HOSTILE (F8 Own name through a shared best text): "plum" is a flywheel Alias of 150 decoys and of the right
    # Payer, whose own name "Plumora Health Plan" matches "plum" only by trigram. Its BEST text is the shared
    # "plum"; an implementation that keeps one text per Payer before the Own-name term gives it no agreement,
    # and it ties with the decoys, which sort first by name.
    rows = []
    for i in range(1, 151):
        pid, name = f"{TAG}8D{i:03d}", f"Aaa F8 Decoy {i:03d}"
        st = STATES[i % len(STATES)]
        rows += [(pid, name, name, "canonical", st), (pid, name, "Plum", "flywheel", st)]
    rows += [(f"{TAG}8R", "Plumora Health Plan", "Plumora Health Plan", "canonical", "WA"),
             (f"{TAG}8R", "Plumora Health Plan", "Plum", "flywheel", "WA")]
    return rows


def _payers_f9():
    # HOSTILE (F9 Specificity): "Quillon Trust" is a flywheel Alias of 150 decoys; the right Payer holds "Trust
    # Quillon" alone. Same words, same length, so the two texts tie on Relevance and neither Payer's own name
    # agrees. Only Specificity (fewer Payers share the text) puts the right Payer first; without it the decoys
    # win on name.
    rows = []
    for i in range(1, 151):
        pid, name = f"{TAG}9D{i:03d}", f"Aaa F9 Decoy {i:03d}"
        st = STATES[i % len(STATES)]
        rows += [(pid, name, name, "canonical", st), (pid, name, "Quillon Trust", "flywheel", st)]
    rows += [(f"{TAG}9R", "Brightline Assurance", "Brightline Assurance", "canonical", "TX"),
             (f"{TAG}9R", "Brightline Assurance", "Trust Quillon", "card_text", "TX")]
    return rows


def _values(rows):
    out = []
    for i, (pid, name, alias, atype, st) in enumerate(rows):
        esc = [v.replace("'", "''") for v in (pid, name, alias, atype, st)]
        out.append(f"({FIRST_ALIAS_ID + i}, '{esc[0]}', '{esc[1]}', '{esc[2]}', '{esc[3]}', '{esc[4]}', 'commercial')")
    return ",\n".join(out)


def _insert(admin_sql, rows):
    admin_sql("INSERT INTO payer_serving.insurer_alias "
              "(alias_id, payer_id, payer_name, alias, alias_type, state, plan_type) VALUES\n" + _values(rows))


def _delete(admin_sql):
    admin_sql(f"DELETE FROM payer_serving.insurer_alias WHERE payer_id LIKE '{TAG}%'")


def _refresh(admin_sql):
    """Refresh every search view that exists, in build order, concurrently (the search keeps answering)."""
    for mv in ("alias_key", "search_text", "vocab"):
        if admin_sql(f"SELECT to_regclass('search_idx.{mv}') IS NOT NULL")[0][0] == "t":
            # The tokenizer dictionary cannot run inside parallel workers (as in sql/10_search_index.sql).
            admin_sql(f"SET max_parallel_workers_per_gather = 0; REFRESH MATERIALIZED VIEW CONCURRENTLY search_idx.{mv}")
            admin_sql(f"VACUUM ANALYZE search_idx.{mv}")


def _all_pages(client, q):
    return [r for p in range(1, MAX_RESULTS // PAGE_SIZE + 1) for r in client.search(q, T, page=p)]


def _ids(rows):
    return [r["payer_id"] for r in rows]


@pytest.fixture(scope="module")
def fixtures(admin_sql):
    branch = os.environ.get("BRANCH", "")
    if not branch.startswith("t-"):
        pytest.skip("hostile fixtures write Alias rows: only on a throwaway t- branch (set BRANCH)")
    _delete(admin_sql)
    _insert(admin_sql, _payers_f1() + _payers_f2() + _payers_f3() + _payers_f8() + _payers_f9())
    _refresh(admin_sql)
    yield
    _delete(admin_sql)
    _refresh(admin_sql)


def test_f1_shared_text_right_payer_sorts_last_ranks_first(client, fixtures):
    rows = client.search("zorvex care", T)
    assert rows and rows[0]["payer_id"] == f"{TAG}1R", _ids(rows)[:5]


def test_f2_alphabetical_decoy_ranks_below_the_brand(client, fixtures):
    ids = _ids(_all_pages(client, "uhc"))
    names = {r["payer_id"]: r["payer_name"] for r in _all_pages(client, "uhc")}
    first_brand = next((i for i, p in enumerate(ids) if "unitedhealthcare" in names[p].lower()), None)
    assert first_brand is not None and first_brand < 10, ids[:10]
    decoy = ids.index(f"{TAG}2D") if f"{TAG}2D" in ids else len(ids)
    assert first_brand < decoy, (first_brand, decoy)


def test_f3_unique_exact_alias_beats_one_typo_own_name(client, fixtures):
    ids = _ids(client.search("qwyntra health", T))
    assert ids[:1] == [f"{TAG}3X"], ids[:5]
    assert f"{TAG}3Y" in ids, ids


def test_f5_paging_gives_fifty_distinct_payers_then_nothing(client, fixtures):
    pages = [client.search("zorvex care", T, page=p) for p in range(1, 7)]
    ids = [r["payer_id"] for p in pages[:5] for r in p]
    assert len(ids) == 50 and len(set(ids)) == 50, (len(ids), len(set(ids)))
    assert pages[5] == []
    assert f"{TAG}1R" in ids


def test_f6_concurrent_refresh_during_searches_has_no_error(client, admin_sql, fixtures):
    errors = []

    def refresh():
        try:
            _refresh(admin_sql)
        except Exception as e:   # noqa: BLE001 - any failure of the refresh is the finding
            errors.append(e)

    t = threading.Thread(target=refresh)
    t.start()
    seen = []
    while t.is_alive():
        seen.append(client.search("zorvex care", T)[0]["payer_id"])
    t.join()
    assert not errors, errors
    assert seen and set(seen) == {f"{TAG}1R"}, seen[:5]


def test_f7_state_form_keeps_the_state_payers_first(client, fixtures):
    # "aarp maine": the Alias "AARP MA" carries no state, the Payer's state (ME) does. Today the 4 AARP
    # Payers in Maine are ranks 1-4 (baseline-1 run, 2026-10-17); a per-text document without the state loses them.
    rows = client.search("aarp maine", T)
    assert [r["state"] for r in rows[:4]] == ["ME"] * 4, [(r["payer_name"], r["state"]) for r in rows[:5]]
    assert all("AARP" in r["payer_name"] for r in rows[:4]), [r["payer_name"] for r in rows[:5]]


def test_f8_own_name_counts_when_the_best_text_is_shared(client, fixtures):
    rows = client.search("plum", T)
    assert rows and rows[0]["payer_id"] == f"{TAG}8R", _ids(rows)[:5]


def test_f9_a_text_held_by_one_payer_beats_an_equal_shared_text(client, fixtures):
    rows = client.search("quillon trust", T)
    assert rows and rows[0]["payer_id"] == f"{TAG}9R", [(r["payer_id"], r["score"]) for r in rows[:3]]


def test_identical_runs_give_identical_order(client, fixtures):
    assert _ids(_all_pages(client, "zorvex care")) == _ids(_all_pages(client, "zorvex care"))


def test_f4_reordered_input_gives_the_same_fifty(client, admin_sql, fixtures):
    # HOSTILE (F4 reordered input): the same F1 rows inserted in reverse order must give the identical 50.
    # Runs last in this file: it rewrites the F1 rows.
    before = _ids(_all_pages(client, "zorvex care"))
    admin_sql(f"DELETE FROM payer_serving.insurer_alias WHERE payer_id LIKE '{TAG}1%'")
    rows = list(reversed(_payers_f1()))
    _insert(admin_sql, rows)
    _refresh(admin_sql)
    after = _ids(_all_pages(client, "zorvex care"))
    assert len(before) == 50 and after == before, (before[:5], after[:5])


def test_every_search_key_has_its_search_text(admin_sql):
    # The expansion joins keys to texts on the normalised text; a key without its text could never be found.
    missing = admin_sql("SELECT count(*) FROM search_idx.alias_key k "
                        "LEFT JOIN search_idx.search_text t USING (key_norm) WHERE t.text_id IS NULL")[0][0]
    counts = admin_sql("SELECT (SELECT count(DISTINCT key_norm) FROM search_idx.alias_key), "
                       "(SELECT count(*) FROM search_idx.search_text), "
                       "(SELECT sum(n_payers) FROM search_idx.search_text), (SELECT count(*) FROM search_idx.alias_key)")[0]
    assert missing == "0"
    assert counts[0] == counts[1] and counts[2] == counts[3], counts

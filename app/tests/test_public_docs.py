"""K9 and item 4: the repo README sends blog readers to sql/ first and the demo second; the data contract names the
app's endpoint and what it never accepts or returns."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PART1 = "https://medium.com/@samuel_selvan/lakebase-branches-for-search-evals-a-clean-copy-of-production-for-every-change-c42c599f2b8b"
PART2 = "https://medium.com/@samuel_selvan/lakebase-search-bm25-vs-postgres-ts-rank-the-keyword-step-of-a-payer-search-measured-on-throwaway-cbec30536a78"


def door_order(readme: str) -> list[str]:
    """The README's doors in the order a reader meets them: 'blog' for the series, 'app' for the demo."""
    blog, app = readme.find(PART1), readme.find("](app/")
    return [name for _, name in sorted((i, n) for i, n in ((blog, "blog"), (app, "app")) if i >= 0)]


def test_readme_sends_blog_readers_to_sql_first():
    readme = (REPO / "README.md").read_text()
    assert PART2 in readme and "sql/20_search_function.sql" in readme
    assert door_order(readme) == ["blog", "app"]


def test_door_check_catches_the_app_first():
    assert door_order(f"see [the demo](app/README.md), then [Part 1]({PART1})") == ["app", "blog"]


def test_data_contract_names_the_app_endpoint_and_its_negative_list():
    text = (REPO / "DATA_CONTRACT.md").read_text()
    section = text[text.index("`POST /api/search`"):]
    for needle in ("`q`", "`page`", "`customer_id`", "`lim`", "`off`", "token", "score", "400", "502"):
        assert needle in section, needle

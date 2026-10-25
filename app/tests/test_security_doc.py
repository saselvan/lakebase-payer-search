"""K10: the public security doc lists every PUBLIC default the production read found, as standard defaults, and makes
no claim that PUBLIC or the app's identity has fewer rights (J1)."""
from __future__ import annotations

from pathlib import Path

from tests.test_panel import PUBLIC_DB, PUBLIC_FUNCTIONS, PUBLIC_RELATIONS, PUBLIC_SCHEMAS, overclaims

DOC = Path(__file__).resolve().parents[1] / "SECURITY.md"
# Extension functions PUBLIC may run on production: 91 (read 2026-10-24, REGISTER.md). Not pinned live by name.
PUBLIC_EXTENSION_FUNCTIONS = 91


def missing_defaults(text: str) -> list[str]:
    need = [*sorted(PUBLIC_DB), *(f"{priv} on schema `{s}`" for s, priv in sorted(PUBLIC_SCHEMAS)),
            *(f"`{rel}`" for rel, _ in sorted(PUBLIC_RELATIONS)), *(f"`{fn}`" for fn, _ in sorted(PUBLIC_FUNCTIONS)),
            f"{PUBLIC_EXTENSION_FUNCTIONS} extension functions", "standard defaults"]
    return [n for n in need if n not in text]


def test_security_doc_lists_the_pinned_public_defaults():
    assert missing_defaults(DOC.read_text()) == []


def test_security_doc_makes_no_overclaim():
    assert overclaims(DOC.read_text()) == []


def test_the_check_catches_a_dropped_default():
    text = DOC.read_text()
    assert missing_defaults(text.replace("`pgrst.pre_config()`", "a helper"))
    assert missing_defaults(text.replace("TEMPORARY", "temp"))

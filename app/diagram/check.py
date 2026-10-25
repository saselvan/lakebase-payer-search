"""Check the motion diagram (blog and video composition) against OBJECTIONS H8, H9, H10, H13, H14 and the ticket 04
items T4-1..T4-60 (T4-24..T4-40 are round 3, T4-41..T4-60 round 4, see the work notes).

    uv run python diagram/check.py                      # green run on the files as they are
    uv run python diagram/check.py --src DIR            # the same checks on another copy of the sources (a red run
                                                        # on an older commit: git archive <commit> diagram)
    uv run python diagram/check.py --mutate NAME        # red run: one known lie put into a temp copy
    uv run python diagram/check.py --mutate all         # every mutation in turn; each one must fail

Exit code 0 only when every check passes. The check runs Chromium on local files; nothing leaves the machine.
Ban values (host names, the tenant label, SP ids) are read at run time from the repo and never printed.
The motion checks read the timeline itself: every animation is paused and seeked every 50 ms, and the overlap check
seeks every video frame (1/30 s) at 1440 x 900, at the blog width (744 x 465) and at 390 px.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PUBLIC_REPO = REPO.parent  # read only
FIXTURES = REPO / "tests/fixtures/search_rows.json"
CATECHISM = REPO / "docs/CATECHISM.md"
SOURCES = ("index.html", "drawer.html", "diagram.js", "diagram.css")

# H13: the hops are a fixed list. No table, no hop that skips the app.
HOP_IDS = ["browser", "app", "data-api", "function"]
HOP_NAMES = ["Browser", "This app", "Lakebase Data API", "Postgres function api.search_insurers"]
FRAME = (1440, 900)        # the composition's frame (index.html data-width, data-height)
BLOG = (744, 465)          # the same 16:9 frame at the blog width: the owner measured on 2026-10-18 that LinkedIn
                           # article bodies show images 744 px wide (post 1); Medium was not measured
PHONE = (390, 1400)        # phone width; tall enough that the whole card is in the viewport
DRAWER = (720, 1100)       # the narrowest drawer that plays the motion: the page layout, not a frame
SAMPLE_MS = 50
VIDEO_FRAME_MS = 1000 / 30  # the overlap check seeks every frame of the 30 fps preview
MOVE_MS = 750              # motion-style rule 5: about 0.75 s for each box a move crosses
PACE_TOL_MS = 50           # one sample
HOLD_MIN_MS = 1400         # every label and the answer line, readable and fully settled
END_HOLD_MS = (1500, 3000)  # the final frame holds at the end of the preview
LABEL_DIV = 36             # motion-style floor: labels at least 1/36 of the frame height
# T4-50: reading aloud, Brysbaert (2019), J. Mem. Lang. 109, 104047: "The average oral reading rate (based on 77 studies
# and 5,965 participants) is 183 wpm." (runs/2026-10-23/s3-t04-r4-reading-rate-source.txt). Code tokens count as words.
READ_WPM = 183
STORYBOARD_FRAMES = 7      # box 1, each hop, the climb, the grow and the final frame (stills.py storyboard_times)
CONTACT_MS = 100           # spec (b): the target's fill starts rising within 0.1 s of contact
LIST_PAD_U = 14            # the list's inner padding in the approved preview, in units (T4-26 no lane)
REASON = {200: "OK", 404: "Not Found"}  # T4-79: the reason phrases the check renders (RFC 9110, section 15)
AUTH_NOTE = "OAuth token of the app's service principal. The browser never gets it."

# T4-28: every sentence in the Data API and function steps, and every sentence with a claims row, word for word,
# with the row in docs/CATECHISM.md and text that row must hold. Rows must be grade 1 to 3 and "OK"; C14 is UNPROVEN
# as a whole, so its sentence must trace to graded text in the row's "What is backed" part.
CLAIMS = {
    "customer_id is a label the server sets.": ("C17", ["server-set Tenant label"]),
    "Authorization: " + AUTH_NOTE: ("C7", ["never receives the server's OAuth token"]),
    "Runs the function as an RPC over HTTP POST. The path names the function.":
        ("C14", ["What is backed: the Data API executes functions as RPCs over HTTP POST (grade 3",
                 "this path ran `api.search_insurers` (grade 1"]),
    "Switches into the app's own Postgres role.": ("C18", ["switches into the Postgres role of the calling identity"]),
    "That role has EXECUTE on the two search functions and no rights on any data table.":
        ("C6", ["EXECUTE on `api.search_insurers` and `search_idx.search_core`", "no rights on any data table"]),
    "Typo matching and ranking run inside it.": ("C2", ["pg_trgm for typos"]),
    "Runs with the caller's rights and calls search_idx.search_core.":
        ("C19", ["runs with the caller's rights (SECURITY INVOKER) and only calls `search_idx.search_core`"]),
    "search_core runs with the rights of its owner, search_owner, a role that cannot log in. "
    "It is the only function that reads the search index.":
        ("C19", ["runs with the rights of its owner `search_owner` (SECURITY DEFINER), a role that cannot log in",
                 "it is the only function that reads the search index"]),
    "Both functions pin search_path to pg_catalog, pg_temp.": ("C8", ["Both search functions trust only `pg_catalog, pg_temp`"]),
}
CLAIM_STEPS = {"data-api", "function"}
TABLE_SENTENCE = "That role has EXECUTE on the two search functions and no rights on any data table."
PHONE_FLOOR = 13           # DECISIONS 2026-10-24: at 390 px the drawer is a page with the app's 13 px floor

# T5-1: the short text the motion shows in each box, word for word as Samuel approved it (round 6 preview,
# storyboard-r6/anim.html), and for each line its rows and the text each row must hold, so a short line says no more
# than its row. Headings and code are shared with the final frame.
SHORT_LINES = {
    "app": ["Server sets customer_id. The browser never gets the token."],
    "data-api": ["As the app's role: EXECUTE on 2 functions, no data table rights."],
    "function": ["Typo matching and ranking run here. search_core runs as search_owner, which cannot log in."],
}
SHORT_CLAIMS = {
    "Server sets customer_id. The browser never gets the token.": {
        "C17": ["server-set Tenant label"], "C7": ["never receives the server's OAuth token"]},
    "As the app's role: EXECUTE on 2 functions, no data table rights.": {
        "C18": ["switches into the Postgres role of the calling identity", "runs as the app's own SP role"],
        "C6": ["EXECUTE on `api.search_insurers` and `search_idx.search_core`",
               "no right on any table, view, column or sequence in a data schema"]},
    "Typo matching and ranking run here. search_core runs as search_owner, which cannot log in.": {
        "C2": ["pg_trgm for typos", "in one SQL function"],
        "C19": ["runs with the rights of its owner `search_owner` (SECURITY DEFINER), a role that cannot log in"]},
}
SHORT_C6 = "As the app's role: EXECUTE on 2 functions, no data table rights."

# H14 (and T4-9, T4-10): text that must never be on the diagram or in its artifacts.
BANNED_TEXT = ["databricksapps.com", "cloud.databricks.com", "azuredatabricks.net", "gcp.databricks.com",
               "database.", "t-finder-", "Bearer", "eyJ", "dapi", "example.invalid", "http://", "https://",
               "localhost", "127.0.0.1", "/Users/"]
BANNED_RE = {
    "uuid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    "C14 statement": r"\bSELECT\b|=>|SQL that ran|query that ran",
    "H9 time unit": r"\d\s*(ms|s|sec|seconds?|milliseconds?)\b|\bseconds?\b|\bmilliseconds?\b",
    "T4-20 definition": r"\bCREATE\b|\bRETURNS\b|\bLANGUAGE\b|\$\$|plpgsql",
}
MARKUP_SINKS = r"innerHTML|outerHTML|insertAdjacentHTML|document\.write"

sys.path.insert(0, str(REPO))
from tests.test_static_checks import FORBIDDEN as COPY_LINT  # noqa: E402


def secret_values() -> list[str]:
    """Values that only this deployment knows: the Data API host, the tenant label, SP id prefixes."""
    vals: set[str] = set()
    app_yaml = (REPO / "app.yaml").read_text()
    for m in re.finditer(r"value:\s*\"?([^\"\n]+)", app_yaml):
        v = m.group(1).strip()
        vals.add(re.sub(r"^https?://", "", v).split("/")[0])
        vals.add(v)
    m = re.search(r'FINDER_TENANT",\s*"([^"]+)"', (REPO / "server/main.py").read_text())
    if m:
        vals.add(m.group(1))
    for env in (REPO / ".env",):
        if env.exists():
            for line in env.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() in {"FINDER_TENANT", "DATABRICKS_CLIENT_ID", "DATA_API_BASE", "DATABRICKS_HOST",
                                 "APP_SP_CLIENT_ID"} and v.strip():
                    v = v.strip().strip('"')
                    vals.add(re.sub(r"^https?://", "", v).split("/")[0])
    for d in ("docs", "tests", "scripts", "REGISTER.md"):
        for f in ([REPO / d] if (REPO / d).is_file() else (REPO / d).rglob("*")):
            if f.is_file() and f.suffix in {".md", ".py", ".txt", ".sh", ""}:
                for u in re.findall(BANNED_RE["uuid"], f.read_text(errors="ignore")):
                    vals.add(u[:8])
    return sorted(v for v in vals if len(v) >= 6)


def table_names() -> list[str]:
    """Every table, view and synced table the public repo's SQL creates (H13: none may appear)."""
    names: set[str] = set()
    for f in (PUBLIC_REPO / "sql").glob("*.sql"):
        for m in re.finditer(r"create\s+(?:unlogged\s+)?(?:materialized\s+)?(?:table|view)\s+(?:if\s+not\s+exists\s+)?"
                             r"([\w.\"]+)", f.read_text(), re.I):
            n = m.group(1).replace('"', "")
            names.add(n)
            names.add(n.split(".")[-1])
    return sorted(names)


def catechism_rows() -> dict[str, list[str]]:
    rows = {}
    for line in CATECHISM.read_text().splitlines():
        m = re.match(r"\| (C\d+) \|", line)
        if m:
            rows[m.group(1)] = [c.strip() for c in line.strip().strip("|").split(" | ")]
    return rows


def mask(v: str) -> str:
    return v[:3] + "…" if len(v) > 3 else "…"


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []
        self.secrets = secret_values()
        self.reading: list[tuple] = []
        self.sizes: list[tuple] = []

    def add(self, cid: str, ok: bool, detail: str = "") -> None:
        for s in self.secrets:  # a detail can quote rendered text, which a mutation may have filled with a ban value
            detail = re.sub(re.escape(s), mask(s), detail, flags=re.I)
        self.rows.append((cid, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {cid}" + (f"  ({detail})" if detail else ""))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.rows)


def scan_text(res: Result, where: str, text: str, secrets: list[str]) -> None:
    """Four checks on one text: H14 (hosts, ids, tenant, tokens), C14/H4 (no statement), H9 (no time unit),
    T4-20 (no function definition)."""
    hits = [b for b in BANNED_TEXT if b.lower() in text.lower()]
    hits += ["uuid"] if re.search(BANNED_RE["uuid"], text, re.I) else []
    hits += [f"deployment value {mask(s)}" for s in secrets if s.lower() in text.lower()]
    res.add(f"H14 {where}: no host, branch, SP id, tenant value or token", not hits, ", ".join(hits))
    stmt = re.findall(BANNED_RE["C14 statement"], text)
    res.add(f"H4/C14 {where}: no SQL statement or call form", not stmt, ", ".join(stmt))
    unit = [m.group(0) for m in re.finditer(BANNED_RE["H9 time unit"], text, re.I)]
    res.add(f"H9 {where}: no time unit", not unit, ", ".join(unit))
    defn = re.findall(BANNED_RE["T4-20 definition"], text)
    res.add(f"T4-20 {where}: no function definition text", not defn, ", ".join(defn))


# ---------- mutations (red run): each puts one known lie into a temp copy ----------

def _sub(path: Path, old: str, new: str) -> None:
    s = path.read_text()
    assert old in s, f"mutation anchor missing in {path.name}: {old[:40]}"
    path.write_text(s.replace(old, new, 1))


def _tenant() -> str:
    m = re.search(r'FINDER_TENANT",\s*"([^"]+)"', (REPO / "server/main.py").read_text())
    return m.group(1) if m else "tenant"


JS, CSS, HTML = "diagram.js", "diagram.css", "index.html"
MUTATIONS = {
    # H8: the answer line is visible only while the motion runs; the final frame without motion hides it.
    "fact-only-in-motion": lambda d: _sub(d / CSS, ".pfd-answer-k { display: block;", ".pfd-ans { opacity: 0; }\n.pfd-answer-k { display: block;"),
    # H13: a hop to a table, as if the SP read it.
    "table-hop": lambda d: _sub(d / JS, '      { id: "function", name:',
                                '      { id: "table", name: ["Table ", code("search_idx.synonym_seed")], short: [], full: [] },\n'
                                '      { id: "function", name:'),
    # T4-4: the browser seems to call the Data API directly (the app hop is gone).
    "skip-app-hop": lambda d: _sub(d / JS, '{ id: "app", name: ["This app"],', '{ id: "app", name: [""],'),
    # H14: a workspace host on the path.
    "host-leak": lambda d: _sub(d / JS, '"/api/rpc/search_insurers"]',
                                '"https://dbc-0a1b2c3d-4e5f.cloud.databricks.com/api/rpc/search_insurers"]'),
    # H14, H15, T4-12: the tenant label's value next to its key.
    "tenant-leak": lambda d: _sub(d / JS, '" is " + SERVER_SET[k] + "."', f'" = {_tenant()}."'),
    # H4, C14, T4-10: the call form on screen.
    "call-on-screen": lambda d: _sub(d / JS, '"Typo matching and ranking run inside it."',
                                     '"SELECT * FROM api.search_insurers(q => \'bcbs georgia\')"'),
    # H9, T4-7: a time next to the answer.
    "timing-label": lambda d: _sub(d / JS, 'run.row_count + " rows")', 'run.row_count + " rows \\u00b7 42 ms")'),
    # H10: reduced motion ignored.
    "motion-ignores-reduce": lambda d: _sub(d / JS, "if (stillOnly() || !root.animate)", "if (matches(NARROW) || !root.animate)"),
    # T4-2: the count is typed in, not read from the data object.
    "count-typed-in": lambda d: _sub(d / JS, '(run.row_count === 1 ? "1 row" : run.row_count + " rows")', '"10 rows"'),
    # T4-3: the query reaches the DOM as markup.
    "markup-sink": lambda d: _sub(d / JS, "root.textContent = \"\";", "root.innerHTML = \"\";"),
    # T4-5, H11: the font from a CDN.
    "cdn-font": lambda d: _sub(d / CSS, 'url("fonts/IBMPlexSans-var.woff2")',
                               'url("https://fonts.example.org/IBMPlexSans-var.woff2")'),
    # Copy lint: a claim the page may never make.
    "copy-claim": lambda d: _sub(d / JS, '"Sent the search to the Data API."',
                                 '"Sent the search to the Data API. Instant and secure."'),
    # T4-6: text under the 13 px floor.
    "tiny-text": lambda d: _sub(d / CSS, ".pfd-text p { margin: 0; }", ".pfd-text p { font-size: 11px; margin: 0; }"),
    # T4-20: a function definition typed by hand under hop 4.
    "definition-text": lambda d: _sub(d / JS, '" Function definition"]);',
                                      '" Function definition", node("pre", "pfd-code", ["CREATE FUNCTION '
                                      'api.search_insurers(q text) RETURNS SETOF record LANGUAGE sql"])]);'),
    # T4-22: the packet stays in the final frame (and under reduced motion) as a stray square.
    "packet-stays": lambda d: _sub(d / CSS, "background: var(--blue); opacity: 0; pointer-events: none;",
                                   "background: var(--blue); opacity: 1; pointer-events: none;"),
    # T4-22: the packet does not fade at the end of the motion.
    "packet-stays-after-motion": lambda d: _sub(d / JS, 'add(pk, "opacity", function (v) { return v.pk.o; });',
                                                'add(pk, "opacity", function (v) { return v.g >= 1 ? 1 : v.pk.o; });'),
    # T4-13: a mid-way frame shows a "Rows" place under the function that the final frame does not hold.
    "interim-text": lambda d: (
        _sub(d / JS, '      list.appendChild(li);', '      if (h.id === "function") li.appendChild(node("p", "pfd-ghost", ["Rows"]));\n'
                                                     '      list.appendChild(li);'),
        _sub(d / JS, '    var ol = q(".pfd-hops");', '    add(q(".pfd-ghost"), "opacity", function (v) { return v.app[3] > 0.5 && v.g < 1 ? 1 : 0; });\n'
                                                      '    var ol = q(".pfd-hops");'),
        _sub(d / CSS, ".pfd-def {", ".pfd-ghost { opacity: 0; }\n.pfd-def {")),
    # H9: a time on the packet's label.
    "packet-timing-label": lambda d: _sub(d / JS, 'label("pfd-tag-app", l.app)', 'label("pfd-tag-app", l.app + " \\u00b7 42 ms")'),

    # ----- round 3 (T4-24..T4-40), moved onto the round 6 code -----
    # T4-24: the label at the app drops the server-set key, so it differs from box 2's body keys.
    "label-drops-customer-id": lambda d: _sub(d / JS, 'app: run.app_body_keys.join(", "),',
                                              'app: run.app_body_keys.filter(function (k) { return !SERVER_SET[k]; }).join(", "),'),
    # T4-24: the label from box 1 to box 2 carries a key the browser never sends.
    "label-1-drifts": lambda d: _sub(d / JS, 'browser: Object.keys(run.browser_body).join(", "),',
                                     'browser: Object.keys(run.browser_body).concat(["lim"]).join(", "),'),
    # T4-18 (round 6, replaces label-answer-early): the first hop shows the app's keys, before the app sent them.
    "label-app-early": lambda d: _sub(d / JS, 'add(q(".pfd-tag-app"), "opacity", function (v) { return v.tag.k === 0 ? 0 : 1; });',
                                      'add(q(".pfd-tag-app"), "opacity", function (v) { return 1; });'),
    # T4-25: the answer appears in box 1 before the answer's square arrives (the grow starts with the climb).
    "answer-early": lambda d: _sub(d / JS, "T.g = c + MOVE + GROW_GAP;", "T.g = c;"),
    # T4-82, spec (a) (round 6, replaces answer-space-reserved): the boxes have their full height from the start, so
    # the short text sits in a box with an empty area under it.
    "box-full-height-early": lambda d: _sub(d / JS, "h: M.sh.map(function (s, i) { return lerp(s, M.fh[i], g); }),",
                                            "h: M.fh.slice(),"),
    # T4-26 (round 6, replaces no-shift): the gap under box 1 is wider than the others.
    "uneven-gap": lambda d: _sub(d / JS, 'add(li, "marginBottom", function (v) { return px(v.mb); }',
                                 'add(li, "marginBottom", function (v) { return px(v.mb + (i === 0 ? 20 : 0)); }'),
    # T4-26: a lane on the left for the labels pushes the steps off the card's edge.
    "left-lane": lambda d: _sub(d / CSS, "padding: var(--pad-full) calc(14 * var(--u));", "padding: var(--pad-full) calc(160 * var(--u));"),
    # T4-27: the step numbers read out of order.
    "numbers-swapped": lambda d: _sub(d / JS, 'node("li", "pfd-hop", [square(i + 1),', 'node("li", "pfd-hop", [square([1, 3, 2, 4][i]),'),
    # T4-83, spec (b) (round 6, replaces step-enters-late): box k+1 appears after the packet left for it, so it is
    # not on screen at contact.
    "appears-late": lambda d: _sub(d / JS, "var APPEAR_LEAD = 0.2;", "var APPEAR_LEAD = -0.5;"),
    # T4-28: a security sentence that is not word for word a claims row.
    "unclaimed-sentence": lambda d: _sub(d / JS, '"Typo matching and ranking run inside it."',
                                         '"Typo matching, ranking and access checks run inside it."'),
    # T4-28: a sentence pointed at an UNPROVEN row.
    "claim-unproven": lambda d: _sub(d / JS, '"Typo matching and ranking run inside it."], "C2")',
                                     '"Typo matching and ranking run inside it."], "C5")'),
    # T4-29: the tag runs past the card's edge (at 1440, 744 and 720; the phone has no tag).
    "tag-too-wide": lambda d: _sub(d / CSS, "grid-area: 1 / 1; justify-self: start; padding: 0 calc(6 * var(--u));",
                                   "grid-area: 1 / 1; justify-self: start; padding: 0 calc(600 * var(--u));"),
    # T4-33: a label holds 0.5 s, too short to read.
    "short-hold": lambda d: _sub(d / JS, "h.off = h.at + tagRead;", "h.off = h.at + 0.5;"),
    # T4-34: the answer climbs three boxes in half a move.
    "fast-climb": lambda d: _sub(d / JS, "lerp(sq(n - 1), sq(0), R(t, T.cl, MOVE))", "lerp(sq(n - 1), sq(0), R(t, T.cl, MOVE / 2))"),
    # T4-34: the moves use an ease that is not symmetric (an ease-out).
    "asymmetric-ease": lambda d: _sub(d / JS, "x >= 1 ? 1 : (1 - Math.cos(Math.PI * x)) / 2; };", "x >= 1 ? 1 : 1 - (1 - x) * (1 - x); };"),
    # T4-34: the composition holds the final frame 6 s, past the 3 s end hold.
    "long-timeline": lambda d: _sub(d / HTML, 'data-duration="25.57"', 'data-duration="28.57"'),
    # T4-80 (round 6, replaces handover-jump): the answer's square climbs in front of the step numbers.
    "answer-in-front": lambda d: _sub(d / CSS, ".pfd-ans-sq { z-index: 1; }", ".pfd-ans-sq { z-index: 5; }"),
    # T4-36: box 1's fill does not drain, so two boxes are lit at once.
    "two-fills": lambda d: _sub(d / JS, "lit: [1 - on(0) + R(", "lit: [1 + R("),
    # T4-36: the fill is a hue with no lightness step, so it fails in greyscale.
    "fill-no-lightness": lambda d: _sub(d / CSS, "--fill: #cfd6ef;", "--fill: #fbfbfd;"),
    # T4-37: boxes 2 to 4 grow during the climb, before the answer is in box 1.
    "shift-early": lambda d: _sub(d / JS, "T.g = c + MOVE + GROW_GAP;", "T.g = c + 0.3;"),
    # T4-38: the card is not in the middle of the frame.
    "card-off-center": lambda d: _sub(d / CSS, "  align-items: center;\n  justify-content: center;\n", "  align-items: flex-start;\n  justify-content: center;\n"),
    # T4-11: the request line does not wrap, so a long query runs out of its code box.
    "long-query-no-wrap": lambda d: _sub(d / CSS, "white-space: pre-wrap; overflow-wrap: break-word;", "white-space: pre;"),
    # T4-29: the frame's padding and border reach the phone, so the card no longer fills 390 px.
    "phone-gets-frame": lambda d: _sub(d / CSS, "@media (min-width: 720px) and (not (min-aspect-ratio: 3/2)) {",
                                       "@media (min-width: 380px) and (not (min-aspect-ratio: 3/2)) {"),
    # T4-40: the gap is shorter than the tag, so a waiting label covers box text.
    "hold-on-text": lambda d: _sub(d / JS, "mbS: 36 * u,", "mbS: 16 * u,"),

    # ----- round 4 (T4-41..T4-60), moved onto the round 6 code -----
    # T4-41: the answer sits on top of the request's code box.
    "answer-in-request": lambda d: _sub(d / CSS, ".pfd-answer { margin-top: calc(4 * var(--u));", ".pfd-answer { margin-top: calc(-40 * var(--u));"),
    # T4-42: tokens are inline again, so a JSON pair breaks across two lines where a code line wraps (the code lines
    # are narrowed to 16 characters: at 14.3 units no code line of this search wraps at 1440, 744 or 390).
    "json-mid-break": lambda d: _sub(d / CSS, ".pfd-tok { display: inline-block;",
                                     ".pfd-code pre { max-width: 16ch; }\n.pfd-tok { display: inline;"),
    # Copy lint, T4-44: the C6 sentence says "any table", which the role does not meet (PUBLIC platform views).
    "c6-any-table": lambda d: _sub(d / JS, "no rights on any data table.", "no rights on any table."),
    # T4-48, T4-49: the tag shows while it is still inside box 1, so it crosses box 1's words.
    "tag-moves-visible": lambda d: _sub(d / JS, "o: clamp((y - bot(k)) / (22 * U)) * (1 - clamp", "o: (1 - clamp"),
    # T4-50: the holds use a reading rate of 400 words a minute, faster than people read aloud.
    "short-read": lambda d: _sub(d / JS, "var WORDS_PER_S = 183 / 60;", "var WORDS_PER_S = 400 / 60;"),
    # T4-46: the frame's body text at 11 units, under h/36 at 1440 x 900 and at 744 x 465.
    "frame-text-small": lambda d: _sub(d / CSS, "--t-body: calc(15 * var(--u));", "--t-body: calc(11 * var(--u));"),
    # T4-59: wider gaps make the card taller than the frame.
    "card-taller-than-frame": lambda d: _sub(d / CSS, "--mb-full: calc(4 * var(--u));", "--mb-full: calc(90 * var(--u));"),
    # T4-58: the composition mounts the Play control.
    "button-in-composition": lambda d: _sub(d / HTML, '<div data-hf-id="hf-b9ut" id="pfd"></div>',
                                            '<div data-hf-id="hf-b9ut" id="pfd" data-controls></div>'),
    # T4-25 (arrival): the climb stops at box 2, so the answer never arrives in box 1.
    "climb-stops-short": lambda d: _sub(d / JS, "lerp(sq(n - 1), sq(0), R(t, T.cl, MOVE))", "lerp(sq(n - 1), sq(1), R(t, T.cl, MOVE))"),

    # ----- round 5 (T4-63..T4-77), moved onto the round 6 code -----
    # T5-1: a short line drifts from the approved text.
    "short-line-drift": lambda d: _sub(d / JS, '". The browser never gets the token."', '". The browser never sees a secret."'),
    # T5-1, H13: the C6 short line says "no table rights", wider than its row.
    "short-says-more": lambda d: _sub(d / JS, "EXECUTE on 2 functions, no data table rights.", "EXECUTE on 2 functions, no table rights."),
    # T5-1: the Data API's short line loses its C6 mark.
    "short-claim-missing": lambda d: _sub(d / JS, '"C18 C6")', '"C18")'),
    # T5-1 (T4-70): the short layer reaches screen readers, so every fact is read twice.
    "short-not-hidden": lambda d: _sub(d / JS, 'shortText.setAttribute("aria-hidden", "true");', ''),
    # Copy lint (T4-77): the rejected wording returns.
    "token-stays": lambda d: _sub(d / JS, '". The browser never gets the token."', '". Token stays on the server."'),
    # T5-3: the full text fades in while the short text fades out.
    "full-early": lambda d: _sub(d / JS, "v.full = E((gl - 0.65) / 0.35);", "v.full = E(gl / 0.35);"),
    # T5-2: the full text shows through the motion.
    "full-in-motion": lambda d: _sub(d / JS, "v.full = E((gl - 0.65) / 0.35);", "v.full = 1;"),
    # H8 (T4-74): the motion ends with the full text hidden.
    "full-stays-hidden": lambda d: _sub(d / JS, "v.full = E((gl - 0.65) / 0.35);", "v.full = 0;"),
    # T5-4: the phone page animates.
    "phone-animates": lambda d: _sub(d / JS, 'var NARROW = "(max-width: 719px)";', 'var NARROW = "(max-width: 1px)";'),
    # T5-5: the phone's code text at 12.5 px, under the 13 px floor.
    "phone-text-12": lambda d: _sub(d / CSS, "--t-mono: calc(14.3 * var(--u));", "--t-mono: calc(12.5 * var(--u));"),

    # ----- round 6 (T4-78..T4-86): the one-line mutation for each new check -----
    # T4-84, spec (c) (replaces answer-read-cut, whose short answer hold is gone): box 1 fades in from a blank frame.
    "box1-fades-in": lambda d: _sub(d / JS, "app: [R(t, T.b1, MOVE)]", "app: [R(t, 0, MOVE)]"),
    # T4-83, spec (b): the target lights before the packet touches it.
    "lights-early": lambda d: _sub(d / JS, "var on = function (k) { return R(t, H[k].at, FILL); };",
                                   "var on = function (k) { return R(t, H[k].at - 0.3, FILL); };"),
    # T4-81: the grow move shifts the headings against their numbers.
    "heading-moves": lambda d: _sub(d / CSS, "align-items: start; margin-bottom: var(--mb-full);", "align-items: center; margin-bottom: var(--mb-full);"),
    # T4-79: "OK" typed as a constant, not derived from the status.
    "ok-typed": lambda d: _sub(d / JS, 'return REASON[code] ? code + " " + REASON[code] : String(code);', 'return code + " OK";'),
    # T4-79: the status shown bare, with no reason phrase ("200 \u00b7 10 rows", the round 5 text).
    "status-bare": lambda d: _sub(d / JS, 'return REASON[code] ? code + " " + REASON[code] : String(code);', 'return String(code);'),
}


def _png_text(path: Path, key: str, text: str) -> None:
    """Add one tEXt chunk after the PNG header chunk."""
    data = path.read_bytes()
    body = key.encode("latin-1") + b"\0" + text.encode("latin-1")
    chunk = len(body).to_bytes(4, "big") + b"tEXt" + body + zlib.crc32(b"tEXt" + body).to_bytes(4, "big")
    path.write_bytes(data[:33] + chunk + data[33:])


def _gray(video: Path, sec: float) -> bytes:
    """One frame of a video as 360 x 225 grey bytes."""
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{sec}", "-i", str(video), "-frames:v", "1",
                           "-vf", "scale=360:225", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                          capture_output=True, check=True).stdout


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def _still_from(src_mutation, out: Path) -> None:
    """Shoot still.png from a copy of the sources with one source mutation (a name or a function) in it."""
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        for n in SOURCES:
            shutil.copy(HERE / n, d / n)
        shutil.copytree(HERE / "fonts", d / "fonts")
        (MUTATIONS[src_mutation] if isinstance(src_mutation, str) else src_mutation)(d)
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": FRAME[0], "height": FRAME[1]}, device_scale_factor=2)
            pg.goto((d / "index.html").as_uri())
            pg.evaluate("document.fonts.ready")
            pg.evaluate("document.getAnimations().forEach(a => a.finish())")
            pg.screenshot(path=str(out / "still.png"), animations="allow")
            b.close()


# Red runs for the rendered files: each one spoils a copy of out/ and runs only the artifact checks.
ARTIFACT_MUTATIONS = {
    "artifact-storyboard-missing": lambda o: (o / "storyboard.png").unlink(),
    "artifact-storyboard-6-frames": lambda o: (
        _ffmpeg("-i", str(o / "storyboard.png"), "-vf", f"crop={6 * 360}:ih:0:0", str(o / "s6.png")),
        (o / "s6.png").replace(o / "storyboard.png")),
    "artifact-preview-too-long": lambda o: (
        _ffmpeg("-stream_loop", "1", "-i", str(o / "preview.mp4"), "-c", "copy", str(o / "long.mp4")),
        (o / "long.mp4").replace(o / "preview.mp4")),
    "artifact-preview-too-short": lambda o: (
        _ffmpeg("-i", str(o / "preview.mp4"), "-t", "8", "-c", "copy", str(o / "short.mp4")),
        (o / "short.mp4").replace(o / "preview.mp4")),
    "artifact-preview-static": lambda o: (
        _ffmpeg("-sseof", "-0.5", "-i", str(o / "preview.mp4"), "-frames:v", "1", str(o / "last.png")),
        _ffmpeg("-loop", "1", "-framerate", "30", "-i", str(o / "last.png"), "-t",
                re.search(r'data-duration="([\d.]+)"', (HERE / "index.html").read_text()).group(1),
                "-pix_fmt", "yuv420p", str(o / "static.mp4")),
        (o / "static.mp4").replace(o / "preview.mp4")),
    "artifact-still-mid-motion": lambda o: (
        _ffmpeg("-ss", "5.25", "-i", str(o / "preview.mp4"), "-frames:v", "1", "-vf", "scale=2880:1800", str(o / "still.png"))),
    "artifact-still-390-mid-motion": lambda o: (
        _ffmpeg("-ss", "5.25", "-i", str(o / "preview.mp4"), "-frames:v", "1", "-vf", "scale=780:-2", str(o / "still-390.png"))),
    "artifact-still-with-definition": lambda o: _still_from("definition-text", o),
    # H10: the list is not laid out again at the last keyframe; the still keeps a sub-pixel paint of the short layout.
    "artifact-still-no-relayout": lambda o: _still_from(
        lambda d: _sub(d / JS, '    add(ol, "display", function () { return "flow-root"; }, "block");\n', ""), o),
    "artifact-still-1x": lambda o: (_ffmpeg("-i", str(o / "still.png"), "-vf", "scale=1440:900", str(o / "s1.png")),
                                    (o / "s1.png").replace(o / "still.png")),
    "artifact-still-390-missing": lambda o: (o / "still-390.png").unlink(),
    "artifact-png-definition-chunk": lambda o: _png_text(o / "storyboard.png", "Comment",
                                                         "CREATE FUNCTION api.search_insurers LANGUAGE sql"),
    "artifact-mp4-definition-tag": lambda o: (
        _ffmpeg("-i", str(o / "preview.mp4"), "-c", "copy", "-metadata", "comment=CREATE FUNCTION x RETURNS SETOF record",
                str(o / "tag.mp4")),
        (o / "tag.mp4").replace(o / "preview.mp4")),
}

# ---------- browser checks ----------

PAGE_JS = r"""
() => {
  const root = document.getElementById('pfd');
  const H = window.innerHeight, W = window.innerWidth;
  const hidden = el => el.closest('[aria-hidden="true"]');
  const alpha = el => { let o = 1; for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
                          const cs = getComputedStyle(e); if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
                          o *= parseFloat(cs.opacity); } return o; };
  const visible = el => { const r = el.getBoundingClientRect();
                          return alpha(el) >= 0.99 && r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= H + 0.5; };
  const shown = el => alpha(el) > 0.01;
  const R = el => { const r = el.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom }; };
  const textBox = el => { const n = [...el.childNodes].find(c => c.nodeType === 3); if (!n) return null;
                          const g = document.createRange(); g.selectNodeContents(n); return R(g); };
  const clipB = el => { const m = getComputedStyle(el).clipPath.match(/inset\(([^)]*)\)/); if (!m) return 0;
                        const v = m[1].trim().split(/\s+/).map(parseFloat); return v.length >= 3 ? v[2] : v[0]; };
  const rgb = s => (s.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => { const f = c => (c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
                               return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const bg = el => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor);
                       if (c.length === 3 || (c.length === 4 && c[3] > 0)) return c.slice(0, 3); } return [255, 255, 255]; };
  const texts = [];
  root.querySelectorAll('*').forEach(el => {
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    if (!own) return;
    const cs = getComputedStyle(el);
    const a = lum(rgb(cs.color)), b = lum(bg(el));
    texts.push({ text: own, px: parseFloat(cs.fontSize), ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
                 visible: visible(el), shown: shown(el), hidden: !!hidden(el),
                 short: !!el.closest('.pfd-short'), full: !!el.closest('.pfd-full'), tag: !!el.closest('.pfd-tag') });
  });
  // T5-1..T5-3: the short lines (motion) and the full lines (final frame) of each box.
  const norm = s => s.replace(/\s+/g, ' ').trim();
  const shortLines = [...root.querySelectorAll('.pfd-short-line')].map(p => ({
    hop: p.closest('li').dataset.hop, text: norm(p.textContent), claim: p.dataset.claim || null,
    a: alpha(p), hiddenLayer: !!hidden(p) }));
  const fullEls = [...root.querySelectorAll('.pfd-full, .pfd-answer-k')];
  const fullHidden = [...root.querySelectorAll('.pfd-note, .pfd-def, .pfd-answer-k')].filter(e => hidden(e)).map(e => norm(e.textContent));
  const seen = [];
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const el = n.parentElement;
    if (!hidden(el) && visible(el)) seen.push(n.textContent);
  }
  const q = s => root.querySelector(s);
  const lis = [...root.querySelectorAll('.pfd-hops > li')];
  const code1 = q('[data-hop="browser"] .pfd-full .pfd-code') || q('[data-hop="browser"] .pfd-code'), pre1 = code1 && code1.querySelector('pre');
  const ans = q('.pfd-ans'), tag = q('.pfd-tag'), pk = q('.pfd-pk-sq'), dot = q('.pfd-ans-sq'), link = q('.pfd-link');
  const fill = q('.pfd-fill');
  // T4-41: the answer is not inside the request's box, the two boxes do not touch, and their grounds differ.
  const reqBox = pre1 ? (pre1.closest('.pfd-code') || pre1) : null;
  const ansBox = ans ? (ans.closest('.pfd-answer') || ans) : null;
  const answer = reqBox && ansBox ? { inRequest: reqBox.contains(ans), gap: R(ansBox).t - R(reqBox).b,
                                      grounds: [bg(reqBox).join(','), bg(ansBox).join(',')] } : null;
  // T4-50: the words a reader sees in each box (whitespace tokens of each fully opaque text block; code counts).
  const boxWords = lis.map(li => { const out = [];
    li.querySelectorAll(':scope > .pfd-body :is(h3, p, pre, .pfd-def)').forEach(e => {
      const w = document.createTreeWalker(e, NodeFilter.SHOW_TEXT); let s = '';
      for (let n = w.nextNode(); n; n = w.nextNode()) s += alpha(n.parentElement) >= 0.99 ? n.textContent : ' ';
      out.push(s); });
    return out.join(' ').split(/\s+/).filter(Boolean); });
  return { text: root.innerText, seen: seen.join(''), texts, answer, boxWords, shortLines, fullHidden,
           shortA: shortLines.length ? Math.max(...shortLines.map(s => s.a)) : 0,
           shortMin: shortLines.length ? Math.min(...shortLines.map(s => s.a)) : 0,
           fullA: fullEls.length ? Math.max(...fullEls.map(alpha)) : 0,
           buttons: [...document.querySelectorAll('button')].map(b => b.textContent.trim()),
           labelPx: [...root.querySelectorAll('.pfd-tag-label')].map(e => parseFloat(getComputedStyle(e).fontSize)),
           hops: lis.map(h => ({ id: h.dataset.hop, name: (h.querySelector('.pfd-short-box > .pfd-name') || h.querySelector('.pfd-name')).textContent,
                                 a: alpha(h) })),
           // Round 6: each box, its two layers (heights and opacity), and its visible heading.
           // (Round 5 and older have one layer: the body stands for both, so the checks still run on them.)
           boxes: lis.map(li => { const b = li.querySelector(':scope > .pfd-body'), s = li.querySelector('.pfd-short-box') || b,
                                        f = li.querySelector('.pfd-full-box') || b, hd = li.querySelector('.pfd-short-box > .pfd-name') || li.querySelector('.pfd-name');
                                        const sh = li.querySelector('.pfd-short'), fu = li.querySelector('.pfd-full');
                                  return { h: R(b).b - R(b).t, sh: R(s).b - R(s).t, fh: R(f).b - R(f).t, a: alpha(b),
                                           sa: sh ? alpha(sh) : 1, fa: fu ? alpha(fu) : 0,
                                           head: R(hd), headA: alpha(hd), fillA: alpha(li.querySelector('.pfd-fill')) }; }),
           dot: dot ? { ...R(dot), a: alpha(dot), z: parseInt(getComputedStyle(dot).zIndex) || 0 } : null,
           sqZ: lis.length ? parseInt(getComputedStyle(lis[0].querySelector(':scope > .pfd-sq')).zIndex) || 0 : 0,
           links: root.querySelectorAll('.pfd-link').length,
           squares: lis.map(li => { const e = li.querySelector(':scope > .pfd-sq'); return { ...R(e), text: e.textContent, a: alpha(e) }; }),
           bodies: lis.map(li => R(li.querySelector(':scope > .pfd-body'))),
           code1: code1 && R(code1), pre1: pre1 && R(pre1),
           ans: ans ? { ...R(ans), a: alpha(ans), text: ans.textContent, txt: textBox(ans),
                        inCode1: !!(code1 && pre1 && code1.contains(ans) && (pre1.compareDocumentPosition(ans) & 4)) } : null,
           fills: [...root.querySelectorAll('.pfd-fill')].map(alpha),
           fillLum: fill ? lum(rgb(getComputedStyle(fill).backgroundColor)) : 1,
           paperLum: lum(bg(root)),
           pk: pk ? { ...R(pk), a: alpha(pk) } : null,
           tag: tag ? { ...R(tag), a: alpha(tag) } : null,
           labels: [...root.querySelectorAll('.pfd-tag-label')].map(e => ({ kind: e.className.replace('pfd-tag-label ', ''),
                                                                         text: e.textContent, a: alpha(e) })),
           lineX: link ? (R(link).l + R(link).r) / 2 : null,
           card: R(q('.pfd-card')), W, H,
           browserCode: pre1 ? pre1.textContent : '',
           appCode: (q('[data-hop="app"] .pfd-full .pfd-code') || q('[data-hop="app"] .pfd-code') || {}).textContent || '',
           notes: [...root.querySelectorAll('.pfd-note')].map(n => ({ hop: n.closest('li').dataset.hop, claim: n.dataset.claim || null,
                                                                       text: n.textContent.replace(/\s+/g, ' ').trim() })),
           def: (() => { const d = q('[data-hop="function"] .pfd-def');
                         return d ? { text: d.textContent, kids: d.children.length, placeholder: d.dataset.placeholder,
                                      shown: visible(d) } : null; })(),
           anims: document.getAnimations().length,
           fonts: [document.fonts.check('16px "IBM Plex Sans"'), document.fonts.check('16px "IBM Plex Mono"')],
           overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
           preFit: [...root.querySelectorAll('.pfd-code pre')].every(e => e.scrollWidth <= e.clientWidth + 1) };
}
"""

# T4-48: seek every video frame; at each, the moving tag (every label with any opacity) against the
# line boxes of every other text on screen with any opacity. Returns the frames with an overlap and the text covered.
OVERLAP_JS = r"""
(ts) => {
  const root = document.getElementById('pfd');
  const alpha = el => { let o = 1; for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
                          const cs = getComputedStyle(e); if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
                          o *= parseFloat(cs.opacity); } return o; };
  const hit = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
  const nodes = [];
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode())
    if (n.textContent.trim() && !n.parentElement.closest('.pfd-tag')) nodes.push(n);
  const movers = [...root.querySelectorAll('.pfd-tag-label')];
  const out = [];
  for (const t of ts) {
    document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; });
    const boxes = movers.filter(m => alpha(m) > 0.01).map(m => m.getBoundingClientRect()).filter(r => r.width > 0);
    if (!boxes.length) continue;
    const covered = [];
    for (const n of nodes) {
      if (alpha(n.parentElement) <= 0.01) continue;
      const g = document.createRange(); g.selectNodeContents(n);
      if ([...g.getClientRects()].some(r => r.width > 0 && boxes.some(b => hit(b, r)))) covered.push(n.textContent.trim().slice(0, 28));
    }
    if (covered.length) out.push({ t, covered });
  }
  return out;
}
"""

# T4-42: no token on two lines. A token is a JSON pair, "body keys:", or any other run of non-space characters.
# Structure-agnostic (character ranges inside each text node), so it runs on older commits too.
TOKENS_JS = r"""
() => {
  const root = document.getElementById('pfd');
  const out = [];
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const s = n.textContent, spans = [], taken = new Array(s.length).fill(false);
    const re1 = /\{?"[^"]*":\s*(?:"[^"]*"|[^,}\s]+)[,}]?|body keys:/g;
    for (let m; (m = re1.exec(s));) { spans.push([m.index, m.index + m[0].length]); for (let i = m.index; i < m.index + m[0].length; i++) taken[i] = true; }
    const re2 = /\S+/g;
    for (let m; (m = re2.exec(s));) {
      let a = m.index; const e = a + m[0].length;
      while (a < e) { while (a < e && taken[a]) a++; let b = a; while (b < e && !taken[b]) b++; if (b > a) spans.push([a, b]); a = b; }
    }
    for (const [a, b] of spans) {
      const g = document.createRange(); g.setStart(n, a); g.setEnd(n, b);
      const tops = [...g.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top));
      if (tops.length && Math.max(...tops) - Math.min(...tops) > 1) out.push(s.slice(a, b));
    }
  }
  return out;
}
"""

SAMPLE_JS = r"""
(ts) => ts.map(t => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; }); return window.__pfdProbe(); })
"""


def facts(run: dict) -> list[str]:
    rows = "1 row" if run["row_count"] == 1 else f"{run['row_count']} rows"
    return HOP_NAMES + ["Function definition", "POST /api/search",
                        '"q": ' + json.dumps(run["browser_body"]["q"]), '"page": 1',
                        "POST /api/rpc/search_insurers", "body keys: q, customer_id, lim, off",
                        f"{run['status']} {REASON[run['status']]} · {rows}"] + list(CLAIMS)


def text_values(st: dict) -> dict:
    """What the box text says, read back from the page: the values the packet's labels must equal."""
    body = st["browserCode"].split("\n", 1)[1] if "\n" in st["browserCode"] else "{}"
    keys = re.search(r"body keys: (.*)", st["appCode"])
    return {"browser": ", ".join(json.loads(body)), "app": keys.group(1).strip() if keys else "",
            "answer": st["ans"]["text"] if st["ans"] else ""}


def overlaps(a: dict, b: dict) -> bool:
    return a["l"] < b["r"] - 0.5 and b["l"] < a["r"] - 0.5 and a["t"] < b["b"] - 0.5 and b["t"] < a["b"] - 0.5


def seek(pg, t: float) -> dict:
    pg.evaluate(f"document.getAnimations().forEach(a => {{ a.pause(); a.currentTime = {t}; }})")
    return pg.evaluate(PAGE_JS)


def frame_geometry(st: dict) -> dict:
    """Where things are in one frame: box bottoms, the gaps between boxes, the numbers' middles, the packet, the tag
    and the answer's square."""
    vis = [b["b"] for b in st["bodies"]]
    sq = st["squares"]
    mid = lambda r: ((r["t"] + r["b"]) / 2) if r else None
    return {"vis": vis, "gaps": [sq[k + 1]["t"] - vis[k] for k in range(len(vis) - 1)],
            "tops": [b["t"] for b in st["bodies"]],
            "sqC": [(s["t"] + s["b"]) / 2 for s in sq],
            "pkY": mid(st["pk"]), "pkX": (st["pk"]["l"] + st["pk"]["r"]) / 2,
            "dotY": mid(st["dot"]), "dotX": (st["dot"]["l"] + st["dot"]["r"]) / 2 if st["dot"] else None,
            "tagY": mid(st["tag"])}


def same_place(a: dict, b: dict) -> bool:
    return abs(a["t"] - b["t"]) < 0.01 and abs(a["l"] - b["l"]) < 0.01


def schedule_of(pg) -> dict:
    return pg.evaluate("() => { const P = window.PayerFinderDiagram, r = document.getElementById('pfd');"
                       " return P && P.schedule ? P.schedule(r) : {}; }")


def sample_motion(res: Result, pg, final: dict, duration: int, where: str, step: int, timing: bool) -> None:
    """Step through the timeline with every animation paused (T4-13, T4-18, T4-22..T4-40, T4-80..T4-84)."""
    vals = text_values(final)
    final_texts = {t["text"] for t in final["texts"] if t["visible"] and not t["hidden"]}
    # T5-2: what the motion may show: the short lines, and every final-frame text outside the full layer.
    motion_texts = {t["text"] for t in final["texts"] if t["short"]} | \
        {t["text"] for t in final["texts"] if t["visible"] and not t["hidden"] and not t["full"]}
    sched = schedule_of(pg).get("m", {})
    cross_s = sched.get("shortOut", {}).get("s", duration + 1)
    cross_e = sched.get("fullIn", {}).get("e", duration + 1)
    climb_s = sched.get("climb", {}).get("s", duration + 1)
    ts = list(range(0, duration + 1, step))
    pg.evaluate(f"() => {{ window.__pfdProbe = {PAGE_JS}; }}")
    frames = list(zip(ts, pg.evaluate(SAMPLE_JS, ts)))
    strays, wrong, early, empty, uneven, order, cover, outside, held_cover, offline, fill_sum, shift, together, front = \
        ([] for _ in range(14))
    arrived = climbing = False
    arrive_t = None
    peak = [0.0] * len(frames[0][1]["boxes"])
    for i, (t, st) in enumerate(frames):
        g = frame_geometry(st)
        for x in st["texts"]:
            if not x["shown"] or x["tag"] or (x["hidden"] and not x["short"]):
                continue
            if t < cross_s:
                bad_text = x["full"] or x["text"] not in motion_texts
            elif t >= cross_e:
                bad_text = x["short"] or x["text"] not in final_texts
            else:
                bad_text = x["text"] not in motion_texts | final_texts
            if bad_text:
                strays.append(f"{t}:{x['text'][:20]}")
        # T5-3: the short text and the full text never show at the same time.
        if st["shortA"] > 0.01 and st["fullA"] > 0.01:
            together.append(t)
        # T4-26: steps 1 to 4 at even spacing in every frame.
        if max(g["gaps"]) - min(g["gaps"]) > 0.5:
            uneven.append(f"{t}:{[round(x, 1) for x in g['gaps']]}")
        # T4-27 (round 6): numbered 1 to 4 from the top in every frame, and a box that is on screen stays on screen.
        sq = sorted(st["squares"], key=lambda s: s["t"])
        if [s["text"] for s in sq] != ["1", "2", "3", "4"]:
            order.append(f"{t}:order")
        for k, bx in enumerate(st["boxes"]):
            if bx["a"] < peak[k] - 0.01 or st["squares"][k]["a"] < peak[k] - 0.01:
                order.append(f"{t}:box {k + 1} fades")
            peak[k] = max(peak[k], bx["a"])
        dot_on = st["dot"] is not None and st["dot"]["a"] > 0.01
        climbing = climbing or dot_on
        # The answer has arrived when its square reaches box 1's number.
        if dot_on and abs(g["dotY"] - g["sqC"][0]) <= 1 and not arrived:
            arrived, arrive_t = True, t
        pk_on = st["pk"]["a"] > 0.01
        if st["lineX"] is not None and ((pk_on and abs(g["pkX"] - st["lineX"]) > 1)
                                        or (dot_on and abs(g["dotX"] - st["lineX"]) > 1)):
            offline.append(t)
        # T4-80: the answer's square climbs under the step numbers.
        if dot_on and any(overlaps(st["dot"], s) for s in st["squares"]) and st["dot"]["z"] >= st["sqZ"]:
            front.append(t)
        # T4-25: the answer line only after the answer's square is in box 1.
        if not arrived and st["ans"]["a"] > 0.01:
            early.append(t)
        # T4-82, spec (a): no box is taller than the text layer it shows plus its padding. The one exception is the
        # grow move itself: a box whose height is changing, never taller than its full layer.
        for k, bx in enumerate(st["boxes"]):
            if bx["a"] <= 0.01:
                continue
            room = max([bx["sh"]] * (bx["sa"] > 0.01) + [bx["fh"]] * (bx["fa"] > 0.01) + [st["squares"][k]["b"] - st["squares"][k]["t"]])
            growing = any(abs(frames[j][1]["boxes"][k]["h"] - bx["h"]) > 0.1 for j in (i - 1, i + 1) if 0 <= j < len(frames))
            if bx["h"] > room + 0.5 and not (growing and bx["h"] <= bx["fh"] + 0.5):
                empty.append(f"{t}:box {k + 1} {bx['h'] - room:.0f} px")
        # T4-37: boxes 2 to 4 stay put until the answer is in box 1.
        if not arrived and any(abs(st["bodies"][k]["t"] - frames[0][1]["bodies"][k]["t"]) > 0.25 for k in (1, 2, 3)):
            shift.append(t)
        # T4-18, T4-24: the label says what rides on this hop: box 1's keys between boxes 1 and 2, box 2's keys after.
        if st["tag"]["a"] >= 0.5:
            texts = {l["text"] for l in st["labels"] if l["a"] >= 0.5}
            hop = sum(1 for top in g["tops"][1:] if top <= g["tagY"])
            allowed = set() if climbing else ({vals["browser"]} if hop == 0 else {vals["app"]})
            if not texts or not texts <= allowed:
                wrong.append(f"{t}:{sorted(texts)}")
        # T4-29, T4-40: the tag stays inside the card, never covers a step square, and waits clear of every box.
        if st["tag"]["a"] > 0.01:
            c = st["card"]
            if st["tag"]["l"] < c["l"] or st["tag"]["r"] > c["r"] - 2:
                outside.append(t)
            cover += [t for s in st["squares"] if overlaps(st["tag"], s)][:1]
            near = [frames[j][1]["tag"] for j in (i - 1, i + 1) if 0 <= j < len(frames)]
            if st["tag"]["a"] >= 0.99 and any(same_place(o, st["tag"]) for o in near):
                held_cover += [t for b, bx in zip(st["bodies"], st["boxes"]) if bx["a"] > 0.01 and overlaps(st["tag"], b)][:1]
        # T4-36 (round 6): on each hop the source drains as the target lights, so the fills add to one until the climb.
        if t < climb_s and abs(sum(st["fills"]) - 1) > 0.02:
            fill_sum.append(f"{t}:{round(sum(st['fills']), 2)}")
    res.add(f"T5-2 {where}: before the cross-fade a frame shows only headings, code and short lines; "
            "after it, only final-frame text", not strays, "; ".join(strays[:4]))
    last_short = max((t for t, st in frames if st["shortLines"] and st["shortMin"] >= 0.99), default=None)
    first_full = min((t for t, st in frames if st["fullA"] > 0.01), default=None)
    res.add(f"T5-3 {where}: the short and the full text never show together; the full text comes after the last short hold",
            not together and last_short is not None and first_full is not None and first_full > last_short,
            f"together at {together[:4]} ms; last short hold ends {last_short}, full text first at {first_full} ms")
    res.add(f"T4-26 {where}: steps 1 to 4 at even spacing in every frame", not uneven, "; ".join(uneven[:3]))
    res.add(f"T4-27 {where}: numbered 1 to 4 from the top in every frame; a box on screen stays on screen", not order,
            f"at {order[:5]}")
    res.add(f"T4-25 {where}: the answer line shows only after the answer's square is in box 1",
            arrived and not early, f"arrived at {arrive_t} ms; answer shown at {early[:5]} ms")
    res.add(f"T4-82 {where}: no box is taller than its shown text plus its padding, in any frame (spec a)", not empty,
            "; ".join(empty[:4]))
    res.add(f"T4-80 {where}: the answer's square climbs under the step numbers", climbing and not front,
            f"in front at {front[:5]} ms" if front else ("" if climbing else "no climb"))
    res.add(f"T4-37 {where}: boxes 2 to 4 move only after the answer is in box 1", not shift, f"at {shift[:5]} ms")
    res.add(f"T4-18 {where}: the label says what rides there (box 1 keys, then box 2 keys, none on the climb)",
            not wrong, "; ".join(wrong[:4]))
    res.add(f"T4-29 {where}: the tag stays inside the card", not outside, f"outside at {outside[:5]} ms")
    res.add(f"T4-26 {where}: the tag never covers a step square", not cover, f"at {cover[:5]} ms")
    res.add(f"T4-40 {where}: a waiting label covers no box", not held_cover, f"at {held_cover[:5]} ms")
    res.add(f"T4-23 {where}: the packet and the answer's square travel on the line", not offline, f"off the line at {offline[:5]} ms")
    res.add(f"T4-36 {where}: the fills hand off (they add to one in every frame before the climb)", not fill_sum,
            "; ".join(fill_sum[:4]))

    # T4-84, spec (c): the first frame shows box 1 complete: the box, its number, its heading, its code and its fill.
    b0, s0 = frames[0][1]["boxes"][0], frames[0][1]["squares"][0]
    first = {"box": b0["a"], "number": s0["a"], "heading": b0["headA"], "text": b0["sa"], "fill": b0["fillA"]}
    res.add(f"T4-84 {where}: the first frame shows box 1 complete (spec c)", min(first.values()) >= 0.99,
            ", ".join(f"{k} {v:.2f}" for k, v in first.items()))

    # T4-83, spec (b): per hop, the target is on screen before the packet reaches its number, and its fill starts to
    # rise within 0.1 s after contact, never before. Seeked every 10 ms around each hop.
    bad, notes = [], []
    for k, h in enumerate(sched.get("hops", [])):
        win = list(range(max(0, h["go"] - 400), h["at"] + 400, 10))
        fr = list(zip(win, pg.evaluate(SAMPLE_JS, win)))
        moving = [t for t, st in fr if st["pk"]["a"] > 0.01]
        if not moving:
            bad.append(f"hop {k + 1}: no packet")
            continue
        contact = next((t for t, st in fr if t > moving[0] and
                        abs(frame_geometry(st)["pkY"] - frame_geometry(st)["sqC"][k + 1]) <= 0.5), None)
        lit = next((t for t, st in fr if st["fills"][k + 1] > 0.01), None)
        appear = next((t for t, st in fr if st["boxes"][k + 1]["a"] > 0.01), None)
        on_at = next((st["boxes"][k + 1]["a"] for t, st in fr if t == contact), 0)
        notes.append(f"hop {k + 1}: appears {appear}, packet {moving[0]}, contact {contact}, lights {lit} ms")
        if contact is None or lit is None or appear is None:
            bad.append(f"hop {k + 1}: contact {contact}, lights {lit}, appears {appear}")
            continue
        if on_at < 0.99 or st_min_number(fr, k + 1, contact) < 0.99:
            bad.append(f"hop {k + 1}: target at {on_at:.2f} on contact")
        if not (contact - 10 <= lit <= contact + CONTACT_MS):
            bad.append(f"hop {k + 1}: lights {lit - contact} ms from contact")
    res.add(f"T4-83 {where}: in every hop the target is on screen before contact and lights within 0.1 s of it (spec b)",
            bool(notes) and not bad, "; ".join(bad or notes))

    # T4-81: the grow move keeps each heading in its place against its number.
    pre = [st for t, st in frames if t < sched.get("grow", {}).get("s", 0)][-1:]
    moved = []
    if pre:
        for k, (a, b) in enumerate(zip(pre[0]["boxes"], frames[-1][1]["boxes"])):
            da = a["head"]["t"] - pre[0]["squares"][k]["t"]
            db = b["head"]["t"] - frames[-1][1]["squares"][k]["t"]
            if abs(da - db) > 0.5 or abs((a["head"]["l"] - pre[0]["squares"][k]["l"]) - (b["head"]["l"] - frames[-1][1]["squares"][k]["l"])) > 0.5:
                moved.append(f"box {k + 1} by {db - da:.1f} px")
    res.add(f"T4-81 {where}: the grow move keeps each heading in place against its number", bool(pre) and not moved,
            "; ".join(moved))
    if not timing:
        return

    # T4-33: each label and the answer line is held, readable and settled, for 1.4 s or more, each time it holds.
    def settled_label(i: int):
        st = frames[i][1]
        if st["ans"]["a"] >= 0.99:
            return "answer line"
        if st["tag"]["a"] < 0.99:
            return None
        full = [l for l in st["labels"] if l["a"] >= 0.99]
        if len(full) != 1:
            return None
        for j in (i - 1, i + 1):
            if 0 <= j < len(frames) and frames[j][1]["tag"]["a"] >= 0.99 and same_place(frames[j][1]["tag"], st["tag"]):
                return full[0]["text"]
        return None

    runs, cur = [], None
    for i, (t, _) in enumerate(frames):
        lab = settled_label(i)
        if cur and lab == cur[0]:
            cur[2] = t
        else:
            if cur:
                runs.append(tuple(cur))
            cur = [lab, t, t] if lab else None
    if cur:
        runs.append(tuple(cur))
    short = [f"{l}: {b - a} ms at {a}" for l, a, b in runs if b - a < HOLD_MIN_MS]
    kinds = {l for l, _, _ in runs}
    want = {vals["browser"], vals["app"], "answer line"}
    res.add("T4-33 every label and the answer line holds 1.4 s or more, settled, each time",
            not short and want <= kinds, "; ".join(short) or f"missing {sorted(want - kinds)}")
    print("      holds: " + "; ".join(f"{l} {a}-{b} ms" for l, a, b in runs))

    # T4-34 (round 6): every move takes 0.75 s (spec 7): three packet hops, and the answer's climb into box 1.
    spans, bad = [], []
    on = [st["pk"]["a"] > 0.01 for _, st in frames]
    k = 0
    while k < len(on):
        if on[k]:
            s = k
            while k < len(on) and on[k]:
                k += 1
            spans.append(("hop", frames[s][0], frames[min(k, len(frames) - 1)][0]))
        k += 1
    d0 = next((i for i, (_, st) in enumerate(frames) if st["dot"] and st["dot"]["a"] > 0.01), None)
    if d0 is not None:
        d1 = next((i for i in range(d0, len(frames)) if abs(frame_geometry(frames[i][1])["dotY"] - frame_geometry(frames[i][1])["sqC"][0]) <= 0.5), None)
        if d1 is not None:
            spans.append(("climb", frames[d0][0], frames[d1][0]))
    bad = [f"{n} {a}-{b} ms" for n, a, b in spans if abs((b - a) - MOVE_MS) > PACE_TOL_MS]
    res.add("T4-34 each move takes 0.75 s: three hops and the climb", len(spans) == 4 and not bad,
            f"{len(spans)} moves: " + "; ".join(bad or [f"{n} {a}-{b}" for n, a, b in spans]))
    # The ease is symmetric (spec 7): seeked at a quarter and three quarters of each hop, the packet has gone p and
    # 1 - p of the way, and it starts slowly (p under one quarter).
    asym = []
    for k, h in enumerate(sched.get("hops", [])):
        qs = [h["go"] + f * (h["at"] - h["go"]) for f in (0.25, 0.5, 0.75)]
        ys = [frame_geometry(st)["pkY"] for st in pg.evaluate(SAMPLE_JS, qs)]
        a0, a1 = frame_geometry(frames[0][1])["sqC"][k], frame_geometry(frames[0][1])["sqC"][k + 1]
        p = [(y - a0) / (a1 - a0) for y in ys]
        if abs(p[0] + p[2] - 1) > 0.02 or abs(p[1] - 0.5) > 0.02 or p[0] >= 0.25:
            asym.append(f"hop {k + 1}: {[round(x, 3) for x in p]}")
    res.add("T4-34 every hop uses a symmetric ease-in-out", bool(sched.get("hops")) and not asym, "; ".join(asym))
    end = pg.evaluate("Math.max(0, ...document.getAnimations().map(a => a.effect.getComputedTiming().endTime))")
    res.add("T4-34 the composition ends with the final frame held 1.5 to 3 s after the motion",
            END_HOLD_MS[0] <= duration - end <= END_HOLD_MS[1], f"motion ends {end:.0f} ms, composition {duration} ms")

    # T4-50: each box stays long enough to read its words aloud at 183 wpm, and at least 1.4 s. Boxes 2 to 4 count
    # the time they are lit. Box 1 counts the time it is visible (owner, round 6: box 1 holds 1.4 s lit before the
    # packet leaves, and stays visible after). A later window of the same box needs time only for its new words.
    reading = []
    for k in range(len(frames[0][1]["fills"])):
        wins, cur = [], None
        for i, (t, st) in enumerate(frames):
            on = (st["boxes"][k]["a"] >= 0.99) if k == 0 else (st["fills"][k] >= 0.99)
            if on and cur is None:
                cur = [i, i]
            elif on:
                cur[1] = i
            elif cur is not None:
                wins.append(cur)
                cur = None
        if cur is not None:
            wins.append(cur)
        # A window splits where words leave the box (the short text fades out before the full text fades in). The
        # last part of a window that reaches the composition's end is the final frame, which never dims: it is the
        # still, and it is marked, not timed.
        parts = []
        for a, b in wins:
            s = a
            for i in range(a + 1, b + 1):
                if len(frames[i][1]["boxWords"][k]) < len(frames[i - 1][1]["boxWords"][k]):
                    parts.append((s, i - 1, s != a))
                    s = i
            parts.append((s, b, s != a))
        seen: list[str] = []
        for a, b, after_split in parts:
            words = frames[b][1]["boxWords"][k]
            new = list(words)
            for w in seen:
                if w in new:
                    new.remove(w)
            start = next(i for i in range(a, b + 1) if len(frames[i][1]["boxWords"][k]) == len(words))
            final_part = b == len(frames) - 1 and after_split
            have = frames[b][0] - frames[start][0] + (step if b == len(frames) - 1 else 0)
            need = max(HOLD_MIN_MS, round(len(new) * 60000 / READ_WPM))
            reading.append((k + 1, frames[start][0], frames[b][0], len(new), need, have, final_part))
            seen = list(words)
    short = [f"box {k} at {a} ms: {have} ms for {n} words, needs {need}"
             for k, a, _, n, need, have, fin in reading if have < need and not fin]
    res.add(f"T4-50 each box stays long enough to read its words aloud ({READ_WPM} wpm, Brysbaert 2019), 1.4 s or more "
            "(box 1: visible; boxes 2 to 4: lit)", bool(reading) and not short, "; ".join(short))
    res.reading = reading


def st_min_number(fr: list, k: int, t: int) -> float:
    """The opacity of box k's number at time t (spec b: the whole target is on screen at contact)."""
    return next((st["squares"][k]["a"] for x, st in fr if x == t), 0)


def browser_checks(res: Result, d: Path, run: dict, secrets: list[str], tables: list[str], duration: int) -> None:
    url = (d / "index.html").as_uri()
    rows = catechism_rows()
    with sync_playwright() as pw:
        b = pw.chromium.launch()

        # Reduced motion: the final frame, with no animation and no change over time (H10, T4-8).
        pg = b.new_page(viewport={"width": FRAME[0], "height": FRAME[1]}, reduced_motion="reduce")
        pg.goto(url)
        pg.evaluate("document.fonts.ready")
        rm = pg.evaluate(PAGE_JS)
        shot1 = pg.screenshot()
        pg.wait_for_timeout(300)
        shot2 = pg.screenshot()
        pg.wait_for_timeout(900)
        shot3 = pg.screenshot()
        res.add("H10 reduced motion: no animation created", rm["anims"] == 0, f"{rm['anims']} animations")
        res.add("H10 reduced motion: frames at 0, 300, 1200 ms are identical", shot1 == shot2 == shot3)
        res.add("T4-22 reduced motion: no packet and no tag on screen",
                rm["pk"] is not None and rm["pk"]["a"] < 0.01 and rm["tag"]["a"] < 0.01)

        # H8: every fact is in the visible text of the final frame (reduced motion = final frame).
        missing = [f for f in facts(run) if f not in rm["seen"]]
        res.add("H8 every fact is in the final frame's visible text", not missing,
                "missing: " + "; ".join(missing) if missing else "")
        unseen = [t["text"] for t in rm["texts"] if not t["hidden"] and not t["visible"]]
        res.add("H8 every text element of the final frame is visible", not unseen, "; ".join(unseen)[:160])

        # H13: the fixed hop list, 3 connectors, no table.
        flat = re.sub(r"\s+", " ", rm["text"])
        ids = [h["id"] for h in rm["hops"]]
        names = [re.sub(r"\s+", " ", h["name"]).strip() for h in rm["hops"]]
        res.add("H13 hops are browser > app > Data API > api.search_insurers, nothing under it",
                ids == HOP_IDS and names == HOP_NAMES, f"{ids} {names}")
        res.add("H13 exactly 3 connectors", rm["links"] == len(HOP_IDS) - 1, str(rm["links"]))
        no_c6 = flat.replace(TABLE_SENTENCE, " ").replace(SHORT_C6, " ")
        tab = [t for t in tables if re.search(rf"\b{re.escape(t)}\b", no_c6)] + \
            (["the word 'table' outside the C6 sentence"] if re.search(r"\btables?\b", no_c6, re.I) else [])
        res.add("H13 no table name in the diagram ('table' only in the C6 sentence and its short line)", not tab, ", ".join(tab))
        df = rm["def"]
        res.add("T4-20 step 4 shows a closed 'Function definition' row with no body (placeholder for ticket 05)",
                bool(df) and df["shown"] and df["text"].strip() == "Function definition" and df["kids"] == 1
                and df["placeholder"] == "ticket 05", str(df))

        # T4-28: every security sentence is word for word a graded, OK claims row.
        bad = []
        for n in rm["notes"]:
            if n["hop"] in CLAIM_STEPS and not n["claim"]:
                bad.append(f"no claims row: {n['text'][:40]}")
            if not n["claim"]:
                continue
            want, row = CLAIMS.get(n["text"]), rows.get(n["claim"])
            if not want or want[0] != n["claim"]:
                bad.append(f"{n['claim']}: sentence not in the claims table: {n['text'][:40]}")
            elif not row:
                bad.append(f"{n['claim']}: no such row in CATECHISM.md")
            else:
                line = " | ".join(row)
                lost = [a for a in want[1] if a not in line]
                if lost:
                    bad.append(f"{n['claim']}: row does not hold {lost}")
                if n["claim"] == "C14":
                    if "(grade " not in line.split("What is backed", 1)[-1]:
                        bad.append("C14: no graded 'What is backed' text")
                elif not (re.match(r"[123]\b", row[2]) and row[-1].startswith("OK")):
                    bad.append(f"{n['claim']}: grade {row[2][:12]!r}, status {row[-1][:12]!r}")
        if re.search(r"only two grants", flat, re.I):
            bad.append("'only two grants' on screen")
        res.add("T4-28 every security sentence is word for word a claims row of grade 1 to 3 (C14: its graded backed part)",
                not bad, "; ".join(bad))

        # T5-1: the short lines are the approved list, word for word, box by box; each carries its rows, each row is
        # graded 1 to 3 and OK and holds the text that backs the line; the short layer is hidden from screen readers
        # and no full line is.
        bad = []
        got = {h: [s["text"] for s in rm["shortLines"] if s["hop"] == h] for h in HOP_IDS}
        if got != {h: SHORT_LINES.get(h, []) for h in HOP_IDS}:
            bad.append(f"lines {got} differ from the approved list")
        for s in rm["shortLines"]:
            want = SHORT_CLAIMS.get(s["text"])
            if want is None:
                continue
            claims = s["claim"].split() if s["claim"] else []
            if sorted(claims) != sorted(want):
                bad.append(f"{s['text'][:30]}: rows {claims}, want {sorted(want)}")
            for c in claims:
                row = rows.get(c)
                if not row:
                    bad.append(f"{c}: no such row")
                    continue
                line = " | ".join(row)
                lost = [x for x in want.get(c, []) if x not in line]
                if lost:
                    bad.append(f"{c}: row does not hold {lost}")
                if not (re.match(r"[123]\b", row[2]) and row[-1].startswith("OK")):
                    bad.append(f"{c}: grade {row[2][:12]!r}, status {row[-1][:12]!r}")
            if not s["hiddenLayer"]:
                bad.append(f"short line not aria-hidden: {s['text'][:30]}")
        if rm["fullHidden"]:
            bad.append(f"full lines hidden from screen readers: {rm['fullHidden'][:2]}")
        res.add("T5-1 each short line is the approved text word for word, with its rows, and says no more than them",
                not bad, "; ".join(bad))

        # H14 on the rendered text, and the page's copy lint.
        scan_text(res, "rendered text", flat, secrets)
        lint = [rx for rx in COPY_LINT if re.search(rx, flat, re.I)]
        res.add("copy lint (the page's forbidden words)", not lint, ", ".join(lint))

        # T4-5, T4-6: fonts, floors.
        res.add("T4-5 IBM Plex Sans and Mono loaded from local files", all(rm["fonts"]), str(rm["fonts"]))
        small = sorted({f"{t['text'][:24]}={t['px']}px" for t in rm["texts"] if t["px"] < 13})
        res.add("T4-6 text at 13 px or more", not small, "; ".join(small))
        low = sorted({f"{t['text'][:24]}={t['ratio']:.2f}" for t in rm["texts"] if not t["hidden"] and t["ratio"] < 4.5})
        res.add("T4-6 text contrast 4.5:1 or more", not low, "; ".join(low))

        # T4-36: the fill is a lightness step, so the emphasis also reads in greyscale (motion-style rule 4).
        lstep = rm["paperLum"] - rm["fillLum"]
        res.add("T4-36 the fill is a lightness step of 0.15 or more from the paper", lstep >= 0.15, f"{lstep:.3f}")

        # T4-26, T4-38: no lane: the steps sit at the card's inner edge; the card sits in the middle of the frame.
        c, s0 = rm["card"], rm["squares"][0]
        pad = LIST_PAD_U * (s0["b"] - s0["t"]) / 28
        res.add(f"T4-26 no lane: the step squares sit at the list's {LIST_PAD_U}-unit inner edge, inside the card's border",
                abs(s0["l"] - c["l"] - 2 - pad) <= 0.5, f"{s0['l'] - c['l'] - 2:.1f} px, want {pad:.1f} px")
        cx, cy = (c["l"] + c["r"]) / 2, (c["t"] + c["b"]) / 2
        res.add("T4-38 the card is in the middle of the 1440 x 900 frame and fits it",
                abs(cx - FRAME[0] / 2) <= 1 and abs(cy - FRAME[1] / 2) <= 1 and c["t"] >= 0 and c["b"] <= FRAME[1],
                f"center {cx:.1f}, {cy:.1f}; top {c['t']:.1f}, bottom {c['b']:.1f}")

        # T4-41: the answer reads as the answer: its own box under the request's box, apart from it, on another ground.
        a = rm["answer"]
        # Round 6: the approved gap is 4 units (7 px at 1440); it was 8 px in round 5.
        unit = (rm["squares"][0]["b"] - rm["squares"][0]["t"]) / 28
        res.add("T4-41 the answer is not in the request's code box, sits 4 units or more under it, on another ground",
                bool(a) and not a["inRequest"] and a["gap"] >= 4 * unit - 0.5 and a["grounds"][0] != a["grounds"][1],
                f"{a}; 4 units = {4 * unit:.1f} px")
        # T4-58: the composition (video and blog stills) has no Play control.
        res.add("T4-58 the composition shows no button (Play lives only in the drawer page)", not rm["buttons"],
                ", ".join(rm["buttons"]))
        pg.close()

        # T4-42, T4-46, T4-47, T4-59: no token on two lines; every text at the floor of the frame it is seen in.
        for name, (w, h), frame in (("1440", FRAME, True), ("744", BLOG, True), ("390", (PHONE[0], 844), False)):
            pg = b.new_page(viewport={"width": w, "height": h}, reduced_motion="reduce")
            pg.goto(url)
            pg.evaluate("document.fonts.ready")
            st = pg.evaluate(PAGE_JS)
            broken = pg.evaluate(TOKENS_JS)
            res.add(f"T4-42 {name}: no word, JSON pair or code token breaks across two lines", not broken,
                    "; ".join(broken[:6]))
            box_px = min(t["px"] for t in st["texts"])
            tag_px = min(st["labelPx"]) if st["labelPx"] else 0
            floor = h / LABEL_DIV if frame else PHONE_FLOOR
            res.sizes.append((name, w, h, round(tag_px, 2), round(box_px, 2), round(floor, 2), frame))
            print(f"      sizes {name}: tag {tag_px:.2f} px, smallest box text {box_px:.2f} px, floor {floor:.2f} px")
            if not frame:
                res.add(f"T5-5 {name}: every text of the phone page at {PHONE_FLOOR} px or more (a page, not a frame)",
                        box_px >= PHONE_FLOOR - 0.01, f"smallest {box_px:.2f} px")
            if frame:
                res.add(f"T4-46 {name}: the tag and every box text at h/{LABEL_DIV} of the {w} x {h} frame or more",
                        min(box_px, tag_px) >= floor - 0.01, f"tag {tag_px:.2f}, box {box_px:.2f}, floor {floor:.2f} px")
                c = st["card"]
                res.add(f"T4-59 {name}: the card fits inside the {w} x {h} frame",
                        c["l"] >= -0.5 and c["t"] >= -0.5 and c["r"] <= w + 0.5 and c["b"] <= h + 0.5,
                        f"card {c['l']:.0f},{c['t']:.0f} to {c['r']:.0f},{c['b']:.0f}")
            pg.close()

        # With motion: the final frame, the labels against the box text, then the whole timeline.
        pg = b.new_page(viewport={"width": FRAME[0], "height": FRAME[1]}, reduced_motion="no-preference")
        pg.goto(url)
        pg.evaluate("document.fonts.ready")
        n = pg.evaluate("document.getAnimations().length")
        res.add("H10 with motion allowed, the diagram animates", n > 0, f"{n} animations")
        btn = pg.evaluate("[...document.querySelectorAll('button')].map(b => b.textContent.trim())")
        res.add("T4-58 with motion allowed, the composition shows no button", not btn, ", ".join(btn))
        end = pg.evaluate("Math.max(0, ...document.getAnimations().map(a => a.effect.getComputedTiming().endTime))")
        st = seek(pg, end)
        unseen = [t["text"] for t in st["texts"] if not t["hidden"] and not t["visible"]]
        missing = [f for f in facts(run) if f not in st["seen"]]
        res.add("H8 with motion, after the motion: every fact visible", not unseen and not missing,
                "; ".join(unseen + missing)[:160])
        res.add("T4-22 after the motion: no packet and no tag on screen", st["pk"]["a"] < 0.01 and st["tag"]["a"] < 0.01)
        vals = text_values(st)
        labels = {l["kind"]: l["text"] for l in st["labels"]}
        res.add("T4-24 the label from box 1 to box 2 equals the keys of box 1's request body",
                labels.get("pfd-tag-browser") == vals["browser"] == ", ".join(run["browser_body"]),
                f"{labels.get('pfd-tag-browser')!r} vs {vals['browser']!r}")
        res.add("T4-24 the label at the app equals box 2's body keys exactly",
                labels.get("pfd-tag-app") == vals["app"] == ", ".join(run["app_body_keys"]),
                f"{labels.get('pfd-tag-app')!r} vs {vals['app']!r}")
        # T4-79: the answer line is the status with its reason phrase, derived from the real status (200 -> "200 OK"),
        # never a constant: 404 renders "404 Not Found"; a code with no listed phrase shows bare.
        rows_txt = "1 row" if run["row_count"] == 1 else f"{run['row_count']} rows"
        want = f"{run['status']} {REASON[run['status']]} · {rows_txt}"
        got = {c: pg.evaluate("c => { const r = JSON.parse(document.getElementById('pfd-run').textContent);"
                              " r.status = c; return window.PayerFinderDiagram.labels(r).answer; }", c) for c in (404, 299)}
        res.add("T4-79 the answer line is the status with its reason phrase, derived from the status "
                "(200 OK; 404 Not Found; 299 bare)",
                vals["answer"] == want and got[404] == f"404 Not Found · {rows_txt}" and got[299] == f"299 · {rows_txt}",
                f"{vals['answer']!r} vs {want!r}; 404 {got[404]!r}; 299 {got[299]!r}")
        sample_motion(res, pg, st, duration, "1440", SAMPLE_MS, timing=True)

        # T4-2: the count comes from the data object, in the text and on the label.
        pg.evaluate("""() => { const r = JSON.parse(document.getElementById('pfd-run').textContent); r.row_count = 7;
                       window.PayerFinderDiagram.render(document.getElementById('pfd'), r); }""")
        st7 = pg.evaluate(PAGE_JS)
        res.add("T4-2 a changed row_count changes the answer line", st7["ans"]["text"].endswith("7 rows"), st7["ans"]["text"])
        pg.close()

        # T4-64: at the blog width the short lines wrap differently; the frame checks hold there too. The drawer's
        # page layout (720 px and wider, not 16:10) also plays the motion, with its own gaps and sizes.
        for name, (w, h) in (("744", BLOG), ("720 page", DRAWER)):
            pg = b.new_page(viewport={"width": w, "height": h}, reduced_motion="no-preference")
            pg.goto(url)
            pg.evaluate("document.fonts.ready")
            sample_motion(res, pg, seek(pg, end), duration, name, 2 * SAMPLE_MS, timing=False)
            pg.close()

        # T4-29, T5-4: the phone is a page, not a frame (DECISIONS 2026-10-24): with motion allowed it still makes no
        # animation and no button, and shows the final frame. This replaces the motion samples at 390 (round 4).
        pg = b.new_page(viewport={"width": PHONE[0], "height": PHONE[1]}, reduced_motion="no-preference")
        pg.goto(url)
        pg.evaluate("document.fonts.ready")
        st = pg.evaluate(PAGE_JS)
        res.add("T4-29 390: the card fills the width with no sideways scroll",
                not st["overflow"] and abs(st["card"]["l"]) <= 0.5 and abs(st["card"]["r"] - PHONE[0]) <= 0.5,
                f"card {st['card']['l']:.0f}-{st['card']['r']:.0f}")
        missing = [f for f in facts(run) if f not in st["seen"]]
        res.add("T5-4 390 with motion allowed: no animation, no button, every fact of the final frame visible",
                st["anims"] == 0 and not st["buttons"] and not missing and st["pk"]["a"] < 0.01,
                f"{st['anims']} animations, buttons {st['buttons']}, missing {missing[:2]}")
        pg.close()

        # T4-48: the moving tag covers no text in any video frame, at 1440, at the blog width and in the drawer page.
        ts = [round(i * VIDEO_FRAME_MS, 3) for i in range(int(duration / VIDEO_FRAME_MS) + 1)]
        for name, (w, h) in (("1440", FRAME), ("744", BLOG), ("720 page", DRAWER)):
            pg = b.new_page(viewport={"width": w, "height": h}, reduced_motion="no-preference")
            pg.goto(url)
            pg.evaluate("document.fonts.ready")
            hits = pg.evaluate(OVERLAP_JS, ts)
            eps, last = [], None
            for x in hits:
                if last is None or x["t"] - last > VIDEO_FRAME_MS * 1.5:
                    eps.append([x["t"], x["t"], set(x["covered"])])
                else:
                    eps[-1][1] = x["t"]
                    eps[-1][2] |= set(x["covered"])
                last = x["t"]
            res.add(f"T4-48 {name}: the tag covers no text in any of {len(ts)} video frames", not eps,
                    "; ".join(f"{a / 1000:.2f}-{e / 1000:.2f} s over {sorted(c)[:3]}" for a, e, c in eps[:6]))
            pg.close()

        # T4-11: a 100-character query at 1440 px and 390 px.
        long_q = ("blue cross blue shield of georgia preferred provider organization plan "
                  "for state employees and famil")[:100]
        for w, h in (FRAME, PHONE):
            pg = b.new_page(viewport={"width": w, "height": h}, reduced_motion="reduce")
            pg.goto(url)
            pg.evaluate("""q => { const r = JSON.parse(document.getElementById('pfd-run').textContent);
                           r.query = q; r.browser_body.q = q;
                           window.PayerFinderDiagram.render(document.getElementById('pfd'), r); }""", long_q)
            st = pg.evaluate(PAGE_JS)
            res.add(f"T4-11 a 100-character query fits its code box at {w} px", not st["overflow"] and st["preFit"]
                    and len(long_q) == 100)
            pg.close()
        b.close()


# ---------- artifact checks ----------

def png_text_chunks(path: Path) -> str:
    data, out, i = path.read_bytes(), [], 8
    while i < len(data):
        n = int.from_bytes(data[i:i + 4], "big")
        kind, body = data[i + 4:i + 8], data[i + 8:i + 8 + n]
        if kind in (b"tEXt", b"iTXt"):
            out.append(body.decode("latin-1"))
        elif kind == b"zTXt":
            k, _, rest = body.partition(b"\0")
            out.append(k.decode("latin-1") + " " + zlib.decompress(rest[1:]).decode("latin-1"))
        elif kind == b"eXIf":
            out.append(body.decode("latin-1"))
        i += 12 + n
    return "\n".join(out)


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def artifact_checks(res: Result, secrets: list[str], out: Path = HERE / "out") -> None:
    still, reduced, preview = out / "still.png", out / "reduced.png", out / "preview.mp4"
    still390, reduced390, board = out / "still-390.png", out / "reduced-390.png", out / "storyboard.png"
    still744, reduced744 = out / "still-744.png", out / "reduced-744.png"
    sheet = HERE / "runs" / "contact-sheet.png"
    for p in (still, reduced, still390, reduced390, still744, reduced744, preview, board, sheet):
        res.add(f"artifact {p.name} exists and is under 10 MB", p.exists() and 0 < p.stat().st_size < 10_000_000)
    if not (still.exists() and reduced.exists() and preview.exists()):
        return
    res.add("H10 reduced-motion frame equals the final-frame still at 1440 (same bytes)",
            still.read_bytes() == reduced.read_bytes())
    res.add("still.png is the 1440 x 900 frame at 2x", png_size(still) == (2 * FRAME[0], 2 * FRAME[1]), str(png_size(still)))
    if still390.exists() and reduced390.exists():
        res.add("H10 reduced-motion frame equals the final-frame still at 390 (same bytes)",
                still390.read_bytes() == reduced390.read_bytes())
    if still744.exists() and reduced744.exists():
        res.add("H10 reduced-motion frame equals the final-frame still at 744 (same bytes)",
                still744.read_bytes() == reduced744.read_bytes())
        res.add("still-744.png is the 744 x 465 frame at 2x", png_size(still744) == (2 * BLOG[0], 2 * BLOG[1]),
                str(png_size(still744)))
    for p in (q for q in (still, reduced, still390, reduced390, still744, reduced744, board, sheet) if q.exists()):
        scan_text(res, f"{p.name} metadata (T4-1)", png_text_chunks(p), secrets)
    if board.exists():
        w = png_size(board)[0]
        res.add(f"storyboard is {STORYBOARD_FRAMES} frames of 360 px (one per move)", w == STORYBOARD_FRAMES * 360, f"{w} px wide")
    ocr = subprocess.run(["tesseract", str(still), "-", "--psm", "4"], capture_output=True, text=True).stdout
    flat = re.sub(r"\s+", " ", ocr)
    scan_text(res, "still text layer (OCR)", flat, secrets)
    words = ("Browser", "This app", "Lakebase Data API", "search_insurers", "10 rows", "Switches into the app",
             "Function definition")
    seen = [w for w in words if w in flat]
    res.add("H8 still's text layer (OCR) shows the steps, the answer line and the role switch", len(seen) == len(words),
            f"missing {[w for w in words if w not in seen]}")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(preview)],
                                      capture_output=True, text=True).stdout)
    dur = float(probe["format"]["duration"])
    want = float(re.search(r'data-duration="([\d.]+)"', (HERE / "index.html").read_text()).group(1))
    res.add("preview is as long as the composition", abs(dur - want) <= 0.1, f"{dur:.2f} s vs {want:.2f} s")
    # T4-61: the render moves: its first second is not the final frame (a seek the adapter ignores renders a still).
    first, last = _gray(preview, 1.0), _gray(preview, dur - 0.5)
    diff = sum(abs(a - b) for a, b in zip(first, last)) / max(1, len(first))
    res.add("T4-61 the preview moves: the frame at 1 s differs from the last frame", diff >= 1.0, f"mean difference {diff:.2f}")
    tags = json.dumps(probe["format"].get("tags", {})) + json.dumps([s.get("tags", {}) for s in probe["streams"]])
    scan_text(res, "preview.mp4 tags (T4-1)", tags, secrets)


def run_all(d: Path, artifacts: bool) -> Result:
    res = Result()
    secrets, tables = secret_values(), table_names()
    print(f"ban list: {len(BANNED_TEXT)} strings, {len(BANNED_RE)} patterns, {len(secrets)} deployment values (not printed), "
          f"{len(tables)} table names from the public repo")
    src = {n: (d / n).read_text() for n in SOURCES}
    allsrc = "\n".join(src.values())
    m = re.search(r'<script type="application/json" id="pfd-run">(.*?)</script>', src["index.html"], re.S)
    run = json.loads(m.group(1))
    duration = int(float(re.search(r'data-duration="([\d.]+)"', src["index.html"]).group(1)) * 1000)
    fixture_rows = len(json.loads(FIXTURES.read_text())["queries"][run["query"]]["page1"])
    res.add("T4-2 data object row_count equals the fixture's page 1", run["row_count"] == fixture_rows,
            f"{run['row_count']} vs {fixture_rows}")
    res.add("data object: query equals the browser body's q", run["query"] == run["browser_body"]["q"])
    res.add("data object holds the five inputs and no time field",
            set(run) == {"query", "browser_body", "app_body_keys", "row_count", "status"})
    code_only = re.sub(r"<!--.*?-->|/\*.*?\*/", " ", allsrc, flags=re.S)
    code_only = re.sub(r"(^|\s)//[^\n]*", " ", code_only)
    scan_text(res, "composition source", code_only, secrets)
    res.add("T4-3 no markup sink in the composition", not re.search(MARKUP_SINKS, allsrc))
    urls = re.findall(r"(?:src|href)\s*=\s*\"([^\"]+)\"|url\(\s*\"?([^\")]+)", allsrc)
    remote = [u for pair in urls for u in pair if u and re.match(r"(https?:)?//", u)]
    res.add("T4-5 every src, href and url() is local", not remote, ", ".join(remote))
    browser_checks(res, d, run, secrets, tables, duration)
    if artifacts:
        artifact_checks(res, secrets)
    return res


def write_tables(res: Result) -> None:
    """runs/reading-time.md and runs/sizes.md, from the values this run measured."""
    names = {1: "Browser", 2: "This app", 3: "Lakebase Data API", 4: "Postgres function api.search_insurers"}
    lines = ["# Reading time per box (T4-50)", "",
             f"Rate: {READ_WPM} words per minute, reading aloud. Source: Brysbaert, M. (2019). How many words do we read",
             "per minute? A review and meta-analysis of reading rate. Journal of Memory and Language 109, 104047:",
             '"The average oral reading rate (based on 77 studies and 5,965 participants) is 183 wpm."',
             "(`runs/2026-10-23/s3-t04-r4-reading-rate-source.txt`). Words: whitespace tokens of every fully opaque text",
             "block in the box; code tokens count. A later window of the same box counts only its new words, from the",
             "frame where all of them are readable. Minimum 1.4 s (motion-style rule 5). Measured on the timeline every",
             f"{SAMPLE_MS} ms (box filled = fill opacity 0.99 or more). The motion shows the short text, so these are",
             "the short words. A window splits where words leave (the short text fades out before the full text fades",
             "in). The final frame is not timed: it never dims, it ends the video and it is the still.", "",
             "Round 6 (owner rule): box 1 is on screen, complete, from frame 0, and it stays visible while it drains, so",
             "its window counts the time box 1 is visible, not the time it is filled (its fill holds 1.4 s). Boxes 2 to 4",
             "count the time they are filled. Box 4 holds 0.5 s longer than the approved preview: the preview fills it",
             "for 5.10 s, under the 5.57 s its 17 words need. The climb and the grow move start 0.5 s later because of it.",
             "",
             "| Box | Filled from | to | New words | Needs | Filled for | Pass |", "|---|---|---|---|---|---|---|"]
    for k, a, b, n, need, have, fin in res.reading:
        verdict = "final frame (still)" if fin else ("yes" if have >= need else "NO")
        lines.append(f"| {k} {names.get(k, '')} | {a / 1000:.2f} s | {b / 1000:.2f} s | {n} | {need / 1000:.2f} s | "
                     f"{have / 1000:.2f} s | {verdict} |")
    (HERE / "runs/reading-time.md").write_text("\n".join(lines) + "\n")
    lines = ["# Text size at each width against its floor (T4-46, T5-5)", "",
             "Frames (1440 and 744): labels at least 1/36 of the frame height (motion-style, Floors). The phone (390) is",
             "a page, not a frame (DECISIONS 2026-10-24): the app's 13 px floor, the still only, no tag. Sizes are CSS px",
             "as rendered at that width, which are the pixels people see (no further scaling). 744 px: the owner measured",
             "on 2026-10-18 that LinkedIn article bodies show images 744 px wide and recompress them (post 1); Medium was",
             "not measured.", "",
             "| Width | Viewport | Tag | Smallest box text | Floor | Kind | Meets floor |", "|---|---|---|---|---|---|---|"]
    for name, w, h, tag, box, floor, frame in res.sizes:
        kind = "16:10 frame, h/36" if frame else "page, 13 px"
        meets = min(tag, box) >= floor - 0.01 if frame else box >= floor - 0.01
        lines.append(f"| {name} | {w} x {h} | {tag if frame else 'not shown'}{' px' if frame else ''} | {box} px | "
                     f"{floor} px | {kind} | {'yes' if meets else 'no'} |")
    (HERE / "runs/sizes.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutate", choices=sorted(MUTATIONS) + sorted(ARTIFACT_MUTATIONS) + ["all"])
    ap.add_argument("--no-artifacts", action="store_true", help="skip the checks on out/ (before the first render)")
    ap.add_argument("--src", type=Path, help="run the source and browser checks on this copy of diagram/")
    ap.add_argument("--tables", action="store_true", help="write the reading-time and size tables to runs/")
    a = ap.parse_args()
    if a.src:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for n in SOURCES:
                if (a.src / n).exists():
                    shutil.copy(a.src / n, d / n)
            shutil.copytree(a.src / "fonts", d / "fonts")
            res = run_all(d, artifacts=False)
        print(f"\n{'GREEN' if res.ok else 'RED'}: {sum(ok for _, ok, _ in res.rows)}/{len(res.rows)} checks pass")
        return 0 if res.ok else 1
    if not a.mutate:
        res = run_all(HERE, artifacts=not a.no_artifacts)
        if a.tables:
            write_tables(res)
        print(f"\n{'GREEN' if res.ok else 'RED'}: {sum(ok for _, ok, _ in res.rows)}/{len(res.rows)} checks pass")
        return 0 if res.ok else 1
    names = sorted(MUTATIONS) + sorted(ARTIFACT_MUTATIONS) if a.mutate == "all" else [a.mutate]
    caught = []
    for name in names:
        print(f"\n=== mutation {name} ===")
        if name in ARTIFACT_MUTATIONS:
            with tempfile.TemporaryDirectory() as t:
                o = Path(t) / "out"
                shutil.copytree(HERE / "out", o)
                ARTIFACT_MUTATIONS[name](o)
                res = Result()
                artifact_checks(res, secret_values(), o)
        else:
            with tempfile.TemporaryDirectory() as t:
                d = Path(t)
                for n in SOURCES:
                    shutil.copy(HERE / n, d / n)
                shutil.copytree(HERE / "fonts", d / "fonts")
                MUTATIONS[name](d)
                res = run_all(d, artifacts=False)
        failed = [cid for cid, ok, _ in res.rows if not ok]
        caught.append((name, bool(failed)))
        print(f"--> {name}: {'RED (caught by: ' + '; '.join(failed) + ')' if failed else 'NOT CAUGHT'}")
    print("\nsummary: " + ", ".join(f"{n}={'red' if c else 'MISSED'}" for n, c in caught))
    return 1 if all(c for _, c in caught) else 2


if __name__ == "__main__":
    sys.exit(main())

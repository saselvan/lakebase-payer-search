# The motion diagram of one search (ticket 04, session 3, round 6)

One search's round trip, drawn from one data object: the browser, this app, the Lakebase Data API and the Postgres
function `api.search_insurers`, then the answer back up the same line into box 1. This folder holds the blog and
video composition, the drawer page with its Play button, the stills, a preview, a storyboard, a contact sheet and the
check. Nothing here is in `static/`.

| File | What it is |
|---|---|
| `index.html` | HyperFrames composition, 1440 x 900, 25.57 s, holding the data object `#pfd-run`; no button |
| `drawer.html` | the drawer: the same diagram, opened on the final frame, with a "Play" button (what ticket 05 mounts) |
| `diagram.js`, `diagram.css`, `fonts/` | the diagram itself: text only (`textContent`), Web Animations API, local IBM Plex |
| `out/preview.mp4` | local HyperFrames render, 25.6 s, 30 fps, 1440 x 900 |
| `out/still.png`, `out/reduced.png` | final frame, the 1440 x 900 frame at 2x; and the same under reduced motion (same bytes) |
| `out/still-744.png`, `out/reduced-744.png` | final frame at the blog width, the 744 x 465 frame at 2x; the same bytes |
| `out/still-390.png`, `out/reduced-390.png` | the final frame at phone width (390 px, whole page, 2x); the same bytes |
| `out/storyboard.png` | one frame per move, cropped to the card, 360 px wide each (times below) |
| `runs/contact-sheet.png` | every 0.25 s of `preview.mp4` (103 frames), 16 to a row, in time order |
| `check.py`, `runs/` | the check (H8..H14, T4-1..T4-61, T5-1..T5-5), its green run, its red run, the reading-time and size tables |
| `play_mutations.py` | the red runs of `tests/test_diagram_play.py` (one-line mutations, `runs/play-red-run.txt`) |
| `stills.py` | makes the six PNGs in Chromium, and the storyboard and contact sheet from `preview.mp4` with ffmpeg |
| `compare.py`, `runs/r6/compare.*` | the built motion against Samuel's approved preview, every 1 s, with a pixel difference |

## Widths

- 1440 x 900: the video frame.
- 744 x 465: the blog. The owner measured on 2026-10-18 that LinkedIn article bodies show images 744 px wide and
  recompress them (post 1). Medium was not measured.
- 390 px: a phone. The drawer is a page, not a frame (DECISIONS 2026-10-24): the still final frame only, no Play
  control, no animation, and the app's 13 px text floor. Below 720 px wide nothing animates.

At 16:10 and wider (`min-width: 720px` and `min-aspect-ratio: 3/2`) the card is laid out as a frame: one unit is
1.75/1440 of the width (the preview's unit), body text is 14.3 units (the h/36 floor), and the step names sit in a column on the left so the card fits the
frame. Below that it is the reading layout of round 3 (15 px text, 14 px code).

## The data object

```json
{"query": "bcbs georgia", "browser_body": {"q": "bcbs georgia", "page": 1},
 "app_body_keys": ["q", "customer_id", "lim", "off"], "row_count": 10, "status": 200}
```

It maps onto the lens the server sends today (`server/main.py`): `browser_request.body`, the keys of
`server_request.body`, `len(rows)` and `status`. It has no time field (H9) and no `customer_id` value (H14, H15).

`PayerFinderDiagram.mount(root, run, { controls })` draws any search. With `controls: true` (the drawer) it opens on
the final frame and adds a `<button type="button">Play</button>`; Play restarts the one set of animations from the
start, however often it is pressed. Nothing starts on load. Under `prefers-reduced-motion: reduce`, and below 720 px
wide, no animation and no button are made; if the reader turns reduced motion on, or the page narrows below 720 px,
during play, the animations are cancelled and the button goes (the still stays).
Without controls (the composition) the animations wait, paused at time 0, for HyperFrames to seek them: its waapi
adapter takes an animation's time when it first finds it as an offset, so an animation parked at its end renders a
still (T4-61 catches that). The packet's labels come from `PayerFinderDiagram.labels(run)`, from the same values as
the box text they must equal (T4-24, checked against the rendered text):

| Packet label | Made from | Must equal |
|---|---|---|
| `q, page` (box 1 to box 2) | the keys of `browser_body` | the keys of box 1's request, `{"q": …, "page": 1}` |
| `q, customer_id, lim, off` (box 2 to step 4) | `app_body_keys` | box 2's `body keys: q, customer_id, lim, off` |
| `200 OK · 10 rows` (the answer, step 4 back to box 1) | `status` with its RFC 9110 reason, `row_count` | box 1's answer, `200 OK · 10 rows` |

## The answer

`200 OK · 10 rows` is its own block under box 1's request: white ground, a 2 px cobalt frame, and the label "Answer
from this app" before the value. "OK" comes from the status (`labels(run)`, the RFC 9110 reason phrase; a code with no
phrase in the map shows bare), never from a constant on the 200 path (T4-79). The drawer's lens line in `static/app.js`
says `200 OK · N rows` the same way. Tokens in the code boxes never break across two lines (T4-42, at 1440, 744 and
390). The block sits 4 units under the request, the approved spacing (T4-41).

## Short text in the motion

Each box has two text layers. The motion shows the short lines; the final frame, the stills and the drawer show the
full sentences, unchanged. The headings and the numbers are in both layouts at the same place against their number
(T4-81). At the end, one grow move (0.75 s): the short text fades out, the boxes grow to the full layout, and the full
text fades in; the two never show together (T5-3). The short layer is `aria-hidden`, so a screen reader reads each
fact once.

| Box | Short text (motion) | Rows |
|---|---|---|
| 1 Browser | the heading and the request code | (code) |
| 2 This app | the heading, the code, "Server sets `customer_id`." "The browser never gets the token." | C17, C7 |
| 3 Lakebase Data API | "As the app's role: EXECUTE on 2 functions, no data table rights." | C18, C6 |
| 4 Postgres function `api.search_insurers` | "Typo matching and ranking run here. `search_core` runs as `search_owner`, which cannot log in." | C2, C19 |

The check holds this list word for word, each line's rows, and the text each row must hold, so a short line says no
more than its row (T5-1): C7 "never receives the server's OAuth token"; C19 "runs with the rights of its owner
`search_owner` (SECURITY DEFINER), a role that cannot log in"; C6 "no right on any table, view, column or sequence in
a data schema"; C18 "switches into the Postgres role of the calling identity".

## Timeline

Round 6 ports Samuel's approved preview (`storyboard-r6/motion-r6a.mp4`, 25.1 s; its `anim.html` `draw(t)` is the
spec). `frame(t)` in `diagram.js` is `draw(t)` as values; `animate()` samples it every 1/30 s into Web Animations
keyframes, so HyperFrames, the check and Play seek one set of animations. Moves take 0.75 s on a symmetric ease.
Box 1 is complete at frame 0. On each hop the target box appears, unlit, 0.2 s before the packet leaves; the blue
square runs from number to number; the tag joins it once it is out of the source box; at contact the target lights
and the source drains (T4-83); the tag waits at the target's top edge for 1.4 s, then fades (0.3 s). The times come from
`PayerFinderDiagram.schedule()`.

| Time | Storyboard frame | What happens |
|---|---|---|
| 0 to 1.4 s | 1 (0.50 s) | Box 1 alone, lit, with its request (8 words). |
| 1.4 to 2.15 s | | Box 2 appears; the packet runs to number 2 with `q, page`; at contact box 2 lights. |
| 2.15 to 8.38 s | 2 (3.15 s) | Box 2 lit (19 words); the tag waits until 3.55 s. |
| 8.38 to 9.13 s | | Box 3 appears; the packet runs to number 3 with `q, customer_id, lim, off`. |
| 9.13 to 14.05 s | 3 (10.13 s) | Box 3 lit (15 words); the tag waits until 10.53 s. |
| 14.05 to 14.80 s | | Box 4 appears; the packet runs to number 4 with the same keys. |
| 14.80 to 20.87 s | 4 (15.80 s) | Box 4 lit (17 words); the tag waits until 16.20 s. |
| 20.87 to 21.62 s | 5 (21.27 s) | The answer climbs as a square on the line, under the numbers, into box 1. |
| 21.82 to 22.57 s | 6 (22.22 s) | The grow: the short text fades out, the boxes grow, the full text and `200 OK · 10 rows` fade in. |
| 22.57 to 25.57 s | 7 (23.57 s) | Final frame, held 3 s. |

One difference from the preview, on purpose: box 4 holds 0.5 s longer. The preview fills it for 5.10 s, under the
5.57 s its 17 words need at 183 words a minute, so the climb and the grow start 0.5 s later (preview 20.37 s, here
20.87 s). Box 1's reading time counts the time it is visible (owner rule): its fill holds 1.4 s, and it stays on
screen. Table: `runs/reading-time.md`. Comparison with the preview: `runs/r6/compare.png` and `compare.md`.

Under reduced motion, and below 720 px, no animation is created: the CSS default is the final frame.

Length: 25.6 s (the preview is 25.1 s).

## Text size against the floor

motion-style asks for labels at 1/36 of the frame height or more in a frame. The phone is a page with the app's
13 px floor (DECISIONS 2026-10-24). Measured in Chromium at each width, as rendered (`runs/sizes.md`):

| Width | Tag | Smallest box text | Floor | Meets it |
|---|---|---|---|---|
| 1440 x 900 frame | 25.02 px | 25.02 px | 25.0 px (h/36) | yes |
| 744 x 465 frame (blog) | 12.93 px | 12.93 px | 12.92 px (h/36) | yes |
| 390 x 844 page (phone) | not shown | 14.3 px | 13 px | yes |

## Evidence for each sentence on screen

Each security sentence carries `data-claim` with its row in `docs/CATECHISM.md`; the check holds each sentence word for
word with text its row must contain (T4-28). Grades: 1 observed live, 2 read in versioned code, 3 a Databricks doc
quoted from the fetched page text.

| On screen | Claims row | Grade |
|---|---|---|
| Box 1: "Sent only the query and the page to this app." `POST /api/search {"q": …, "page": 1}` | (code) `static/app.js` sends `{ q, page }`; `SearchIn` has `extra="forbid"` | 2 |
| Box 1 answer `200 OK · 10 rows`, "Answer from this app" | `fixtures.json` ("bcbs georgia" page 1 has 10 rows); `status` 200 is set only on the success path | 1 + 2 |
| Box 2: "Sent the search to the Data API." `POST /api/rpc/search_insurers`, `body keys: q, customer_id, lim, off` | C17 (the lens arguments are the body sent) | 1 |
| "`customer_id` is a label the server sets." | C17 | 1 |
| "Authorization: OAuth token of the app's service principal. The browser never gets it." | C7 | 1 |
| Box 3: "Runs the function as an RPC over HTTP POST. The path names the function." | C14, "What is backed" part only | 3 + 1 |
| "Switches into the app's own Postgres role." | C18 | 3 + 1 |
| "That role has EXECUTE on the two search functions and no rights on any data table." | C6 | 1 |
| Box 4: "Typo matching and ranking run inside it." | C2 | 2 |
| "Runs with the caller's rights and calls `search_idx.search_core`." | C19 | 1 + 2 |
| "`search_core` runs with the rights of its owner, `search_owner`, a role that cannot log in. It is the only function that reads the search index." | C19 | 1 + 2 |
| "Both functions pin `search_path` to `pg_catalog, pg_temp`." | C8 | 1 |

C6 says "data table": a table in a data schema, as the row says. Its live evidence is the scoped read on
`t-finder-1023d` (commit 08c86b7): no table, view, column or sequence right in `api`, `audit`, `payer_serving`,
`search_idx` or `tokenizer_ext`, USAGE on `api` and `search_idx` only. Reads of `pg_catalog` and `information_schema`
and of the two platform views through PUBLIC are expected and are not a finding. Not on screen, on purpose: any SQL
statement or call form (C14 is UNPROVEN), "only two grants" (C6), "no rights on any table" and "Token stays on the
server" (copy lint), a measured time, the `customer_id` value, host names, the branch, SP ids.

## Placeholder: "Function definition"

The closed "Function definition" row under step 4 is a placeholder (`data-placeholder="ticket 05"`). It has no body,
and nothing in `diagram/` holds the definition text. The check bans `CREATE`, `RETURNS`, `LANGUAGE`, `$$` and
`plpgsql` in the source and the text (T4-20).

## Check

```
uv run python diagram/stills.py                 # remake the PNGs, the storyboard and the contact sheet (after the render)
uv run python diagram/check.py --tables         # green: exit 0; writes runs/reading-time.md and runs/sizes.md
uv run python diagram/check.py --mutate all     # red run: exit 1 when every mutation is caught
uv run python diagram/check.py --src DIR        # the source and browser checks on another copy (round 3 red run)
uv run python diagram/play_mutations.py         # red runs of tests/test_diagram_play.py
```

The check runs Chromium on the local files. It reads the ban values from the repo at run time and never prints them.
It scans the source, the rendered text, the still's text layer (OCR), the PNG text chunks and the MP4 tags. For the
motion it pauses every animation and seeks the timeline every 50 ms at 1440 x 900 and every 100 ms at 744 x 465 and
in the drawer's page layout at 720 x 1100, and every video frame (1/30 s) at 1440, 744 and 720 for covered text. At
390 px it checks that nothing animates (T5-4). Runs:

- `runs/green-run.txt`: the round 6 green run.
- `runs/red-run.txt`: every mutation (sources and rendered files) red, round 6.
- `runs/r6/check-red-before.txt`: the round 6 checks on the round 5 sources: red.
- `runs/r6/play-tests-red-before.txt`, `runs/r6/tests-red-before.txt`: the changed first-frame Play test and the
  `200 OK` lens test on the round 5 sources: red.
- `runs/r6/compare.png`, `compare.md`: the built motion against the approved preview, every 1 s.
- `runs/r5/check-red-before.txt`: the round 5 checks on the round 4 sources: red on T5-1, T5-3 (1440, 744) and T5-4.
- `runs/r5/play-tests-red-before.txt`: the two new Play tests on the round 4 sources: red.
- `runs/red-run-335bd9f.txt`: the round 4 checks on round 3 (commit 335bd9f): red, with the covered text at
  4.0 s and 11.2 s, the answer inside the request, `"page": 1}` broken at 390 px, 14 px text and the short reads.
- `runs/play-red-run.txt`: each Play test red under its one-line mutation.
- `runs/copy-lint-red-run.txt`: the copy-lint rule `no rights? on any table`, red on the app's drawer text in round 4 (fixed in `static/` by 2e9183b).

Render and snapshot commands (local only; the snapshot never calls Gemini):

```
env -u GEMINI_API_KEY -u GOOGLE_API_KEY HYPERFRAMES_NO_TELEMETRY=1 npx --yes hyperframes@0.8.166 render --quality standard --output out/preview.mp4
env -u GEMINI_API_KEY -u GOOGLE_API_KEY HYPERFRAMES_NO_TELEMETRY=1 npx --yes hyperframes@0.8.166 snapshot --at 0.5,3.15,10.13,15.8,21.27,22.22,23.57 --describe false --output .hyperframes/artifacts/snapshots/round6
uv run python diagram/compare.py                # runs/r6/compare.png and compare.md, from out/preview.mp4
```

## Runtime notes

No animation library: the Web Animations API only, same-origin files, so a page CSP of `'self'` holds (H11). The
keyframes hold lengths of the layout; they are made again when the fonts arrive or the width changes. Each track's
last keyframe is the CSS default's own value (`auto`, `none`, `var(--mb-full)` …), and the list's `display` goes from
`flow-root` to `block` at the last keyframe, so the list is laid out again: after the boxes grow, Chromium otherwise
keeps a sub-pixel paint of the old layout. So the last frame of the motion and the reduced-motion frame paint the same
pixels (the stills are byte-equal at all three widths; `artifact-still-no-relayout` is the red run).
`data-layout-allow-*` and `data-no-timeline` are HyperFrames markers.

## Open for Samuel

- Sign-off on the port: 25.6 s against the preview's 25.1 s (box 4 holds 0.5 s longer for its reading time), the
  stills, the storyboard and `runs/r6/compare.png`.
- The code and tag text is 14.3 units, not 14, for the h/36 floor at 1440 and 744; a few lines of the final frame
  wrap in other places than in the preview.
- HyperFrames lint keeps one warning on `.pfd-fill` (`z-index: -1`). Its parent `.pfd-full-box` is a stacking context
  (`isolation: isolate`), so the fill paints; the lint reads the rule without its parent.

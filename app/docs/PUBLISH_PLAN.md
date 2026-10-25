# PUBLISH_PLAN.md: git history clean-up before any public home (session 5, item 3)

**Status (2026-10-24):** plan only. Nothing is rewritten, pushed or made public. The repo has no remote. Samuel runs every step below as a `!` command, one per message, after he picks a method. Objections J8-J12 govern it.

## What the history holds
Scan: `runs/2026-10-24/s5/history_scan.py` (read only; prints kinds, never values) over 78 commits on 9 local refs, output `runs/2026-10-24/s5/item3-history-scan.json`. Values come from `.env` through `tests/test_tracked_files.py`; shapes are a JWT, `dose`/`dapi` tokens, a `Bearer` value of 20+ characters and an endpoint host.

| Kind | Commits | Paths (all commits) | In HEAD |
|---|---|---|---|
| Tenant value | 67 | `app.yaml`, `server/main.py`, `docs/plans/telemetry-module.md` | no |
| Production Data API host | 67 | `app.yaml` | no |
| Endpoint host (any branch) | 67 | `app.yaml`, 7 run logs in `runs/2026-10-23/` and `runs/2026-10-24/` | no |
| Workspace id | 69 | `app.yaml`, `docs/STATE.md`, run logs, and `runs/2026-10-24/s5/item1-deployed.log` in commit 7fe1561 (masked after) | no (after the mask commit) |
| Workspace host | 47 | 7 run logs | no |
| App SP client id | 47 | `docs/STATE.md`, 3 run logs | no |
| App SP id | 47 | `SESSION_PLAN.md`, `docs/STATE.md` | no |
| JWT shape | 2 | `tests/test_panel.py`: a synthetic planted value, not a token | yes (synthetic) |
| `dose`/`dapi` token, `Bearer` value | 0 | none | no |
| Commit messages | 0 | none | no |

No client secret value is in any commit: the session-1 mutation logs were redacted before their first commit (SESSION_PLAN.md, session 1 close-out), and the scan finds no secret shape. The scan cannot read a value that never sat in `.env` (an expired secret, an old branch host typed by hand); the shape patterns cover hosts and tokens, not free text.

Binary files in history: 118 paths (PNG, MP4, WOFF2): `runs/2026-10-23/directions` 38, `runs/2026-10-23/screens` 18, `runs/2026-10-24/fix` 16, `runs/2026-10-24/fix2` 16, `runs/2026-10-24/screens` 12, `runs/2026-10-24/s4` 8, `diagram/out` 5, `diagram/fonts` 3, `diagram/runs` 2. A text scan cannot read them (J10).

## Backup (done 2026-10-24, before any rewrite)
- Local branch `backup/pre-rewrite-2026-10-24` at `392e17e` (= `session-4` then).
- A local bundle `lakebase-payer-finder-pre-rewrite-2026-10-24.bundle`, all 11 refs, `git bundle verify` okay, SHA-256 in `runs/2026-10-24/s5/item3-backup.txt`.

## Two methods (Samuel picks; DECISIONS.md)
**A. Fresh history (recommended).** A new repo holds one commit: the current tree with the public allowlist below. No old object can travel, so J11 holds by construction, and the J9 scan of the new repo is the whole proof. The development record stays in this private repo and the bundle.

**B. Rewrite this history.** `git filter-repo` on a fresh clone with `--replace-text` (an expressions file built from `.env` in `/tmp`, never in the repo) and `--invert-paths` for the run logs. It keeps the commit story, but each of the 78 commits changes its id, the binary files still need review, and a missed free-text value stays in history.

## Public allowlist (both methods)
`server/`, `static/`, `sql/deployed/`, `app.yaml` (placeholder), `requirements.txt`, `pyproject.toml`, `uv.lock`, `tests/`, `scripts/`, `diagram/` without `diagram/runs/`, `docs/` without `docs/STATE.md`, and a README. Left out: `runs/` (logs and screens with deployment detail), `SESSION_PLAN.md`, `REGISTER.md`, `DECISIONS*.md`, `FIRST_PROMPT.md` (internal record). Samuel decides each left-out file; any screen that goes public is reviewed by eye first (J10).

## Steps for method A (each one `!` command, after Samuel's decision)
1. Export the allowlist from `session-4` into a new folder outside this repo (`git archive session-4 <paths> | tar -x -C <new folder>`).
2. In the new folder: `git init`, one commit.
3. Agent (read only): run the J9 scan on the new repo with this repo's `.env` values, plus `tests/test_tracked_files.py`; record the result. A hit stops the plan.
4. Samuel creates the remote and pushes. The agent never runs `git push` or makes a repo public.

## Steps for method B
1. `git clone --no-local` this repo to a scratch folder (the clone holds no reflog and no stash).
2. Build `/tmp/replace.txt` from `.env` (one `value==><kind:masked>` line per value).
3. Samuel: `git filter-repo --replace-text /tmp/replace.txt --path runs/ --invert-paths` in the scratch clone.
4. Agent (read only): J9 scan of the scratch clone; each branch tip's tree equals the old tip's tree except masked lines and `runs/` (J8).
5. Delete `/tmp/replace.txt`. Samuel pushes from the scratch clone only.

## Open for Samuel
- Method A or B.
- The final home: its own repo or a folder in the public repo (DECISIONS.md 2026-10-23).
- Which left-out files go public, and the review of any screen.

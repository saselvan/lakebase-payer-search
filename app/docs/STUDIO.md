# Studio roster (Tier 3 sales demo + Lakebase demo + demo film)

Each seat is a fresh subagent that reads only the artifact and its charter, returns YAML findings, and cites a published rule. The coordinator verifies every finding against the live app before acting. Charters: `~/.claude/skills/atelier/references/studio-roster.md`.

| Seat | Reviews | Veto |
|---|---|---|
| Accessibility Engineer | app (gate) | any WCAG 2.2 AA failure |
| Human Factors Engineer | app | anything a tired registrar would misread or misclick |
| Content Designer (must) | app, deck, film | jargon on the front door; a promise the system cannot keep |
| Integrity (must) | app, deck, film | any on-screen claim not in the claims table |
| Product Designer | app | two ways of doing one thing |
| The User (the registrar, Q4) | app | none formally; wins over the professionals |
| Front-Door Buyer | app, deck | the 15 s comprehension check fails |
| Front-Door App Builder | app | "how do I call this?" not answered on Surface 2 |
| **Named Skeptic: DBA / security engineer** | Surface 3, deck security slide, film | a drawer claim that does not trace to a claims row |
| Search Critic (Hearst) | app | search used as an oracle; empty and error states |
| Narrative (Duarte/Cohan) | demo script, deck, film | no before-vs-after; no ask |
| Production designer | app, deck, film | the three do not read as one family |
| Director/DP | film | `~/.claude/skills/atelier/references/film/director-lenses.md` |
| Typography & Grid | deck (always), app (once) | |

Gates, not voices: WCAG floors, `copy_lint` (forbidden phrases + claims coverage), the 8 risk gates in `docs/CATECHISM.md` Q5.

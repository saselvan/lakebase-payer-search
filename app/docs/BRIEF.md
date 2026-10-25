# BRIEF: Payer finder (approved by Samuel, 2026-10-23: claim, 7 non-negotiables, 4 drawer questions)

## The claim
Owner, `docs/CATECHISM.md` Q1: "show Lakebase Search, along with Data API as the mechanism with security rules." Level: product (Lakebase).
What the viewer watches happen: **type a messy insurer name, get the right plan, and see that the search ran inside Postgres, reached through the Data API, by an identity that can only run the search.** The architecture argument belongs in the deck.

## Non-negotiables (testable; each names the failure it prevents)
1. Every result comes from a live call to `api.search_insurers`. No mock or cached rows. *Prevents a demo that lies.*
2. The browser never holds a credential, a token or a `Bearer` header (leak test, C7). *Prevents decorative security.*
3. Only contract fields render; `score` only in the drawer's raw JSON (contract test). *Prevents invented data.*
4. No word on screen claims more than a claims row: no "best", "instant", "secure", "real insurer data", no Data API status (copy lint, C5). *Prevents overclaims.*
5. The synthetic label stays on the front screen: payer names are public, IDs and plans are synthetic. *Prevents fake-looks-real.*
6. No timing outside the drawer; the drawer's ms is measured and says a first search may include a database wake-up. *Prevents a cold start read as the product's speed.*
7. Floors: body text 4.5:1, large text 3:1, reads in greyscale; red and yellow appear only as structure blocks in the page frame, and yellow also as the highlight on matched words; red, amber/yellow and green never mark a row, a state or a plan (clinical status colours), checked on computed colours in the browser; reduced motion honoured; body >= 16px, targets >= 32px, measured in the browser. *Prevents illegible or misread screens.* (Amended by Samuel, 2026-10-23, for the Mondrian palette.)

Everything not on this list is the designer's call. Argue with a rule before breaking it.

## Who it is for
- **Inside the story:** a front-desk registrar, 30-55, a desktop at normal zoom, a patient reading a card aloud ("the blue cross one in Georgia"). About 20 seconds. Burned by dropdowns that need exact spelling, and by denied claims from a wrong plan.
- **In the room:** the buyer (gets the screen in 15 s); the app builder ("how do I call this?"); the named skeptic, the customer's DBA or security engineer, who opens the drawer and types a hostile query.

## The 3 surfaces (martini glass: a short guided path, then open exploration)
1. **Front screen, the 15-second read.** One headline that says the job, one search field, a few example typos from the shared top-hit list, the synthetic label. After a search: results grouped by plan, one group per plan name across states. Nothing else competes. Eye path: headline, field, first group.
2. **The mechanism, one click away ("How this search ran").** Tied to this search: what the browser sent (`q`, `page` only), what the app server sent to the Data API and as whom (the app's service principal; the token stays on the server), and the answer's row count and measured time.
3. **The proof, closed by default, inside the drawer.** For the skeptic, each answer names its evidence:
   - "Which grants does the app's identity hold?" → EXECUTE on two functions; the grants test and its last run date (not read live: owner decision 2026-10-23).
   - "Can the browser get the token?" → no; the leak test and its run date.
   - "Where does the ranking run?" → inside Postgres, in `api.search_insurers`; the function's name, never SQL text typed by hand.
   - "What does the raw answer hold?" → the response rows as JSON, `score` included.

## What is already decided
FastAPI + plain static files, no build step · IBM Plex Sans and Mono · wordmark only, no mark · group key: `payer_name` minus ` - {state}` when the suffix equals `state` · groups form from rows on screen; "Show 10 more" extends an open group · the drawer is closed by default · one brilliant colour doing one job (house-style rule 2). Contract: `DATA_CONTRACT.md` (public repo) and `docs/CATECHISM.md` claims C1-C13.

## States to design
Empty page (first visit) · searching · results (one group; many groups; a group that continues on the next page) · no match (the query shown as text) · query too short · upstream error · end of list (50 rows) · drawer open, before and after a search · phone width (390 px).

## Content contract (negative list)
A group shows: the plan name, then per row: state, plan type, payer ID marked synthetic, a copy action. It never shows: score, network status, eligibility or "in network", freshness claims, timing, a total the response does not hold ("10 states" means "on this page"), customer names, or any word from the copy lint.

## Learning from references
- Live: Marti Hearst, *Search User Interfaces*, ch. 5 (searchuserinterfaces.com/book/sui_ch5_retrieval_results.html): group results by well-defined metadata fields; searchers rarely look past the first page. Used for the plan groups and the page-only grouping (rule 3, a few captions).
- Live: USWDS Search (designsystem.digital.gov/components/search) and NHS Header search (service-manual.nhs.uk): the search field always has a label, visible or for screen readers (floors).
- Live, nothing fit: Information is Beautiful, searched `site:informationisbeautiful.net health insurance`; results were job and merchandise pages.
- From knowledge, not checked live: Segel and Heer's martini glass (the 3 surfaces); Tufte's integration of words and graphics (labels on the row, not a legend).
- Domain colours: registration systems use red/amber/green for eligibility status (unverified; treated as a floor anyway).

## Next
Do not draw yet. After sign-off: three whole-page directions on structure, judged at 1440 px and 390 px; then colour once, judged by a contrast table.

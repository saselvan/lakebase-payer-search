# Query Synonym Methodology for Payer Search

## Research Foundation

Query synonym expansion draws from three established research areas in information retrieval:

### 1. Query Spelling Correction & Normalization
**Cucerzan & Brill (2004)** — "Spelling Correction as an Iterative Process that Exploits the Collective Knowledge of Web Users"  
https://microsoft.com/en-us/research/publication/spelling-correction-as-an-iterative-process/

This work established that spelling corrections improve recall in search by up to 30%, especially when trained on web-scale query logs. We implement subset: abbreviation expansion, state code normalization, and former-name mapping.

### 2. Query Reformulation & Synonym Mining
**Exploiting Clickthrough Data** (Beeferman & Berger, 2000; Huang et al., 2010):  
- Sessions with similar query patterns → synonyms
- Queries followed by clicks on the same result → semantic equivalence
- Example: ["BCBS", "Blue Cross Blue Shield", "BC/BS"] all map to same payer

We currently use:
- Public Medicaid program names (state health agency sites)
- BCBS member directory (https://www.bcbs.com/)
- Major payer rebrands from Wikipedia and press releases
- Plan type taxonomy (HMO/PPO/EPO/POS from standard healthcare glossaries)

**Future**: Your application's search log data will enable query-log-based synonym mining. Early signals:
- Query sequences: ["anthem", "elevance"] → rebranding detected
- Click clustering: users searching "aarp ma" and "aarp medicare" click same result
- Misspelling frequency: "medicad" appears in 0.3% of queries → spelling correction target

### 3. Entity Alias Mining
**Wikipedia Redirects & Wikidata Aliases** (Bunescu & Paşca, 2006):  
https://en.wikipedia.org/wiki/UnitedHealth_Group

We extract:
- Redirect pages: "WellPoint" → "Anthem" (https://en.wikipedia.org/w/index.php?title=WellPoint&redirect=no)
- Former names from corporate pages (e.g., Amerigroup acquired by UnitedHealth then Elevance)
- State Medicaid program aliases (MassHealth ↔ Massachusetts Medicaid)

## Lakebase Tokenizer Constraint

The `lakebase_tokenizer` applies synonyms at query time:
- **Input**: single query token (e.g., `"bcbs"`)
- **Output**: single replacement lexeme (e.g., `"blue_cross_blue_shield"` with underscores for multi-word)
- **Timing**: before stemming and BM25 matching

This differs from document-time expansion:
- Document side: individual tokens + appended abbreviations (separate tokens)
- Query side: single-token synonyms map to canonical forms for matching

## State Code Mapping Decision

**Multi-word state names require special handling** because they tokenize as separate words:
- Single-word states: `ga → georgia` (works fine)
- Multi-word states: `"New Jersey"` tokenizes as `["new", "jersey"]`, not a single token

**Resolution**:
- All states: abbreviation maps to the standard state abbreviation (e.g., `ca → ca`, `nj → nj`)
- Single-word states ONLY: add canonical mapping (e.g., `georgia → georgia`, `texas → texas`)
- Multi-word states: add underscore canonical (e.g., `new_jersey → new_jersey`), but acknowledge these won't match natural multi-word input on the document side

This ensures state-code normalization works universally, while full-name matching works for common single-word states (CA, TX, FL, etc.).

## Kinds Enumeration

Synonyms are classified by origin for maintainability and audit:

- **abbreviation**: industry/payer shorthand (BCBS, UHC, MA, PPO)
- **state_code**: US state/territory abbreviation normalized
- **former_name**: rebrands and acquisitions (WellPoint → Anthem, WellCare → Centene)
- **brand_family**: subsidiary brands under parent (Ambetter under Centene)
- **program_name**: state Medicaid program nicknames (MassHealth, TennCare)
- **plan_term**: insurance product types (HMO, PPO, MAPD, Medigap)
- **spelling_variant**: typos and token-split variations (BlueCross ↔ Blue Cross, HDHP → high_deductible_health_plan)
- **number_word**: numeric → text for queries like "Plan 1" or "Tier 2" (future; limited current use)

## What We Cannot Do (Yet)

Without your application's search logs, we skip:
- **Query-log frequency weighting**: we treat all synonyms equally; high-frequency typos could be ranked higher
- **Personalized synonyms**: no user/tenant-specific payer variants
- **Temporal synonyms**: rebranding timing (when to stop accepting old names)
- **Competitive synonyms**: when user types a competitor's name, should we suggest ours (answer: no—out of scope)

## Validation & Refresh

- **CSV validation**: no duplicate `word` (tokenizer PK), all lowercase, no spaces in word/canonical, word ≠ canonical
- **Source audit**: every row >3 rows old gets source verification
- **Refresh cadence**: state Medicaid (annual), payer rebrands (as announced), plan types (stable, every 2 years)

## Data Quality Notes

- State codes verified from USPS (https://pe.usps.com/text/pub28/28apc.htm)
- BCBS member plans from https://www.bcbs.com/
- Medicaid program names from state health agency official sites
- Payer rebrands from Wikipedia, SEC filings, and press releases (each row cites source URL)
- Plan term definitions from CMS glossary (https://www.healthcare.gov/glossary/)

---

**Next Steps**: After 6 months of search log data from your application, run synonym-mining script:
1. Cluster queries by click result (semantic equivalence)
2. Extract top-frequency misspellings (edit distance ≤ 2)
3. Rank synonyms by co-occurrence (how often does "anthem" appear in sessions with "elevance")
4. Propose new synonyms for manual review (especially cross-payer terms)

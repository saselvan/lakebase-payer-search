# Search-quality flywheel

Synonyms should not be added one at a time by hand. The payer universe is closed (about 120
brands), so we can generate the useful forms, test them against the live API, and only then apply
them. The flywheel is one serverless Databricks job, run on demand.

## The loop

1. **Propose.** A Foundation Model (default `databricks-claude-sonnet-4-5`, called with
   `ai_query` on serverless) gets each payer brand and returns JSON: abbreviations, former names,
   one-word misspellings, card text, and 8 queries a front-desk user would type. Answers are
   cached in the UC table `<catalog>.<schema>.fm_payer_proposals`, so a rerun only asks about new
   brands. Public knowledge only.
2. **Validate.** Each proposal becomes a tokenizer synonym row (`word -> canonical`) only if it
   passes the same rules as the rule generator (`insurer_search.synonyms`):
   one lowercase word; not shared by two brands; not a real corpus word; not a state code; not
   already a hand-written or generated synonym. Multi-word proposals are dropped: the tokenizer
   maps one word to one lexeme, and trigram + "did you mean" already handle those.
   Survivors land in `<catalog>.<schema>.fm_synonym_candidates`.
3. **Evaluate.** Two query sets run against the **live** Data API with the real service principal:
   - the rule-generated set (`insurer_search.evaluate`: exact, typo, dropped word, initials,
     joined words, state first);
   - the FM query set (each brand x 8 queries, expected brand known).
   Metrics: brand@10 (a payer of the right brand is in the top 10), product@10, MRR, per kind,
   and the 10 worst brands.
4. **Log.** Each evaluation is an MLflow run in `/Users/<you>/lakebase-payer-search-quality` with
   params (synonym counts by source, model, sample sizes), metrics, and artifacts (misses,
   candidates, the gate report). The current runs are tagged `baseline=current`.
5. **Gate.** The job lists which candidate words appear in which misses. **It applies nothing.**
   A person reviews `results/synonyms_fm_candidates.csv`, copies the good rows into
   `data/seed/synonyms.csv`, runs `SKIP_DATA=1 scripts/setup.sh`, and reruns the job. A change is
   kept only if brand@10 and MRR do not drop.

## Run it

```bash
uv run python scripts/run_quality_job.py                  # cached proposals, 600-payer rule eval
uv run python scripts/run_quality_job.py --refresh-proposals --rule-sample 1500
```

Needs `.env` (see `.env.example`) and the SP secret in secret scope `lakebase-payer-search`
(key `sp_client_secret`). The job runs as you; search calls run as the service principal.

## Where results land

| What | Where |
|---|---|
| FM answers (cache) | UC table `fm_payer_proposals` |
| Candidates that passed validation | UC table `fm_synonym_candidates`, `results/synonyms_fm_candidates.csv` |
| All numbers, misses, run links | `results/flywheel-run.json`, MLflow experiment |
| Summary | `results/flywheel-<date>.md` |

## What comes next: the Search log

Every search is already logged (`audit.search_log`: Tenant, query, result count, time). Once the
EHR also records which result the user picked, the strongest synonym source becomes the users
themselves: queries that end on the same payer are the same thing (click-graph mining), and a
query that is retyped seconds later is a correction (reformulation mining). Those pairs feed
step 1 in place of, or next to, the Foundation Model.

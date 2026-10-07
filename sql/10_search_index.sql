-- Search index over the synced payer aliases. Run as the project owner (admin).
-- Idempotent and zero-downtime: on a re-run the search keys are refreshed CONCURRENTLY, so
-- searches keep reading the old keys until the new ones are ready. A structural change (new
-- columns, new index type) needs a rebuild on purpose: psql -v rebuild=1 (short outage).
-- Schemas:  payer_serving  synced table (owned by the sync)      not exposed
--           search_idx   index + private search function        not exposed
--           audit        search log                             not exposed
--           api          the one public RPC                     EXPOSED in the Data API
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS lakebase_text;
CREATE SCHEMA IF NOT EXISTS tokenizer_ext;
CREATE EXTENSION IF NOT EXISTS lakebase_tokenizer WITH SCHEMA tokenizer_ext;
CREATE SCHEMA IF NOT EXISTS search_idx;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS api;

-- 1. Synonyms: one word -> one lexeme, applied to documents and queries.
--    data/seed/synonyms.csv is hand-written; data/seed/synonyms_generated.csv comes from
--    `python -m insurer_search.synonyms` (initials, bcbs+state, joined words). Hand rows win.
CREATE TABLE IF NOT EXISTS search_idx.synonym_seed (word text PRIMARY KEY, canonical text NOT NULL, kind text, source text);
CREATE TEMP TABLE synonym_generated (LIKE search_idx.synonym_seed INCLUDING DEFAULTS);
TRUNCATE search_idx.synonym_seed;
\copy search_idx.synonym_seed (word, canonical, kind, source) FROM 'data/seed/synonyms.csv' WITH (FORMAT csv, HEADER true)
\copy synonym_generated (word, canonical, kind, source) FROM 'data/seed/synonyms_generated.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO search_idx.synonym_seed SELECT * FROM synonym_generated ON CONFLICT (word) DO NOTHING;
-- 1b. A tokenizer synonym REPLACES the word (one word -> one lexeme, like Postgres' own synonym
--     dictionary). That is right for another form of the same name (bcbs, uhc, a misspelling) and
--     wrong for "is a kind of" (badgercare -> medicaid, ambetter -> centene): it would erase the brand.
--     Rule: an "is a kind of" row (program_name, brand_family, former_name) whose word is still a live
--     brand in the payer names EXPANDS instead - the product keeps its brand and gets the wider word
--     added to its document (step 3). Equivalents (abbreviations, spellings, state codes, initials,
--     joined words) keep replacing, even when they appear in names (bluecross = blue cross, ga = georgia):
--     measured, expanding those dropped brand@10 from 0.986 to 0.942.
ALTER TABLE search_idx.synonym_seed ADD COLUMN IF NOT EXISTS expand boolean NOT NULL DEFAULT false;
CREATE TEMP TABLE payer_name_text AS      -- once: ~6k distinct names, not 3M rows per synonym
SELECT ' ' || btrim(regexp_replace(lower(payer_name), '[^a-z0-9]+', ' ', 'g')) || ' ' AS t
FROM (SELECT DISTINCT payer_name FROM payer_serving.insurer_alias) n;
UPDATE search_idx.synonym_seed s
SET expand = s.kind IN ('program_name', 'brand_family', 'former_name')
         AND EXISTS (SELECT 1 FROM payer_name_text n
                     WHERE n.t LIKE '% ' || replace(lower(s.word), '_', ' ') || ' %');
DELETE FROM tokenizer_ext.lakebase_tokenizer_synonyms WHERE name = 'payer_synonyms';
INSERT INTO tokenizer_ext.lakebase_tokenizer_synonyms (name, word, synonym)
SELECT 'payer_synonyms', lower(word), lower(canonical) FROM search_idx.synonym_seed WHERE NOT expand;

-- 2. Text search config: whole words, lowercase, no accents, synonyms, no stemming (names, not prose).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_ts_dict WHERE dictname = 'payer_dict') THEN
    CREATE TEXT SEARCH DICTIONARY search_idx.payer_dict (
      TEMPLATE = tokenizer_ext.tokenizer_wholeword, Lowercase = 'true', StripAccents = 'true',
      Synonyms = 'payer_synonyms');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'payer_cfg') THEN
    CREATE TEXT SEARCH CONFIGURATION search_idx.payer_cfg (COPY = pg_catalog.simple);
    ALTER TEXT SEARCH CONFIGURATION search_idx.payer_cfg
      ALTER MAPPING FOR asciiword, word, numword, hword_numpart, hword_part, hword_asciipart
      WITH search_idx.payer_dict;
  END IF;
END $$;
ALTER TEXT SEARCH DICTIONARY search_idx.payer_dict (dummy);  -- reload the synonym list

-- 3. The search keys. Aliases are normalised (lowercase, digit runs of 4+ such as group numbers
--    removed, punctuation to spaces) and de-duplicated per Payer. Millions of alias rows collapse
--    to tens of thousands of distinct searchable forms, so search cost follows the number of
--    distinct names, not the number of rows.
--    A multi-word canonical (blue_cross_blue_shield) can only match if the document also holds a
--    word that maps to it, so we append the short form (bcbs) when the long form is present, and
--    the state code (nj -> new_jersey).
-- The tokenizer dictionary cannot run inside parallel workers.
SET max_parallel_workers_per_gather = 0;
DROP MATERIALIZED VIEW IF EXISTS search_idx.alias_doc CASCADE;  -- earlier per-row design (ADR 0003)
\if :{?rebuild}
DROP MATERIALIZED VIEW IF EXISTS search_idx.alias_key CASCADE;
DROP MATERIALIZED VIEW IF EXISTS search_idx.vocab;
\endif
CREATE MATERIALIZED VIEW IF NOT EXISTS search_idx.alias_key AS
WITH norm AS (
  SELECT a.payer_id, a.payer_name, a.alias, a.alias_type, a.state, a.plan_type,
         btrim(regexp_replace(regexp_replace(lower(a.alias), '[0-9]{4,}', ' ', 'g'),
                              '[^a-z0-9]+', ' ', 'g')) AS key_norm
  FROM payer_serving.insurer_alias a
), grouped AS (
  SELECT payer_id, key_norm,
         min(payer_name) AS payer_name, min(state) AS state, min(plan_type) AS plan_type,
         -- show the canonical alias when it is in the group, else the shortest one
         (array_agg(alias ORDER BY alias_type <> 'canonical', length(alias), alias))[1] AS sample_alias,
         bool_or(alias_type = 'canonical') AS is_canon,
         count(*) AS n_aliases
  FROM norm
  WHERE key_norm <> ''
  GROUP BY payer_id, key_norm
)
SELECT row_number() OVER (ORDER BY g.payer_id, g.key_norm) AS key_id, g.*,
       to_tsvector('search_idx.payer_cfg', g.key_norm || ' ' || lower(g.state) || coalesce(' ' || x.extra, '')) AS tsv
FROM grouped g
LEFT JOIN LATERAL (
  SELECT string_agg(e.w, ' ') AS extra
  FROM (
    SELECT s.word AS w            -- long form present: add the short form that maps to it (bcbs)
    FROM search_idx.synonym_seed s
    WHERE NOT s.expand AND s.canonical LIKE '%\_%'
      AND replace(g.key_norm, ' and ', ' ') LIKE '%' || replace(s.canonical, '_', ' ') || '%'
    UNION ALL
    SELECT replace(s.canonical, '_', ' ')   -- brand present: add the wider word (medicaid, centene)
    FROM search_idx.synonym_seed s
    WHERE s.expand AND ' ' || g.key_norm || ' ' LIKE '% ' || replace(s.word, '_', ' ') || ' %'
  ) e
) x ON true
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS alias_key_pk ON search_idx.alias_key (key_id);
-- Stable identity of a key across refreshes (key_id is a row number and can shift).
CREATE UNIQUE INDEX IF NOT EXISTS alias_key_payer_norm ON search_idx.alias_key (payer_id, key_norm);
-- GiST, not GIN: it returns the nearest keys first (ORDER BY <<-> LIMIT n), so a common word such
-- as "care" does not make Postgres score thousands of matches (measured: 2-24 ms vs 79 ms).
CREATE INDEX IF NOT EXISTS alias_key_trgm ON search_idx.alias_key USING gist (key_norm gist_trgm_ops(siglen=128));
-- (BM25 index is created after the first fill: lakebase_bm25 needs a populated table.)

-- 3b. The corpus word list, for "did you mean" correction of query words before BM25.
CREATE MATERIALIZED VIEW IF NOT EXISTS search_idx.vocab AS
SELECT DISTINCT w AS word
FROM (SELECT regexp_split_to_table(key_norm, ' ') AS w FROM search_idx.alias_key
      UNION ALL SELECT lower(word) FROM search_idx.synonym_seed) x
WHERE length(w) >= 2
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS vocab_pk ON search_idx.vocab (word);
CREATE INDEX IF NOT EXISTS vocab_trgm ON search_idx.vocab USING gist (word gist_trgm_ops);

-- 3c. Fill or refresh. First run: plain fill. Later runs: CONCURRENTLY (no read lock).
SELECT relispopulated AS keys_ready FROM pg_class WHERE oid = 'search_idx.alias_key'::regclass \gset
\if :keys_ready
REFRESH MATERIALIZED VIEW CONCURRENTLY search_idx.alias_key;
\else
REFRESH MATERIALIZED VIEW search_idx.alias_key;
\endif
CREATE INDEX IF NOT EXISTS alias_key_bm25 ON search_idx.alias_key USING lakebase_bm25 (tsv);
VACUUM ANALYZE search_idx.alias_key;    -- also updates the BM25 statistics
SELECT relispopulated AS vocab_ready FROM pg_class WHERE oid = 'search_idx.vocab'::regclass \gset
\if :vocab_ready
REFRESH MATERIALIZED VIEW CONCURRENTLY search_idx.vocab;
\else
REFRESH MATERIALIZED VIEW search_idx.vocab;
\endif
ANALYZE search_idx.vocab;

-- 4. Search log: one row per Search. Only the search function writes it.
CREATE TABLE IF NOT EXISTS audit.search_log (
  search_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  searched_at  timestamptz NOT NULL DEFAULT now(),
  customer_id  text NOT NULL,          -- the Tenant
  caller       text NOT NULL,          -- the service identity that called the API
  q            text NOT NULL,
  lim          int NOT NULL,
  off          int NOT NULL,
  result_count int NOT NULL,
  duration_ms  numeric(10,2) NOT NULL
);
CREATE INDEX IF NOT EXISTS search_log_tenant_time ON audit.search_log (customer_id, searched_at);

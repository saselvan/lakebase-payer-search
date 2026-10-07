-- The search function and its owner role. Run as the project owner (admin). Idempotent.
--
-- Security model:
--   api.search_insurers   SECURITY INVOKER wrapper, the only object the Data API exposes.
--                         It records who called (current_user) and hands off.
--   search_idx.search_core SECURITY DEFINER, owned by NOLOGIN role search_owner, which alone can
--                         read the index and append to the log. The caller gets EXECUTE only:
--                         no SELECT on any table, no INSERT on the log.
\set ON_ERROR_STOP on

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'search_owner') THEN
    CREATE ROLE search_owner NOLOGIN;
  END IF;
END $$;
GRANT search_owner TO CURRENT_USER WITH SET TRUE;   -- needed to hand ownership to it
GRANT USAGE ON SCHEMA search_idx, audit, tokenizer_ext TO search_owner;
GRANT SELECT ON search_idx.alias_key, search_idx.vocab TO search_owner;
GRANT SELECT ON tokenizer_ext.lakebase_tokenizer_synonyms TO search_owner;
GRANT INSERT ON audit.search_log TO search_owner;

CREATE OR REPLACE FUNCTION search_idx.search_core(
  q text, customer_id text, lim int, off int, caller text)
RETURNS TABLE (rank int, payer_id text, payer_name text, matched_alias text,
               state text, plan_type text, score real)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
  t0 timestamptz := clock_timestamp();
  qn text := lower(btrim(regexp_replace(coalesce(q, ''), '\s+', ' ', 'g')));
  qc text;   -- qn after "did you mean" (used for BM25; trigram uses qn)
  ws text[];
  i  int;
  n  int;
BEGIN
  -- Input contract. SQLSTATE 22023 -> HTTP 400 from the Data API.
  IF length(qn) < 2 OR length(qn) > 100 THEN
    RAISE EXCEPTION 'q must be 2 to 100 characters' USING ERRCODE = '22023';
  END IF;
  IF customer_id IS NULL OR customer_id !~ '^[A-Za-z0-9_.-]{1,64}$' THEN
    RAISE EXCEPTION 'customer_id must be 1 to 64 characters of A-Z a-z 0-9 _ . -' USING ERRCODE = '22023';
  END IF;
  IF lim IS NULL OR lim < 1 OR lim > 10 THEN
    RAISE EXCEPTION 'lim must be 1 to 10' USING ERRCODE = '22023';
  END IF;
  IF off IS NULL OR off < 0 THEN
    RAISE EXCEPTION 'off must be 0 or more' USING ERRCODE = '22023';
  END IF;

  -- "Did you mean", against the closed corpus word list:
  --  1. a word of 4+ letters that is not in the list becomes the closest list word (fewest edits,
  --     then most trigram-similar), if it is at most 1 edit per 3 letters away;
  --  2. two neighbour words are joined when the joined form is a list word
  --     ("united health" -> "unitedhealth", which the synonym list maps to UnitedHealthcare).
  SELECT array_agg(coalesce(fix.word, t.w) ORDER BY t.ord) INTO ws
  FROM regexp_split_to_table(qn, ' ') WITH ORDINALITY AS t(w, ord)
  LEFT JOIN LATERAL (
    SELECT v.word
    FROM (SELECT word FROM search_idx.vocab ORDER BY word <-> t.w LIMIT 10) v
    WHERE length(t.w) >= 4
      AND NOT EXISTS (SELECT 1 FROM search_idx.vocab x WHERE x.word = t.w)
      AND levenshtein(v.word, t.w) <= greatest(1, length(t.w) / 3)
    ORDER BY levenshtein(v.word, t.w), similarity(v.word, t.w) DESC, v.word
    LIMIT 1
  ) fix ON true;
  qc := '';
  i := 1;
  WHILE i <= coalesce(array_length(ws, 1), 0) LOOP
    IF i < array_length(ws, 1)
       AND EXISTS (SELECT 1 FROM search_idx.vocab x WHERE x.word = ws[i] || ws[i + 1]) THEN
      qc := qc || ' ' || ws[i] || ws[i + 1];
      i := i + 2;
    ELSE
      qc := qc || ' ' || ws[i];
      i := i + 1;
    END IF;
  END LOOP;
  qc := btrim(qc);

  RETURN QUERY
  WITH qv AS MATERIALIZED (  -- the BM25 query, built once
    SELECT to_bm25query(to_tsvector('search_idx.payer_cfg', qc), 'search_idx.alias_key_bm25') AS q
  ), near AS (             -- typo tolerance: the 100 nearest keys by trigram word distance (GiST).
                           -- This KNN scan is most of the CPU of a search; 100 is enough because
                           -- every candidate is scored by BM25 below, not only the BM25 top-k.
    SELECT k.key_id, k.key_norm
    FROM search_idx.alias_key k
    ORDER BY qn <<-> k.key_norm
    LIMIT 100
  ), bm AS (               -- synonyms + exact words: BM25, index-ordered top-k
    SELECT k.key_id,
           -(k.tsv <@> to_bm25query(to_tsvector('search_idx.payer_cfg', qc),
                                    'search_idx.alias_key_bm25')) AS b
    FROM search_idx.alias_key k
    ORDER BY k.tsv <@> to_bm25query(to_tsvector('search_idx.payer_cfg', qc),
                                    'search_idx.alias_key_bm25')
    LIMIT 100
  ), cand AS (             -- union of both candidate sets; each is scored by both methods.
                           -- BM25 is scored for EVERY candidate (standalone <@> scoring, ~free): a
                           -- trigram-only candidate used to get 0 BM25, so a perfect name match
                           -- could lose to a weaker one that happened to be in the BM25 top-k.
    SELECT u.key_id, nullif(greatest(-(k.tsv <@> qv.q), 0), 0) AS b
    FROM (SELECT near.key_id FROM near
          UNION
          SELECT bm.key_id FROM bm WHERE bm.b > 0) u
    JOIN search_idx.alias_key k USING (key_id)
    CROSS JOIN qv
  ), scored AS (           -- Relevance = trigram word similarity (+0.1 x whole-string similarity)
                           --             + BM25 scaled to the best hit (an exact word or synonym match)
    SELECT k.payer_id, k.payer_name, k.sample_alias, k.state, k.plan_type, k.is_canon,
           (word_similarity(qn, k.key_norm) + 0.1 * similarity(qn, k.key_norm)
            + coalesce(c.b / nullif(max(c.b) OVER (), 0), 0))::real AS sc,
           word_similarity(qn, k.key_norm) AS ws, c.b
    FROM cand c JOIN search_idx.alias_key k USING (key_id)
  ), best AS (             -- one row per Payer: its best-matching alias
    SELECT DISTINCT ON (s.payer_id) s.*
    FROM scored s
    WHERE s.ws >= 0.3 OR s.b > 0                       -- drop weak trigram-only neighbours
    ORDER BY s.payer_id, s.sc DESC, s.is_canon DESC, s.sample_alias
  ), ranked AS (
    SELECT row_number() OVER (ORDER BY b.sc DESC, b.payer_name, b.payer_id)::int AS rk, b.*
    FROM best b
  )
  SELECT r.rk, r.payer_id, r.payer_name, r.sample_alias, r.state, r.plan_type,
         round(r.sc::numeric, 4)::real
  FROM ranked r
  WHERE r.rk > off AND r.rk <= least(off + lim, 50)   -- hard cap: 50 results = 5 Pages of 10
  ORDER BY r.rk;

  GET DIAGNOSTICS n = ROW_COUNT;
  INSERT INTO audit.search_log (customer_id, caller, q, lim, off, result_count, duration_ms)
  VALUES (customer_id, coalesce(caller, 'unknown'), qn, lim, off, n,
          round((extract(epoch FROM clock_timestamp() - t0) * 1000)::numeric, 2));
END
$fn$;
-- A new owner needs CREATE on the schema while ownership moves; take it back right after.
GRANT CREATE ON SCHEMA search_idx TO search_owner;
ALTER FUNCTION search_idx.search_core(text, text, int, int, text) OWNER TO search_owner;
REVOKE CREATE ON SCHEMA search_idx FROM search_owner;
REVOKE ALL ON FUNCTION search_idx.search_core(text, text, int, int, text) FROM PUBLIC;

-- The public RPC: POST /api/search_insurers {"q": ..., "customer_id": ..., "lim": 10, "off": 0}
-- CREATE OR REPLACE, never DROP: a drop removes the Caller's EXECUTE grant until step 7 runs,
-- and live searches fail with 403 in between (measured: 83 of 1,051 during a re-run).
CREATE OR REPLACE FUNCTION api.search_insurers(
  q text, customer_id text, lim int DEFAULT 10, off int DEFAULT 0)
RETURNS TABLE (rank int, payer_id text, payer_name text, matched_alias text,
               state text, plan_type text, score real)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
  -- Read the caller into a variable first. Passing current_user straight into the call made the
  -- search ~7x slower (102 ms vs 14 ms, measured 2026-10-06).
  who text := current_user;
BEGIN
  RETURN QUERY SELECT * FROM search_idx.search_core(q, customer_id, lim, off, who);
END
$fn$;
REVOKE ALL ON FUNCTION api.search_insurers(text, text, int, int) FROM PUBLIC;
COMMENT ON FUNCTION api.search_insurers(text, text, int, int) IS
  'Typo-tolerant insurer search. Max 10 per page, max 50 results. customer_id = the Tenant.';

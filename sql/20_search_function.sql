-- The search function and its owner role. Run as the project owner (admin). Idempotent.
--
-- Security model:
--   api.search_insurers   SECURITY INVOKER wrapper, the only object the Data API exposes.
--                         It records who called (current_user) and hands off.
--   search_idx.search_core SECURITY DEFINER, owned by NOLOGIN role search_owner, which alone can
--                         read the index and append to the log. The caller gets EXECUTE only:
--                         no SELECT on any table, no INSERT on the log.
--   The caller also holds EXECUTE on search_core (the INVOKER wrapper needs it), so it can log in to
--   Postgres directly and pass any caller text. Only the Data API logs in as authenticator, so on any
--   other login search_core records session_user, which the caller cannot set.
\set ON_ERROR_STOP on

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'search_owner') THEN
    CREATE ROLE search_owner NOLOGIN;
  END IF;
END $$;
GRANT search_owner TO CURRENT_USER WITH SET TRUE;   -- needed to hand ownership to it
GRANT USAGE ON SCHEMA search_idx, audit, tokenizer_ext TO search_owner;
GRANT SELECT ON search_idx.alias_key, search_idx.search_text, search_idx.vocab TO search_owner;
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
  -- A direct login records its own login name, never the caller argument (see the security model).
  IF session_user::text <> 'authenticator' THEN caller := session_user::text; END IF;
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

  -- Ranking (ADR 0005). Candidates are Search texts, not Search keys: the cut takes 100 different texts, so
  -- a text shared by hundreds of Payers takes one place. Each candidate text expands to every Payer that
  -- has it. Relevance is then computed per Payer.
  RETURN QUERY
  WITH qv AS MATERIALIZED (  -- the query, built once: BM25 query on the keys, and "all query words" for Own name
    SELECT to_bm25query(x.v, 'search_idx.alias_key_bm25') AS qk,
           (SELECT string_agg(quote_literal(l), ' & ') FROM unnest(tsvector_to_array(x.v)) l)::tsquery AS qall
    FROM (SELECT to_tsvector('search_idx.payer_cfg', qc) AS v) x
  ), near AS (             -- typo tolerance: the 100 nearest texts by trigram word distance (GiST)
    SELECT t.text_id, t.key_norm, t.n_payers
    FROM search_idx.search_text t
    ORDER BY qn <<-> t.key_norm, t.text_id   -- text_id: ties at the cut resolve the same on every build
    LIMIT 100
  ), bmk AS (              -- synonyms + exact words: BM25 on the keys (each key's document holds its
                           -- Payer's state word), index-ordered
    SELECT k.key_norm,
           -(k.tsv <@> to_bm25query(to_tsvector('search_idx.payer_cfg', qc), 'search_idx.alias_key_bm25')) AS b
    FROM search_idx.alias_key k
    ORDER BY k.tsv <@> to_bm25query(to_tsvector('search_idx.payer_cfg', qc), 'search_idx.alias_key_bm25'), k.key_id
    LIMIT 1000
  ), bm AS (               -- ... cut over different texts: each text's best key score, 100 texts
    SELECT t.text_id, t.key_norm, t.n_payers
    FROM (SELECT key_norm, max(b) AS b FROM bmk WHERE b > 0 GROUP BY key_norm) x
    JOIN search_idx.search_text t USING (key_norm)
    ORDER BY x.b DESC, t.text_id
    LIMIT 100
  ), tsc AS MATERIALIZED (  -- trigram scores, once per candidate text
    SELECT t.key_norm, t.n_payers,
           word_similarity(qn, t.key_norm) AS ws,
           word_similarity(qn, t.key_norm) + 0.1 * similarity(qn, t.key_norm) AS tri
    FROM (SELECT * FROM near UNION SELECT * FROM bm) t
  ), ex AS MATERIALIZED (               -- expansion: every Payer that has a candidate text; BM25 per key (state word)
    SELECT k.payer_id, k.payer_name, k.sample_alias, k.state, k.plan_type, k.is_canon, k.key_norm,
           s.n_payers, s.ws, s.tri, nullif(greatest(-(k.tsv <@> qv.qk), 0), 0) AS b
    FROM tsc s JOIN search_idx.alias_key k ON k.key_norm = s.key_norm
    CROSS JOIN qv
  ), scored AS (           -- text Relevance = trigram word similarity (+0.1 x whole-string similarity)
                           --                  + BM25 scaled to the best hit
    SELECT e.*, (e.tri + coalesce(e.b / nullif(max(e.b) OVER (), 0), 0))::real AS sc
    FROM ex e
    WHERE e.ws >= 0.3 OR e.b > 0                       -- drop weak trigram-only neighbours
  ), best AS (             -- one row per Payer: its best-matching text (Payer Relevance); of equal texts, the
                           -- one held by the fewest Payers (its Specificity)
    SELECT DISTINCT ON (s.payer_id) s.*
    FROM scored s
    ORDER BY s.payer_id, s.sc DESC, s.n_payers, s.is_canon DESC, s.sample_alias, s.key_norm
  ), floor AS (            -- the 50th best Relevance: a Payer below it cannot reach the 50 results, since
                           -- Own name and Specificity only order Payers of equal Relevance
    SELECT coalesce((SELECT b.sc FROM best b ORDER BY b.sc DESC LIMIT 1 OFFSET 49), -1) AS f
  ), agreed AS MATERIALIZED (  -- Own-name agreement, per Payer over ALL its Own-name texts (not only the
                               -- text it was found by): trigram, or every query word incl. Synonyms
    SELECT b.*,
           EXISTS (SELECT 1 FROM search_idx.alias_key o
                   WHERE o.payer_id = b.payer_id
                     AND o.alias_types && ARRAY['canonical', 'brand', 'state_variant', 'state_first', 'abbreviation']
                     AND (word_similarity(qn, o.key_norm) >= 0.6 OR o.tsv @@ qv.qall)) AS own
    FROM best b CROSS JOIN floor CROSS JOIN qv
    WHERE b.sc >= floor.f
  ), ranked AS (           -- Relevance, then Own name agrees, then Specificity (fewer Payers share the text),
                           -- then name and id. Own name and Specificity order ties only;
                           -- as score weights every tried value lost queries on the strict bar (ADR 0005).
    SELECT row_number() OVER (ORDER BY a.sc DESC, a.own DESC, a.n_payers, a.payer_name, a.payer_id)::int AS rk, a.*
    FROM agreed a
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

-- The public RPC: POST /api/rpc/search_insurers {"q": ..., "customer_id": ..., "lim": 10, "off": 0}
-- CREATE OR REPLACE, never DROP: a drop removes the Caller's EXECUTE grant until step 7 runs,
-- and live searches fail with 403 in between (measured: 83 of 1,051 during a re-run).
CREATE OR REPLACE FUNCTION api.search_insurers(
  q text, customer_id text, lim int DEFAULT 10, off int DEFAULT 0)
RETURNS TABLE (rank int, payer_id text, payer_name text, matched_alias text,
               state text, plan_type text, score real)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
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

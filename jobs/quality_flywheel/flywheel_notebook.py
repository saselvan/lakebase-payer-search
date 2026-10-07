# Databricks notebook source
# MAGIC %md
# MAGIC # Search-quality flywheel: propose -> validate -> evaluate -> gate
# MAGIC 1. **Propose**: a Foundation Model suggests short forms, former names, misspellings and
# MAGIC    realistic dropdown queries for every payer brand (cached in a UC table).
# MAGIC 2. **Validate**: proposals become tokenizer synonym rows under the same safety rules as the
# MAGIC    rule generator (`insurer_search.synonyms`).
# MAGIC 3. **Evaluate**: the rule-generated query set and the FM query set run against the LIVE API.
# MAGIC 4. **Gate**: report which candidates would fix which misses. Nothing is applied here.
# MAGIC Every evaluation is an MLflow run.

# COMMAND ----------
# MAGIC %pip install -q mlflow-skinny "typing_extensions>=4.12"

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
import csv, io, json, os, re, sys, time, difflib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

for k, d in [("data_api_base", ""), ("workspace_host", ""), ("sp_application_id", ""),
             ("uc_catalog", ""), ("uc_schema", ""), ("model", "databricks-claude-sonnet-4-5"),
             ("rule_sample", "600"), ("concurrency", "10"), ("refresh_proposals", "false")]:
    dbutils.widgets.text(k, d)
W = {k: dbutils.widgets.get(k) for k in ["data_api_base", "workspace_host", "sp_application_id", "uc_catalog",
                                         "uc_schema", "model", "rule_sample", "concurrency", "refresh_proposals"]}
os.environ.update(DATA_API_BASE=W["data_api_base"], WORKSPACE_HOST=W["workspace_host"],
                  SP_APPLICATION_ID=W["sp_application_id"],
                  SP_CLIENT_SECRET=dbutils.secrets.get("lakebase-payer-search", "sp_client_secret"))
here = os.path.dirname(dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get())
sys.path.insert(0, f"/Workspace{here}/lib")
from insurer_search.generate import STATES, aliases_for, build_payers, load_seeds  # noqa: E402
from insurer_search import evaluate as rule_eval  # noqa: E402
from insurer_search.client import InsurerSearchClient  # noqa: E402

SEED = f"/Workspace{here}/data/seed"
bases, abbrevs = load_seeds()
payers = build_payers(bases)
by_id = {p.payer_id: p for p in payers}
brands = sorted({b["base_name"] for b in bases})
T = f"{W['uc_catalog']}.{W['uc_schema']}"
print(len(brands), "brands,", len(payers), "payers")

# COMMAND ----------
# 1. PROPOSE: one FM call per brand, cached in UC. Public knowledge only.
PROMPT = ("You help build a US health-insurance payer search dropdown used by clinic front-desk staff. "
          "For the payer brand below, reply with ONLY a JSON object with these keys: "
          "abbreviations (list of initialisms/short forms people type, lowercase), "
          "former_names (list of earlier or legacy brand names), "
          "misspellings (list of common misspellings of the brand), "
          "card_text (list of how the brand is printed on member ID cards), "
          "queries (exactly 8 realistic things a user types into the dropdown to find this brand: "
          "include typos, partial words, abbreviations, and state names or codes where relevant). "
          "Use public knowledge only. Brand: ")
spark.sql(f"CREATE TABLE IF NOT EXISTS {T}.fm_payer_proposals "
          "(brand STRING, model STRING, response STRING, error STRING, created_at TIMESTAMP)")
if W["refresh_proposals"] == "true":
    spark.sql(f"DELETE FROM {T}.fm_payer_proposals WHERE model = '{W['model']}'")
have = {r.brand for r in spark.sql(f"SELECT brand FROM {T}.fm_payer_proposals WHERE model = '{W['model']}' AND error IS NULL").collect()}
todo = [b for b in brands if b not in have]
print("cached:", len(have), "to propose:", len(todo))
if todo:
    spark.createDataFrame([(b,) for b in todo], "brand STRING").createOrReplaceTempView("todo_brands")
    t0 = time.time()
    spark.sql(f"""
      INSERT INTO {T}.fm_payer_proposals
      SELECT brand, '{W['model']}', r.result, r.errorMessage, current_timestamp()
      FROM (SELECT brand, ai_query('{W['model']}', concat('{PROMPT}', brand), failOnError => false) AS r
            FROM todo_brands)""")
    print(f"proposed {len(todo)} brands in {time.time() - t0:.0f}s")


def parse(txt):
    if not txt:
        return None
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


rows = spark.sql(f"""SELECT brand, response, error FROM {T}.fm_payer_proposals WHERE model = '{W['model']}'
                     QUALIFY row_number() OVER (PARTITION BY brand ORDER BY created_at DESC) = 1""").collect()
proposals = {r.brand: parse(r.response) for r in rows}
bad = [b for b, p in proposals.items() if p is None]
fm_errors = [r.error for r in rows if r.error][:5]
print("parsed:", len(proposals) - len(bad), "unparsed:", len(bad), fm_errors)

# COMMAND ----------
# 2. VALIDATE: the same safety rules as insurer_search.synonyms.
def words(s):
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w]


vocab = {w for p in payers for a, _ in aliases_for(p, abbrevs) for w in words(a)}
existing = set()
for f in ["synonyms.csv", "synonyms_generated.csv"]:
    with open(f"{SEED}/{f}", newline="") as fh:
        existing |= {r["word"] for r in csv.DictReader(fh)}

proposed = defaultdict(set)   # word -> {(canonical, kind, brand)}
dropped = defaultdict(int)
for brand, p in proposals.items():
    if not p:
        continue
    btoks = words(brand)
    long_form = "_".join(btoks)
    for kind, key in [("abbreviation", "abbreviations"), ("former_name", "former_names"),
                      ("misspelling", "misspellings"), ("card_text", "card_text")]:
        for item in p.get(key) or []:
            toks = words(item)
            if len(toks) != 1:
                dropped[f"{kind}:multiword"] += 1      # tokenizer maps one word to one lexeme
                continue
            w = toks[0]
            if kind == "misspelling":
                # a one-word misspelling points at the brand word it misspells
                best = max(btoks + ["".join(btoks)], key=lambda t: difflib.SequenceMatcher(None, w, t).ratio())
                if difflib.SequenceMatcher(None, w, best).ratio() < 0.7:
                    dropped["misspelling:unrelated"] += 1
                    continue
                canon = best
            else:
                canon = long_form
            proposed[w].add((canon, kind, brand))

candidates, reasons = [], defaultdict(int)
for w, opts in sorted(proposed.items()):
    canons = {c for c, _, _ in opts}
    why = ("ambiguous" if len(canons) > 1 else "corpus_word" if w in vocab else "state_code" if w.upper() in STATES
           else "exists" if w in existing else "too_short" if len(w) < 3 else "same" if w in canons else None)
    if why:
        reasons[why] += 1
        continue
    canon, kind, brand = sorted(opts)[0]
    candidates.append({"word": w, "canonical": canon, "kind": kind, "brand": brand, "source": f"fm:{W['model']}"})
print(len(candidates), "candidates; dropped:", dict(reasons), dict(dropped))
if candidates:
    spark.createDataFrame(candidates).write.mode("overwrite").saveAsTable(f"{T}.fm_synonym_candidates")

# COMMAND ----------
# 3. EVALUATE against the live API.
client = InsurerSearchClient.from_env()
conc = int(W["concurrency"])

fm_cases = [(brand, q) for brand, p in proposals.items() if p for q in (p.get("queries") or [])[:8]
            if isinstance(q, str) and 2 <= len(q.strip()) <= 100]


def run_fm(case):
    brand, q = case
    try:
        res = client.search(q.strip(), "eval-fm")
    except Exception as e:  # noqa: BLE001
        return brand, q, None, f"error: {str(e)[:120]}"
    rank = next((i + 1 for i, r in enumerate(res) if r["payer_id"] in by_id and by_id[r["payer_id"]].base_name == brand), None)
    return brand, q, rank, res[0]["payer_name"] if res else None


t0 = time.time()
with ThreadPoolExecutor(conc) as ex:
    fm_results = list(ex.map(run_fm, fm_cases))
fm_secs = time.time() - t0
n = len(fm_results)
fm_metrics = {"brand_at_10": round(sum(r[2] is not None for r in fm_results) / n, 4),
              "mrr": round(sum(1 / r[2] for r in fm_results if r[2]) / n, 4), "n": n}
per_brand = defaultdict(list)
for r in fm_results:
    per_brand[r[0]].append(r[2] is not None)
worst = sorted(((b, round(sum(v) / len(v), 3), len(v)) for b, v in per_brand.items()), key=lambda x: x[1])[:10]
fm_misses = [{"brand": r[0], "q": r[1], "top": r[3]} for r in fm_results if r[2] is None]
print("FM eval:", fm_metrics, f"{fm_secs:.0f}s")

t0 = time.time()
rule = rule_eval.run(int(W["rule_sample"]), workers=conc)
print("rule eval:", rule["total"], f"{time.time() - t0:.0f}s")

# COMMAND ----------
# 4. GATE: which candidate words appear in which misses (report only; nothing is applied).
cand_words = {c["word"]: c for c in candidates}
would_fix = []
for m in fm_misses:
    hit = [w for w in words(m["q"]) if w in cand_words]
    if hit:
        would_fix.append({**m, "candidate_words": hit})
print(len(would_fix), "FM misses contain a candidate word")

# COMMAND ----------
# 5. MLFLOW: one run per evaluation.
import mlflow

me = spark.sql("SELECT current_user()").first()[0]
exp = f"/Users/{me}/lakebase-payer-search-quality"
mlflow.set_experiment(exp)
syn_counts = {}
for f in ["synonyms.csv", "synonyms_generated.csv"]:
    with open(f"{SEED}/{f}", newline="") as fh:
        syn_counts[f.replace(".csv", "")] = sum(1 for _ in csv.DictReader(fh))
common = {"model": W["model"], "brands": len(brands), "payers": len(payers), "corpus_rows_target": 3_000_000,
          **{f"syn_{k}": v for k, v in syn_counts.items()}, "fm_candidates": len(candidates)}


def csv_text(rows_, cols):
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    wr.writeheader()
    wr.writerows(rows_)
    return buf.getvalue()


urls = {}
with mlflow.start_run(run_name="rule-eval") as r1:
    mlflow.log_params({**common, "eval": "rule", "sample": W["rule_sample"]})
    mlflow.log_metrics({"brand_at_10": rule["total"]["brand@10"], "product_at_10": rule["total"]["product@10"],
                        "mrr": rule["total"]["mrr"], "n": rule["total"]["n"]})
    for k, v in rule["by_kind"].items():
        mlflow.log_metrics({f"{k}_brand_at_10": v["brand@10"], f"{k}_mrr": v["mrr"]})
    mlflow.set_tags({"baseline": "current", "eval": "rule"})
    mlflow.log_text(json.dumps(rule, indent=1), "rule_eval.json")
    urls["rule"] = r1.info.run_id
with mlflow.start_run(run_name="fm-eval") as r2:
    mlflow.log_params({**common, "eval": "fm", "fm_queries": n})
    mlflow.log_metrics({"brand_at_10": fm_metrics["brand_at_10"], "mrr": fm_metrics["mrr"], "n": n,
                        "misses_with_candidate": len(would_fix)})
    mlflow.set_tags({"baseline": "current", "eval": "fm"})
    mlflow.log_text(csv_text(fm_misses, ["brand", "q", "top"]), "fm_misses.csv")
    mlflow.log_text(csv_text(candidates, ["word", "canonical", "kind", "brand", "source"]), "fm_synonym_candidates.csv")
    mlflow.log_text(json.dumps(would_fix, indent=1), "gate_would_fix.json")
    urls["fm"] = r2.info.run_id
exp_id = mlflow.get_experiment_by_name(exp).experiment_id
host = W["workspace_host"].rstrip("/")
run_urls = {k: f"{host}/ml/experiments/{exp_id}/runs/{v}" for k, v in urls.items()}
print(run_urls)

# COMMAND ----------
dbutils.notebook.exit(json.dumps({
    "fm": fm_metrics, "fm_seconds": round(fm_secs), "fm_worst_brands": worst, "fm_misses": fm_misses,
    "fm_unparsed": bad, "fm_errors": fm_errors, "rule": {"total": rule["total"], "by_kind": rule["by_kind"],
                                                        "misses": rule["misses"]},
    "candidates": candidates, "dropped_reasons": dict(reasons), "dropped_shape": dict(dropped),
    "would_fix": would_fix, "mlflow": run_urls, "experiment": exp}))

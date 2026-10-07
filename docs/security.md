# Security: authentication, authorization, and compliance

## Authentication and authorization

### Data API authentication
The Data API endpoint is HTTPS and requires a **service-principal OAuth token** (no passwords, no database connection strings on the client):

```bash
# Get a token (M2M, service principal credentials)
TOKEN=$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d 'grant_type=client_credentials&scope=all-apis' \
  "https://<workspace-host>/oidc/v1/token" | jq -r .access_token)

# Call the search endpoint
curl -X POST "$DATA_API_BASE/api/rpc/search_insurers" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"q":"bcbs ga","customer_id":"tenant-001","lim":10,"off":0}'
```

Tokens expire after 1 hour. Refresh before expiry; the reference client handles this automatically (see `src/insurer_search/client.py`).

**Error handling**:
- **401**: Token missing, malformed, or invalid signature. The Data API gateway rejects it before reaching Postgres.
- **403**: Token is valid but the identity was not granted `EXECUTE` on the search function.
- **400**: Input validation failed (see `DATA_CONTRACT.md`).

### Role and grants

The search function is exposed through the Data API with a **SECURITY INVOKER** wrapper that runs as the caller. The wrapper hands off to a **SECURITY DEFINER** core function owned by a `NOLOGIN` role:

```sql
-- The caller's Postgres role (created by databricks_create_role, granted to 'authenticator')
GRANT EXECUTE ON FUNCTION api.search_insurers TO "my-service-principal-id";

-- The service principal then:
-- 1. Authenticates with their OAuth secret
-- 2. The Data API converts the token to a Postgres role (via 'authenticator')
-- 3. Runs api.search_insurers as that role
-- 4. api.search_insurers calls search_idx.search_core (SECURITY DEFINER, search_owner role)
-- 5. search_core reads the index and appends to the log
```

The caller has **no direct `SELECT` or `INSERT` grants**; all data access is through the one function. This prevents:
- Reading the raw aliases table.
- Reading the search log (which contains other tenants' queries).
- Inserting or modifying anything.

### Least privilege
The `search_owner` role (which owns the core function):
- Is `NOLOGIN` (cannot log in directly).
- Has `USAGE` on the index schemas and `SELECT` on the index tables.
- Has `INSERT` on the search log (append-only).
- No other grants (no data write, no schema modifications).

## Audit and compliance

### Search log
Every search is logged in `audit.search_log` with:

| Field | Logged | Example |
|---|---|---|
| `searched_at` | Timestamp | `2026-10-06 14:23:45+00` |
| `customer_id` | The Tenant | `tenant-001` |
| `caller` | The caller's Postgres role (identity) | `b75b4e1a-1234-5678-abcd` |
| `q` | The query text (trimmed, normalized) | `bcbs ga` |
| `lim`, `off` | Pagination | `10, 0` |
| `result_count` | Results returned | `8` |
| `duration_ms` | Time in Postgres | `14.25` |

**Interpretation**: The query is not hidden; `customer_id` is the Tenant but does NOT filter results. For per-tenant data isolation, add a WHERE clause or a separate `search_log` per tenant.

Query for audit trail:
```sql
SELECT * FROM audit.search_log
WHERE customer_id = 'tenant-001' AND searched_at > now() - interval '7 days'
ORDER BY searched_at DESC;
```

### HIPAA compliance
Lakebase is supported in Databricks workspaces with the **HIPAA compliance security profile**. The Data API is the REST interface to that same Postgres database.

**HIPAA audit logging** (requires HIPAA workspace):
- Lakebase sends Postgres SQL audit events (`pgaudit`) to `system.access.audit` with `service_name = 'lakebase'`.
- Events include: login attempts, SQL statements (parameters NOT logged), DDL, permission denied errors.
- Log retention: 365 days.
- Statements are NOT logged in this repo's `audit.search_log` (SQL text is in the search log, but the HIPAA pgaudit layer is separate).

**Key point**: PHI never reaches this search. The corpus is payer names (public) and synthetic IDs. No patient data of any kind.

**Before go-live**: Run one test search and confirm `service_name = 'lakebase'` rows appear in `system.access.audit`:

```sql
SELECT service_name, ip_address, action_name, object_name
FROM system.access.audit
WHERE service_name = 'lakebase'
ORDER BY timestamp DESC LIMIT 5;
```

**Turning HIPAA on later**: If the workspace did not have HIPAA when the Lakebase project was created, the project must **restart compute once** before audit logging applies.

References:
- [AWS HIPAA compliance](https://docs.databricks.com/aws/en/oltp/projects/hipaa-compliance)
- [Azure HIPAA compliance](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/hipaa-compliance)
- [AWS HIPAA audit logging](https://docs.databricks.com/aws/en/oltp/projects/hipaa-audit-logging)
- [Azure HIPAA audit logging](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/hipaa-audit-logging)

### Private Link
The Data API is HTTPS on port 443. A Data API-only application uses the standard **inbound Private Link endpoint** (no need for the separate Postgres port 5432 endpoint).

Reference: [AWS Private Link](https://docs.databricks.com/aws/en/oltp/projects/private-link) or [Azure Private Link](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/private-link).

## Input validation and SQL injection

The search function validates all input in SQL:

```sql
-- Length check
IF length(qn) < 2 OR length(qn) > 100 THEN
  RAISE EXCEPTION 'q must be 2 to 100 characters' USING ERRCODE = '22023';
END IF;

-- customer_id format
IF customer_id IS NULL OR customer_id !~ '^[A-Za-z0-9_.-]{1,64}$' THEN
  RAISE EXCEPTION 'customer_id must be 1 to 64 characters' USING ERRCODE = '22023';
END IF;
```

**Parameterized queries**: The `q` parameter is passed as a parameter and used in `to_tsvector()` (full-text vector building) and regex matching, never concatenated into SQL. This prevents SQL injection. See tests: `tests/test_api_live.py::test_sql_injection_*`.

## Security model summary

| Layer | Control | Who enforces |
|---|---|---|
| **Network** | HTTPS only, no insecure HTTP | Databricks |
| **Authentication** | OAuth M2M token (1 h expiry) | Databricks identity service |
| **Authorization** | `EXECUTE` on one function, no table access | Lakebase Postgres |
| **Data isolation** | `SECURITY DEFINER` core, `NOLOGIN` owner | Postgres |
| **Input validation** | Length, format, charset (error = HTTP 400) | Search function |
| **Audit** | Per-search log + HIPAA pgaudit (if enabled) | Postgres + Databricks |

## Known limits

- **`customer_id` is trusted**: It is logged but does not filter results. Add a WHERE clause in your application if you need per-tenant result filtering.
- **No row-level security (RLS)** on the payer list. All callers see all payers.
- **Query text is logged in `audit.search_log`** (not PHI, but potentially sensitive if queries embed patient context). Use generic queries if privacy is a concern.
- **The Data API rate limits are not documented**. Load-test at your peak to confirm headroom.
- **8 s statement timeout** on the `authenticator` role. Searches take milliseconds, so this is not a concern. Custom queries running longer will hit this limit.

## Testing security

The repo includes security tests that run against the live API:

```bash
# All security tests
uv run pytest tests/test_api_live.py -m security -v

# Individual tests
uv run pytest tests/test_api_live.py::test_missing_token -v
uv run pytest tests/test_api_live.py::test_wrong_sp_403 -v
uv run pytest tests/test_api_live.py::test_sql_injection_q -v
```

These tests verify:
- Missing or malformed tokens are rejected (401).
- Valid tokens for other identities get 403 (permission denied).
- SQL injection attempts are handled as input validation (400 or no harm).
- The search log records the correct caller identity.

## Related

- [Architecture: SECURITY DEFINER design](architecture.md#security-definer--nologin-owner)
- [ADR 0004: SECURITY DEFINER function is the only door](adr/0004-definer-function-is-the-only-door.md)
- [Databricks Data API docs](https://docs.databricks.com/api/workspace/endpoints/list-get) (TODO: confirm public URL)

-- Give the Caller service principal the least access it needs. Run as the project owner.
-- Usage: scripts/psql.sh -v sp=<SP_APPLICATION_ID> -f sql/40_caller_grants.sql
-- The role MUST come from databricks_create_role(): a role made in the UI or the roles REST API
-- cannot be granted to authenticator, and the Data API then answers 403.
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS databricks_auth;
SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'sp') THEN 'role exists'
            ELSE (SELECT 'created ' || databricks_create_role(:'sp', 'SERVICE_PRINCIPAL')::text) END;
GRANT :"sp" TO authenticator;

-- Only: call the public RPC (which calls the private definer function). Nothing else.
GRANT USAGE ON SCHEMA api, search_idx TO :"sp";
GRANT EXECUTE ON FUNCTION api.search_insurers(text, text, int, int) TO :"sp";
GRANT EXECUTE ON FUNCTION search_idx.search_core(text, text, int, int, text) TO :"sp";
-- Explicitly none: no SELECT on payer_serving / search_idx tables, no access to audit.
REVOKE ALL ON ALL TABLES IN SCHEMA payer_serving, search_idx, audit FROM :"sp";

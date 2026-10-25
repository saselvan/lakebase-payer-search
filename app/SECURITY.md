# Security of the demo app: what its identity can do

The app server calls the search as its own service principal. The browser talks only to the app server and never receives a token. This page lists the rights that identity holds in the database, as read on production on 2026-10-24.

## Direct grants

The app's Postgres role holds these grants and no others:

- EXECUTE on `api.search_insurers` and `search_idx.search_core`.
- USAGE on schemas `api` and `search_idx`.

It holds no right on any table, view, column or sequence in a data schema, no CREATE, and no role membership. Checked by `test_app_sp_grants` on production, 2026-10-24 (`docs/CATECHISM.md`, claim C6).

## Standard defaults that every role gets

Postgres gives the role PUBLIC a set of rights, and every role inherits them. These are standard defaults of the database, not grants made for this app. On production on 2026-10-24, PUBLIC held:

- CONNECT and TEMPORARY on the database: connect, and create temporary tables.
- USAGE on schema `public`.
- SELECT on 2 platform views: `databricks_list_roles` and `databricks_synced_table_managers`.
- EXECUTE on 4 platform functions: `grant_all_on_new_sequences()`, `grant_select_on_new_objects()`, `grant_usage_on_new_schema()` and `pgrst.pre_config()`.
- EXECUTE on 91 extension functions.

None of these objects is in schema `api`, so the Data API does not expose them. The platform roles own the views and the functions; only the database rights are the project owner's to change. The owner kept all of them on 2026-10-24. Both search functions set their search path to `pg_catalog, pg_temp`, and PUBLIC can run neither (claim C8).

`tests/test_panel.py::test_public_defaults_on_production_are_the_pinned_set` pins the database, schema, view and platform function rights on production. The 91 extension functions are a count from the same read, not pinned by name.

## The token

The app holds no database password. The Databricks Apps runtime gives the server the service principal's OAuth credentials, and the server exchanges them for a token. The browser never receives the token, a `Bearer` header or the client secret. Checked on the deployed app by `test_deployed_responses_hold_no_secret_by_shape`, 2026-10-24 (claim C7).

## What the page shows

The lens payload holds no tenant value and no host: `customer_id` appears by name only, and the Data API call appears by path only. The page runs under a strict Content Security Policy with no inline script and no third-party origin.

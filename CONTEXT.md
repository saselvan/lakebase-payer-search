# Insurer search

A typo-tolerant search over US health insurers, served as a REST API from Lakebase. An EHR calls it from an insurance-company dropdown.

## Language

### The data

**Payer**:
One insurance company or program that pays claims, identified by a `payer_id`.
_Avoid_: Insurer (in code), carrier, plan company

**Alias**:
One name a user might type for a Payer: its legal name, a brand, an abbreviation, a state or plan variant, or a former name. A Payer has many Aliases.
_Avoid_: Synonym (that word is for the search engine's word list), variant

**Synonym**:
A word-level equivalence the search engine applies at query time, such as `bcbs` = `blue cross blue shield`. Synonyms reduce how many Aliases must be stored.
_Avoid_: Alias

**Search key**:
One normalised form of a Payer's Aliases (lowercase, no group numbers or punctuation). Many Aliases share one Search key; search runs over Search keys.
_Avoid_: Index row, document

### The request

**Tenant**:
The EHR vendor's own customer (a clinic or practice) on whose behalf a search runs. Carried as `customer_id`.
_Avoid_: Customer (ambiguous: the EHR vendor is also a customer), client

**Caller**:
The one service identity that sends every search for every Tenant.
_Avoid_: User, app

**Search**:
One request with a query text, a Tenant, and a Page. Returns Payers ranked by Relevance.

**Relevance**:
How close the query is to a Payer's best-matching Alias, by spelling and by Synonym. The only sort order.

**Page**:
A window of at most 10 results. A Search can reach at most 5 Pages (50 results); asking past that returns nothing.

**Search log**:
The record of every Search: Tenant, query, result count, time, and Caller.

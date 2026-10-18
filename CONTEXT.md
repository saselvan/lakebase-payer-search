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

**Own name**:
A Payer's canonical name and the forms built from it: its brand, state forms and abbreviation forms. What the Payer is called, as opposed to an Alias another source says it is also called.
_Avoid_: Canonical alias, primary alias

**Search text**:
One normalised text (lowercase, no group numbers or punctuation). One or more Payers have it; "uhc" is one Search text that 561 Payers share. Search finds candidates by Search text.
_Avoid_: Key, token

**Search key**:
One Payer's link to one Search text. Many of a Payer's Aliases collapse into one Search key.
_Avoid_: Index row, document

**Specificity**:
How few Payers share a Search text. A text held by one Payer is fully specific; a text shared by hundreds tells the search little about which Payer is meant.
_Avoid_: Uniqueness, rarity

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
How well the query fits a Payer: by spelling and by Synonym, through the Payer's best-matching Search text. A match on the Payer's Own name counts more than an Alias match, and a match on a Search text with low Specificity counts less. The only sort order.

**Page**:
A window of at most 10 results. A Search can reach at most 5 Pages (50 results); asking past that returns nothing.

**Search log**:
The record of every Search: Tenant, query, result count, time, and Caller.

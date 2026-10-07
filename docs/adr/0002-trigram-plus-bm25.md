# Rank with trigram similarity plus BM25 with a synonym list

Typos ("blu cros") need character-level matching, which trigrams give and BM25 does not. Abbreviations ("bcbs", "uhc") share no characters with their expansions, which a BM25 synonym list fixes and trigrams cannot. We combine both in one SQL function instead of exploding the data into one row per abbreviation, which is how the corpus grows to millions of rows today.

# Mutation gate

## Offline (2026-10-06, committed state, scripts/mutation_gate_offline.py)

| Mutation applied | Test that went red | Result line |
|---|---|---|
| payer dedup removed | test_national_payer_expands_per_state_and_unknown_codes_are_dropped | RED: 1 failed, 2 passed in 0.91s |
| product_name repeats words | test_product_name_never_repeats_the_joining_words[Humana | RED: 1 failed, 5 passed in 0.08s |
| abbreviation not longest-first | test_abbreviation_uses_longest_expansion_and_tolerates_and | RED: 1 failed, 3 passed in 0.07s |
| unknown state codes kept | test_national_payer_expands_per_state_and_unknown_codes_are_dropped | RED: 1 failed, 2 passed in 0.06s |
| synonym ambiguity check removed | test_generated_rows | RED: 1 failed, 11 passed in 0.33s |
| synonym real-word check removed | test_generated_rows | RED: 1 failed, 11 passed in 0.44s |
| bcbs+state does not win | test_candidates_for_a_bcbs_state_plan | RED: 1 failed, 10 passed in 0.34s |
| cross-word joins allowed | test_two_word_state_gets_the_bcbs_state_form_and_no_cross_word_joins | RED: 1 failed, 12 passed in 0.33s |

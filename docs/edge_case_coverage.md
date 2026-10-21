# Edge-case coverage matrix (round 20)

Cohort: `data/wasl_edge.db`, 20,000 planned people (seed 2020), generated 2026-10-21T05:51 UTC by `python -m wasl.seed --edge`. Matrix built by `scripts/edge_coverage.py`.

A cell counts a person only when the **database** shows the scenario actually happened (e.g. a recorded no-show, a withdrawal Consent, a death audit event, the review reason on the case) **and** the result was recorded (never lost). Planned-but-not-realised people (e.g. the journey closed before the planned withdrawal) are not counted. `n/a` = combination the rules/pathways cannot produce (reason listed below). Severity = the tier the rules engine gives the clean result (T0 critical, T1 urgent, T2 high, T3 routine, negative = no case).

## diabetes_hba1c

| scenario | critical | urgent | high | routine | negative |
|---|---|---|---|---|---|
| baseline | 119 | 121 | 129 | 126 | 123 |
| boundary_value | 49 | 36 | 42 | 45 | 57 |
| multimorbidity | 30 | 31 | 27 | 25 | 5 |
| duplicate_result | 35 | 28 | 26 | 18 | 32 |
| conflicting_result | 30 | 27 | 28 | 25 | 32 |
| missing_value | 39 | 27 | 29 | 34 | 30 |
| unknown_value | 36 | 40 | 32 | 47 | 28 |
| unit_mismatch | 16 | 14 | 28 | 21 | 19 |
| amended_result | 26 | 34 | 34 | 28 | 34 |
| preliminary_then_final | 33 | 29 | 27 | 29 | 24 |
| pregnancy | 28 | 33 | 36 | 25 | 26 |
| age_youngest | 37 | 30 | 30 | 31 | 33 |
| age_oldest | 34 | 23 | 32 | 26 | 30 |
| haemoglobinopathy | 43 | 28 | 31 | 30 | 23 |
| specialist_care | 26 | 35 | 26 | 35 | 24 |
| deceased_before_result | 28 | 25 | 21 | 33 | 32 |
| died_mid_pathway | 15 | 20 | 32 | 42 | n/a |
| consent_withdrawn | 22 | 22 | 35 | 39 | n/a |
| reopened | 28 | 25 | 26 | 21 | n/a |
| interpreter_need | 21 | 22 | 44 | 40 | 23 |
| no_show | 108 | n/a | 94 | n/a | n/a |
| repeated_reschedule | 120 | n/a | 92 | n/a | n/a |
| out_of_area_move | 24 | 25 | 39 | 34 | n/a |
| ramadan_holiday | 35 | 24 | 35 | 39 | 37 |
| stale_specimen | 31 | 41 | 28 | 23 | 27 |
| future_specimen | 20 | 24 | 23 | 35 | 36 |
| malformed_hl7 | 53 | 42 | 41 | 48 | 30 |
| duplicate_identity | 20 | 27 | 33 | 39 | 24 |

## breast

| scenario | critical | urgent | high | routine | negative |
|---|---|---|---|---|---|
| baseline | n/a | 347 | 334 | n/a | 329 |
| boundary_value | n/a | n/a | n/a | n/a | n/a |
| multimorbidity | n/a | 53 | 55 | n/a | 15 |
| duplicate_result | n/a | 51 | 64 | n/a | 54 |
| conflicting_result | n/a | 54 | 56 | n/a | 51 |
| missing_value | n/a | 65 | 61 | n/a | 52 |
| unknown_value | n/a | 40 | 58 | n/a | 51 |
| unit_mismatch | n/a | n/a | n/a | n/a | n/a |
| amended_result | n/a | 46 | 43 | n/a | 64 |
| preliminary_then_final | n/a | 47 | 49 | n/a | 55 |
| pregnancy | n/a | 38 | 41 | n/a | 36 |
| age_youngest | n/a | 51 | 55 | n/a | 49 |
| age_oldest | n/a | 40 | 55 | n/a | 57 |
| haemoglobinopathy | n/a | 51 | 39 | n/a | 59 |
| specialist_care | n/a | 40 | 58 | n/a | 50 |
| deceased_before_result | n/a | 54 | 42 | n/a | 56 |
| died_mid_pathway | n/a | 67 | 77 | n/a | n/a |
| consent_withdrawn | n/a | 83 | 76 | n/a | n/a |
| reopened | n/a | 62 | 64 | n/a | n/a |
| interpreter_need | n/a | 49 | 50 | n/a | 52 |
| no_show | n/a | 111 | 96 | n/a | n/a |
| repeated_reschedule | n/a | 107 | 82 | n/a | n/a |
| out_of_area_move | n/a | 63 | 41 | n/a | n/a |
| ramadan_holiday | n/a | 48 | 50 | n/a | 54 |
| stale_specimen | n/a | 56 | 50 | n/a | 43 |
| future_specimen | n/a | 39 | 38 | n/a | 53 |
| malformed_hl7 | n/a | 72 | 86 | n/a | 72 |
| duplicate_identity | n/a | 38 | 53 | n/a | 58 |

## cervical

| scenario | critical | urgent | high | routine | negative |
|---|---|---|---|---|---|
| baseline | n/a | 228 | 245 | 242 | 250 |
| boundary_value | n/a | n/a | n/a | n/a | n/a |
| multimorbidity | n/a | 37 | 34 | 43 | 16 |
| duplicate_result | n/a | 48 | 30 | 39 | 38 |
| conflicting_result | n/a | 50 | 34 | 46 | 28 |
| missing_value | n/a | 37 | 35 | 30 | 47 |
| unknown_value | n/a | 28 | 34 | 42 | 26 |
| unit_mismatch | n/a | n/a | n/a | n/a | n/a |
| amended_result | n/a | 34 | 41 | 39 | 38 |
| preliminary_then_final | n/a | 45 | 41 | 37 | 48 |
| pregnancy | n/a | 41 | 32 | 42 | 41 |
| age_youngest | n/a | 34 | 38 | 39 | 29 |
| age_oldest | n/a | 32 | 37 | 49 | 44 |
| haemoglobinopathy | n/a | 34 | 38 | 34 | 33 |
| specialist_care | n/a | 45 | 36 | 33 | 34 |
| deceased_before_result | n/a | 47 | 31 | 28 | 28 |
| died_mid_pathway | n/a | 45 | 49 | 54 | n/a |
| consent_withdrawn | n/a | 27 | 51 | 46 | n/a |
| reopened | n/a | 38 | 37 | 31 | n/a |
| interpreter_need | n/a | 43 | 38 | 37 | 37 |
| no_show | n/a | 101 | 118 | n/a | n/a |
| repeated_reschedule | n/a | 93 | 91 | n/a | n/a |
| out_of_area_move | n/a | 44 | 36 | 43 | n/a |
| ramadan_holiday | n/a | 42 | 40 | 44 | 32 |
| stale_specimen | n/a | 36 | 40 | 35 | 33 |
| future_specimen | n/a | 38 | 33 | 37 | 43 |
| malformed_hl7 | n/a | 53 | 70 | 57 | 71 |
| duplicate_identity | n/a | 34 | 43 | 52 | 36 |

## colorectal_fit

| scenario | critical | urgent | high | routine | negative |
|---|---|---|---|---|---|
| baseline | n/a | 150 | 159 | 138 | 150 |
| boundary_value | n/a | 81 | 73 | n/a | 62 |
| multimorbidity | n/a | 36 | 27 | 40 | 11 |
| duplicate_result | n/a | 40 | 36 | 36 | 33 |
| conflicting_result | n/a | 47 | 45 | 39 | 45 |
| missing_value | n/a | 47 | 58 | 37 | 35 |
| unknown_value | n/a | 36 | 34 | 43 | 28 |
| unit_mismatch | n/a | 40 | 32 | 38 | 29 |
| amended_result | n/a | 39 | 35 | 41 | 46 |
| preliminary_then_final | n/a | 28 | 45 | 39 | 44 |
| pregnancy | n/a | 48 | 40 | 44 | 39 |
| age_youngest | n/a | 43 | 34 | 33 | 30 |
| age_oldest | n/a | 30 | 45 | 34 | 37 |
| haemoglobinopathy | n/a | 39 | 48 | 41 | 28 |
| specialist_care | n/a | 38 | 33 | 44 | 35 |
| deceased_before_result | n/a | 41 | 36 | 37 | 39 |
| died_mid_pathway | n/a | 40 | 46 | 52 | n/a |
| consent_withdrawn | n/a | 45 | 52 | 48 | n/a |
| reopened | n/a | 43 | 39 | 48 | n/a |
| interpreter_need | n/a | 39 | 34 | 43 | 43 |
| no_show | n/a | 66 | 76 | 67 | n/a |
| repeated_reschedule | n/a | 63 | 66 | 38 | n/a |
| out_of_area_move | n/a | 45 | 50 | 43 | n/a |
| ramadan_holiday | n/a | 40 | 35 | 32 | 31 |
| stale_specimen | n/a | 34 | 39 | 50 | 38 |
| future_specimen | n/a | 42 | 39 | 27 | 29 |
| malformed_hl7 | n/a | 62 | 52 | 58 | 46 |
| duplicate_identity | n/a | 34 | 45 | 37 | 30 |

## Summary

* Supported cells: 403; filled: 403; **empty: 0**; n/a cells: 157.
* People with a verified scenario: 19,202 of 20,000 planned (96.0%).
* Languages (all patients incl. duplicate records): {'ar': 3326, 'en': 3347, 'hi': 3355, 'ml': 3651, 'tl': 3581, 'ur': 3343}; interpreter call tasks recorded on 1,508 cases.
* Malformed HL7 variants: {'dob_mismatch': 136, 'bad_delimiters': 114, 'not_hl7': 110, 'two_pids': 114, 'unknown_loinc': 163, 'sn_comparator': 42, 'missing_pid': 117, 'bom_mllp': 118}; first-message outcome: {'needs_match_review': 253, 'rejected': 224, 'needs_review': 241, 'processed': 196} (rejected messages are re-sent by the lab; held ones are resolved by a coordinator or re-sent - no result is lost).
* Ingest message statuses: {'resolved': 253, 'processed': 1842, 'needs_review': 241}.

### Why cells are n/a

* breast has no critical or routine tier; FIT and cervical have no critical tier (clinical_rules.md).
* journey scenarios (death or consent withdrawal mid-pathway, re-open, no-show, reschedule, move) need an open case; a negative result creates none.
* boundary values and unit mismatches need a numeric result (FIT, HbA1c/FPG), not BI-RADS or HPV/cytology categories; the FIT spoilt-kit category has no cut-off.
* no-show / reschedule need a specialist appointment: GP-led diabetes pathways (prediabetes, diabetes range, hypoglycaemia review) and routine cervical pathways (repeat test, 12-month surveillance) do not book one.

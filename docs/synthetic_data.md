# Synthetic data: calibration, assumptions and invariants

All people, results and events in Wasl are **synthetic**. Nothing describes a real person. Values come from a
published source where one was found; anything else is an **ASSUMPTION (A)** chosen to be plausible and to
exercise the safety paths. Code: `wasl/synth/cohort.py` (people), `wasl/synth/results.py` (results),
`wasl/synth/history.py` (pathway journeys), `wasl/seed.py`.

## Two modes

| | `make seed` (default demo) | `make seed-large` |
|---|---|---|
| People | 2,000 (+5 named demo stories) | 20,000 (+5) |
| Ages | 25–84 (keeps the demo queue focused) | 18–84 (all adults) |
| Results | **enriched**: drawn from the same distributions, kept only if they create a case (rejection sampling), defects × 5 | **realistic**: full distribution (most results negative), defects at base rates, duplicate re-sends, late results after closure |
| Database | `data/wasl.db` | `data/wasl_large.db` (models in `data/models_large`, the demo is untouched) |
| Time | ~1 min incl. ML training | ~3 min, no ML training |

## Population (cohort.py)

| Parameter | Value | Source |
|---|---|---|
| Sex ratio | 33 % female, 67 % male | Abu Dhabi Census 2023 (3,789,860 residents; 67 % male) - Statistics Centre Abu Dhabi, https://www.mediaoffice.abudhabi/en/government-affairs/statistics-centre-abu-dhabi-reports-results-of-abu-dhabi-census-2023/ |
| Adult age bands 18–24/25–34/35–44/45–54/55–64/65–74/75–84 | 10/30/28/17/10/4/1 % | A, shaped to the census median age of 33 (whole population incl. children; adult median in the cohort ≈ 38) |
| Nationality mix (Emirati, Indian, Pakistani, Filipino, Other Arab, Western, Other) | see `NATIONALITY` | A - no official nationality split is published with the 2023 census |
| Languages by nationality, regions, phone/smartphone/WhatsApp access, responsiveness | see code | A |
| Programme of the screening event | diabetes 40 %, FIT 25 %, cervical 20 %, breast 15 %, among the programmes the person is eligible for | A; eligibility from DoH/ADPHC (FIT 40–75, cervical women 25–65, breast women ≥ 40, diabetes adults) |
| Opportunistic screening outside the eligible age | 1 % of people (only where an out-of-age programme exists) | A - exercises the age-review path |
| Pregnant (women 18–45, cervical/diabetes events) | 1.5 % | A |
| Haemoglobin variant flag | 5 % | A (carrier states are common in the Gulf; no single verified UAE figure used) |
| Already under the specialist team for the programme | 0.3 % | A |
| Recorded deceased before the result is handled | 0.05 % | A |

## Results (results.py)

| Programme | Parameter | Value | Source |
|---|---|---|---|
| FIT | positive at ≥ 80 µg/g | 4.5 % of kits | A - between England 1.71 % at 120 µg/g (UKHSA 2024-25; pilot 2.1 %, Br J Cancer 2022 https://www.nature.com/articles/s41416-022-01919-y) and Abu Dhabi 13.5 % (FIT/gFOBT 2015–16, BMC Cancer 2021 https://doi.org/10.1186/s12885-021-08623-9); lower threshold → higher positivity |
| FIT | ≥ 400 µg/g among positives | 22 % | A |
| FIT | invalid/spoilt kit | 1 % | A |
| Cervical | high-risk HPV positive | 14.7 % | UAE scoping review, Qatar Med J 2024, https://doi.org/10.5339/qmj.2024.33 |
| Cervical | HPV16/18 among positives | 28 % | A |
| Cervical | reflex cytology mix (other hrHPV / 16-18) | NILM 66/50 %, ASC-US 17/15, LSIL 10/12, ASC-H 3/7, HSIL 3.5/14, AGC 0.5/1, SCC 0/1 % | A |
| Breast | BI-RADS 1 / 2 / 0 / 3 / 4 / 5 | 60 / 29.2 / 10 / 0.2 / 0.5 / 0.1 % | A, consistent with recall (abnormal interpretation) 5–12 % benchmark (ACR) and BCSC mean 11.6 %, cancer detection 5.1/1000 (https://pmc.ncbi.nlm.nih.gov/articles/PMC5375631/); DoH target recall < 10–15 % |
| Diabetes | HbA1c ≥ 6.5 % | 12.3 % | IDF Diabetes Atlas 2021, UAE (20–79), https://www.ncbi.nlm.nih.gov/books/NBK582202/ |
| Diabetes | prediabetes 5.7–6.4 % | 18 % | IDF 2021 UAE IGT 18.3 % used as a proxy (A: IGT ≠ HbA1c-defined prediabetes) |
| Diabetes | split of diabetes range: 6.5–9.9 / 10–14.9 / ≥ 15 % | 9.4 / 2.7 / 0.2 % | A |
| Diabetes | reported in mmol/mol; FPG instead of HbA1c; FPG in mmol/L | 20 %; 15 %; 30 % of FPG | A - exercises unit handling |
| All | data defects per result: missing unit / malformed / implausible / amended / preliminary | 0.3 / 0.1 / 0.05 / 0.3 / 0.2 % | A |
| All | duplicate re-send of the same report; new result after closure | 0.4 %; 0.3 % of closed cases | A |

## Journeys (history.py)

| Parameter | Value | Source |
|---|---|---|
| Appointment no-show (with reminders) | emerges at ≈ 12–13 % in the 20k replay | UAE primary care 20.8 % → 10.3 % after an AI-reminder intervention (https://pmc.ncbi.nlm.nih.gov/articles/PMC11729783/); the replay represents "with Wasl" |
| Confirmatory step positive, by pathway | reflex cytology 34 %, reflex HPV 45 %, breast assessment 25 %, diabetes confirmation 80 %, repeat FIT 10 %, repeat sample 15 % | A |
| Pathway logic | prediabetes and HPV-other/NILM → GP surveillance care plan (no specialist referral); critical diabetes → same-day review; negative follow-up for cervical/diabetes → surveillance plan, never a bare closure | clinical rules doc |
| Review queue | clinical lead resolves review cases; deceased → authorised closure; under-specialist-care → "care elsewhere verified" closure | prototype workflow |

The simulator used for the KPI comparison (`wasl/synth/simulator.py`) has its own baseline parameters; the
Abu Dhabi 2015–16 study (30.5 % colonoscopy completion, median 72 days) is the reference for the "no Wasl"
baseline. KPIs from the simulator are **simulated**, not evidence of real-world effect.

## Realised values in the 20,000-person realistic cohort (seed 42)

| Quantity | Realised | Target |
|---|---|---|
| Female share | 32.8 % | 33 % (census) |
| FIT positive (≥ 80) | 5.1 % (+1.0 % repeat-kit) | 4.5 % (A) |
| Cervical HPV-positive pathways (actionable) | 16.1 % actionable | hrHPV 14.7 % + repeat samples |
| Breast actionable (recall) | 10.3 % | ~10 % (A; benchmark 5–12 %) |
| Diabetes actionable | 29.1 % (prediabetes + diabetes) | 30.3 % (12.3 + 18) |
| Diabetes critical | 0.3 % of diabetes screens | 0.2 % (A) |
| Review cases (all reasons) | ≈ 11 % of cases | – |
| No-show | 12.6 % | 10–21 % |

`tests/test_synthetic_data.py` re-checks these calibrations on 40,000 fresh draws per programme and checks
that every distribution table sums to 1.

## Invariants (scripts/validate_data.py, `make validate` / `make validate-large`)

42 invariants checked on **every** record (exit code 1 on any violation):

* Patient: valid sex, plausible birth year, pregnancy only for females.
* Report/observation: exactly one observation per screening report; confirmatory results valid and linked;
  every actionable or review result linked to a case (**no lost results**); **independent re-application of the
  rules to every stored result** finds no case-creating result filed as negative or unlinked; sex and age
  eligibility respected or flagged for review; HbA1c in the plausible range or under review; numeric results
  have a unit or are under review; interpretation code consistent with the conclusion.
* Case: valid tier; open cases have an owner and exactly one running step; closed cases have a valid closure
  (evidence that exists and belongs to the case, or an authorised reason with rationale), were closed by a
  human, have no unresolved review flag; no negative-confirmatory closure for cervical/diabetes; at most one
  open case per patient and programme.
* Dates in causal order: steps after case creation and in order; step end after start; SLA due never before
  start; service requests and care plans after the case; appointments only after a referral, never before it.
* Safety: tier never lower than at creation without an audited clinician downgrade; no patient message before
  the clinician review of a review case; deceased patients never messaged; pregnancy always flagged; review
  cases always owned.
* Audit: every case has a creation event; the hash chain verifies end to end.

Result on the 20k cohort: **0 violations** (see ITERATIONS.md, Iteration 6).

# Wasl clinical decision rules (prototype)

> **Status: DRAFT for clinical-lead review. Not approved for clinical use.**
> These rules are implemented in `wasl/clinical_rules.py` (pure, deterministic, no ML) and tested by
> `tests/test_clinical_golden.py` (130 hand-written golden cases), `tests/test_properties.py` (Hypothesis) and
> `tests/test_failsafe.py`. Each rule has an ID that appears on the case (`Case.rule_id`), in the audit trail
> and in the case page ("Rule CRC-FIT-POS · pathway colonoscopy · target ...").
> Items marked **CONFIRM** are ambiguous or locally configurable. Where a guideline is ambiguous, the
> prototype uses the **safer (more urgent / more human review) option**. Sources were checked by web search
> on 20–21 Oct 2026 (IST); see "Verification notes" for anything that could not be read in full.

## 1. Principles

1. **Fail safe, never silent.** A result the engine cannot interpret with certainty is never dropped and never
   auto-downgraded. It becomes a **human-review case** (`review_required`) owned by the programme clinical lead,
   with machine-readable reasons, at a tier **no lower than the most urgent plausible interpretation**.
2. **Native units.** Thresholds are compared in the unit the lab reported. HbA1c in % (NGSP/DCCT) and mmol/mol
   (IFCC) have their own cut-offs; glucose in mg/dL and mmol/L likewise. A missing or unknown unit is evaluated
   under every plausible unit and the **most urgent** interpretation sets the review tier (e.g. HbA1c "16" with
   no unit: 16 % would be critical, so the review is critical).
3. **Tiers only go up automatically.** `max_tier()` combines rule tier and review floors. A database-level
   interlock (`TierDowngradeError`, `wasl/models.py`) blocks any lowering of `Case.tier` except inside
   `resolve_review()`, which needs a clinician, a written rationale (≥10 characters) and writes an audited
   `downgrade: true` event.
4. **The ML score never touches the tier.** It orders cases only *within* the same SLA status and tier
   (`wasl/services/queue.py: sort_key`).
5. **Nothing progresses before review.** A review case cannot be triaged by the system, cannot message the
   patient and cannot be closed with evidence until a clinician has resolved the review (or closed it with an
   authorised decision, e.g. deceased).

## 2. Urgency tiers

| Tier | Code | Meaning | End-to-end target used when the pathway has none of its own |
|---|---|---|---|
| critical | T0 | Same-day clinician action | 1 day (**CONFIRM**) |
| urgent | T1 | Suspected cancer / high-grade abnormality / unsafe data | 14 days (**CONFIRM**) |
| high | T2 | Positive screen needing diagnostic test | 31 days (**CONFIRM**) |
| routine | T3 | Surveillance, prevention, repeat sample | 90 days (**CONFIRM**) |

Per-step SLA clocks (hours per state and tier) are configuration in `wasl/sla.py` and are illustrative, not
guidance. Since Iteration 6 every case also carries an **end-to-end pathway target** (`Case.target_due_at`,
from the pathway table below). No step clock may run past it: a step's due time is `min(step SLA, pathway
target)`, so the escalation sweep fires before a guideline timeframe is missed.

## 3. Colorectal cancer screening - FIT

**Eligibility:** ages 40–75, FIT yearly (ADPHC colorectal programme page). Outside this range: human review
(`age_below_programme` / `age_above_programme`, routine floor) - the result is still actioned, never dropped.

| Rule ID | Input | Decision | Tier | Pathway | Target | Source |
|---|---|---|---|---|---|---|
| CRC-FIT-NEG | f-Hb < 80 µg/g | negative, annual FIT | – | routine_recall | – | threshold: **CONFIRM** (see below) |
| CRC-FIT-POS | 80 ≤ f-Hb < 400 µg/g | positive | high (T2) | colonoscopy | colonoscopy ≤ 31 days of referral | DoH Abu Dhabi CRC programme specification (2019) |
| CRC-FIT-URGENT | f-Hb ≥ 400 µg/g | positive, expedite | urgent (T1) | colonoscopy | ≤ 14 days (**CONFIRM**, local rule) | local rule; NHS BCSP: specialist appointment within 14 days |
| CRC-FIT-QUAL-POS / NEG | qualitative "positive" / "negative" | as above | high / – | colonoscopy / routine_recall | as above | |
| CRC-FIT-REPEAT | "invalid", "spoilt", "insufficient" | new kit | routine (T3) | repeat_fit | new kit ≤ 5 working days (**CONFIRM**) | DoH spec: repeat FIT if unclear/spoilt |
| CRC-ERR | missing value, non-numeric text, unit missing/unknown, ng/mL (assay conversion needed), value < 0 or > 10 000 | **review** | ≥ urgent | human_review | review within T1 SLA | fail-safe |

* **FIT threshold (CONFIRM).** No published DoH Abu Dhabi f-Hb threshold was found. UK practice: England
  120 µg/g (NICE CKS), Scotland and Wales 80 µg/g, England moving to 80 µg/g by March 2028 (UK NSC). The
  prototype uses **80 µg/g** - the more sensitive (safer) option - pending the programme's assay-specific
  threshold. ng/mL results are routed to review because conversion depends on the device buffer volume.
* Boundary rule: a value **equal** to the threshold is positive (`>=`), tested at 79.9 / 80 / 80.1 and
  399.9 / 400 / 400.1.

## 4. Cervical cancer screening - primary HPV with reflex cytology

**Eligibility:** women 25–65, HPV DNA primary test every 5 years (DoH cervical standard 2025). Sampling in
pregnancy is avoided unless following up a prior abnormality → `pregnant` review. Male patient → `sex_mismatch`
review (urgent floor) - this catches identity/demographic errors rather than discarding the result.

| Rule ID | HPV | Cytology | Decision | Tier | Pathway | Target | Source |
|---|---|---|---|---|---|---|---|
| CX-NEG | negative | none / NILM | negative, 5-yearly | – | routine_recall | – | DoH 2025 |
| CX-HPV1618 | 16 or 18 | any non-high-grade (incl. NILM) | colposcopy | high | colposcopy | ≤ 6 weeks | ASCCP 2019 (HPV16/18 → colposcopy even if NILM); NHS low-grade standard 6 weeks |
| CX-HIGH-GRADE | 16/18/other/unknown | ASC-H, HSIL, AGC, AIS | colposcopy | urgent | colposcopy_urgent | ≤ 2 weeks | ASCCP 2019; NHS high-grade standard 2 weeks (93 %) |
| CX-SUSP-INVASIVE | any | SCC / adenocarcinoma | urgent gynae-oncology | urgent | colposcopy_urgent | ≤ 2 weeks | NHS colposcopy standards |
| CX-HPVOTHER-REFLEX | other hr | not yet reported | reflex cytology on same sample | routine (confirm) | reflex_cytology | ≤ 2 weeks (**CONFIRM**) | ASCCP 2019 (reflex cytology for HPV+) |
| CX-HPVOTHER-LG | other hr | ASC-US, LSIL | colposcopy | high | colposcopy | ≤ 6 weeks | ASCCP 2019; NHS |
| CX-HPVOTHER-NILM | other hr | NILM | **repeat HPV in 12 months** (surveillance plan required; cannot close without it) | routine | surveillance_1y | plan documented, recall at 12 months | ASCCP 2019 |
| CX-UNSAT | other hr / invalid | unsatisfactory | repeat sample | routine | repeat_sample | as soon as convenient, ≤ 4 months | ASCCP 2019 + 2023 update |
| CX-HPVINV-LG | invalid | ASC-US / LSIL | **review** | high | human_review | – | ambiguous → review (**CONFIRM**) |
| CX-HPVNEG-HG | negative | high-grade | **review** | urgent | human_review | – | discordant → review |
| CX-HPVNEG-LG | negative | ASC-US / LSIL / unsatisfactory | **review** | high | human_review | – | ASCCP 2023: a negative HPV on an inadequate sample is not valid → review (**CONFIRM**) |
| CX-CYTO-LG-NOHPV | not done | ASC-US / LSIL | reflex HPV | routine (confirm) | reflex_hpv | ≤ 2 weeks (**CONFIRM**) | |
| CX-CYTO-ONLY | not done | NILM | **review** (cytology-only is not the DoH primary test) | urgent | human_review | – | **CONFIRM** |
| CX-ERR | contradictory (HPV negative or invalid together with another HPV result; two different cytology results), unknown text, numeric value | **review** | urgent | human_review | – | fail-safe |

**Co-reported HPV types** (e.g. "HPV16; other hrHPV" from a genotyping assay, or "HPV positive" plus "HPV other")
are managed by the most significant type: 16/18 before other (ASCCP 2019: HPV16/18 → colposcopy) - **CONFIRM**.
High-grade cytology with HPV invalid or not done is managed as high-grade (urgent colposcopy).

**Negative follow-up test (cervical and diabetes):** a negative reflex/repeat test does **not** close the case
by itself (state-machine v1.1): the clinician must record a surveillance care plan (repeat HPV at 12 months /
retest) - closing on "negative confirmatory result" is refused for these programmes.

## 5. Breast cancer screening - mammography (BI-RADS, ACR 5th edition)

**Eligibility:** women ≥ 40, every 2 years; excluded during pregnancy or breastfeeding (DoH breast standard
2025). No upper age limit is stated - **CONFIRM** (prototype: none; review only below 40).

| Rule ID | BI-RADS | Decision | Tier | Pathway | Target | Source |
|---|---|---|---|---|---|---|
| BR-1 / BR-2 | 1 negative / 2 benign | negative, 2-yearly | – | routine_recall | – | DoH 2025, ACR |
| BR-0 | 0 incomplete | recall for assessment | high (confirm) | assessment_imaging | assessment offered ≤ 5 working days | DoH 2025 |
| BR-4, BR-4A/B/C, BR-5 | 4 suspicious / 5 highly suggestive | triple assessment + core biopsy | urgent | biopsy | assessment ≤ 5 working days; biopsy result ≤ 5 working days; treatment referral ≤ 10 working days | DoH 2025 |
| BR-3 | 3 probably benign | **review** - a diagnostic, not a screening, category | high | human_review | – | ACR (screening uses 0/1/2); **CONFIRM** |
| BR-6 | 6 known malignancy | **review** - confirm under the breast cancer team | urgent | human_review | – | ACR |
| BR-ERR | missing, malformed, category > 6, sub-category on a non-4 | **review** | urgent | human_review | – | fail-safe |

## 6. Diabetes - HbA1c or fasting plasma glucose (FPG)

**Eligibility:** adults ≥ 18 in the prototype (DoH IFHAS screening packages cover adults); ADA 2026 recommends
screening all adults from age 35 and earlier with risk factors - **CONFIRM** which applies. ADA criteria apply
to non-pregnant adults → `pregnant` review. HbA1c may be unreliable with haemoglobin variants or altered red
cell turnover → `haemoglobinopathy` review (use glucose criteria).

### 6.1 Thresholds (cut-off value is INCLUDED in the higher category)

| Category | HbA1c % (NGSP) | HbA1c mmol/mol (IFCC) | FPG mg/dL | FPG mmol/L | Rule ID | Tier | Pathway | Source |
|---|---|---|---|---|---|---|---|---|
| Normal | < 5.7 | < 38.7 | 54 – 99 | 3.0 – 5.5 | DM-*-NEG | – | rescreen ≤ 3 years | ADA 2026 §2 |
| Prediabetes | 5.7 – 6.4 | 38.7 – 47.4 (39–47) | 100 – 125 | 5.6 – 6.9 | DM-A1C-PRE / DM-FPG-PRE | routine | prediabetes_gp | ADA 2026 §2 Table 2.5; case opened = **CONFIRM** (safer default) |
| Diabetes range | 6.5 – 9.9 | 47.5 – 85.6 (48–85) | 126 – 299 | 7.0 – 16.6 | DM-*-DIAB | routine (confirm) | confirm_then_gp | ADA 2026 §2 Table 2.1 (confirm unless unequivocal hyperglycaemia) |
| High | 10.0 – 14.9 | 85.7 – 139.8 (86–139) | 300 – 499 | 16.7 – 27.6 | DM-*-HIGH | high (confirm) | confirm_then_gp | ADA 2026 §9: consider insulin if A1C > 10 % or glucose ≥ 300 mg/dL; tier = **CONFIRM** |
| Critical | ≥ 15.0 | ≥ 139.9 (≥ 140) | ≥ 500 | ≥ 27.7 | DM-*-CRIT | critical | same_day_review | **local rule, CONFIRM** |
| Unexpectedly low | < 4.0 | < 20 | – | – | DM-A1C-LOW | review, routine | human_review | **CONFIRM** (variant / haemolysis) |
| Hypoglycaemic FPG | – | – | < 54 | < 3.0 | DM-FPG-HYPO | review, urgent | human_review | ADA level-2 hypoglycaemia threshold (54 mg/dL) |
| Implausible | < 3 or > 20 | < 9 or > 195 | < 10 or > 1500 | < 0.6 or > 83 | DM-ERR | review, urgent | human_review | fail-safe |

### 6.2 HbA1c unit conversion

NGSP % = 0.09148 × IFCC (mmol/mol) + 2.152, i.e. IFCC = (NGSP − 2.152) / 0.09148 (ngsp.org master equation).
Exact conversions: 5.7 % = 38.78, 6.5 % = 47.53, 10 % = 85.79, 15 % = 140.43 mmol/mol.
The mmol/mol cut-offs are the exact conversion **rounded down** to 0.1 (38.7 / 47.5 / 85.7), so integer lab
reports classify exactly like ADA's published pairs (39 / 48 / 86) and decimal reports never fall on the less
urgent side. The critical cut-off additionally tolerates integer rounding by the lab (15.0 % may be reported as
"140"), so it is 139.9. `test_hba1c_units_agree_on_category` checks every 0.1 % step from 3.0 to 20.0 %:
the same result in mmol/mol is never less urgent than in %. (This test found the original 140.4 cut-off.)

Glucose: mg/dL = mmol/L × 18.016 (display only; thresholds use the ADA-published pairs in each unit;
27.7 mmol/L = 499 mg/dL, i.e. slightly more inclusive than 500 - safer).

## 7. Result status, context and data-quality reasons (all programmes)

| Reason code | Trigger | Minimum tier | Notes |
|---|---|---|---|
| amended_result / corrected_result | HL7 OBX-11 = C/A, FHIR status amended/corrected | urgent | the earlier result may have been acted on |
| preliminary_result | OBX-11 = P/R/I | high | final report expected; linked to the same case |
| result_retracted | entered-in-error / cancelled | urgent | someone must check what was already done |
| missing_unit / unknown_unit / unit_needs_assay_conversion | | urgent | most urgent plausible unit sets the tier |
| missing_value / malformed_value / implausible_value / unknown_test / unknown_programme | | urgent | |
| sex_mismatch | cervical/breast result for a non-female record | urgent | identity / demographic error |
| age_below_programme / age_above_programme | outside eligibility | routine (clinical tier still applies) | opportunistic tests are actioned, not dropped |
| age_unknown | no birth year | high | |
| pregnant | pregnancy flag | high | |
| deceased | deceased flag | high | patient messaging blocked; closure only by authorised decision |
| under_specialist_care | patient already under the specialist team for this programme | high | avoid duplicate pathways; confirm with the team |
| haemoglobinopathy | HbA1c result with a variant flag | high | use glucose criteria |
| duplicate_result | same result again while a case is open | routine | linked; never a second case |
| conflicting_result | different result while a case is open (e.g. negative after positive) | high | never lowers the open case |
| more_urgent_result | new result more urgent than the open case | urgent | case escalated, step clock re-timed earlier |
| result_after_closure | actionable result within 180 days of a closed case (same programme) | high | new case, linked history |
| possible_duplicate_identity | another patient record has the same Emirates ID (one person, two MRNs); open cases are checked across both records | high | review case or linked to the other record's open case |
| multiple_results | one message carries several different values for the same test (e.g. left/right mammogram, two FIT samples, HbA1c + FPG); the MOST severe value is evaluated and all values are kept on the report | high | review case at no lower than the clinical tier |
| control_id_reused | an HL7 control ID arrives again with DIFFERENT content (possibly a correction) | high | processed as a new review case; a byte-identical replay stays idempotent |
| screening_category_unexpected / known_malignancy / hpv_negative_abnormal_cytology / hypoglycaemia / unexpectedly_low | see programme tables | see tables | |

Unknown LOINC codes, messages mixing programmes and messages without a PID are kept in the ingest **review
queue** (`IngestMessage.status = needs_review`, HL7 ACK `AE`); messages without MSH-10 get a content-hash ID
(`NOID-…`) so they are still idempotent. Unmatched patients go to the existing **match-review** queue.

## 8. Owners

* Actionable result → programme **coordinator** (as before); clinician-confirmed tiers (critical, urgent)
  still need a clinician to triage.
* Review result → programme **clinical lead** (falls back to the programme team pool if no active lead,
  so the result is never rejected).
* Patient questions go to the patient's GP if active, else the case owner if a clinician, else the clinical lead.

## 9. Items for the clinical lead to confirm

1. FIT positivity threshold 80 µg/g (assay-specific; England uses 120).
2. FIT ≥ 400 µg/g → urgent tier with a 14-day target (local rule).
3. HbA1c ≥ 15 % / ≥ 140 mmol/mol and FPG ≥ 500 mg/dL / ≥ 27.7 mmol/L → critical, same-day contact (local rule).
4. HbA1c ≥ 10 % / FPG ≥ 300 mg/dL → high tier, GP within 2 weeks.
5. Prediabetes opens a routine case (GP advice + 12-month retest) rather than a letter only. This is the safer
   default and roughly half of all cases in the realistic 20k cohort - a workload decision.
6. HbA1c < 4.0 % / < 20 mmol/mol → review.
7. Diabetes screening age (18+ prototype vs ADA 35+).
8. Breast screening upper age limit (none stated by DoH).
9. BI-RADS 3 on a screening report → review (not negative).
10. Cytology-only cervical screening (no HPV) → review.
11. HPV-negative with low-grade or unsatisfactory cytology → review; HPV invalid with low-grade cytology → review.
12. Targets marked CONFIRM: reflex test 2 weeks, new FIT kit 5 working days, GP review 2 weeks (high) and
    4 weeks (diabetes confirmation), prediabetes 3 months, tier defaults in section 2.
13. Review floors (section 7), e.g. data-quality problems at T1.
14. A negative follow-up test for cervical/diabetes requires a surveillance plan before closure.
15. "Result after closure" window of 180 days.
16. Patient age is computed from birth year only (±1 year at eligibility boundaries).
17. Co-reported HPV types are managed by the most significant type (16/18 > other) rather than sent to review.

## 10. Sources

| # | Source | Used for | Verified how |
|---|---|---|---|
| S1 | American Diabetes Association. *2. Diagnosis and Classification of Diabetes: Standards of Care in Diabetes—2026.* Diabetes Care 49(Suppl 1):S27. https://diabetesjournals.org/care/article/49/Supplement_1/S27/163926 (PMC12690183) | diabetes and prediabetes criteria, confirmation, non-pregnant scope, screening from 35, retest intervals | read via web search/fetch |
| S2 | ADA. *9. Pharmacologic Approaches to Glycemic Treatment: Standards of Care—2026.* https://diabetesjournals.org/care/article/49/Supplement_1/S183/163934 (PMC12690185) | consider insulin if A1C > 10 % or glucose ≥ 300 mg/dL or symptomatic | search summary of the section |
| S3 | NGSP. *Harmonizing HbA1c results: IFCC–NGSP master equation.* https://ngsp.org/ifccngsp.asp | % ↔ mmol/mol conversion | read |
| S4 | Department of Health Abu Dhabi. *Standard for Breast Cancer Screening* DoH/ST/BCSD/ADPHC/V2/2025. https://www.doh.gov.ae/-/media/Feature/Resources/Standards/2025/10072025_-Standard-for-Breast-Cancer-Screening--for-Publication.ashx | eligibility, BI-RADS reporting, 5-working-day assessment and biopsy targets, recall targets | fetched in full |
| S5 | ADPHC. *Breast cancer programme.* https://www.adphc.gov.ae/en/Public-Health-Programs/Cancers-Program/Breast-Cancer | programme context | read |
| S6 | ADPHC. *Colorectal cancer programme.* https://www.adphc.gov.ae/en/Public-Health-Programs/Cancers-Program/Colorectal-cancer | ages 40–75, FIT yearly, colonoscopy every 10 years | read |
| S7 | DoH Abu Dhabi. *Colorectal Cancer Screening Program Specifications* (2019). https://www.doh.gov.ae/-/media/980F1D8087214B6D9E630B457B85E4B9.ashx | colonoscopy ≤ 31 days of referral; results ≤ 15 working days; repeat FIT if spoilt | **search-index excerpt only** (doh.gov.ae not reachable from the build machine for this file) |
| S8 | DoH Abu Dhabi. *Standard for Cervical Cancer Screening* (2025). https://www.doh.gov.ae/-/media/Feature/Resources/Standards/2025/for-publication_-Standard-Cervical-Cancer-Screening-Standard.ashx | HPV primary 5-yearly, 25–65, avoid sampling in pregnancy | read via search/fetch |
| S9 | Perkins RB et al. *2019 ASCCP Risk-Based Management Consensus Guidelines.* J Low Genit Tract Dis 2020;24:102–131. doi:10.1097/LGT.0000000000000525. https://asccp.org/wp-content/uploads/2025/09/2019_ASCCP_Risk_Based_Management_Consensus.3.pdf | HPV16/18 → colposcopy; reflex cytology; other hrHPV + NILM → 1 year; high-grade management | read |
| S10 | *2019 ASCCP Risk-Based Management Consensus Guidelines: Updates Through 2023.* https://pmc.ncbi.nlm.nih.gov/articles/PMC10755815/ ; ASCCP practice pearl "Unsatisfactory cytology" https://www.asccp.org/clinical-practice/practice-pearls/unsatisfactory-cytology | unsatisfactory cytology: repeat as soon as convenient, ≤ 4 months; HPV-negative result on an inadequate sample not valid | search summary |
| S11 | NHS England. *Cervical screening: programme and colposcopy management - 2. Providing a quality colposcopy clinic.* https://www.gov.uk/government/publications/cervical-screening-programme-and-colposcopy-management/2-providing-a-quality-colposcopy-clinic | high-grade ≤ 2 weeks, low-grade ≤ 6 weeks | read |
| S12 | NICE CKS. *Bowel screening - the NHS bowel screening programme.* https://cks.nice.org.uk/topics/bowel-screening/background-information/the-nhs-bowel-screening-programme/ | England FIT threshold 120 µg/g | read |
| S13 | UK National Screening Committee blog, 26 Jan 2026. https://nationalscreening.blog.gov.uk/2026/01/26/more-sensitive-bowel-cancer-screening-test-implemented-in-line-with-uk-nsc-recommendation/ | Scotland/Wales 80 µg/g; England to 80 µg/g by March 2028 | read |
| S14 | UKHSA. *Bowel cancer screening: standards data report 2024 to 2025.* https://www.gov.uk/government/statistics/bowel-cancer-screening-annual-report-2024-to-2025/bowel-cancer-screening-standards-data-report-2024-25 | specialist appointment within 14 days; positivity 1.71 % at 120 | read |
| S15 | ACR. *BI-RADS Mammography reporting* (5th ed.). https://edge.sitecorecloud.io/americancoldf5f-acrorgf92a-productioncb02-3650/media/ACR/Files/RADS/BI-RADS/Mammography-Reporting.pdf | screening categories 0/1/2; 3–6 diagnostic | read |

## 11. Verification notes (honest limits)

* The DoH colorectal specification (S7) could not be opened from the build machine (`doh.gov.ae` timed out);
  the 31-day colonoscopy target is taken from the search engine's excerpt of that PDF.
* ADA Section 9 (S2) was confirmed from search summaries of the published section, not a full read.
* No DoH Abu Dhabi f-Hb threshold, diabetes critical-value rule or cervical colposcopy timeframe was found;
  those values come from UK/US sources or are local rules and are marked CONFIRM.
* These rules have **not** been validated on real patient data and have **not** been signed off by a
  clinician. See `docs/clinical_safety_case.md`.

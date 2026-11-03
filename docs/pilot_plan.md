# 90-day pilot plan: one Abu Dhabi primary care cluster

**Status:** proposal. No pilot partner has agreed yet, and no organisation named here has endorsed KareShafaa.
Facts were checked on 3 Nov 2026 (links below); figures marked **ASSUMPTION** are planning estimates to agree with
the partner.

## Why this pilot

* In Abu Dhabi's colorectal screening programme (2015–16), only **30.5 % of people with a positive FIT had a
  colonoscopy** (BMC Cancer 2021, https://link.springer.com/article/10.1186/s12885-021-08623-9).
  The gap KareShafaa closes is the step after the abnormal result.
* Delay matters: colonoscopy more than 12 months after a positive FIT was linked to higher odds of colorectal cancer
  (OR 2.25) and of advanced-stage disease (OR 3.22) compared with 8–30 days (Corley et al., JAMA 2017,
  https://jamanetwork.com/journals/jama/fullarticle/2620087).
* Screening volume is growing fast: more than 34,000 women had breast screening through the Abu Dhabi Public Health
  Centre's programme in 2024 (https://www.wam.ae/en/article/bm03fob-abu-dhabi-public-health-centre-launches-breast),
  and Abu Dhabi reported a rise of over 280 % in cancer screenings in 2025
  (https://en.aletihad.ae/news/uae/4696340/abu-dhabi-records-over-280--increase-in-cancer-screenings--s).
  More results mean more follow-ups to track.

## Scope

| Item | Pilot choice |
|---|---|
| Setting | One primary care cluster in Abu Dhabi, for example a group of SEHA CLINICS family medicine sites (SEHA CLINICS, formerly Ambulatory Healthcare Services, part of PureHealth: https://purehealth.ae/purehealth-rebrands-ambulatory-healthcare-services-to-seha-clinics/) plus its referral endoscopy and breast imaging units |
| Programmes | Bowel (FIT) first, with the eligibility and pathway in the DoH colorectal programme specification that the rules already cite (`docs/clinical_rules.md`); cervical and breast from day 45 if the go/no-go gate is passed |
| Patients | Adults with an abnormal screening result at the pilot sites (eligibility as in the programme specification; to confirm with the cluster) |
| Volume | **ASSUMPTION:** 300–600 abnormal results in 90 days; confirm from the cluster's last-year data in week 1 |
| Mode | Runs alongside current practice. Staff still follow existing referral routes; KareShafaa adds ownership, deadlines, patient messages and evidence of closure |
| Hosting | UAE cloud region or the cluster's data centre (see `docs/deployment_uae.md`); the free public hosting is never used for real data |

## Timeline

| Weeks | Work | Exit check |
|---|---|---|
| −8 to 0 (before day 1) | Data sharing agreement, DoH research/QI approval route, ADHICS security assessment, clinical sign-off of rules, deadlines and messages, UAE hosting, HL7 feed from the laboratory | Clinical safety case signed; security assessment passed; test messages flowing |
| 1–2 | Train coordinators, family doctors and specialists (role guides in the product); baseline extract of last 12 months | Baseline loop-closure and time-to-colonoscopy measured |
| 3–6 | Bowel programme live; weekly safety huddle reviews every critical/urgent case and every human override | No unresolved safety incident; staff use KareShafaa for at least 90 % of new abnormal results (**ASSUMPTION**: target to agree) |
| 7 (day 45) | **Go/no-go gate** with the clinical lead | Criteria below on track → add cervical and breast |
| 7–12 | All programmes; equity review by language, nationality group and region | Equity gaps above 10 points (CONFIRM value) investigated |
| 13 | Final report to the steering group | Success criteria below |

## KPIs (all in Outcomes → Loop impact, exportable as CSV/PDF)

| KPI | Definition (as built) | Baseline | Pilot target |
|---|---|---|---|
| Loop closure rate | mature cases (≥ 90 days old) closed with an attended specialist visit or a signed decision | from baseline extract | **ASSUMPTION:** +20 points over baseline |
| Time to first specialist contact, by tier | days from result to first attended visit, median and 90th centile, share within target | from baseline extract | **ASSUMPTION:** at least 80 % within target for critical/urgent |
| Lost to follow-up | open, past target by more than 30 days, no step change in 30 days | from baseline extract | **ASSUMPTION:** halved |
| Recall uptake | recalls completed / recalls due | from baseline extract | report only |
| Equity gap | largest difference in loop closure between groups (language, nationality group, region) | measured in weeks 1–2 | no group more than 10 points below the overall rate, or a documented reason (10 points is the product's flag level, a CONFIRM value) |
| Safety | missed critical results, wrong-patient matches, messages sent in the wrong language | 0 tolerated | 0 |
| Staff time | minutes per case for coordinators (time-and-motion, 2 sample weeks) | week 2 | **ASSUMPTION:** no increase |

## Success criteria at day 90

1. No serious safety incident attributable to KareShafaa.
2. Loop closure and within-target rates better than baseline for bowel screening, with the 90 % confidence interval
   reported (small numbers are expected; the report says so).
3. No equity group more than 10 points below the overall rate without an explanation and an action.
4. Coordinators and clinicians would keep using it (short survey, ≥ 70 % agree, **ASSUMPTION**).
5. Security assessment passed and audit chain intact for the whole pilot.

## Stakeholders

| Who | Role in the pilot |
|---|---|
| Department of Health – Abu Dhabi (DoH) | Regulator: data standards, ADHICS, approval route for the pilot |
| Abu Dhabi Public Health Centre (ADPHC) | Owner of the screening programmes and their guidelines |
| SEHA / PureHealth (SEHA CLINICS) | Candidate provider cluster: coordinators, family doctors, endoscopy and imaging |
| Daman (administers Thiqa: https://www.thiqa.ae/about-thiqa-programme/) | Payer view on avoided late-stage cost and on pricing per screened member |
| Abu Dhabi Health Data Services (Malaffi) | Later phase: connection to the health information exchange (onboarding requirements at https://www.malaffi.ae/providers/facilities-required-to-connect/) |
| Patients and community representatives | Review of messages in Arabic, English, Hindi and Urdu; feedback on contact preferences |

## Risks

| Risk | Mitigation |
|---|---|
| Laboratory feed delayed | Manual result entry by coordinators (already in the product) for the first weeks |
| Low numbers in 90 days | Report confidence intervals; extend to 6 months if the gate is passed |
| Staff workload | Coordinators only; doctors see review items only; weekly check of time per case |
| Data protection | UAE hosting, ADHICS assessment, role separation, audit chain, retention per Federal Law No. 2 of 2019 |

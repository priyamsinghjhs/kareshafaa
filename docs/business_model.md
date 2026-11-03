# Business model

**Status:** proposal for discussion with a pilot partner. Every number is either from a cited public source (checked
3 Nov 2026) or marked **ASSUMPTION**. No price has been quoted to, or agreed with, any organisation.

## Who pays and why

| Buyer | What they get | Why it matters to them |
|---|---|---|
| Provider cluster (e.g. a primary care network) | One accountable owner and deadline for every abnormal result; evidence of closure; audit | Fewer missed follow-ups, simpler quality reporting, lower medico-legal risk |
| Screening programme owner (public health) | Loop closure, time to specialist and equity by group across sites | Programme results beyond "number screened" |
| Payer (e.g. the Thiqa programme administered by Daman) | Earlier diagnosis in its members | Late-stage cancer costs more to treat (below) |

## Pricing options (ASSUMPTION — for discussion)

| Option | Unit | Illustrative price | Fits |
|---|---|---|---|
| A. Per screened patient | each person with a screening result loaded | **ASSUMPTION:** AED 2–5 per screened patient per year | Programme owners and payers; scales with volume |
| B. Per abnormal result tracked | each case opened | **ASSUMPTION:** AED 40–80 per case | Smaller clusters; pays only for the work done |
| C. Per facility | each clinic site, unlimited users | **ASSUMPTION:** AED 2,000–4,000 per site per month | Provider networks wanting a fixed budget |

The pilot (90 days, one cluster) is proposed at **no licence fee**, with the partner providing staff time and UAE
hosting, in exchange for an outcome report and a reference if the success criteria are met.

## Cost to run

| Set-up | What it costs | Source |
|---|---|---|
| Current public hosting (synthetic records) | AED 0: Render free web service + Neon free PostgreSQL | `render.yaml`, `DEPLOY.md` |
| Pilot, one cluster, UAE cloud region | **ASSUMPTION:** 2 small containers + managed PostgreSQL + backups + logs; to be quoted from the provider's UAE-region price list | AWS me-central-1 / Azure UAE North (see `docs/deployment_uae.md`) |
| SMS and WhatsApp messages | **ASSUMPTION:** per-message price from the partner's existing messaging contract | — |
| People | **ASSUMPTION:** 0.2 FTE support engineer and 0.1 FTE clinical safety officer per cluster | — |

Measured capacity on one 8-CPU test machine is in `docs/loadtest_locust.md`; it is not a production sizing.

## Return on investment: how to calculate it

KareShafaa's value comes from moving people from late to early diagnosis by shortening the time from an abnormal
result to the specialist. Published inputs:

| Input | Value | Source |
|---|---|---|
| Follow-up gap today (Abu Dhabi bowel screening, 2015–16) | 30.5 % of FIT-positive people had a colonoscopy | BMC Cancer 2021, https://link.springer.com/article/10.1186/s12885-021-08623-9 |
| Effect of delay | colonoscopy > 12 months after positive FIT: OR 2.25 for CRC and 3.22 for advanced stage (vs 8–30 days) | JAMA 2017, https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| Cost by stage, colorectal (Spain) | €10,442 stage I vs €19,423 stage IV | https://www.sciencedirect.com/science/article/pii/S0213911121000091 |
| Cost by stage, all cancers (England) | early £11.2K vs late £23.8K | https://link.springer.com/article/10.1186/s12962-025-00657-1 |
| Cost by stage, breast (US) | $82,121 stage I/II vs $134,682 stage IV | https://pmc.ncbi.nlm.nih.gov/articles/PMC4822976/ |
| Cervical late-stage share, Abu Dhabi | fell from 30.3 % (2012) to 14.8 % (2015) after programme work | https://www.doh.gov.ae/en/news/late-stage-diagnosis-of-cervical-cancer |

No UAE cost-by-stage figure was found in public sources; the pilot should use the partner's own claims data.

Formula (per year, per cluster):

```
avoided cost = abnormal results × extra share completing follow-up × cancers found per completed follow-up
               × share shifted from late to early stage × (late-stage cost − early-stage cost)
ROI          = (avoided cost − KareShafaa cost) / KareShafaa cost
```

Worked example — **every input below is an ASSUMPTION except the cost difference**, which uses the Spanish
colorectal figures (€19,423 − €10,442 = €8,981):

| Input | Value |
|---|---|
| Positive FIT results per year | ASSUMPTION: 1,000 |
| Extra share completing colonoscopy thanks to tracking | ASSUMPTION: 20 points |
| Cancers found per completed colonoscopy after positive FIT | ASSUMPTION: 4 % (to replace with local data) |
| Share of those cancers moved from late to early stage | ASSUMPTION: 25 % |
| Cost difference late vs early | €8,981 (sourced) |

1,000 × 0.20 × 0.04 × 0.25 × €8,981 ≈ **€17,962 avoided treatment cost per year** for bowel screening alone in this
example, before counting survival, productivity or the other programmes. The pilot replaces each assumption with
measured data.

## Path to scale

1. Pilot in one Abu Dhabi cluster (`docs/pilot_plan.md`).
2. All programme sites in the cluster; connection to Malaffi after the DoH security assessment.
3. Other Abu Dhabi clusters; then other emirates through NABIDH (Dubai) and Riayati (Northern Emirates), which have
   been integrated with Malaffi since January 2023
   (https://mohap.gov.ae/en/w/uae-health-authorities-announce-successful-integration-between-riayati-malaffi-and-nabidh-).

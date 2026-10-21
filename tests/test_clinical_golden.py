"""Golden clinical cases: hand-written expected decisions derived from docs/clinical_rules.md.

Each row: (id, programme, input, patient context, expected disposition, tier, pathway, rule, owner).
Boundary rows test every threshold exactly AT, just BELOW and just ABOVE. Owner: 'lead' = programme clinical
lead (human review), 'coord' = programme coordinator, None = no case (negative result, report only).
If a row fails, do NOT edit the expectation to make it pass: the rule or the doc is wrong - ask the clinical lead.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wasl.clinical_rules import PatientContext, ResultInput, evaluate, hba1c_mmol_to_pct, hba1c_pct_to_mmol

F50 = dict(age=50, sex="female")
M50 = dict(age=50, sex="male")
CRC, CX, BR, DM = "colorectal_fit", "cervical", "breast", "diabetes_hba1c"

# id, programme, value, value_text, unit, test, status, cytology, ctx, disposition, tier, pathway, rule, owner
G = [
    # ---------------- FIT (threshold 80 ug/g positive; >=400 urgent local rule) -----------------------------
    ("CRC-01", CRC, 79.9, None, "ug/g", None, "final", None, M50, "negative", None, "routine_recall", "CRC-FIT-NEG", None),
    ("CRC-02", CRC, 80, None, "ug/g", None, "final", None, M50, "actionable", "high", "colonoscopy", "CRC-FIT-POS", "coord"),
    ("CRC-03", CRC, 80.1, None, "ug/g", None, "final", None, M50, "actionable", "high", "colonoscopy", "CRC-FIT-POS", "coord"),
    ("CRC-04", CRC, 399.9, None, "ug/g", None, "final", None, M50, "actionable", "high", "colonoscopy", "CRC-FIT-POS", "coord"),
    ("CRC-05", CRC, 400, None, "ug/g", None, "final", None, M50, "actionable", "urgent", "colonoscopy", "CRC-FIT-URGENT", "coord"),
    ("CRC-06", CRC, 400.1, None, "ug/g", None, "final", None, M50, "actionable", "urgent", "colonoscopy", "CRC-FIT-URGENT", "coord"),
    ("CRC-07", CRC, 0, None, "ug/g", None, "final", None, M50, "negative", None, "routine_recall", "CRC-FIT-NEG", None),
    ("CRC-08", CRC, 250, None, "µg/g", None, "final", None, M50, "actionable", "high", "colonoscopy", "CRC-FIT-POS", "coord"),
    ("CRC-09", CRC, 250, None, None, None, "final", None, M50, "review", "urgent", "human_review", "CRC-ERR", "lead"),
    ("CRC-10", CRC, 250, None, "ng/mL", None, "final", None, M50, "review", "urgent", "human_review", "CRC-ERR", "lead"),
    ("CRC-11", CRC, 250, None, "mg/L", None, "final", None, M50, "review", "urgent", "human_review", "CRC-ERR", "lead"),
    ("CRC-12", CRC, -5, None, "ug/g", None, "final", None, M50, "review", "urgent", "human_review", "CRC-ERR", "lead"),
    ("CRC-13", CRC, 10001, None, "ug/g", None, "final", None, M50, "review", "urgent", "human_review", "CRC-ERR", "lead"),
    ("CRC-14", CRC, None, "positive", None, None, "final", None, M50, "actionable", "high", "colonoscopy", "CRC-FIT-QUAL-POS", "coord"),
    ("CRC-15", CRC, None, "negative", None, None, "final", None, M50, "negative", None, "routine_recall", "CRC-FIT-QUAL-NEG", None),
    ("CRC-16", CRC, None, "spoilt", None, None, "final", None, M50, "actionable", "routine", "repeat_fit", "CRC-FIT-REPEAT", "coord"),
    ("CRC-17", CRC, None, "see comment", None, None, "final", None, M50, "review", "urgent", "human_review", "CRC-ERR", "lead"),
    ("CRC-18", CRC, None, None, None, None, "final", None, M50, "review", "urgent", "human_review", "CRC-ERR", "lead"),
    ("CRC-19", CRC, 120, None, "ug/g", None, "final", None, dict(age=39, sex="male"), "review", "high", "colonoscopy", "CRC-FIT-POS", "lead"),
    ("CRC-20", CRC, 120, None, "ug/g", None, "final", None, dict(age=40, sex="male"), "actionable", "high", "colonoscopy", "CRC-FIT-POS", "coord"),
    ("CRC-21", CRC, 120, None, "ug/g", None, "final", None, dict(age=75, sex="male"), "actionable", "high", "colonoscopy", "CRC-FIT-POS", "coord"),
    ("CRC-22", CRC, 120, None, "ug/g", None, "final", None, dict(age=76, sex="male"), "review", "high", "colonoscopy", "CRC-FIT-POS", "lead"),
    ("CRC-23", CRC, 20, None, "ug/g", None, "final", None, dict(age=76, sex="male"), "review", "routine", "human_review", "CRC-FIT-NEG", "lead"),
    ("CRC-24", CRC, 120, None, "ug/g", None, "amended", None, M50, "review", "urgent", "colonoscopy", "CRC-FIT-POS", "lead"),
    ("CRC-25", CRC, 120, None, "ug/g", None, "preliminary", None, M50, "review", "high", "colonoscopy", "CRC-FIT-POS", "lead"),
    ("CRC-26", CRC, 500, None, "ug/g", None, "final", None, dict(M50, under_specialist_care=True), "review", "urgent", "colonoscopy", "CRC-FIT-URGENT", "lead"),
    ("CRC-27", CRC, 120, None, "ug/g", None, "final", None, dict(age=None, sex="male"), "review", "high", "colonoscopy", "CRC-FIT-POS", "lead"),
    ("CRC-28", CRC, 120, None, "ug/g", "HBA1C", "final", None, M50, "review", "urgent", "human_review", "GEN-ERR", "lead"),
    # ---------------- Cervical (primary HPV + reflex cytology; ASCCP 2019, NHS colposcopy standards) ----------
    ("CX-01", CX, None, "HPV negative", None, None, "final", None, F50, "negative", None, "routine_recall", "CX-NEG", None),
    ("CX-02", CX, None, "HPV negative", None, None, "final", "NILM", F50, "negative", None, "routine_recall", "CX-NEG", None),
    ("CX-03", CX, None, "HPV16", None, None, "final", None, F50, "actionable", "high", "colposcopy", "CX-HPV1618", "coord"),
    ("CX-04", CX, None, "HPV18", None, None, "final", "NILM", F50, "actionable", "high", "colposcopy", "CX-HPV1618", "coord"),
    ("CX-05", CX, None, "HPV16", None, None, "final", "HSIL", F50, "actionable", "urgent", "colposcopy_urgent", "CX-HIGH-GRADE", "coord"),
    ("CX-06", CX, None, "HPV other hr", None, None, "final", None, F50, "actionable", "routine", "reflex_cytology", "CX-HPVOTHER-REFLEX", "coord"),
    ("CX-07", CX, None, "HPV other hr", None, None, "final", "NILM", F50, "actionable", "routine", "surveillance_1y", "CX-HPVOTHER-NILM", "coord"),
    ("CX-08", CX, None, "HPV other hr", None, None, "final", "ASC-US", F50, "actionable", "high", "colposcopy", "CX-HPVOTHER-LG", "coord"),
    ("CX-09", CX, None, "HPV other hr", None, None, "final", "LSIL", F50, "actionable", "high", "colposcopy", "CX-HPVOTHER-LG", "coord"),
    ("CX-10", CX, None, "HPV other hr", None, None, "final", "ASC-H", F50, "actionable", "urgent", "colposcopy_urgent", "CX-HIGH-GRADE", "coord"),
    ("CX-11", CX, None, "HPV other hr", None, None, "final", "AGC", F50, "actionable", "urgent", "colposcopy_urgent", "CX-HIGH-GRADE", "coord"),
    ("CX-12", CX, None, "HPV other hr", None, None, "final", "SCC", F50, "actionable", "urgent", "colposcopy_urgent", "CX-SUSP-INVASIVE", "coord"),
    ("CX-13", CX, None, "HPV other hr", None, None, "final", "unsatisfactory", F50, "actionable", "routine", "repeat_sample", "CX-UNSAT", "coord"),
    ("CX-14", CX, None, "HPV negative", None, None, "final", "HSIL", F50, "review", "urgent", "human_review", "CX-HPVNEG-HG", "lead"),
    ("CX-15", CX, None, "HPV negative", None, None, "final", "ASC-US", F50, "review", "high", "human_review", "CX-HPVNEG-LG", "lead"),
    ("CX-16", CX, None, "HPV invalid", None, None, "final", None, F50, "actionable", "routine", "repeat_sample", "CX-UNSAT", "coord"),
    ("CX-17", CX, None, "HPV invalid", None, None, "final", "LSIL", F50, "review", "high", "human_review", "CX-HPVINV-LG", "lead"),
    ("CX-18", CX, None, "LSIL", None, None, "final", None, F50, "actionable", "routine", "reflex_hpv", "CX-CYTO-LG-NOHPV", "coord"),
    ("CX-19", CX, None, "NILM", None, None, "final", None, F50, "review", "urgent", "human_review", "CX-CYTO-ONLY", "lead"),
    ("CX-20", CX, None, "HPV negative|HPV16", None, None, "final", None, F50, "review", "urgent", "human_review", "CX-ERR", "lead"),
    ("CX-40", CX, None, "HPV16|HPV other hr", None, None, "final", None, F50, "actionable", "high", "colposcopy", "CX-HPV1618", "coord"),
    ("CX-41", CX, None, "HPV positive|HPV other", None, None, "final", None, F50, "actionable", "routine", "reflex_cytology", "CX-HPVOTHER-REFLEX", "coord"),
    ("CX-42", CX, None, "HPV invalid|HPV18", None, None, "final", None, F50, "review", "urgent", "human_review", "CX-ERR", "lead"),
    ("CX-21", CX, None, "HPV maybe", None, None, "final", None, F50, "review", "urgent", "human_review", "CX-ERR", "lead"),
    ("CX-22", CX, 1.0, None, None, None, "final", None, F50, "review", "urgent", "human_review", "CX-ERR", "lead"),
    ("CX-23", CX, None, "HPV16", None, None, "final", None, dict(age=50, sex="male"), "review", "urgent", "colposcopy", "CX-HPV1618", "lead"),
    ("CX-24", CX, None, "HPV16", None, None, "final", None, dict(age=24, sex="female"), "review", "high", "colposcopy", "CX-HPV1618", "lead"),
    ("CX-25", CX, None, "HPV16", None, None, "final", None, dict(age=25, sex="female"), "actionable", "high", "colposcopy", "CX-HPV1618", "coord"),
    ("CX-26", CX, None, "HPV16", None, None, "final", None, dict(age=65, sex="female"), "actionable", "high", "colposcopy", "CX-HPV1618", "coord"),
    ("CX-27", CX, None, "HPV16", None, None, "final", None, dict(age=66, sex="female"), "review", "high", "colposcopy", "CX-HPV1618", "lead"),
    ("CX-28", CX, None, "HPV other hr", None, None, "final", "NILM", dict(F50, pregnant=True), "review", "high", "surveillance_1y", "CX-HPVOTHER-NILM", "lead"),
    ("CX-29", CX, None, "HPV negative", None, None, "final", None, dict(F50, deceased=True), "review", "high", "human_review", "CX-NEG", "lead"),
    # ---------------- Breast (BI-RADS; DoH 2025 standard, ACR 5th ed.) --------------------------------------
    ("BR-01", BR, None, "BI-RADS 1", None, None, "final", None, F50, "negative", None, "routine_recall", "BR-1", None),
    ("BR-02", BR, None, "BI-RADS 2", None, None, "final", None, F50, "negative", None, "routine_recall", "BR-2", None),
    ("BR-03", BR, None, "BI-RADS 0", None, None, "final", None, F50, "actionable", "high", "assessment_imaging", "BR-0", "coord"),
    ("BR-04", BR, None, "BI-RADS 3", None, None, "final", None, F50, "review", "high", "human_review", "BR-3", "lead"),
    ("BR-05", BR, None, "BI-RADS 4", None, None, "final", None, F50, "actionable", "urgent", "biopsy", "BR-4", "coord"),
    ("BR-06", BR, None, "BI-RADS 4C", None, None, "final", None, F50, "actionable", "urgent", "biopsy", "BR-4C", "coord"),
    ("BR-07", BR, None, "BI-RADS 5", None, None, "final", None, F50, "actionable", "urgent", "biopsy", "BR-5", "coord"),
    ("BR-08", BR, None, "BI-RADS 6", None, None, "final", None, F50, "review", "urgent", "human_review", "BR-6", "lead"),
    ("BR-09", BR, None, "BI-RADS 7", None, None, "final", None, F50, "review", "urgent", "human_review", "BR-ERR", "lead"),
    ("BR-10", BR, None, "BI-RADS 2A", None, None, "final", None, F50, "review", "urgent", "human_review", "BR-ERR", "lead"),
    ("BR-11", BR, 4, None, None, None, "final", None, F50, "actionable", "urgent", "biopsy", "BR-4", "coord"),
    ("BR-12", BR, None, None, None, None, "final", None, F50, "review", "urgent", "human_review", "BR-ERR", "lead"),
    ("BR-13", BR, None, "BI-RADS 1", None, None, "final", None, dict(age=39, sex="female"), "review", "routine", "human_review", "BR-1", "lead"),
    ("BR-14", BR, None, "BI-RADS 1", None, None, "final", None, dict(age=40, sex="female"), "negative", None, "routine_recall", "BR-1", None),
    ("BR-15", BR, None, "BI-RADS 4", None, None, "final", None, dict(age=50, sex="male"), "review", "urgent", "biopsy", "BR-4", "lead"),
    ("BR-16", BR, None, "BI-RADS 0", None, None, "final", None, dict(F50, pregnant=True), "review", "high", "assessment_imaging", "BR-0", "lead"),
    ("BR-17", BR, None, "BI-RADS 4", None, None, "cancelled", None, F50, "review", "urgent", "biopsy", "BR-4", "lead"),
    # ---------------- Diabetes HbA1c % (ADA 2026: 5.7 / 6.5; local 10 / 15) ---------------------------------
    ("DM-01", DM, 5.6, None, "%", None, "final", None, M50, "negative", None, "routine_recall", "DM-A1C-NEG", None),
    ("DM-02", DM, 5.7, None, "%", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-A1C-PRE", "coord"),
    ("DM-03", DM, 5.8, None, "%", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-A1C-PRE", "coord"),
    ("DM-04", DM, 6.4, None, "%", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-A1C-PRE", "coord"),
    ("DM-05", DM, 6.5, None, "%", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-A1C-DIAB", "coord"),
    ("DM-06", DM, 6.6, None, "%", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-A1C-DIAB", "coord"),
    ("DM-07", DM, 9.9, None, "%", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-A1C-DIAB", "coord"),
    ("DM-08", DM, 10.0, None, "%", None, "final", None, M50, "actionable", "high", "confirm_then_gp", "DM-A1C-HIGH", "coord"),
    ("DM-09", DM, 10.1, None, "%", None, "final", None, M50, "actionable", "high", "confirm_then_gp", "DM-A1C-HIGH", "coord"),
    ("DM-10", DM, 14.9, None, "%", None, "final", None, M50, "actionable", "high", "confirm_then_gp", "DM-A1C-HIGH", "coord"),
    ("DM-11", DM, 15.0, None, "%", None, "final", None, M50, "actionable", "critical", "same_day_review", "DM-A1C-CRIT", "coord"),
    ("DM-12", DM, 15.1, None, "%", None, "final", None, M50, "actionable", "critical", "same_day_review", "DM-A1C-CRIT", "coord"),
    ("DM-13", DM, 3.9, None, "%", None, "final", None, M50, "review", "routine", "human_review", "DM-A1C-LOW", "lead"),
    ("DM-14", DM, 4.0, None, "%", None, "final", None, M50, "negative", None, "routine_recall", "DM-A1C-NEG", None),
    ("DM-15", DM, 2.9, None, "%", None, "final", None, M50, "review", "urgent", "human_review", "DM-ERR", "lead"),
    ("DM-16", DM, 20.1, None, "%", None, "final", None, M50, "review", "urgent", "human_review", "DM-ERR", "lead"),
    # ---------------- Diabetes HbA1c mmol/mol (IFCC; exact conversion rounded down) -------------------------
    ("DM-20", DM, 38, None, "mmol/mol", None, "final", None, M50, "negative", None, "routine_recall", "DM-A1C-NEG", None),
    ("DM-21", DM, 39, None, "mmol/mol", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-A1C-PRE", "coord"),
    ("DM-22", DM, 47, None, "mmol/mol", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-A1C-PRE", "coord"),
    ("DM-23", DM, 48, None, "mmol/mol", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-A1C-DIAB", "coord"),
    ("DM-24", DM, 49, None, "mmol/mol", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-A1C-DIAB", "coord"),
    ("DM-25", DM, 85, None, "mmol/mol", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-A1C-DIAB", "coord"),
    ("DM-26", DM, 86, None, "mmol/mol", None, "final", None, M50, "actionable", "high", "confirm_then_gp", "DM-A1C-HIGH", "coord"),
    ("DM-27", DM, 139, None, "mmol/mol", None, "final", None, M50, "actionable", "high", "confirm_then_gp", "DM-A1C-HIGH", "coord"),
    ("DM-27b", DM, 140, None, "mmol/mol", None, "final", None, M50, "actionable", "critical", "same_day_review", "DM-A1C-CRIT", "coord"),
    ("DM-28", DM, 141, None, "mmol/mol", None, "final", None, M50, "actionable", "critical", "same_day_review", "DM-A1C-CRIT", "coord"),
    ("DM-29", DM, 47.4, None, "mmol/mol", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-A1C-PRE", "coord"),
    ("DM-30", DM, 47.5, None, "mmol/mol", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-A1C-DIAB", "coord"),
    # the classic unit bug: 40 mmol/mol is prediabetes, NOT 40 % (impossible) - and never "critical"
    ("DM-31", DM, 40, None, "mmol/mol", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-A1C-PRE", "coord"),
    ("DM-32", DM, 40, None, "%", None, "final", None, M50, "review", "urgent", "human_review", "DM-ERR", "lead"),
    ("DM-33", DM, 8.1, None, None, None, "final", None, M50, "review", "urgent", "human_review", "DM-ERR", "lead"),
    ("DM-34", DM, 16, None, None, None, "final", None, M50, "review", "critical", "human_review", "DM-ERR", "lead"),
    ("DM-35", DM, 8.1, None, "mg/dL", "HBA1C", "final", None, M50, "review", "urgent", "human_review", "DM-ERR", "lead"),
    ("DM-36", DM, 8.1, None, "%", None, "final", None, dict(M50, haemoglobinopathy=True), "review", "high", "human_review", "DM-A1C-DIAB", "lead"),
    ("DM-37", DM, 8.1, None, "%", None, "final", None, dict(age=30, sex="female", pregnant=True), "review", "high", "confirm_then_gp", "DM-A1C-DIAB", "lead"),
    ("DM-38", DM, 8.1, None, "%", None, "final", None, dict(age=17, sex="male"), "review", "routine", "confirm_then_gp", "DM-A1C-DIAB", "lead"),
    ("DM-39", DM, 16.0, None, "%", None, "final", None, dict(M50, deceased=True), "review", "critical", "same_day_review", "DM-A1C-CRIT", "lead"),
    # ---------------- Diabetes fasting plasma glucose (ADA 2026: 100 / 126 mg/dL = 5.6 / 7.0 mmol/L) --------
    ("FPG-01", DM, 99, None, "mg/dL", None, "final", None, M50, "negative", None, "routine_recall", "DM-FPG-NEG", None),
    ("FPG-02", DM, 100, None, "mg/dL", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-FPG-PRE", "coord"),
    ("FPG-03", DM, 125, None, "mg/dL", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-FPG-PRE", "coord"),
    ("FPG-04", DM, 126, None, "mg/dL", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-FPG-DIAB", "coord"),
    ("FPG-05", DM, 127, None, "mg/dL", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-FPG-DIAB", "coord"),
    ("FPG-06", DM, 299, None, "mg/dL", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-FPG-DIAB", "coord"),
    ("FPG-07", DM, 300, None, "mg/dL", None, "final", None, M50, "actionable", "high", "confirm_then_gp", "DM-FPG-HIGH", "coord"),
    ("FPG-08", DM, 500, None, "mg/dL", None, "final", None, M50, "actionable", "critical", "same_day_review", "DM-FPG-CRIT", "coord"),
    ("FPG-09", DM, 499, None, "mg/dL", None, "final", None, M50, "actionable", "high", "confirm_then_gp", "DM-FPG-HIGH", "coord"),
    ("FPG-10", DM, 53, None, "mg/dL", None, "final", None, M50, "review", "urgent", "human_review", "DM-FPG-HYPO", "lead"),
    ("FPG-11", DM, 54, None, "mg/dL", None, "final", None, M50, "negative", None, "routine_recall", "DM-FPG-NEG", None),
    ("FPG-12", DM, 5.5, None, "mmol/L", None, "final", None, M50, "negative", None, "routine_recall", "DM-FPG-NEG", None),
    ("FPG-13", DM, 5.6, None, "mmol/L", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-FPG-PRE", "coord"),
    ("FPG-14", DM, 6.9, None, "mmol/L", None, "final", None, M50, "actionable", "routine", "prediabetes_gp", "DM-FPG-PRE", "coord"),
    ("FPG-15", DM, 7.0, None, "mmol/L", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-FPG-DIAB", "coord"),
    ("FPG-16", DM, 7.1, None, "mmol/L", None, "final", None, M50, "actionable", "routine", "confirm_then_gp", "DM-FPG-DIAB", "coord"),
    ("FPG-17", DM, 16.7, None, "mmol/L", None, "final", None, M50, "actionable", "high", "confirm_then_gp", "DM-FPG-HIGH", "coord"),
    ("FPG-18", DM, 27.7, None, "mmol/L", None, "final", None, M50, "actionable", "critical", "same_day_review", "DM-FPG-CRIT", "coord"),
    ("FPG-19", DM, 2.9, None, "mmol/L", None, "final", None, M50, "review", "urgent", "human_review", "DM-FPG-HYPO", "lead"),
    ("FPG-20", DM, 1600, None, "mg/dL", None, "final", None, M50, "review", "urgent", "human_review", "DM-ERR", "lead"),
    ("FPG-21", DM, 7.0, None, "mmol/L", "OGTT", "final", None, M50, "review", "urgent", "human_review", "DM-ERR", "lead"),
    # ---------------- round 20 edge cohort: combinations (each checked against clinical_rules.md) -----------
    ('EDGE-01', DM, 139.8, None, 'mmol/mol', None, 'final', None, M50, 'actionable', 'high', 'confirm_then_gp', 'DM-A1C-HIGH', 'coord'),
    ('EDGE-02', DM, 139.9, None, 'mmol/mol', None, 'final', None, M50, 'actionable', 'critical', 'same_day_review', 'DM-A1C-CRIT', 'coord'),
    ('EDGE-03', DM, 27.6, None, 'mmol/L', 'FPG', 'final', None, M50, 'actionable', 'high', 'confirm_then_gp', 'DM-FPG-HIGH', 'coord'),
    ('EDGE-04', DM, 3.9, None, '%', None, 'final', None, M50, 'review', 'routine', 'human_review', 'DM-A1C-LOW', 'lead'),
    ('EDGE-05', DM, 4.0, None, '%', None, 'final', None, M50, 'negative', None, 'routine_recall', 'DM-A1C-NEG', None),
    ('EDGE-06', DM, 53, None, '%', None, 'final', None, M50, 'review', 'urgent', 'human_review', 'DM-ERR', 'lead'),
    ('EDGE-07', DM, 140, None, 'mmol/L', 'FPG', 'final', None, M50, 'review', 'urgent', 'human_review', 'DM-ERR', 'lead'),
    ('EDGE-08', DM, 7.0, None, 'mg/dL', 'FPG', 'final', None, M50, 'review', 'urgent', 'human_review', 'DM-ERR', 'lead'),
    ('EDGE-09', DM, 6.6, None, '%', 'HBA1C', 'final', None, dict(age=50, sex='male', haemoglobinopathy=True), 'review', 'high', 'human_review', 'DM-A1C-DIAB', 'lead'),
    ('EDGE-10', DM, 130, None, 'mg/dL', 'FPG', 'final', None, dict(age=50, sex='male', haemoglobinopathy=True), 'actionable', 'routine', 'confirm_then_gp', 'DM-FPG-DIAB', 'coord'),
    ('EDGE-11', DM, 7.0, None, '%', None, 'final', None, dict(age=30, sex='female', pregnant=True), 'review', 'high', 'confirm_then_gp', 'DM-A1C-DIAB', 'lead'),
    ('EDGE-12', BR, None, 'BI-RADS 4', None, None, 'final', None, dict(age=42, sex='female', pregnant=True), 'review', 'urgent', 'biopsy', 'BR-4', 'lead'),
    ('EDGE-13', DM, 6.0, None, '%', None, 'final', None, dict(age=17, sex='male'), 'review', 'routine', 'prediabetes_gp', 'DM-A1C-PRE', 'lead'),
    ('EDGE-14', CRC, 120, None, 'ug/g', None, 'final', None, dict(age=39, sex='male'), 'review', 'high', 'colonoscopy', 'CRC-FIT-POS', 'lead'),
    ('EDGE-15', CRC, 120, None, 'ug/g', None, 'final', None, dict(age=76, sex='male'), 'review', 'high', 'colonoscopy', 'CRC-FIT-POS', 'lead'),
    ('EDGE-16', BR, None, 'BI-RADS 0', None, None, 'final', None, dict(age=39, sex='female'), 'review', 'high', 'assessment_imaging', 'BR-0', 'lead'),
    ('EDGE-17', CX, None, 'HPV16', None, 'HPV', 'final', 'NILM', dict(age=24, sex='female'), 'review', 'high', 'colposcopy', 'CX-HPV1618', 'lead'),
    ('EDGE-18', CX, None, 'HPV16', None, 'HPV', 'final', 'NILM', dict(age=66, sex='female'), 'review', 'high', 'colposcopy', 'CX-HPV1618', 'lead'),
    ('EDGE-19', CRC, 450, None, 'ug/g', None, 'final', None, dict(age=60, sex='male', deceased=True), 'review', 'urgent', 'colonoscopy', 'CRC-FIT-URGENT', 'lead'),
    ('EDGE-20', BR, None, 'BI-RADS 5', None, None, 'final', None, dict(age=60, sex='female', under_specialist_care=True), 'review', 'urgent', 'biopsy', 'BR-5', 'lead'),
    ('EDGE-21', CX, None, 'HPV negative', None, 'HPV', 'final', 'ASC-US', F50, 'review', 'high', 'human_review', 'CX-HPVNEG-LG', 'lead'),
    ('EDGE-22', CRC, 85, None, 'ug/g', None, 'amended', None, M50, 'review', 'urgent', 'colonoscopy', 'CRC-FIT-POS', 'lead'),
    ('EDGE-23', BR, None, 'BI-RADS 4', None, None, 'preliminary', None, F50, 'review', 'urgent', 'biopsy', 'BR-4', 'lead'),
    ('EDGE-24', CRC, None, None, None, None, 'final', None, M50, 'review', 'urgent', 'human_review', 'CRC-ERR', 'lead'),
    ('EDGE-25', CX, None, '#VALUE!', None, 'HPV', 'final', None, F50, 'review', 'urgent', 'human_review', 'CX-ERR', 'lead'),
    ('EDGE-26', BR, None, 'BI-RADS 6', None, None, 'final', None, dict(age=90, sex='female'), 'review', 'urgent', 'human_review', 'BR-6', 'lead'),
    ('EDGE-27', DM, 15.0, None, '%', None, 'final', None, dict(age=92, sex='female'), 'actionable', 'critical', 'same_day_review', 'DM-A1C-CRIT', 'coord'),
    ('EDGE-28', CRC, 120, None, 'ug/g', None, 'corrected', None, M50, 'review', 'urgent', 'colonoscopy', 'CRC-FIT-POS', 'lead'),
    ('EDGE-29', CX, None, 'ASC-US', None, 'CYTOLOGY', 'final', None, F50, 'actionable', 'routine', 'reflex_hpv', 'CX-CYTO-LG-NOHPV', 'coord'),
    ('EDGE-30', DM, 2.9, None, 'mmol/L', 'FPG', 'final', None, dict(age=30, sex='female', pregnant=True), 'review', 'urgent', 'human_review', 'DM-FPG-HYPO', 'lead'),
    ('EDGE-31', CRC, 80, None, 'ug/g', None, 'final', None, dict(age=40, sex='female', pregnant=True), 'review', 'high', 'colonoscopy', 'CRC-FIT-POS', 'lead'),
    ('EDGE-32', BR, None, 'BI-RADS 2', None, None, 'final', None, dict(age=50, sex='female', deceased=True), 'review', 'high', 'human_review', 'BR-2', 'lead'),
]

IDS = [g[0] for g in G]
assert len(IDS) == len(set(IDS)), "duplicate golden ids"


def _decide(row):
    _, prog, val, txt, unit, test, status, cyto, ctx, *_ = row
    return evaluate(ResultInput(prog, val, txt, unit, test, status, cyto), PatientContext(**ctx))


def test_golden_count():
    assert len(G) >= 60


@pytest.mark.parametrize("row", G, ids=IDS)
def test_golden_rule(row):
    gid, *_, disp, tier, pathway, rule, owner = row
    d = _decide(row)
    assert (d.disposition, d.tier, d.pathway, d.rule_id) == (disp, tier, pathway, rule), gid
    assert d.creates_case == (owner is not None)
    if d.disposition == "review":
        assert d.reasons, "every review decision carries a machine-readable reason"
    if d.clinical_tier and d.tier:  # review floors never LOWER the clinical tier
        from wasl.clinical_rules import TIER_RANK
        assert TIER_RANK[d.tier] <= TIER_RANK[d.clinical_tier]


@pytest.mark.parametrize("row", [g for g in G if g[0] in {
    "CRC-02", "CRC-05", "CRC-09", "CRC-19", "CRC-24", "CX-07", "CX-14", "CX-23", "BR-03", "BR-05", "BR-08", "BR-13",
    "DM-11", "DM-13", "DM-31", "DM-33", "DM-36", "DM-39", "FPG-10", "FPG-13", "CRC-01", "BR-01"}], ids=lambda r: r[0])
def test_golden_owner_end_to_end(db, row):
    """Through the real ingest path (DB): owner is the coordinator for actionable, the clinical lead for review."""
    import random

    from wasl.models import Patient, Practitioner, utcnow
    from wasl.services import workflow as wf

    gid, prog, val, txt, unit, test, status, cyto, ctx, disp, tier, pathway, rule, owner = row
    now = utcnow()
    age = ctx.get("age") or 50
    p = Patient(mrn=f"GOLD-{gid}-{random.randint(0, 10**9)}", given_name="Gold", family_name=gid, gender=ctx["sex"],
                birth_year=now.year - age, age_band="x", nationality_group="emirati", language="en", region="Abu Dhabi",
                phone_valid=True, has_smartphone=True, whatsapp=False, managing_org_id=3, gp_id=19, responsiveness=0.8,
                pregnant=ctx.get("pregnant", False), deceased=ctx.get("deceased", False),
                haemoglobinopathy=ctx.get("haemoglobinopathy", False),
                specialist_care=prog if ctx.get("under_specialist_care") else None)
    db.add(p)
    db.flush()
    rep, case = wf.ingest_result(db, p, prog, value=val, value_text=txt, unit=unit, test=test, status=status,
                                 cytology=cyto, performer_org_id=1, now=now)
    if owner is None:
        assert case is None and rep.conclusion == "negative"
    else:
        assert case is not None and case.tier == tier and case.rule_id == rule
        role = db.get(Practitioner, case.owner_id).role
        assert role == {"lead": "clinical_lead", "coord": "coordinator"}[owner], (gid, role)
        assert case.review_required == (owner == "lead")
    db.rollback()


@pytest.mark.parametrize("pct,mmol", [(5.7, 38.8), (6.5, 47.5), (10.0, 85.8), (15.0, 140.4), (4.0, 20.2), (20.0, 195.1)])
def test_hba1c_conversion_ngsp_ifcc(pct, mmol):
    """NGSP % = 0.09148 x IFCC + 2.152 (ngsp.org master equation)."""
    assert round(hba1c_pct_to_mmol(pct), 1) == mmol
    assert abs(hba1c_mmol_to_pct(hba1c_pct_to_mmol(pct)) - pct) < 1e-9


@pytest.mark.parametrize("pct", [x / 10 for x in range(30, 201)])
def test_hba1c_units_agree_on_category(pct):
    """For every 0.1 % step, the % result and the same result in mmol/mol (rounded to an integer as labs
    report it) never land in a LESS urgent category in mmol/mol than in %."""
    from wasl.clinical_rules import TIER_RANK

    ctx = PatientContext(age=50, sex="male")
    a = evaluate(ResultInput(DM, pct, unit="%"), ctx)
    b = evaluate(ResultInput(DM, round(hba1c_pct_to_mmol(pct)), unit="mmol/mol"), ctx)
    rank = lambda d: 9 if d.tier is None else TIER_RANK[d.tier]  # noqa: E731
    assert rank(b) <= rank(a) or b.disposition == "review" or a.disposition == "review", (pct, a.rule_id, b.rule_id)
    assert a.creates_case == b.creates_case or b.disposition == "review" or a.disposition == "review", pct


# ---- strengthening added after mutation testing (mutmut survivors) -----------------------------------------
def test_is_downgrade_strict():
    from wasl.clinical_rules import is_downgrade

    assert not is_downgrade("high", "high") and not is_downgrade("high", "urgent")
    assert is_downgrade("urgent", "high") and not is_downgrade(None, "high") and not is_downgrade("high", None)


@pytest.mark.parametrize("row", G, ids=IDS)
def test_every_decision_explains_itself(row):
    """Clinicians see next step and target: they must never be empty for a case-creating decision."""
    d = _decide(row)
    assert d.next_step and d.next_step.strip()
    if d.disposition == "actionable":
        assert d.target and d.target.strip() and d.pathway != "human_review"


REASONS = {"CRC-09": "missing_unit", "CRC-10": "unit_needs_assay_conversion", "CRC-11": "unknown_unit",
           "CRC-12": "implausible_value", "CRC-13": "implausible_value", "CRC-17": "malformed_value",
           "CRC-18": "missing_value", "CRC-28": "unknown_test", "CX-20": "malformed_value", "CX-21": "malformed_value",
           "CX-22": "malformed_value", "CX-19": "unknown_test", "CX-14": "hpv_negative_abnormal_cytology",
           "BR-04": "screening_category_unexpected", "BR-08": "known_malignancy", "BR-09": "malformed_value",
           "BR-10": "malformed_value", "BR-12": "missing_value", "DM-13": "unexpectedly_low", "DM-15": "implausible_value",
           "DM-32": "implausible_value", "DM-33": "missing_unit", "DM-35": "unknown_unit", "DM-36": "haemoglobinopathy",
           "FPG-10": "hypoglycaemia", "FPG-20": "implausible_value", "FPG-21": "unknown_test",
           "CRC-19": "age_below_programme", "CRC-22": "age_above_programme", "CRC-24": "amended_result",
           "CRC-25": "preliminary_result", "CRC-26": "under_specialist_care", "CRC-27": "age_unknown",
           "CX-23": "sex_mismatch", "CX-28": "pregnant", "CX-29": "deceased", "BR-17": "result_retracted"}


@pytest.mark.parametrize("gid,reason", sorted(REASONS.items()))
def test_review_reason_codes(gid, reason):
    row = next(g for g in G if g[0] == gid)
    assert reason in _decide(row).reasons


@pytest.mark.parametrize("val,expect", [(4.0, "BR-4"), (0.0, "BR-0"), (1.0, "BR-1"), (4.5, "BR-ERR"), (" ", "BR-ERR")])
def test_birads_numeric_values(val, expect):
    r = ResultInput(BR, None if isinstance(val, str) else val, val if isinstance(val, str) else None)
    assert evaluate(r, PatientContext(**F50)).rule_id == expect


@pytest.mark.parametrize("pathway,rule,tier,clin,days", [
    ("colonoscopy", "CRC-FIT-POS", "high", "high", 31), ("colonoscopy", "CRC-FIT-URGENT", "urgent", "urgent", 14),
    ("colonoscopy", "CRC-FIT-POS", "urgent", "high", 14),  # escalated by a review floor -> tier default applies
    ("biopsy", "BR-4", "urgent", "urgent", 7), ("assessment_imaging", "BR-0", "high", "high", 7),
    ("colposcopy", "CX-HPV1618", "high", "high", 42), ("colposcopy_urgent", "CX-HIGH-GRADE", "urgent", "urgent", 14),
    ("same_day_review", "DM-A1C-CRIT", "critical", "critical", 1), ("prediabetes_gp", "DM-A1C-PRE", "routine", "routine", 90),
    ("confirm_then_gp", "DM-A1C-HIGH", "high", "high", 14), ("human_review", "CRC-ERR", "urgent", None, 14),
    ("human_review", "DM-ERR", "critical", "critical", 1), ("repeat_sample", "CX-UNSAT", "routine", "routine", 120),
])
def test_target_days(pathway, rule, tier, clin, days):
    from wasl.clinical_rules import target_days

    assert target_days(pathway, rule, tier, clin) == days


@pytest.mark.parametrize("reasons,tier", [(["duplicate_result"], "routine"), (["conflicting_result"], "high"),
                                          (["duplicate_result", "more_urgent_result"], "urgent"),
                                          (["not_a_known_reason"], "urgent"), ([], None)])
def test_review_min_tier(reasons, tier):
    from wasl.clinical_rules import review_min_tier

    assert review_min_tier(reasons) == tier


# ---- full-output snapshot (Iteration 7): next-step and target wording is clinician-facing and must only change
# deliberately. Regenerate with WASL_UPDATE_SNAPSHOT=1 after clinical-lead approval of the new wording.
SNAPSHOT = Path(__file__).parent / "snapshots" / "golden_decisions.json"


def _full(d):
    return {"disposition": d.disposition, "tier": d.tier, "pathway": d.pathway, "next_step": d.next_step,
            "needs_confirmation": d.needs_confirmation, "target": d.target, "rule_id": d.rule_id,
            "reasons": d.reasons, "clinical_tier": d.clinical_tier, "normalised": d.normalised}


def test_golden_full_output_snapshot():
    got = {row[0]: _full(_decide(row)) for row in G}
    if os.environ.get("WASL_UPDATE_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(got, indent=1, sort_keys=True))
    want = json.loads(SNAPSHOT.read_text())
    diff = {k: (want.get(k), v) for k, v in got.items() if want.get(k) != v}
    assert not diff, json.dumps(dict(list(diff.items())[:3]), indent=1)

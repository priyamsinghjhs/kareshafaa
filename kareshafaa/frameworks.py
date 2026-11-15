"""Framework alignment for KareShafaa (pilot / hackathon readiness).

Maps the system to the standards researched for an Abu Dhabi healthcare pilot:
- Malaffi (Abu Dhabi HIE) — read-only skeleton only; no live connection claimed
- ADHICS V2 (DoH, effective Aug 2024) — sector cybersecurity standard
- IoMT Security Standard V0.9 (2020) — retired; requirements now inside ADHICS V2
- UAE Information Assurance Standard (formerly NESA IAS) — national CII baseline

This is a design and readiness mapping, not a certification. Full compliance is a facility
and pilot-owner responsibility under the Accession Agreement and DoH licensing.
"""
from __future__ import annotations

from typing import Any

FRAMEWORKS = [
    {
        "id": "malaffi",
        "name": "Malaffi (Abu Dhabi HIE)",
        "issuer": "Abu Dhabi Health Data Services (ADHDS) / DoH",
        "status": "skeleton (mock mode)",
        "applies_to": "DoH-licensed facilities; connection required for licence renewal",
        "kareshafaa": "Read-only adapter skeleton (services/malaffi.py). Match on Emirates ID only. Never writes clinical data. GET /api/admin/about/malaffi.",
        "next_step": "Facility completes Accession Agreement + security assessment; subscribe to result feeds.",
    },
    {
        "id": "adhics",
        "name": "ADHICS V2",
        "issuer": "DoH Information & Cyber Security Office",
        "document": "DOH/SD/ICSO/ADHICS/V2/2024",
        "effective": "August 2024",
        "status": "aligned by design (not certified)",
        "applies_to": "Any entity in Abu Dhabi that handles health information; required for Malaffi onboarding",
        "tiers": "Basic (all) / Transitional / Advanced / Service Provider",
        "kareshafaa": "RBAC, append-only audit, session controls, synthetic data by default, no live HIE write. Formal ADHICS gap assessment is a pilot task.",
        "domains_covered_by_design": [
            "Access Control (unique IDs, role-based)",
            "Communications and Operations (logging, timeouts)",
            "Data Privacy and Protection (synthetic default, purpose limitation)",
            "Information Security Incident Management (audit trail supports response)",
            "Information Systems Acquisition, Development and Maintenance (tests, OpenAPI)",
        ],
    },
    {
        "id": "iomt",
        "name": "IoMT Security Standard",
        "issuer": "DoH",
        "document": "DOH/SD/IoMT/0.9 (October 2020)",
        "status": "retired — requirements now inside ADHICS V2",
        "kareshafaa": "KareShafaa is software, not a medical device. If a pilot facility connects physical devices (FIT analyser, HbA1c meter, imaging), the facility owns inventory, segmentation, and vendor controls under ADHICS V2. KareShafaa treats resulting data as health information under the same access and audit rules.",
    },
    {
        "id": "uae_ia",
        "name": "UAE Information Assurance Standard (formerly NESA IAS)",
        "issuer": "UAE Cyber Security Council",
        "version": "v2 / v2.1 (2025)",
        "status": "aligned by design where applicable",
        "applies_to": "Federal entities and designated Critical Information Infrastructure; often required contractually by CII clients",
        "controls": "134 controls (70 always applicable); priorities P1–P4",
        "kareshafaa": "Supports P1-relevant practices: risk-aware design, access control, logging, incident-supporting audit, third-party (HIE) boundary. Full IA compliance is the hosting organisation's responsibility if designated CII.",
    },
]


def frameworks_status() -> dict[str, Any]:
    """Summary for the About page and judges."""
    return {
        "frameworks": FRAMEWORKS,
        "disclaimer": "Design and readiness mapping only. No certification is claimed. "
                      "Malaffi connection and formal ADHICS / IA assessments are facility and pilot-owner tasks. "
                      "Synthetic data only in the public demo.",
        "pilot_owner": "Pilot facility (DoH-licensed) owns Accession, security assessment, and licensing. "
                       "KareShafaa provides the loop-closure engine and the adapter skeleton.",
    }

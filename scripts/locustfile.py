"""Locust load profile for a local or staging server. Never point it at the hosted service: it creates cases.

    locust -f scripts/locustfile.py --host http://127.0.0.1:8041 --headless -u 20 -r 5 -t 60s --csv /tmp/ks

Needs a server started with sign-in for test accounts switched on (standard mode, KARESHAFAA_DEMO_LOGINS=true) on a
COPY of the database. Mix per user class: coordinators work the queue and open cases, the clinical lead reads the
impact report and KPIs, an integration engine posts HL7 v2 results, patients open their own result.
"""
from __future__ import annotations

import random

from locust import HttpUser, between, task


def _login(user: HttpUser, username: str) -> dict:
    r = user.client.post("/api/auth/demo-login", json={"username": username}, name="sign in")
    r.raise_for_status()
    user.client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['token']}"}


class Coordinator(HttpUser):
    weight = 6
    wait_time = between(0.5, 2)

    def on_start(self):
        self.h = _login(self, "aisha.rahman")
        q = self.client.get("/api/queue?view=all&limit=200", headers=self.h, name="/api/queue").json()
        self.ids = [i["id"] for i in q.get("items", [])] or [1]

    @task(4)
    def queue(self):
        self.client.get("/api/queue?view=all&limit=50", headers=self.h, name="/api/queue")

    @task(2)
    def overdue(self):
        self.client.get("/api/queue?view=overdue&limit=50", headers=self.h, name="/api/queue?view=overdue")

    @task(4)
    def case(self):
        self.client.get(f"/api/cases/{random.choice(self.ids)}", headers=self.h, name="/api/cases/{id}")


class ClinicalLead(HttpUser):
    weight = 2
    wait_time = between(1, 4)

    def on_start(self):
        self.h = _login(self, "fatima.al.mansoori")

    @task(3)
    def impact(self):
        self.client.get("/api/analytics/impact", headers=self.h, name="/api/analytics/impact")

    @task(1)
    def impact_programme(self):
        p = random.choice(["colorectal_fit", "cervical", "breast", "diabetes_hba1c"])
        self.client.get(f"/api/analytics/impact?programme={p}", headers=self.h, name="/api/analytics/impact?programme")

    @task(2)
    def kpis(self):
        self.client.get("/api/analytics/kpis", headers=self.h, name="/api/analytics/kpis")

    @task(1)
    def rule_cards(self):
        self.client.get("/api/governance/rule-cards", headers=self.h, name="/api/governance/rule-cards")


class IntegrationEngine(HttpUser):
    weight = 1
    wait_time = between(1, 3)

    def on_start(self):
        self.h = _login(self, "aisha.rahman")

    @task
    def hl7(self):
        msg = self.client.get("/api/ingest/sample", headers=self.h, name="/api/ingest/sample").text
        self.client.post("/api/ingest/hl7v2", data=msg, headers={**self.h, "Content-Type": "text/plain"},
                         name="POST /api/ingest/hl7v2")


class Patient(HttpUser):
    weight = 1
    wait_time = between(2, 5)

    def on_start(self):
        self.h = _login(self, random.choice(["patient.en", "patient.ar"]))

    @task
    def my_case(self):
        self.client.get("/api/me/case", headers=self.h, name="/api/me/case")


class Probe(HttpUser):
    weight = 1
    fixed_count = 1
    wait_time = between(5, 5)

    @task
    def ready(self):
        self.client.get("/readyz", name="/readyz")

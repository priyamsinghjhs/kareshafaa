"""Time the service-layer hot paths directly (no HTTP) against the configured database."""
from __future__ import annotations

import time

from sqlalchemy import func, select

from kareshafaa.api.serializers import case_detail
from kareshafaa.db import session_factory
from kareshafaa.models import Case, StepTask, User
from kareshafaa.services import analytics
from kareshafaa.services.escalation import run_sweep
from kareshafaa.services.queue import work_queue
from kareshafaa.synth.history import actor_from_user


def t(label, fn, n=5):
    xs = []
    for _ in range(n):
        s = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - s) * 1000)
    print(f"{label:40s} median {sorted(xs)[len(xs) // 2]:8.1f} ms  (n={n})")


db = session_factory()()
print("cases", db.scalar(select(func.count()).select_from(Case)), "steps", db.scalar(select(func.count()).select_from(StepTask)))
users = {u.username: actor_from_user(u) for u in db.scalars(select(User))}
cid = db.scalar(select(Case.id).where(Case.state != "closed").limit(1))
t("work_queue coordinator", lambda: work_queue(db, users["aisha.rahman"], "all", limit=50))
t("work_queue GP (org-scoped)", lambda: work_queue(db, users["omar.haddad"], "all", limit=50))
t("case_detail", lambda: case_detail(db, db.get(Case, cid), users["fatima.al.mansoori"]))
t("analytics.kpis", lambda: analytics.kpis(db), n=3)
t("analytics.fairness_slices", lambda: analytics.fairness_slices(db), n=3)
t("run_sweep (all open)", lambda: run_sweep(db), n=3)
db.rollback()

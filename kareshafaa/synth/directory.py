"""Synthetic organisations, practitioners and demo users (all fictional)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Organization, Practitioner, User
from ..programmes import PROGRAMMES

PHCS = [("Corniche Family Health Centre", "Abu Dhabi"), ("Khalifa City Family Health Centre", "Abu Dhabi"),
        ("Mussafah Family Health Centre", "Abu Dhabi"), ("Al Ain Central Family Health Centre", "Al Ain"),
        ("Hili Family Health Centre", "Al Ain"), ("Madinat Zayed Family Health Centre", "Al Dhafra")]
HOSPITALS = [("Capital General Hospital", "Abu Dhabi"), ("Oasis Teaching Hospital", "Al Ain"),
             ("Western Region Hospital", "Al Dhafra")]
SPECIALTY_NAMES = {"gastroenterology": "Gastroenterology", "gynaecology": "Colposcopy / Gynaecology",
                   "breast_surgery": "Breast Clinic", "endocrinology": "Endocrinology"}


def build_directory(db: Session) -> dict:
    d: dict = {"phc": [], "hosp": [], "gps": {}, "specialists": {}, "coordinators": {}, "pools": {}, "leads": {}}
    prog_org = Organization(name="Emirate Screening Programme", kind="screening_programme", region="Abu Dhabi")
    lab = Organization(name="Central Screening Laboratory", kind="lab", region="Abu Dhabi")
    db.add_all([prog_org, lab])
    for name, region in PHCS:
        o = Organization(name=name, kind="primary_care", region=region)
        db.add(o)
        d["phc"].append(o)
    for name, region in HOSPITALS:
        o = Organization(name=name, kind="hospital", region=region)
        db.add(o)
        d["hosp"].append(o)
    db.flush()
    d["programme_org"], d["lab"] = prog_org, lab
    director = Practitioner(name="Dr. Layla Al Hashimi (Programme Medical Director)", role="clinical_lead",
                            org_id=prog_org.id, programme=None)
    sup = Practitioner(name="Mariam Al Suwaidi (Coordination Supervisor)", role="coordinator", specialty="supervisor",
                       org_id=prog_org.id)
    db.add_all([director, sup])
    db.flush()
    d["director"], d["supervisor"] = director, sup
    lead_names = {"colorectal_fit": "Dr. Fatima Al Mansoori", "cervical": "Dr. Noura Al Ketbi",
                  "breast": "Dr. Sarah Thompson", "diabetes_hba1c": "Dr. Rashid Al Nuaimi"}
    coord_names = {"colorectal_fit": ["Aisha Rahman", "Joseph Mathew"], "cervical": ["Hessa Al Dhaheri", "Grace Santos"],
                   "breast": ["Sana Qureshi", "Emily Clarke"], "diabetes_hba1c": ["Khalid Al Ameri", "Priya Nair"]}
    for key, prog in PROGRAMMES.items():
        lead = Practitioner(name=f"{lead_names[key]} (Clinical Lead, {prog.name.split(' - ')[0]})", role="clinical_lead",
                            org_id=prog_org.id, programme=key, supervisor_id=director.id)
        pool = Practitioner(name=f"{prog.name.split(' - ')[0]} team pool (unassigned)", role="coordinator",
                            org_id=prog_org.id, programme=key, is_pool=True, supervisor_id=sup.id)
        db.add_all([lead, pool])
        d["leads"][key], d["pools"][key] = lead, pool
        d["coordinators"][key] = []
        for n in coord_names[key]:
            c = Practitioner(name=f"{n} (Care Coordinator)", role="coordinator", org_id=prog_org.id, programme=key,
                             supervisor_id=sup.id)
            db.add(c)
            d["coordinators"][key].append(c)
    gp_names = ["Dr. Omar Haddad", "Dr. Anjali Menon", "Dr. Yousef Al Marzouqi", "Dr. Hannah Weber", "Dr. Imran Siddiqui",
                "Dr. Reem Al Shamsi", "Dr. Carlos Reyes", "Dr. Meera Pillai", "Dr. Ahmed Fathy", "Dr. Zainab Al Falasi",
                "Dr. Rohan Kapoor", "Dr. Amina Yusuf"]
    i = 0
    for o in d["phc"]:
        d["gps"][o.id] = []
        for _ in range(2):
            g = Practitioner(name=f"{gp_names[i]} (GP)", role="gp", org_id=o.id, supervisor_id=None)
            db.add(g)
            d["gps"][o.id].append(g)
            i += 1
    spec_names = iter(["Dr. Vikram Mehta", "Dr. Salma Al Hosani", "Dr. James O'Connor", "Dr. Leena George",
                       "Dr. Tariq Aziz", "Dr. Maryam Al Blooshi", "Dr. Arjun Rao", "Dr. Nadia Karim",
                       "Dr. Peter Novak", "Dr. Huda Al Zaabi", "Dr. Faisal Rahim", "Dr. Elena Petrova"])
    for o in d["hosp"]:
        for spec in SPECIALTY_NAMES:
            s = Practitioner(name=f"{next(spec_names)} ({SPECIALTY_NAMES[spec]})", role="specialist", specialty=spec,
                             org_id=o.id)
            db.add(s)
            d["specialists"][(o.id, spec)] = s
    db.flush()
    for o in d["phc"]:
        lead_gp = d["gps"][o.id][0]
        d["gps"][o.id][1].supervisor_id = lead_gp.id
        lead_gp.supervisor_id = director.id
    return d


def build_users(db: Session, d: dict) -> list[User]:
    users = []
    for p in db.query(Practitioner).filter(Practitioner.is_pool.is_(False)).all():
        uname = p.name.split(" (")[0].lower().replace("dr. ", "").replace("'", "").replace(" ", ".")
        users.append(User(username=uname, display_name=p.name, role=p.role, practitioner_id=p.id))
    users.append(User(username="analyst", display_name="Dana Analyst (Programme Analytics)", role="analyst"))
    users.append(User(username="admin", display_name="Sam Admin (System Administrator)", role="admin"))
    users.append(User(username="translator", display_name="Layla Haddad (Medical translation reviewer, DEMO)",
                      role="translator"))
    db.add_all(users)
    db.flush()
    return users

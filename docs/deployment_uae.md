# Deployment guide: hosted, cloud in the UAE, and on-premises

KareShafaa is one container image plus one PostgreSQL database. The same image runs in all three set-ups below;
only environment variables change. Sources were checked on 3 Nov 2026.

## 1. Current hosting (free, synthetic records only)

* Render free web service (`render.yaml`, one worker, sleeps after 15 minutes idle) and a Neon free PostgreSQL
  database (`DEPLOY.md` has the steps).
* **Data residency:** Render runs in Oregon, Ohio, Virginia, Frankfurt or Singapore
  (https://render.com/docs/regions) and Neon's regions are in the US, Europe, Asia-Pacific and South America
  (https://neon.com/docs/introduction/regions). **Neither has a UAE region**, so this hosting holds synthetic sample
  records only and must never hold real patient data.

## 2. Pilot or production in a UAE cloud region

UAE Federal Law No. 2 of 2019 (ICT in health fields), Article 13, requires health data to be stored and processed
inside the UAE unless an exception is granted
(https://elaws.moj.gov.ae/UAE-MOJ_LC-En/00_Health/UAE-LC-En_2019-02-06_00002_Kait.html?val=EL1). Options with an
in-country region (checked 3 Nov 2026):

| Provider | Region | Source |
|---|---|---|
| AWS | Middle East (UAE), `me-central-1`, three availability zones | https://aws.amazon.com/blogs/aws/now-open-aws-region-in-the-united-arab-emirates-uae/ |
| Microsoft Azure | UAE North (Dubai, three zones); UAE Central (Abu Dhabi, restricted, for in-country recovery) | https://learn.microsoft.com/en-us/azure/reliability/regions-list |

Steps:

1. Build the image from this repository (`docker build -t kareshafaa .`) and push it to the provider's registry in
   the UAE region.
2. Create a managed PostgreSQL 16+ database in the same region (AWS RDS / Azure Database for PostgreSQL), with
   encryption at rest, automated backups kept in-region and point-in-time recovery.
3. Run 2+ containers behind a load balancer with TLS (ECS/Fargate, Azure Container Apps or Kubernetes). Set
   `KARESHAFAA_MODE=production`, `KARESHAFAA_DEMO_LOGINS=false`, `KARESHAFAA_SECRET_KEY` (from the secrets
   manager), `DATABASE_URL`, `WEB_WORKERS=2-4`, `KARESHAFAA_LOG_FORMAT=json`.
4. Health checks: liveness `/healthz`, readiness `/readyz`.
5. Run the timed jobs in one place (see `docs/background_jobs.md`).
6. Ship JSON logs to the provider's log service in the same region; keep the audit trail in the database (it is
   never deleted, see Governance → Data protection).
7. Restrict administrator pages to the organisation network or VPN; put the service behind the organisation's
   single sign-on before go-live (not built in this prototype).

The facility or cluster must still complete the DoH security assessment (ADHICS) and the Malaffi onboarding steps
before any connection to the exchange (see Admin → About → Malaffi readiness).

## 3. On-premises (hospital or cluster data centre)

`docker-compose.yml` runs the app and PostgreSQL on one server:

```bash
cp .env.example .env        # set KARESHAFAA_SECRET_KEY, KARESHAFAA_DEMO_PASSWORD (first staff password), POSTGRES_PASSWORD
docker compose up --build -d
curl -fsS http://localhost:8000/readyz
```

* The database is in the `pgdata` volume; rebuilding or restarting the app never resets it.
* Put a reverse proxy (nginx, the hospital's load balancer) in front for TLS and connect it to the organisation's
  backup system (`pg_dump` nightly, kept on-site).
* HL7 v2 from the laboratory: point the interface engine (for example Rhapsody or Mirth) at
  `POST /api/ingest/hl7v2` over HTTPS; the response is an HL7 ACK.

## Sizing (measured locally, see `docs/loadtest_locust.md` and `docs/loadtest_ramp.md`)

The figures are from a single 8-CPU test machine, not from production; use them as a starting point only.

## Configuration per emirate and programme

* Programmes (bowel, cervical, breast, diabetes) with their rules, deadlines and message templates are in
  `kareshafaa/programmes.py`, `kareshafaa/clinical_rules.py` and `kareshafaa/comms/`.
* Emirate profiles (regulator, exchange, residency) are in `kareshafaa/integration.py` (`EMIRATE_PROFILES`). Abu
  Dhabi is configured; Dubai (DHA, NABIDH) and the Northern Emirates (MOHAP, Riayati) are listed but not configured.
  A new emirate needs its own clinical rule sign-off, message approval and exchange connection.

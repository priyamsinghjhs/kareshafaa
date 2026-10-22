# Hosting KareShafaa (free tier) with permanent data

Checked 22 Oct 2026. KareShafaa is one Docker container (FastAPI). On Render it runs in **production mode**
(`KARESHAFAA_MODE=production`, also the default whenever `RENDER=true`): user management, every clinical workflow and
data entry, governance behind the normal role checks and approval gates, password sign-in only, an abuse limit of 300
writes per minute per user, and no resets.

## Where the data lives

Render's free web services have a **temporary disk**: anything written to local files (including SQLite) is lost on
every restart, sleep/wake-up and redeploy, and free services cannot attach a persistent disk. So the app needs an
external PostgreSQL database for permanent data. Until `DATABASE_URL` is set it still runs on SQLite, logs a clear
warning, and shows "Data is not stored permanently" in **Admin > System**.

| Free PostgreSQL option | Permanent? | Limits (free plan, checked 22 Oct 2026) | Verdict |
|---|---|---|---|
| **Neon Free** | Yes, no expiry | 1 GB storage per project, 100 CU-hours compute per project per month (≈400 h at 0.25 CU), compute scales to zero after 5 min idle and wakes in a few hundred ms, 5 GB transfer/month, 6 h restore history, no card needed. Hitting a limit suspends compute or blocks writes; it does not delete data | **Recommended** |
| Supabase Free | Yes, but **pauses after 1 week of low activity** (resume by hand within 1 year) | 500 MB database, 2 active projects | Second choice |
| Render Free Postgres | **No - expires 30 days after creation**, deleted 14 days later, no backups | 1 GB, one per workspace | Not suitable |

KareShafaa's database is about 25-40 MB with the initial records, so Neon's 1 GB is ample. Compute: Render's free
service sleeps after 15 min idle, and Neon's compute then sleeps after 5 more minutes, so normal use stays well
inside 100 CU-hours a month.

## Steps (one-time, about 10 minutes)

1. **Neon account:** go to https://neon.com, click *Sign up* and sign in with GitHub or Google (free plan, no card).
2. **Create a project:** name `kareshafaa`, Postgres 17, region **AWS US West 2 (Oregon)** - the same region as
   the Render service (Render's default; pick the Neon region closest to your Render region if you changed it).
3. **Copy the connection string:** on the project dashboard click *Connect*, keep the default database `neondb`
   and role, and copy the URL. It looks like
   `postgresql://neondb_owner:...@ep-...-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require`.
   (Either the pooled or direct URL works; the app accepts `postgres://` and `postgresql://`.)
4. **Render:** Dashboard -> service `kareshafaa` -> *Environment* -> add
   * `DATABASE_URL` = the Neon URL (secret),
   * `KARESHAFAA_DEMO_PASSWORD` = the first password for the staff accounts (12+ characters, not a common password),
   * `KARESHAFAA_MODE` = `production` (already in `render.yaml`; add it by hand if the service was not created
     from the Blueprint). Remove `KARESHAFAA_PUBLIC_DEMO` if it is still set to `1` (production mode ignores it).
   Then *Save, rebuild and deploy*.
5. **First start:** the bootstrap finds the empty Neon database and copies the initial records into it once
   (organisations, programmes, sites, staff directory and accounts, sample patient records; under a minute). Every
   later restart or redeploy keeps the data as it is. Check **Admin > System**: it should say
   "Data is stored permanently (PostgreSQL · ep-...neon.tech)".
6. **Change passwords:** sign in as `admin`, reset passwords for people who need their own, and add new users in
   **Admin > Users**. The environment password is only used for accounts that have no password yet.

Backups: Neon Free keeps 6 hours of restore history and 1 manual snapshot. For anything important, also take a
regular `pg_dump` to storage you control.

## Alternatives for the web service

| Host | Free? | Notes |
|---|---|---|
| **Render** (used) | Free web service: 512 MB RAM, 0.1 vCPU, sleeps after 15 min idle (≈1 min cold start), 750 instance-hours/month | `render.yaml` Blueprint |
| Google Cloud Run | Always-free request tier, needs a billing account (card) | `gcloud run deploy kareshafaa --source . --set-env-vars KARESHAFAA_MODE=production,...` |
| Koyeb | One free 512 MB service, may ask for a card | Docker from GitHub |

## Not for real patient data

None of these free tiers is suitable for real health data. A pilot needs UAE data residency (hosting inside the UAE),
a hosting agreement with the health authority, backups and monitoring - not a free hobby tier.

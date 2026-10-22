# Permanent free hosting for the KareShafaa public demo

Checked 22 Oct 2026. The app is one Docker container (FastAPI + SQLite, synthetic data only). It runs in public demo
mode (`KARESHAFAA_PUBLIC_DEMO=1`): destructive admin actions are off, writes are rate-limited, and the database is
restored from a seed copy on every start. So an **ephemeral disk is fine**, and free hosts that sleep and lose local
files work for it.

## Recommended: Render free web service (Docker)

| | |
|---|---|
| Cost | $0. No payment method needed for a free web service |
| Limits | 512 MB RAM, 0.1 vCPU; sleeps after 15 min idle, about 1 min cold start; 750 free instance-hours a month per workspace (enough for one always-reachable service); ephemeral disk |
| HTTPS URL | `https://kareshafaa.onrender.com` (or the next free name), managed TLS, so the PWA is installable |
| Account | A Render account (sign up with GitHub), plus a **GitHub (or GitLab/Bitbucket) repo** holding this `app/` folder |

**One step once signed in:** push this folder to a GitHub repo. Then in Render choose **New -> Blueprint**, pick the
repo and click **Apply**. `render.yaml` sets everything: Docker build, free plan, `/healthz` health check, public
demo mode, a generated `KARESHAFAA_SECRET_KEY`, and one worker. The Dockerfile seeds the synthetic database and its
seed copy at build time, so a cold start does not need to generate data or train models.

## Alternatives

| Host | Free? | Fit | Account needed |
|---|---|---|---|
| **Google Cloud Run** | Always-free tier: 2 M requests, 180k vCPU-s and 360k GiB-s a month (request-based billing) | Best performance (scales to zero, a few seconds cold start, 1-2 GB RAM possible) | Google account **with a billing account (card)** |
| **Koyeb** | One free web service: 512 MB, 0.1 vCPU, Frankfurt/Washington, scales to zero after 1 h idle | Similar to Render | Koyeb account; may ask for a card |
| **Hugging Face Spaces (Docker)** | No longer free for Docker: on `cpu-basic`, Docker/Gradio Spaces need a **PRO subscription** (about $9/month) for personal accounts | Good if PRO already exists; config in `deploy/huggingface/README.md` | HF account + PRO |
| **Fly.io** | No free tier for new organisations (trial only) | - | Card |
| **Railway** | Trial credit only, then paid | - | Card after trial |

Cloud Run (after `gcloud auth login` and picking a project with billing):

```sh
gcloud run deploy kareshafaa --source . --region europe-west1 --allow-unauthenticated \
  --memory 1Gi --set-env-vars KARESHAFAA_PUBLIC_DEMO=1,WEB_WORKERS=2,KARESHAFAA_SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
```

Koyeb (CLI after `koyeb login`): `koyeb app init kareshafaa --docker <image> --instance-type free --ports 8000:http
--routes /:8000 --env KARESHAFAA_PUBLIC_DEMO=1 --env WEB_WORKERS=1`. This needs an image pushed to a registry,
or use the "GitHub repo + Dockerfile" option in the Koyeb UI.

## Not for real patient data

None of these free tiers is suitable for real health data. A pilot would need UAE data residency and a hosting
agreement with the health authority, not a free hobby tier.

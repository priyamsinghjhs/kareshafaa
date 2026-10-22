---
title: KareShafaa
emoji: 🩺
colorFrom: green
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
license: other
short_description: Screening-to-care loop prototype (synthetic data only)
---

# KareShafaa - public demo (synthetic data only, not for clinical use)

Hugging Face Docker Space config. Copy this file to the root of the Space repo as `README.md`, together with the app's
`Dockerfile`, `requirements.txt` and `kareshafaa/`. In Space Settings -> Variables add `KARESHAFAA_PUBLIC_DEMO=1`
and `WEB_WORKERS=2`, and a Secret `KARESHAFAA_SECRET_KEY` (any long random string).

Note (checked 22 Oct 2026): Docker Spaces on the free `cpu-basic` hardware now require a PRO subscription for personal
accounts (creating one without it fails with HTTP 402). The container must run as UID 1000 on Spaces; if the build
complains about permissions, change `--uid 10001` to `--uid 1000` in the Dockerfile.

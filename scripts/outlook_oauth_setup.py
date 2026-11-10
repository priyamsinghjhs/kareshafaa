"""One-time sign-in for the app mailbox (default kareshafaa_com@outlook.com) so KareShafaa can send e-mail.

Personal Outlook.com mailboxes refuse password sign-in; they need OAuth2. This script runs the Microsoft "device
code" sign-in on YOUR computer (standard library only), then prints a refresh token to paste into the host's
environment as KARESHAFAA_MAIL_OAUTH_REFRESH_TOKEN. Nothing is written to disk or sent anywhere except Microsoft
(and, only with --test-to, one test e-mail).

The app sends through Microsoft Graph over HTTPS by default (Render's free plan blocks SMTP ports), so the default
permission is Graph "Mail.Send". Use --transport smtp only if the server sends over SMTP (KARESHAFAA_MAIL_TRANSPORT=smtp).

Before you run it (free, once): register an app in the Microsoft Entra admin centre / Azure portal
  App registrations -> New registration -> "Personal Microsoft accounts only" (or "...and personal accounts")
  -> Authentication: "Allow public client flows" = Yes
  -> API permissions: Microsoft Graph -> Delegated -> "Mail.Send" and "offline_access"
     (for --transport smtp: Office 365 Exchange Online "SMTP.Send" instead)
Copy its Application (client) ID.

Run:   python scripts/outlook_oauth_setup.py --client-id <APPLICATION_CLIENT_ID>
Then:  sign in at the address shown AS kareshafaa_com@outlook.com, approve, and copy the two values printed at the end
       into Render -> Environment: KARESHAFAA_MAIL_OAUTH_CLIENT_ID and KARESHAFAA_MAIL_OAUTH_REFRESH_TOKEN (secret).
The refresh token is a password-equivalent secret: do not commit it, paste it in chat or share it.
"""
from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SCOPES = {"graph": "https://graph.microsoft.com/Mail.Send offline_access",
          "smtp": "https://outlook.office.com/SMTP.Send offline_access"}


def _post(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed Microsoft endpoint
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-id", required=True, help="Application (client) ID of your Entra app registration")
    ap.add_argument("--tenant", default="consumers", help="'consumers' for outlook.com/hotmail (default)")
    ap.add_argument("--mailbox", default="kareshafaa_com@outlook.com", help="mailbox to sign in as")
    ap.add_argument("--transport", choices=("graph", "smtp"), default="graph", help="graph (default) or smtp")
    ap.add_argument("--test-to", help="graph: send one test e-mail to this address after signing in")
    ap.add_argument("--no-check", action="store_true", help="smtp: skip the SMTP sign-in check")
    a = ap.parse_args()
    base = f"https://login.microsoftonline.com/{urllib.parse.quote(a.tenant, safe='')}/oauth2/v2.0"
    dc = _post(f"{base}/devicecode", {"client_id": a.client_id, "scope": SCOPES[a.transport]})
    if "device_code" not in dc:
        print("Microsoft refused the request:", dc.get("error"), "-", (dc.get("error_description") or "")[:300])
        return 1
    print(f"\n1. Open {dc['verification_uri']}\n2. Enter the code {dc['user_code']}\n"
          f"3. Sign in as {a.mailbox} and approve sending e-mail.\nWaiting for you to finish...\n")
    interval, deadline = int(dc.get("interval", 5)), time.time() + int(dc.get("expires_in", 900))
    tok: dict = {}
    while time.time() < deadline:
        time.sleep(interval)
        tok = _post(f"{base}/token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                       "client_id": a.client_id, "device_code": dc["device_code"]})
        if tok.get("error") == "authorization_pending":
            continue
        if tok.get("error") == "slow_down":
            interval += 5
            continue
        break
    if "refresh_token" not in tok:
        print("Sign-in did not complete:", tok.get("error"), "-", (tok.get("error_description") or "")[:300])
        return 1
    if a.transport == "graph" and a.test_to:
        body = {"message": {"subject": "KareShafaa: test email", "body": {"contentType": "Text", "content":
                "Sign-in for the KareShafaa app mailbox worked. No patient data."},
                "toRecipients": [{"emailAddress": {"address": a.test_to}}]}, "saveToSentItems": True}
        req = urllib.request.Request("https://graph.microsoft.com/v1.0/me/sendMail", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "Authorization": f"Bearer {tok['access_token']}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                print(f"Test e-mail accepted by Microsoft Graph (HTTP {r.status}).")
        except urllib.error.HTTPError as e:
            print(f"Test e-mail FAILED (HTTP {e.code}); the token is still printed below.")
    if a.transport == "smtp" and not a.no_check:
        try:
            with smtplib.SMTP("smtp-mail.outlook.com", 587, timeout=20) as s:
                s.ehlo(); s.starttls(context=ssl.create_default_context()); s.ehlo()
                s.auth("XOAUTH2", lambda c=None: f"user={a.mailbox}\x01auth=Bearer {tok['access_token']}\x01\x01")
            print("SMTP sign-in check: OK (no e-mail was sent).")
        except smtplib.SMTPAuthenticationError as e:
            print(f"SMTP sign-in check FAILED (code {e.smtp_code}). If it mentions 5.7.139, Outlook has SMTP sending "
                  "disabled for this mailbox; the token is still printed below.")
        except OSError as e:
            print(f"SMTP sign-in check could not connect ({type(e).__name__}); the token is still printed below.")
    print("\nSet these on the server (Render -> Environment), then redeploy:\n")
    print(f"KARESHAFAA_MAIL_OAUTH_CLIENT_ID={a.client_id}")
    if a.transport == "smtp":
        print("KARESHAFAA_MAIL_TRANSPORT=smtp")
    print(f"KARESHAFAA_MAIL_OAUTH_REFRESH_TOKEN={tok['refresh_token']}")
    print("\nKeep the refresh token secret. Run this script again if e-mail later reports the sign-in expired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

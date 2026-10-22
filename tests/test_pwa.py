"""Installable PWA: manifest, root-scope service worker, offline page; the SW never caches patient data."""
from __future__ import annotations

import json
import re


def test_manifest_is_installable(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["name"] == m["short_name"] == "KareShafaa"
    assert m["start_url"] == "/" and m["scope"] == "/" and m["display"] == "standalone"
    assert re.fullmatch(r"#[0-9a-f]{6}", m["theme_color"]) and re.fullmatch(r"#[0-9a-f]{6}", m["background_color"])
    sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in m["icons"]:
        ir = client.get(icon["src"])
        assert ir.status_code == 200 and ir.headers["content-type"] == "image/png"
        w, h = int.from_bytes(ir.content[16:20], "big"), int.from_bytes(ir.content[20:24], "big")
        assert f"{w}x{h}" == icon["sizes"]


def test_brand_is_latin_only(client):
    m = client.get("/manifest.webmanifest").json()
    html = client.get("/").text
    header = html.split("</header>")[0]
    for text in (json.dumps(m, ensure_ascii=False), header):
        assert not re.search(r"[\u0600-\u06FF]", text), "no Arabic script in the mark / header"


def test_base_template_links_manifest_and_meta(client):
    html = client.get("/").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert '<meta name="theme-color"' in html and 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'rel="apple-touch-icon" href="/static/icons/apple-touch-icon-180.png"' in html
    assert client.get("/static/icons/apple-touch-icon-180.png").status_code == 200
    assert 'id="install-app"' in html and "/static/js/pwa.js" in html
    js = client.get("/static/js/pwa.js").text
    assert "beforeinstallprompt" in js and 'register("/sw.js", { scope: "/" })' in js


def _shell(sw: str) -> list[str]:
    return json.loads(re.search(r"const SHELL = (\[.*?\]);", sw).group(1))


def test_service_worker_served_at_root_scope(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    assert r.headers["service-worker-allowed"] == "/"
    assert "no-cache" in r.headers["cache-control"]
    shell = _shell(r.text)
    assert "/static/kareshafaa.css" in shell and "/static/js/common.js" in shell
    assert "/static/icons/icon-192.png" in shell
    for url in shell:
        assert client.get(url).status_code == 200, url


def test_service_worker_never_caches_patient_data_or_pages(client):
    sw = client.get("/sw.js").text
    shell = _shell(sw)
    # only static assets are precached (plus the offline page); no API, FHIR, HTML page or sample data
    assert all(u.startswith("/static/") for u in shell)
    assert not any("/samples/" in u for u in shell)
    assert json.loads(re.search(r"const NEVER_CACHE = (\[.*?\]);", sw).group(1)) == ["/api/", "/fhir"]
    # cache.put is only ever reached inside the SHELL branch; navigations fall back to the offline page only
    assert sw.count(".put(") == 1
    put_at, shell_branch = sw.index(".put("), sw.index("if (SHELL.includes(url.pathname))")
    assert put_at > shell_branch
    assert sw.index("NEVER_CACHE.some") < shell_branch and sw.index('req.mode === "navigate"') < shell_branch
    assert 'if (req.method !== "GET") return;' in sw


def test_offline_page(client):
    r = client.get("/offline")
    assert r.status_code == 200 and "You are offline" in r.text and "KareShafaa" in r.text
    assert "/api/" not in r.text  # the offline page loads no data


def test_pwa_routes_are_public_and_not_audited(client, db):
    from sqlalchemy import func, select

    from kareshafaa.models import AuditEvent

    before = db.scalar(select(func.count()).select_from(AuditEvent))
    for p in ("/manifest.webmanifest", "/sw.js", "/offline"):
        assert client.get(p, cookies={}).status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == before

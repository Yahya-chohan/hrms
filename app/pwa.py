"""Installable-app support: manifest, service worker, icons, Android digital-asset-links."""
import json
import os
import struct
import zlib

from . import db as D
from .web import Response

_ICONS = {}

SW_JS = """// Minimal service worker: makes the site installable. Pages are never cached (they contain private data).
self.addEventListener('install', function(e){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function(e){
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(function(){
      return new Response('<meta name=viewport content="width=device-width"><body style="font-family:sans-serif;padding:24px"><h2>No internet</h2><p>Check-in and the rest of the app need an internet connection. Please reconnect and try again.</p>', {headers: {'Content-Type': 'text/html'}});
    }));
  }
});
"""


def _png(size, maskable):
    """Draw a simple 'person' glyph on a teal background (pure Python, no libraries)."""
    key = (size, maskable)
    if key in _ICONS:
        return _ICONS[key]
    bg = (15, 118, 110)
    scale = 0.72 if maskable else 1.0  # maskable icons need a safe zone
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            u, v = x / size, y / size
            # background shape: full square when maskable, else rounded square (transparent corners)
            a_bg = 1.0
            if not maskable:
                cx, cy, rad = min(max(u, 0.2), 0.8), min(max(v, 0.2), 0.8), 0.2
                sd = ((u - cx) ** 2 + (v - cy) ** 2) ** 0.5 - rad
                a_bg = max(0.0, min(1.0, 0.5 - sd * size))
            gu, gv = (u - 0.5) / scale + 0.5, (v - 0.5) / scale + 0.5
            head = ((gu - 0.5) ** 2 + (gv - 0.34) ** 2) ** 0.5 - 0.13
            ex = ((gu - 0.5) / 0.27) ** 2 + ((gv - 0.82) / 0.30) ** 2
            body = (ex ** 0.5 - 1.0) * 0.27 if gv <= 0.82 else 1.0
            sd_g = min(head, body)
            a_g = max(0.0, min(1.0, 0.5 - sd_g * size * scale))
            r = int(bg[0] + (255 - bg[0]) * a_g)
            g = int(bg[1] + (255 - bg[1]) * a_g)
            b = int(bg[2] + (255 - bg[2]) * a_g)
            row += bytes((r, g, b, int(255 * a_bg)))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    _ICONS[key] = png
    return png


def asset(method, path, conn):
    """Return a Response for public app-support files, or None."""
    if method != "GET":
        return None
    day = [("Cache-Control", "public, max-age=86400")]
    if path == "/manifest.webmanifest":
        name = D.get_setting(conn, "company_name") or "HRMS"
        m = {
            "name": name + " HRMS", "short_name": "HRMS", "description": "Attendance, leave and payslips", "id": "/", "start_url": "/",
            "scope": "/", "display": "standalone", "orientation": "portrait", "background_color": "#f4f6f8", "theme_color": "#0f766e",
            "icons": [
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
            "shortcuts": [{"name": "Check in / out", "url": "/my/attendance"}],
        }
        return Response(json.dumps(m), ctype="application/manifest+json", headers=day)
    if path == "/sw.js":
        return Response(SW_JS, ctype="application/javascript; charset=utf-8", headers=[("Cache-Control", "no-cache")])
    if path == "/icon-192.png":
        return Response(_png(192, False), ctype="image/png", headers=day)
    if path == "/icon-512.png":
        return Response(_png(512, False), ctype="image/png", headers=day)
    if path == "/icon-maskable-512.png":
        return Response(_png(512, True), ctype="image/png", headers=day)
    if path == "/.well-known/assetlinks.json":
        pkg = os.environ.get("HRMS_ANDROID_PACKAGE", "").strip()
        fps = [x.strip() for x in os.environ.get("HRMS_ANDROID_SHA256", "").split(",") if x.strip()]
        data = []
        if pkg and fps:
            data = [{"relation": ["delegate_permission/common.handle_all_urls"],
                     "target": {"namespace": "android_app", "package_name": pkg, "sha256_cert_fingerprints": fps}}]
        return Response(json.dumps(data), ctype="application/json", headers=[("Cache-Control", "no-cache")])
    return None

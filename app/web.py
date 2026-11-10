"""Tiny web framework: routing, sessions, CSRF, layout, HTML helpers."""
import hmac
import html
import os
import re
import secrets
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import db as D

ROUTES = []
SESSION_TTL = 8 * 3600
LOGIN_FAILS = {}  # key -> (count, first_ts)


class Raw(str):
    """Marks a string as already-safe HTML."""


def esc(x):
    return html.escape("" if x is None else str(x), quote=True)


def cell(x):
    return x if isinstance(x, Raw) else esc(x)


def H(*parts):
    """Join parts into safe HTML: Raw parts pass through, everything else is escaped."""
    return Raw("".join(cell(p) for p in parts))


class HTTPError(Exception):
    def __init__(self, code, msg=""):
        Exception.__init__(self, msg)
        self.code = code
        self.msg = msg


def abort(code, msg=""):
    raise HTTPError(code, msg)


class Response:
    def __init__(self, body=b"", status=200, ctype="text/html; charset=utf-8", headers=None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.ctype = ctype
        self.headers = list(headers or [])


def redirect(url):
    return Response(b"", 303, headers=[("Location", url)])


def route(method, pattern, roles=None):
    rx = re.compile("^" + pattern + "$")

    def deco(f):
        ROUTES.append((method, rx, f, roles))
        return f

    return deco


class Request:
    def __init__(self, conn, method, path, query, form, cookies, ip):
        self.conn = conn
        self.method = method
        self.path = path
        self.query = query
        self.form = form
        self.cookies = cookies
        self.ip = ip
        self.user = None
        self.sid = None
        self.csrf = ""
        self.perms = set()

    def q(self, k, d=""):
        return self.query.get(k, [d])[0]

    def f(self, k, d="", strip=True):
        v = self.form.get(k, [d])[0]
        return v.strip() if strip else v

    def fl(self, k):
        return [v.strip() for v in self.form.get(k, [])]

    def flash(self, msg, kind="ok"):
        if self.sid:
            self.conn.execute("UPDATE sessions SET flash=? WHERE token=?", (kind + "|" + msg, self.sid))

    @property
    def role(self):
        return self.user["role"] if self.user else None

    def has_role(self, *keys):
        """True for admin, or when the user has any of the given access rights."""
        return self.role == "admin" or any(k in self.perms for k in keys)


# ------------------------------------------------------------ sessions
def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions(token,user_id,csrf,expires) VALUES(?,?,?,?)",
        (token, user_id, secrets.token_urlsafe(24), time.time() + SESSION_TTL),
    )
    return token


def load_session(req):
    token = req.cookies.get("sid")
    conn = req.conn
    conn.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
    if not token:
        return
    r = conn.execute(
        "SELECT s.token, s.csrf, u.* FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token=? AND u.active=1",
        (token,),
    ).fetchone()
    if r:
        req.user = r
        req.sid = token
        req.csrf = r["csrf"]
        req.perms = D.user_perms(r)
        conn.execute("UPDATE sessions SET expires=? WHERE token=?", (time.time() + SESSION_TTL, token))


def csrf_field(req):
    return Raw('<input type="hidden" name="csrf" value="%s">' % esc(req.csrf))


# ------------------------------------------------------------ html helpers
def inp(label, name, value="", type="text", required=False, extra="", hint=""):
    return Raw(
        '<label class="fld"><span>%s%s</span><input type="%s" name="%s" value="%s"%s %s>%s</label>'
        % (esc(label), " *" if required else "", type, esc(name), esc(value), " required" if required else "", extra,
           ('<small>%s</small>' % esc(hint)) if hint else "")
    )


def textarea(label, name, value="", rows=3):
    return Raw('<label class="fld"><span>%s</span><textarea name="%s" rows="%d">%s</textarea></label>'
               % (esc(label), esc(name), rows, esc(value)))


def sel(label, name, options, selected=None, required=False, blank=None, extra=""):
    out = []
    if blank is not None:
        out.append('<option value="">%s</option>' % esc(blank))
    for v, t in options:
        s = " selected" if selected is not None and str(v) == str(selected) else ""
        out.append('<option value="%s"%s>%s</option>' % (esc(v), s, esc(t)))
    return Raw('<label class="fld"><span>%s%s</span><select name="%s"%s %s>%s</select></label>'
               % (esc(label), " *" if required else "", esc(name), " required" if required else "", extra, "".join(out)))


def table(headers, rows, num_cols=(), empty="No records found."):
    if not rows:
        return Raw('<p class="muted">%s</p>' % esc(empty))
    th = "".join('<th class="%s">%s</th>' % ("num" if i in num_cols else "", esc(h)) for i, h in enumerate(headers))
    body = []
    for r in rows:
        body.append("<tr>" + "".join('<td class="%s">%s</td>' % ("num" if i in num_cols else "", cell(c)) for i, c in enumerate(r)) + "</tr>")
    return Raw('<div class="tw"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (th, "".join(body)))


def badge(text, kind="gray"):
    return Raw('<span class="badge %s">%s</span>' % (kind, esc(text)))


STATUS_KIND = {
    "active": "green", "inactive": "gray", "pending": "amber", "approved": "green", "rejected": "red",
    "cancelled": "gray", "draft": "amber", "posted": "blue", "paid": "green",
}


def status_badge(s):
    return badge(s.title(), STATUS_KIND.get(s, "gray"))


def post_form(req, action, inner, cls="", confirm=None):
    c = ' onsubmit="return confirm(\'%s\')"' % esc(confirm).replace("'", "\\'") if confirm else ""
    return Raw('<form method="post" action="%s" class="%s"%s>%s%s</form>' % (esc(action), cls, c, csrf_field(req), inner))


def btn(text, kind="primary", extra=""):
    return Raw('<button class="btn %s" %s>%s</button>' % (kind, extra, esc(text)))


def link_btn(text, href, kind="ghost"):
    return Raw('<a class="btn %s" href="%s">%s</a>' % (kind, esc(href), esc(text)))


def card(title, body, actions=""):
    return Raw('<section class="card"><div class="card-h"><h2>%s</h2><div>%s</div></div>%s</section>'
               % (esc(title), actions, body))


# ------------------------------------------------------------ layout
NAV = [
    ("Dashboard", "/", ("*any",), None),
    ("Employees", "/employees", ("emp_view",), "People"),
    ("Attendance", "/attendance", ("att_view",), "People"),
    ("Leave Requests", "/leave", ("leave_manage",), "People"),
    ("Payroll", "/payroll", ("pay_view",), "People"),
    ("Vouchers", "/accounts/vouchers", ("acc_view",), "Accounts"),
    ("Chart of Accounts", "/accounts/chart", ("acc_view",), "Accounts"),
    ("Ledger", "/accounts/ledger", ("acc_view",), "Accounts"),
    ("Trial Balance", "/accounts/trial", ("acc_view",), "Accounts"),
    ("Financial Reports", "/accounts/reports", ("acc_view",), "Accounts"),
    ("My Leave", "/my/leave", ("*emp",), "My Portal"),
    ("My Payslips", "/my/payslips", ("*emp",), "My Portal"),
    ("My Attendance", "/my/attendance", ("*emp",), "My Portal"),
    ("Users & Access", "/admin/users", ("admin",), "Admin"),
    ("Departments", "/admin/departments", ("admin",), "Admin"),
    ("Designations", "/admin/designations", ("admin",), "Admin"),
    ("Leave Types", "/admin/leave-types", ("admin",), "Admin"),
    ("Settings", "/admin/settings", ("admin",), "Admin"),
    ("Audit Log", "/admin/audit", ("admin",), "Admin"),
    ("Backup", "/admin/backup", ("admin",), "Admin"),
]


def nav_html(req):
    out = []
    last = None
    for label, url, roles, group in NAV:
        if "*emp" in roles:
            if not req.user["employee_id"]:
                continue
        elif "*any" in roles:
            pass
        elif not req.has_role(*roles):
            continue
        if group != last:
            if group:
                out.append('<div class="nav-g">%s</div>' % esc(group))
            last = group
        active = (req.path == url) or (url != "/" and req.path.startswith(url + "/")) or (url != "/" and req.path == url)
        out.append('<a href="%s" class="%s">%s</a>' % (url, "on" if active else "", esc(label)))
    return "".join(out)


def page(req, title, body, status=200):
    flash = ""
    r = req.conn.execute("SELECT flash FROM sessions WHERE token=?", (req.sid,)).fetchone()
    if r and r["flash"]:
        kind, _, msg = r["flash"].partition("|")
        flash = '<div class="flash %s">%s</div>' % (esc(kind), esc(msg))
        req.conn.execute("UPDATE sessions SET flash=NULL WHERE token=?", (req.sid,))
    company = D.get_setting(req.conn, "company_name")
    doc = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s · %s</title><link rel="stylesheet" href="/static/style.css"><link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#0f766e"><link rel="apple-touch-icon" href="/icon-192.png"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="HRMS"></head><body>
<div class="shell"><aside class="side" id="side"><div class="brand"><b>HRMS</b><span>%s</span></div>
<nav>%s</nav></aside>
<div class="main"><header class="top"><button class="burger" onclick="document.getElementById('side').classList.toggle('open')">&#9776;</button>
<div class="who"><span>%s <em>(%s)</em></span>
<a href="/change-password">Password</a>
<form method="post" action="/logout" class="inline">%s<button class="link">Logout</button></form></div></header>
<main class="content">%s<h1>%s</h1>%s</main></div></div><script>if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){})}</script></body></html>""" % (
        esc(title), esc(company), esc(company), nav_html(req), esc(req.user["username"]), esc(req.role),
        csrf_field(req), flash, esc(title), body)
    return Response(doc, status)


def bare_page(title, body, status=200):
    doc = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>
<link rel="stylesheet" href="/static/style.css"><link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#0f766e"><link rel="apple-touch-icon" href="/icon-192.png"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="HRMS"></head><body class="bare">%s<script>if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){})}</script></body></html>""" % (esc(title), body)
    return Response(doc, status)


CSS = """
:root{--bg:#f4f6f8;--card:#fff;--ink:#17212b;--mut:#6b7885;--line:#e2e7ec;--pri:#0f766e;--pri-d:#0b5a54;--side:#10242a;--red:#b42318;--amb:#b45309;--grn:#067647;--blu:#175cd3}
*{box-sizing:border-box}body{margin:0;font:14.5px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
a{color:var(--pri)}h1{font-size:22px;margin:0 0 16px}h2{font-size:16px;margin:0}
.shell{display:flex;min-height:100vh}.side{width:224px;background:var(--side);color:#cfe3e1;flex:none;padding:16px 0;position:sticky;top:0;height:100vh;overflow:auto}
.brand{padding:0 18px 14px;border-bottom:1px solid #ffffff1a;margin-bottom:8px}.brand b{display:block;font-size:20px;color:#fff;letter-spacing:.5px}.brand span{font-size:12px;opacity:.7}
.side a{display:block;padding:7px 18px;color:#cfe3e1;text-decoration:none;border-left:3px solid transparent}.side a:hover{background:#ffffff0f}.side a.on{background:#ffffff1a;border-color:#2dd4bf;color:#fff}
.nav-g{padding:12px 18px 4px;font-size:11px;text-transform:uppercase;letter-spacing:1px;opacity:.5}
.main{flex:1;min-width:0}.top{display:flex;justify-content:space-between;align-items:center;padding:10px 24px;background:#fff;border-bottom:1px solid var(--line)}
.who{display:flex;gap:14px;align-items:center;margin-left:auto}.who em{color:var(--mut);font-style:normal}.inline{display:inline;margin:0}
.burger{display:none;border:0;background:none;font-size:22px}.content{padding:22px 24px;max-width:1280px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:18px}.card-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:10px;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:18px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.stat b{display:block;font-size:24px}.stat span{color:var(--mut);font-size:13px}
.form{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;align-items:start}.fld{display:flex;flex-direction:column;gap:4px}.fld span{font-size:12.5px;color:var(--mut);font-weight:600}
.btn.big{font-size:20px;padding:18px 22px;width:100%;text-align:center;border-radius:12px}.fld small{color:var(--mut)}.form>.btn{justify-self:start}input,select,textarea{font:inherit;padding:8px 10px;border:1px solid #c9d2da;border-radius:7px;background:#fff;width:100%}
input:focus,select:focus,textarea:focus{outline:2px solid #99e0d8;border-color:var(--pri)}
.btn{display:inline-block;font:inherit;font-weight:600;padding:8px 14px;border-radius:7px;border:1px solid transparent;cursor:pointer;text-decoration:none;line-height:1.2}
.btn.primary{background:var(--pri);color:#fff}.btn.primary:hover{background:var(--pri-d)}.btn.ghost{background:#fff;border-color:#c9d2da;color:var(--ink)}.btn.ghost:hover{background:#f1f5f8}
.btn.danger{background:#fff;border-color:#f0b4ae;color:var(--red)}.btn.sm{padding:4px 9px;font-size:13px}button.link{background:none;border:0;color:var(--pri);cursor:pointer;font:inherit;padding:0}
.tw{overflow:auto}table{border-collapse:collapse;width:100%}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}th{font-size:12px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);background:#fafbfc;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}tr:hover td{background:#fafcfc}tfoot td{font-weight:700;background:#fafbfc}
.badge{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600}.badge.green{background:#dcfae6;color:var(--grn)}.badge.amber{background:#fef0c7;color:var(--amb)}.badge.red{background:#fee4e2;color:var(--red)}.badge.blue{background:#d1e9ff;color:var(--blu)}.badge.gray{background:#eef1f4;color:#475467}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:14px}.flash.ok{background:#dcfae6;color:#054f31}.flash.err{background:#fee4e2;color:#7a271a}
.muted{color:var(--mut)}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:14px}.bar .fld{min-width:150px}
.kv{display:grid;grid-template-columns:150px 1fr;gap:6px 12px}.kv dt{color:var(--mut)}.kv dd{margin:0}
.bare{display:flex;align-items:center;justify-content:center;min-height:100vh;background:linear-gradient(135deg,#10242a,#0f766e)}
.login{background:#fff;border-radius:14px;padding:30px;width:min(380px,92vw);box-shadow:0 20px 50px #0004}.login h1{margin-bottom:4px}.login .fld{margin-bottom:12px}.login .btn{width:100%}
.slip{max-width:760px;background:#fff;border:1px solid var(--line);padding:26px;border-radius:10px}.slip h2{font-size:20px}.slip .hd{display:flex;justify-content:space-between;border-bottom:2px solid var(--ink);padding-bottom:10px;margin-bottom:14px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:20px}.seg label{margin-right:10px;white-space:nowrap}.seg input{width:auto}
@media(max-width:860px){.side{position:fixed;left:-240px;z-index:9;transition:left .2s}.side.open{left:0}.burger{display:block}.content{padding:16px}.two{grid-template-columns:1fr}}
@media print{.side,.top,.noprint,.flash,h1.pt{display:none!important}body{background:#fff}.content{padding:0;max-width:none}.slip{border:0;box-shadow:none}}
"""


# ------------------------------------------------------------ server
class Handler(BaseHTTPRequestHandler):
    server_version = "HRMS"
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")

    def _send(self, resp):
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.ctype)
        self.send_header("Content-Length", str(len(resp.body)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if not any(k.lower() == "cache-control" for k, _ in resp.headers):
            self.send_header("Cache-Control", "no-store")
        for k, v in resp.headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp.body)

    def _go(self, method):
        n = 0
        if method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > MAX_BODY:
                return self._send(Response("Request too large", 413))
        body = self.rfile.read(n) if n else b""
        ip = client_ip(self.headers.get("X-Forwarded-For"), self.client_address[0])
        self._send(dispatch(method, self.path, body, self.headers.get("Cookie") or "", ip))


MAX_BODY = 2 * 1024 * 1024


def client_ip(forwarded, remote):
    """Behind a trusted reverse proxy (HRMS_TRUST_PROXY=1) use the address the proxy appended."""
    if forwarded and os.environ.get("HRMS_TRUST_PROXY") == "1":
        return forwarded.split(",")[-1].strip()
    return remote


def dispatch(method, rawpath, body, cookie_header, ip):
    """Server-independent request handling: returns a Response."""
    parsed = urllib.parse.urlparse(rawpath)
    path = parsed.path
    if len(path) > 1:
        path = path.rstrip("/")
    if method == "GET" and path == "/static/style.css":
        r = Response(CSS, ctype="text/css; charset=utf-8")
        r.headers.append(("Cache-Control", "max-age=300"))
        return r
    form = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True) if method == "POST" else {}
    cookies = {}
    for part in cookie_header.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    conn = D.connect()
    try:
        from . import pwa
        resp = pwa.asset(method, path, conn)
        if resp is None:
            req = Request(conn, method, path, urllib.parse.parse_qs(parsed.query), form, cookies, ip)
            resp = handle(req)
        conn.commit()
    except HTTPError as e:
        conn.rollback()
        resp = error_page(e.code, e.msg)
    except Exception:
        conn.rollback()
        traceback.print_exc()
        resp = error_page(500, "Something went wrong on the server.")
    finally:
        conn.close()
    return resp


def error_page(code, msg):
    titles = {403: "Not allowed", 404: "Page not found", 413: "Too large", 500: "Server error"}
    body = '<div class="login"><h1>%s</h1><p class="muted">%s</p><a class="btn primary" href="/">Go to dashboard</a></div>' % (
        esc(titles.get(code, "Error")), esc(msg))
    return bare_page("Error", body, code)


PUBLIC = ("/login", "/privacy")


def handle(req):
    load_session(req)
    matched = None
    path_exists = False
    for method, rx, f, roles in ROUTES:
        m = rx.match(req.path)
        if m:
            path_exists = True
            if method == req.method:
                matched = (f, roles, m.groupdict())
                break
    if not matched:
        abort(405 if path_exists else 404, "Method not allowed." if path_exists else "No such page.")
    f, roles, kwargs = matched
    if req.path not in PUBLIC:
        if not req.user:
            return redirect("/login")
        if req.user["must_change"] and req.path not in ("/change-password", "/logout"):
            return redirect("/change-password")
        if roles is not None and not req.has_role(*roles):
            abort(403, "Your role does not have access to this page.")
        if req.method == "POST":
            if not hmac.compare_digest(req.f("csrf", strip=False), req.csrf):
                abort(403, "Security token expired. Go back, refresh the page and try again.")
    return f(req, **kwargs)

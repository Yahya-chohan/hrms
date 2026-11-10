"""Admin: users, master data, settings, audit log, backup."""
import json
import os
import sqlite3
import tempfile
from decimal import Decimal, InvalidOperation

from . import db as D
from .web import (H, Raw, Response, abort, badge, btn, card, esc, inp, link_btn, page, post_form, redirect, route, sel, table)
from .views_core import fail

ADM = ("admin",)


# ----------------------------------------------------------------- users
ROLE_CHOICES = [("admin", "Admin (everything)"), ("hr", "HR"), ("accountant", "Accountant"), ("employee", "Employee (own pages only)")]


def perm_summary(u):
    if u["role"] == "admin":
        return "Everything"
    p = [k for k in D.PERM_KEYS if k in D.user_perms(u)]
    return "Own pages only" if not p else ", ".join(D.PERM_LABELS[k].split(" / ")[0].split(" and ")[0] for k in p)


@route("GET", r"/admin/users", ADM)
def users(req):
    c = req.conn
    rows = []
    for u in c.execute("SELECT u.*, e.name ename, e.code ecode FROM users u LEFT JOIN employees e ON e.id=u.employee_id ORDER BY u.id"):
        acts = H(Raw('<a class="btn ghost sm" href="/admin/users/%d/access">Access</a> ' % u["id"]))
        if u["id"] != req.user["id"]:
            acts = H(acts, post_form(req, "/admin/users/%d/toggle" % u["id"], Raw('<button class="btn ghost sm">%s</button>' % ("Disable" if u["active"] else "Enable")), "inline"), " ",
                     post_form(req, "/admin/users/%d/reset" % u["id"], Raw('<input name="pw" placeholder="New password" style="width:130px"> <button class="btn ghost sm">Reset</button>'), "inline"))
        rows.append([u["username"], dict(ROLE_CHOICES).get(u["role"], u["role"]).split(" (")[0], (u["ecode"] + " " + u["ename"]) if u["ename"] else "—",
                     Raw('<small>%s</small>' % esc(perm_summary(u))), badge("Active", "green") if u["active"] else badge("Disabled", "gray"), acts])
    emps = [(e["id"], "%s — %s" % (e["code"], e["name"])) for e in c.execute("SELECT * FROM employees WHERE status='active' ORDER BY code")]
    new = post_form(req, "/admin/users/new", H(inp("Username", "username", "", required=True), sel("Start from", "role", ROLE_CHOICES, "hr", True),
                                                sel("Employee record (so they can also use their own leave / attendance / payslip)", "employee_id", emps, blank="— none —"),
                                                inp("Temporary password", "pw", "", "text", True, hint="Min 8 chars. User must change at first login."),
                                                Raw(btn("Create user"))), "form")
    note = Raw('<p class="muted">Pick HR or Accountant to start from a ready set of rights, then fine-tune exactly what this person may do on the <b>Access</b> page. '
               'Employees always see only their own leave, attendance and payslips.</p>')
    return page(req, "Users & Access", H(note, card("User accounts", table(["Username", "Role", "Employee", "Can do", "Status", ""], rows)), card("Create user", new)))


def admin_count(c):
    return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]


@route("POST", r"/admin/users/new", ADM)
def user_new(req):
    c = req.conn
    un, role, pw = req.f("username"), req.f("role"), req.f("pw", strip=False)
    if not un or role not in D.ROLES:
        return fail(req, "Username and role are required.", "/admin/users")
    if len(pw) < 8:
        return fail(req, "Password must be at least 8 characters.", "/admin/users")
    eid = int(req.f("employee_id")) if req.f("employee_id").isdigit() else None
    if role == "employee" and not eid:
        return fail(req, "An employee login must be linked to an employee record.", "/admin/users")
    if c.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
        return fail(req, "Username already exists.", "/admin/users")
    perms = None if role == "admin" else ",".join(D.PRESETS[role])
    uid = c.execute("INSERT INTO users(username,pw_hash,role,employee_id,must_change,perms,created) VALUES(?,?,?,?,1,?,?)",
                    (un, D.hash_password(pw), role, eid, perms, D.now_local(c).strftime("%Y-%m-%d %H:%M"))).lastrowid
    D.audit(c, req.user["username"], "user_created", "%s (%s)" % (un, role))
    req.flash("User %s created. Check what they may do below." % un)
    return redirect("/admin/users/%d/access" % uid)


def get_user(req, uid):
    u = req.conn.execute("SELECT u.*, e.name ename, e.code ecode FROM users u LEFT JOIN employees e ON e.id=u.employee_id WHERE u.id=?", (uid,)).fetchone()
    if not u:
        abort(404, "User not found.")
    return u


@route("GET", r"/admin/users/(?P<uid>\d+)/access", ADM)
def user_access(req, uid):
    u = get_user(req, uid)
    me = u["id"] == req.user["id"]
    cur = D.user_perms(u)
    head = Raw("<dl class='kv'><dt>Username</dt><dd>%s</dd><dt>Employee</dt><dd>%s</dd></dl>"
               % (esc(u["username"]), esc((u["ecode"] + " " + u["ename"]) if u["ename"] else "— not linked —")))
    if u["role"] == "admin":
        note = Raw('<p>Admin has <b>full access</b> to everything, including users, settings and backup.</p>')
        body = H(note, "" if me else post_form(req, "/admin/users/%d/access" % u["id"], H(sel("Change role to", "role", ROLE_CHOICES, "admin"), Raw(btn("Change role", "ghost"))), "form",
                                              confirm="Change this admin's role? They will lose admin rights."))
        return page(req, "Access — %s" % u["username"], H(head, card("Rights", body)))
    groups = []
    for gname, items in D.PERM_GROUPS:
        boxes = "".join('<label style="display:flex;gap:8px;align-items:flex-start;margin:6px 0"><input type="checkbox" name="perm" value="%s" style="width:auto;margin-top:4px"%s> <span>%s</span></label>'
                        % (k, " checked" if k in cur else "", esc(lbl)) for k, lbl in items)
        groups.append('<div><h3 style="margin:0 0 4px">%s</h3>%s</div>' % (esc(gname), boxes))
    presets = " ".join('<button type="button" class="btn ghost sm" onclick="preset(\'%s\')">%s</button>' % (k, esc(lbl.split(" (")[0])) for k, lbl in ROLE_CHOICES if k != "admin")
    js = ("<script>var P=%s;function preset(r){document.querySelectorAll('input[name=perm]').forEach(function(i){i.checked=P[r].indexOf(i.value)>=0})}</script>"
          % json.dumps({k: D.PRESETS[k] for k in ("hr", "accountant", "employee")}))
    own = '<p class="muted">Everyone with a linked employee record can always use <b>My Leave, My Attendance (check in/out) and My Payslips</b> in addition to the rights below.</p>' if u["employee_id"] else \
          '<p class="muted">This user is not linked to an employee record, so they have no personal leave/attendance/payslip pages.</p>'
    form = post_form(req, "/admin/users/%d/access" % u["id"], H(
        Raw('<div class="form" style="margin-bottom:10px">'), sel("Role label", "role", [r for r in ROLE_CHOICES if r[0] != "admin"] + [("admin", "Admin (everything)")], u["role"]), Raw("</div>"),
        Raw('<p class="muted">Quick fill: %s</p><div class="two" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">%s</div>' % (presets, "".join(groups))),
        Raw(own + "<p>%s %s</p>" % (btn("Save access"), link_btn("Back", "/admin/users")))))
    return page(req, "Access — %s" % u["username"], H(head, card("What this person may do", form), Raw(js)))


@route("POST", r"/admin/users/(?P<uid>\d+)/access", ADM)
def user_access_save(req, uid):
    c = req.conn
    u = get_user(req, uid)
    role = req.f("role") or u["role"]
    if role not in D.ROLES:
        return fail(req, "Invalid role.", "/admin/users/%s/access" % uid)
    if u["id"] == req.user["id"] and role != u["role"]:
        return fail(req, "You cannot change your own role.", "/admin/users/%s/access" % uid)
    if u["role"] == "admin" and role != "admin" and u["active"] and admin_count(c) <= 1:
        return fail(req, "At least one active admin is required.", "/admin/users/%s/access" % uid)
    if role == "employee" and not u["employee_id"] and False:
        pass
    if role == "admin":
        perms = None
    elif u["role"] == "admin":  # demoted: start from the new role's preset
        perms = ",".join(D.PRESETS[role])
    else:
        perms = ",".join(sorted(D.close_perms(req.fl("perm"))))
    c.execute("UPDATE users SET role=?, perms=? WHERE id=?", (role, perms, u["id"]))
    c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],)) if u["id"] != req.user["id"] else None
    D.audit(c, req.user["username"], "access_changed", "%s -> %s [%s]" % (u["username"], role, perms or "all"))
    req.flash("Access saved for %s." % u["username"])
    return redirect("/admin/users/%s/access" % uid)


@route("POST", r"/admin/users/(?P<uid>\d+)/toggle", ADM)
def user_toggle(req, uid):
    c = req.conn
    u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u or u["id"] == req.user["id"]:
        return fail(req, "You cannot disable your own account.", "/admin/users")
    if u["active"] and u["role"] == "admin" and admin_count(c) <= 1:
        return fail(req, "At least one active admin is required.", "/admin/users")
    c.execute("UPDATE users SET active=1-active WHERE id=?", (u["id"],))
    if u["active"]:
        c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    D.audit(c, req.user["username"], "user_toggled", u["username"])
    return redirect("/admin/users")


@route("POST", r"/admin/users/(?P<uid>\d+)/reset", ADM)
def user_reset(req, uid):
    c = req.conn
    u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    pw = req.f("pw", strip=False)
    if not u or len(pw) < 8:
        return fail(req, "Enter a new password of at least 8 characters.", "/admin/users")
    c.execute("UPDATE users SET pw_hash=?, must_change=1 WHERE id=?", (D.hash_password(pw), u["id"]))
    c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    D.audit(c, req.user["username"], "password_reset", u["username"])
    req.flash("Password reset for %s. They must change it at next login." % u["username"])
    return redirect("/admin/users")


# ------------------------------------------------- departments/designations
def simple_list(req, table_name, title, url, fk):
    c = req.conn
    rows = []
    for r in c.execute("SELECT * FROM %s ORDER BY name" % table_name):
        used = c.execute("SELECT COUNT(*) FROM employees WHERE %s=?" % fk, (r["id"],)).fetchone()[0]
        ren = post_form(req, url + "/%d/rename" % r["id"], Raw('<input name="name" value="%s" style="width:200px"> <button class="btn ghost sm">Rename</button>' % esc(r["name"])), "inline")
        dele = post_form(req, url + "/%d/delete" % r["id"], Raw('<button class="btn danger sm">Delete</button>'), "inline", confirm="Delete?") if not used else ""
        rows.append([ren, used, dele])
    new = post_form(req, url + "/new", H(inp("Name", "name", "", required=True), Raw(btn("Add"))), "form")
    return page(req, title, H(card(title, table(["Name", "Employees", ""], rows, (1,))), card("Add new", new)))


def simple_post(req, table_name, url, action, rid=None):
    c = req.conn
    name = req.f("name")
    try:
        if action == "new":
            if not name:
                raise ValueError("Name is required.")
            c.execute("INSERT INTO %s(name) VALUES(?)" % table_name, (name,))
        elif action == "rename":
            if not name:
                raise ValueError("Name is required.")
            c.execute("UPDATE %s SET name=? WHERE id=?" % table_name, (name, rid))
        else:
            fk = "dept_id" if table_name == "departments" else "desig_id"
            if c.execute("SELECT 1 FROM employees WHERE %s=?" % fk, (rid,)).fetchone():
                raise ValueError("Cannot delete: employees are using it.")
            c.execute("DELETE FROM %s WHERE id=?" % table_name, (rid,))
    except sqlite3.IntegrityError:
        return fail(req, "That name already exists.", url)
    except ValueError as e:
        return fail(req, str(e), url)
    D.audit(c, req.user["username"], "%s_%s" % (table_name, action), name)
    req.flash("Saved.")
    return redirect(url)


@route("GET", r"/admin/departments", ADM)
def depts(req):
    return simple_list(req, "departments", "Departments", "/admin/departments", "dept_id")


@route("POST", r"/admin/departments/new", ADM)
def dept_new(req):
    return simple_post(req, "departments", "/admin/departments", "new")


@route("POST", r"/admin/departments/(?P<i>\d+)/(?P<a>rename|delete)", ADM)
def dept_act(req, i, a):
    return simple_post(req, "departments", "/admin/departments", a, int(i))


@route("GET", r"/admin/designations", ADM)
def desigs(req):
    return simple_list(req, "designations", "Designations", "/admin/designations", "desig_id")


@route("POST", r"/admin/designations/new", ADM)
def desig_new(req):
    return simple_post(req, "designations", "/admin/designations", "new")


@route("POST", r"/admin/designations/(?P<i>\d+)/(?P<a>rename|delete)", ADM)
def desig_act(req, i, a):
    return simple_post(req, "designations", "/admin/designations", a, int(i))


# ------------------------------------------------------------ leave types
@route("GET", r"/admin/leave-types", ADM)
def leave_types(req):
    rows = []
    for t in req.conn.execute("SELECT * FROM leave_types ORDER BY id"):
        f = post_form(req, "/admin/leave-types/%d" % t["id"], Raw(
            '<input name="name" value="%s" style="width:160px"> <input name="days" type="number" min="0" max="365" value="%d" style="width:80px"> '
            '<label><input type="checkbox" name="paid" value="1" style="width:auto"%s> Paid</label> <button class="btn ghost sm">Save</button>'
            % (esc(t["name"]), t["days_per_year"], " checked" if t["paid"] else "")), "inline")
        rows.append([f])
    new = post_form(req, "/admin/leave-types/new", H(inp("Name", "name", "", required=True), inp("Days per year", "days", "0", "number", True, 'min="0" max="365"', "0 = no limit"),
                                                      Raw('<label class="fld"><span>Paid?</span><input type="checkbox" name="paid" value="1" checked style="width:auto"></label>'), Raw(btn("Add type"))), "form")
    return page(req, "Leave types", H(card("Leave types", table(["Name · days per year · paid"], rows)), card("Add leave type", new)))


@route("POST", r"/admin/leave-types/new", ADM)
def lt_new(req):
    return lt_save(req, None)


@route("POST", r"/admin/leave-types/(?P<tid>\d+)", ADM)
def lt_edit(req, tid):
    return lt_save(req, int(tid))


def lt_save(req, tid):
    name = req.f("name")
    days = req.f("days") or "0"
    if not name or not days.isdigit() or int(days) > 365:
        return fail(req, "Enter a name and a number of days (0-365).", "/admin/leave-types")
    paid = 1 if req.f("paid") == "1" else 0
    try:
        if tid:
            req.conn.execute("UPDATE leave_types SET name=?, days_per_year=?, paid=? WHERE id=?", (name, int(days), paid, tid))
        else:
            req.conn.execute("INSERT INTO leave_types(name,days_per_year,paid) VALUES(?,?,?)", (name, int(days), paid))
    except sqlite3.IntegrityError:
        return fail(req, "That leave type name already exists.", "/admin/leave-types")
    D.audit(req.conn, req.user["username"], "leave_type_saved", name)
    req.flash("Saved.")
    return redirect("/admin/leave-types")


# --------------------------------------------------------------- settings
@route("GET", r"/admin/settings", ADM)
def settings(req):
    c = req.conn
    g = lambda k: D.get_setting(c, k)
    f = post_form(req, "/admin/settings", H(
        inp("Company name", "company_name", g("company_name"), required=True), inp("Currency label", "currency", g("currency")),
        inp("Weekly off days", "weekly_off", g("weekly_off"), hint="Comma separated, e.g. Sun or Sat,Sun"),
        inp("Days in a salary month", "month_days", g("month_days"), "number", True, 'min="1" max="31"', "Absence deduction = gross ÷ this × absent days"),
        inp("Timezone offset from UTC (hours)", "tz_offset", g("tz_offset"), "number", True, 'step="0.5" min="-12" max="14"', "Pakistan = 5"),
        inp("Office start time", "office_start", g("office_start"), "time", True), inp("Late after (grace minutes)", "late_grace", g("late_grace"), "number", True, 'min="0" max="240"'),
        Raw('<label style="grid-column:1/-1;display:flex;gap:8px;align-items:center"><input type="checkbox" name="geofence" value="1" style="width:auto"%s> '
            'Employees can check in / out only near the office (needs the location below)</label>' % (" checked" if g("geofence") == "1" else "")),
        inp("Office latitude", "office_lat", g("office_lat"), "text", False, 'id="olat" inputmode="decimal"', "e.g. 31.52037"), inp("Office longitude", "office_lng", g("office_lng"), "text", False, 'id="olng" inputmode="decimal"', "e.g. 74.35875"),
        inp("Allowed distance (metres)", "office_radius", g("office_radius"), "number", True, 'min="20" max="5000"', "100-300 is typical"),
        Raw("<div><button type='button' class='btn ghost' onclick=\"navigator.geolocation.getCurrentPosition(function(p){document.getElementById('olat').value=p.coords.latitude.toFixed(6);document.getElementById('olng').value=p.coords.longitude.toFixed(6)},function(){alert('Could not get location. Allow location access.')},{enableHighAccuracy:true})\">Use my current location</button>"
            "<div class='muted' style='font-size:12.5px;margin-top:4px'>Stand inside the office, then tap this and Save.</div></div>"),
        inp("Contact email (shown in privacy policy)", "contact_email", g("contact_email"), "email"),
        inp("Provident fund % of basic", "pf_percent", g("pf_percent"), "number", True, 'step="0.001" min="0" max="100"', "Standard = 8.333"),
        Raw('<label class="fld"><span>Income tax</span><label><input type="checkbox" name="medical_exempt" value="1" style="width:auto"%s> '
            'Medical allowance exempt up to 10%% of basic (untick if employer gives free medical)</label></label>' % (" checked" if g("medical_exempt") == "1" else "")),
        Raw(btn("Save settings"))), "form")
    return page(req, "Settings", card("Company & payroll settings", f))


@route("POST", r"/admin/settings", ADM)
def settings_save(req):
    c = req.conn
    name = req.f("company_name")
    md, tz = req.f("month_days"), req.f("tz_offset")
    offs = [x.strip().title()[:3] for x in req.f("weekly_off").split(",") if x.strip()]
    try:
        tzf = float(tz)
    except ValueError:
        tzf = None
    if not name or not md.isdigit() or not 1 <= int(md) <= 31 or tzf is None or not -12 <= tzf <= 14:
        return fail(req, "Check company name, days per month and timezone.", "/admin/settings")
    try:
        pf = Decimal(req.f("pf_percent"))
        if not (0 <= pf <= 100):
            raise InvalidOperation
    except InvalidOperation:
        return fail(req, "Provident fund % must be a number between 0 and 100.", "/admin/settings")
    try:
        grace = int(req.f("late_grace"))
        radius = int(req.f("office_radius"))
        lat_t, lng_t = req.f("office_lat"), req.f("office_lng")
        if lat_t or lng_t:
            if not (-90 <= float(lat_t) <= 90 and -180 <= float(lng_t) <= 180):
                raise ValueError
        if not (0 <= grace <= 240 and 20 <= radius <= 5000):
            raise ValueError
        h, m_ = req.f("office_start").split(":")
        if not (0 <= int(h) < 24 and 0 <= int(m_) < 60):
            raise ValueError
    except ValueError:
        return fail(req, "Check office start time, late minutes, latitude/longitude and allowed distance.", "/admin/settings")
    if req.f("geofence") == "1" and not (lat_t and lng_t):
        return fail(req, "Set the office latitude and longitude before turning on the location check.", "/admin/settings")
    if any(o not in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") for o in offs):
        return fail(req, "Weekly off must use day names like Sat,Sun.", "/admin/settings")
    for k, v in (("company_name", name), ("currency", req.f("currency") or "PKR"), ("weekly_off", ",".join(offs)), ("month_days", md), ("tz_offset", str(tzf)),
                 ("medical_exempt", "1" if req.f("medical_exempt") == "1" else "0"), ("pf_percent", str(pf)),
                 ("office_start", req.f("office_start")), ("late_grace", str(grace)), ("geofence", "1" if req.f("geofence") == "1" else "0"),
                 ("office_lat", lat_t), ("office_lng", lng_t), ("office_radius", str(radius)), ("contact_email", req.f("contact_email"))):
        D.set_setting(c, k, v)
    D.audit(c, req.user["username"], "settings_changed")
    req.flash("Settings saved.")
    return redirect("/admin/settings")


# ------------------------------------------------------------ audit/backup
@route("GET", r"/admin/audit", ADM)
def audit_log(req):
    rows = [[r["ts"], r["username"], r["action"], r["detail"] or ""] for r in req.conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300")]
    return page(req, "Audit log", card("Last 300 actions", table(["Time", "User", "Action", "Detail"], rows)))


@route("GET", r"/admin/backup", ADM)
def backup_page(req):
    body = Raw("<p>Download a complete copy of the database (all employees, payroll and accounts). Keep it in a safe place.</p>"
               "<p>To restore: stop the server, replace <code>data/hrms.db</code> with the backup file, start again.</p>")
    return page(req, "Backup", card("Database backup", body, Raw(post_form(req, "/admin/backup", Raw(btn("Download backup"))))))


@route("POST", r"/admin/backup", ADM)
def backup_download(req):
    req.conn.commit()
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(path)
        req.conn.backup(dst)
        dst.close()
        with open(path, "rb") as f:
            data = f.read()
    finally:
        os.remove(path)
    name = "hrms-backup-%s.db" % D.now_local(req.conn).strftime("%Y%m%d-%H%M")
    D.audit(req.conn, req.user["username"], "backup_downloaded")
    return Response(data, ctype="application/octet-stream", headers=[("Content-Disposition", 'attachment; filename="%s"' % name)])

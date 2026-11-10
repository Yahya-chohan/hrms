"""Login, dashboard, employees, attendance, leave, employee self-service."""
import datetime
import json
import os
import re
import secrets
import time

from . import db as D
from . import logic as L
from .web import (H, Raw, Response, abort, badge, bare_page, btn, card, create_session, csrf_field, esc, inp,
                  link_btn, page, post_form, redirect, route, sel, status_badge, table, textarea, LOGIN_FAILS)

EMPV, EMPE, ATTV, ATTM, LEAVE = ("emp_view",), ("emp_edit",), ("att_view",), ("att_mark",), ("leave_manage",)
ALL = None  # any logged-in user


def back(req, default="/"):
    ref = req.form.get("back", [""])[0]
    return ref if ref.startswith("/") and not ref.startswith("//") else default


def fail(req, msg, to):
    req.flash(msg, "err")
    return redirect(to)


# ---------------------------------------------------------------- auth
@route("GET", r"/login")
def login_form(req):
    if req.user:
        return redirect("/")
    return login_page("")


def login_page(error, username=""):
    err = '<div class="flash err">%s</div>' % esc(error) if error else ""
    body = ('<form method="post" action="/login" class="login"><h1>Sign in</h1>'
            '<p class="muted">HR, Payroll &amp; Accounts</p>%s%s%s<button class="btn primary">Login</button></form>'
            % (err, inp("Username", "username", username, required=True, extra="autofocus autocomplete=username"),
               inp("Password", "password", "", "password", True, "autocomplete=current-password")))
    return bare_page("Login", body)


@route("POST", r"/login")
def login_post(req):
    username = req.f("username")
    key = "%s|%s" % (req.ip, username.lower())
    cnt, first = LOGIN_FAILS.get(key, (0, time.time()))
    if time.time() - first > 600:
        cnt, first = 0, time.time()
    if cnt >= 5:
        return login_page("Too many failed attempts. Try again in 10 minutes.", username)
    u = req.conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    pw = req.f("password", strip=False)
    if not u or not D.check_password(pw, u["pw_hash"]):
        LOGIN_FAILS[key] = (cnt + 1, first)
        D.audit(req.conn, username, "login_failed", req.ip)
        return login_page("Wrong username or password.", username)
    LOGIN_FAILS.pop(key, None)
    token = create_session(req.conn, u["id"])
    D.audit(req.conn, u["username"], "login", req.ip)
    return Response(b"", 303, headers=[("Location", "/"),
                    ("Set-Cookie", "sid=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=28800%s" % (token, "; Secure" if os.environ.get("HRMS_SECURE") == "1" else ""))])


@route("POST", r"/logout", ALL)
def logout(req):
    req.conn.execute("DELETE FROM sessions WHERE token=?", (req.sid,))
    return Response(b"", 303, headers=[("Location", "/login"), ("Set-Cookie", "sid=; Path=/; Max-Age=0")])


@route("GET", r"/change-password", ALL)
def pw_form(req):
    note = '<p class="flash err">You must set a new password before continuing.</p>' if req.user["must_change"] else ""
    f = post_form(req, "/change-password", H(
        inp("Current password", "old", "", "password", True), inp("New password", "new", "", "password", True, hint="At least 8 characters"),
        inp("Repeat new password", "new2", "", "password", True), Raw(btn("Change password"))), "form")
    return page(req, "Change password", H(Raw(note), card("Your account", f)))


@route("POST", r"/change-password", ALL)
def pw_post(req):
    if not D.check_password(req.f("old", strip=False), req.user["pw_hash"]):
        return fail(req, "Current password is wrong.", "/change-password")
    new = req.f("new", strip=False)
    if len(new) < 8:
        return fail(req, "New password must be at least 8 characters.", "/change-password")
    if new != req.f("new2", strip=False):
        return fail(req, "The two new passwords do not match.", "/change-password")
    if new == req.f("old", strip=False):
        return fail(req, "Choose a password different from the old one.", "/change-password")
    req.conn.execute("UPDATE users SET pw_hash=?, must_change=0 WHERE id=?", (D.hash_password(new), req.user["id"]))
    D.audit(req.conn, req.user["username"], "password_changed")
    req.flash("Password changed.")
    return redirect("/")


# ----------------------------------------------------------- dashboard
def stat(label, value, href=None):
    inner = "<b>%s</b><span>%s</span>" % (esc(value), esc(label))
    return Raw('<a class="stat" href="%s" style="text-decoration:none;color:inherit">%s</a>' % (href, inner) if href
               else '<div class="stat">%s</div>' % inner)


@route("GET", r"/", ALL)
def dashboard(req):
    c = req.conn
    today = D.today(c)
    parts = []
    if req.has_role("emp_view", "att_view", "leave_manage"):
        n = c.execute("SELECT COUNT(*) FROM employees WHERE status='active'").fetchone()[0]
        link = lambda perm, url: url if req.has_role(perm) else None
        tiles = [stat("Active employees", n, link("emp_view", "/employees"))]
        if req.has_role("leave_manage"):
            pend = c.execute("SELECT COUNT(*) FROM leave_requests WHERE status='pending'").fetchone()[0]
            onleave = c.execute("SELECT COUNT(*) FROM leave_requests WHERE status='approved' AND ? BETWEEN start_date AND end_date",
                                (today.isoformat(),)).fetchone()[0]
            tiles += [stat("Pending leave requests", pend, "/leave?status=pending"), stat("On leave today", onleave, "/leave")]
        if req.has_role("att_view"):
            marked = c.execute("SELECT COUNT(*) FROM attendance WHERE adate=?", (today.isoformat(),)).fetchone()[0]
            tiles.append(stat("Attendance marked today", "%d / %d" % (marked, n), "/attendance"))
        parts.append(H(Raw('<div class="grid">'), *tiles, Raw("</div>")))
    if req.has_role("pay_view"):
        runs = c.execute("SELECT * FROM payroll_runs ORDER BY month DESC LIMIT 3").fetchall()
        rows = [[Raw('<a href="/payroll/%d">%s</a>' % (r["id"], esc(r["month"]))), status_badge(r["status"])] for r in runs]
        parts.append(card("Recent payroll runs", table(["Month", "Status"], rows, empty="No payroll run yet.")))
    if req.has_role("acc_view"):
        def bal(code):
            return D.account_balance(c, D.account_id(c, code))
        m_first = today.replace(day=1).isoformat()
        inc = c.execute("SELECT COALESCE(SUM(l.credit-l.debit),0) FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
                        "JOIN accounts a ON a.id=l.account_id WHERE a.atype='income' AND v.vdate>=?", (m_first,)).fetchone()[0]
        exp = c.execute("SELECT COALESCE(SUM(l.debit-l.credit),0) FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
                        "JOIN accounts a ON a.id=l.account_id WHERE a.atype='expense' AND v.vdate>=?", (m_first,)).fetchone()[0]
        parts.append(H(Raw('<div class="grid">'), stat("Cash in hand", D.fmt(bal("1001")), "/accounts/ledger"),
                       stat("Bank", D.fmt(bal("1002")), "/accounts/ledger"), stat("Income this month", D.fmt(inc)),
                       stat("Expenses this month", D.fmt(exp)), Raw("</div>")))
    if req.user["employee_id"]:
        eid = req.user["employee_id"]
        bals = L.leave_balances(c, eid, today.year)
        rows = [[b["name"], b["quota"] or "—", b["used"], b["pending"], "—" if b["remaining"] is None else b["remaining"]] for b in bals]
        parts.append(card("My leave balance %d" % today.year, table(["Type", "Yearly quota", "Used", "Pending", "Remaining"], rows, (1, 2, 3, 4)),
                          link_btn("Apply for leave", "/my/leave", "primary")))
        parts.append(card("Attendance today", H(Raw("<p style='margin:0 0 10px'><b>%s</b></p>" % esc(today_text(c, eid)[1])), link_btn("Check in / out", "/my/attendance", "primary"))))
    if not parts:
        parts.append(card("Welcome", Raw("<p>Your account is not linked to an employee record yet. Ask the administrator.</p>")))
    return page(req, "Dashboard", H(*parts))


# ----------------------------------------------------------- employees
EMP_FIELDS = ["name", "father_name", "cnic", "phone", "email", "address", "dob", "gender", "join_date", "dept_id", "desig_id",
              "status", "bank_name", "bank_account"]
MONEY_FIELDS = [("basic", "Basic salary"), ("house_rent", "House rent"), ("medical", "Medical allowance"),
                ("conveyance", "Conveyance"), ("other_allow", "Other allowance"), ("income_tax", "Income tax (monthly)"),
                ("provident_fund", "Provident fund (monthly)"), ("loan_installment", "Loan installment (monthly)")]


def emp_form_html(req, v, action, title):
    c = req.conn
    depts = [(r["id"], r["name"]) for r in c.execute("SELECT * FROM departments ORDER BY name")]
    desigs = [(r["id"], r["name"]) for r in c.execute("SELECT * FROM designations ORDER BY name")]
    g = lambda k: v.get(k, "") if v.get(k) is not None else ""
    ids = {"basic": "f_basic", "house_rent": "f_house_rent", "medical": "f_medical", "conveyance": "f_conveyance",
           "other_allow": "f_other_allow", "income_tax": "f_income_tax", "provident_fund": "f_provident_fund", "loan_installment": "f_loan_installment"}
    money = [inp(lbl, k, g(k) or "0", "number", False, 'step="0.01" min="0"' + (' id="%s"' % ids[k] if k in ids else "")) for k, lbl in MONEY_FIELDS]
    try:
        total_default = D.plain(sum(D.to_paisa(g(k) or "0") for k in ("basic", "house_rent", "medical")))
        gross_default = D.plain(sum(D.to_paisa(g(k) or "0") for k in ("basic", "house_rent", "medical", "conveyance", "other_allow")))
    except ValueError:
        total_default = gross_default = "0"
    total_v = g("total") or total_default
    auto_on = v.get("_auto") == "1"
    pf_on = v.get("_pf") == "1"
    pf_pct = L.pf_percent(c)
    inner = H(
        Raw("<h3 style='grid-column:1/-1;margin:0'>Personal</h3>"),
        inp("Full name", "name", g("name"), required=True), inp("Father name", "father_name", g("father_name")),
        inp("CNIC", "cnic", g("cnic"), hint="e.g. 35202-1234567-1"), inp("Phone", "phone", g("phone")),
        inp("Email", "email", g("email"), "email"), inp("Date of birth", "dob", g("dob"), "date"),
        sel("Gender", "gender", [("M", "Male"), ("F", "Female")], g("gender"), blank="—"),
        inp("Address", "address", g("address")),
        Raw("<h3 style='grid-column:1/-1;margin:8px 0 0'>Job</h3>"),
        inp("Joining date", "join_date", g("join_date"), "date", True),
        sel("Department", "dept_id", depts, g("dept_id"), blank="—"), inp("…or add a new department", "new_dept", g("new_dept"), hint="Type a name to create it"),
        sel("Designation", "desig_id", desigs, g("desig_id"), blank="—"), inp("…or add a new designation", "new_desig", g("new_desig"), hint="Type a name to create it"),
        sel("Status", "status", [("active", "Active"), ("inactive", "Inactive / left")], g("status") or "active"),
        Raw("<h3 style='grid-column:1/-1;margin:8px 0 0'>Salary (per month, %s)</h3>" % esc(D.get_setting(c, "currency"))),
        inp("Total salary (to split)", "total", total_v, "number", False, 'step="0.01" min="0" id="total"', "Split into basic / house rent / medical"),
        inp("Gross salary (auto)", "gross_view", gross_default, "number", False, 'readonly id="gross" style="background:#f1f5f8;font-weight:700"', "Total + allowances"),
        Raw('<label style="grid-column:1/-1;display:flex;gap:8px;align-items:center"><input type="checkbox" name="auto" value="1" id="auto" style="width:auto"%s> '
            'Auto: split 50%% basic / 30%% house rent / 20%% medical and calculate income tax (FBR %s slabs)</label>' % (" checked" if auto_on else "", L.TAX_YEAR)),
        Raw('<label style="grid-column:1/-1;display:flex;gap:8px;align-items:center"><input type="checkbox" name="pf_on" value="1" id="pf_on" style="width:auto"%s> '
            'Provident fund %s%% of basic (used only when Auto is on)</label>' % (" checked" if pf_on else "", L.fmt_pct(pf_pct))),
        *money,
        Raw('<div style="grid-column:1/-1;border:2px solid #17212b;border-radius:8px;padding:12px 16px;background:#eaf6f3;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">'
            '<div><b>NET SALARY (take-home)</b><div class="muted" style="font-size:13px">Gross − income tax − provident fund − loan installment</div></div>'
            '<div style="font-size:26px;font-weight:800">%s <span id="net">0.00</span></div></div>' % esc(D.get_setting(c, "currency"))),
        Raw("<h3 style='grid-column:1/-1;margin:8px 0 0'>Bank</h3>"),
        inp("Bank name", "bank_name", g("bank_name")), inp("Account / IBAN", "bank_account", g("bank_account")),
        Raw("<div style='grid-column:1/-1'>%s %s</div>" % (btn("Save employee"), link_btn("Cancel", "/employees"))))
    script = ("<script>(function(){var S=%s,MEX=%s,PF=%s;function $(i){return document.getElementById(i)}"
              "function tax(a){var t=0;for(var i=S.length-1;i>=0;i--){if(a>S[i][0]){t=S[i][1]+(a-S[i][0])*S[i][2]/100;break}}return t}"
              "function f(n){return (Math.round(n*100)/100).toFixed(2)}function v(i){return parseFloat($(i).value)||0}"
              "function calc(){var on=$('auto').checked,ids=['basic','house_rent','medical','income_tax','provident_fund'];"
              "ids.forEach(function(k){$('f_'+k).readOnly=on});"
              "if(on){var T=v('total'),b=Math.floor(T*50)/100,h=Math.floor(T*30)/100,m=Math.round((T-b-h)*100)/100;"
              "$('f_basic').value=f(b);$('f_house_rent').value=f(h);$('f_medical').value=f(m);"
              "var g=T+v('f_conveyance')+v('f_other_allow'),ex=MEX?Math.min(m,Math.floor(b*10)/100):0;"
              "$('f_income_tax').value=f(tax((g-ex)*12)/12);$('f_provident_fund').value=$('pf_on').checked?f(b*PF/100):'0.00'}"
              "var g2=v('f_basic')+v('f_house_rent')+v('f_medical')+v('f_conveyance')+v('f_other_allow');$('gross').value=f(g2);"
              "$('net').textContent=f(g2-v('f_income_tax')-v('f_provident_fund')-v('f_loan_installment'))}"
              "['auto','pf_on'].forEach(function(i){$(i).addEventListener('change',calc)});"
              "['total','f_conveyance','f_other_allow','f_basic','f_house_rent','f_medical','f_income_tax','f_provident_fund','f_loan_installment'].forEach(function(i){$(i).addEventListener('input',calc)});calc()})();</script>"
              % (json.dumps([list(s) for s in L.TAX_SLABS]), "true" if D.get_setting(c, "medical_exempt", "1") == "1" else "false", str(pf_pct)))
    return H(card(title, post_form(req, action, inner, "form")), Raw(script))


def raw_vals(req):
    v = {k: req.f(k) for k in EMP_FIELDS + [m[0] for m in MONEY_FIELDS]}
    v.update(total=req.f("total"), new_dept=req.f("new_dept"), new_desig=req.f("new_desig"), _auto="1" if req.f("auto") == "1" else "",
             _pf="1" if req.f("pf_on") == "1" else "")
    return v


def named_id(conn, table_name, name):
    r = conn.execute("SELECT id FROM %s WHERE name=? COLLATE NOCASE" % table_name, (name,)).fetchone()
    if r:
        return r[0]
    return conn.execute("INSERT INTO %s(name) VALUES(?)" % table_name, (name,)).lastrowid


def read_emp_form(req):
    v = {k: req.f(k) for k in EMP_FIELDS}
    for k, _ in MONEY_FIELDS:
        v[k] = req.f(k) or "0"
    if not v["name"]:
        raise ValueError("Name is required.")
    try:
        D.parse_date(v["join_date"])
    except ValueError:
        raise ValueError("Joining date is required (valid date).")
    for k in ("dob",):
        if v[k]:
            try:
                D.parse_date(v[k])
            except ValueError:
                raise ValueError("Date of birth is not a valid date.")
    out = dict(v)
    for k, lbl in MONEY_FIELDS:
        p = D.to_paisa(v[k])
        if p < 0:
            raise ValueError("%s cannot be negative." % lbl)
        out[k] = p
    for k in ("dept_id", "desig_id"):
        out[k] = int(v[k]) if v[k].isdigit() else None
    if v["status"] not in ("active", "inactive"):
        raise ValueError("Invalid status.")
    if req.f("auto") == "1":
        total = D.to_paisa(req.f("total"))
        if total <= 0:
            raise ValueError("Enter the total monthly salary.")
        out["basic"], out["house_rent"], out["medical"] = L.split_salary(total)
        gross = total + out["conveyance"] + out["other_allow"]  # allowances are added on top of the split salary
        out["income_tax"] = L.monthly_tax(req.conn, gross, out["basic"], out["medical"])
        out["provident_fund"] = L.pf_amount(req.conn, out["basic"]) if req.f("pf_on") == "1" else 0
    # create new department / designation only after everything else validated
    if req.f("new_dept"):
        out["dept_id"] = named_id(req.conn, "departments", req.f("new_dept"))
    if req.f("new_desig"):
        out["desig_id"] = named_id(req.conn, "designations", req.f("new_desig"))
    return v, out


@route("GET", r"/employees",EMPV)
def employees(req):
    q, st = req.q("q").strip(), req.q("status", "active")
    sql = ("SELECT e.*, d.name dept, g.name desig FROM employees e LEFT JOIN departments d ON d.id=e.dept_id "
           "LEFT JOIN designations g ON g.id=e.desig_id WHERE 1=1")
    args = []
    if st in ("active", "inactive"):
        sql += " AND e.status=?"
        args.append(st)
    if q:
        sql += " AND (e.name LIKE ? OR e.code LIKE ? OR e.cnic LIKE ?)"
        args += ["%" + q + "%"] * 3
    rows = req.conn.execute(sql + " ORDER BY e.code", args).fetchall()
    data = [[r["code"], Raw('<a href="/employees/%d">%s</a>' % (r["id"], esc(r["name"]))), r["dept"] or "—", r["desig"] or "—",
             r["join_date"], D.fmt(r["basic"] + r["house_rent"] + r["medical"] + r["conveyance"] + r["other_allow"]),
             status_badge(r["status"])] for r in rows]
    bar = Raw('<form class="bar" method="get"><label class="fld"><span>Search</span><input name="q" value="%s" placeholder="Name, code or CNIC"></label>'
              '%s<button class="btn ghost">Filter</button></form>' % (esc(q), sel("Status", "status", [("active", "Active"), ("inactive", "Inactive"), ("all", "All")], st)))
    return page(req, "Employees", H(card("All employees", H(bar, table(["Code", "Name", "Department", "Designation", "Joined", "Gross salary", "Status"], data, (5,))),
                                           link_btn("+ New employee", "/employees/new", "primary"))))


@route("GET", r"/employees/new",EMPE)
def emp_new(req):
    return page(req, "New employee", emp_form_html(req, {"join_date": D.today(req.conn).isoformat(), "_auto": "1", "_pf": "1"}, "/employees/new", "Employee details"))


@route("POST", r"/employees/new",EMPE)
def emp_create(req):
    try:
        v, o = read_emp_form(req)
    except ValueError as e:
        req.flash(str(e), "err")
        return page(req, "New employee", emp_form_html(req, raw_vals(req), "/employees/new", "Employee details"), 400)
    c = req.conn
    n = c.execute("SELECT COALESCE(MAX(id),0)+1 FROM employees").fetchone()[0]
    while c.execute("SELECT 1 FROM employees WHERE code=?", ("EMP-%04d" % n,)).fetchone():
        n += 1
    code = "EMP-%04d" % n
    cols = EMP_FIELDS + [m[0] for m in MONEY_FIELDS]
    c.execute("INSERT INTO employees(code,%s,created) VALUES(?,%s,?)" % (",".join(cols), ",".join("?" * len(cols))),
              [code] + [o[k] for k in cols] + [D.now_local(c).strftime("%Y-%m-%d")])
    D.audit(c, req.user["username"], "employee_created", "%s %s" % (code, o["name"]))
    req.flash("Employee %s created." % code)
    return redirect("/employees")


def get_emp(req, eid):
    e = req.conn.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone()
    if not e:
        abort(404, "Employee not found.")
    return e


@route("GET", r"/employees/(?P<eid>\d+)", EMPV)
def emp_view(req, eid):
    e = get_emp(req, eid)
    c = req.conn
    dept = c.execute("SELECT name FROM departments WHERE id=?", (e["dept_id"],)).fetchone()
    desig = c.execute("SELECT name FROM designations WHERE id=?", (e["desig_id"],)).fetchone()
    gross = e["basic"] + e["house_rent"] + e["medical"] + e["conveyance"] + e["other_allow"]
    kv = lambda items: Raw("<dl class='kv'>%s</dl>" % "".join("<dt>%s</dt><dd>%s</dd>" % (esc(k), esc(v or "—")) for k, v in items))
    left = kv([("Code", e["code"]), ("Name", e["name"]), ("Father name", e["father_name"]), ("CNIC", e["cnic"]), ("Phone", e["phone"]),
               ("Email", e["email"]), ("Address", e["address"]), ("Date of birth", e["dob"]), ("Department", dept and dept[0]),
               ("Designation", desig and desig[0]), ("Joined", e["join_date"]), ("Status", e["status"].title()),
               ("Bank", e["bank_name"]), ("Account", e["bank_account"])])
    right = kv([("Basic", D.fmt(e["basic"])), ("House rent", D.fmt(e["house_rent"])), ("Medical", D.fmt(e["medical"])),
                ("Conveyance", D.fmt(e["conveyance"])), ("Other allowance", D.fmt(e["other_allow"])), ("Gross", D.fmt(gross)),
                ("Income tax", D.fmt(e["income_tax"])), ("Provident fund", D.fmt(e["provident_fund"])),
                ("Loan installment", D.fmt(e["loan_installment"])),
                ("NET SALARY (take-home)", D.fmt(gross - e["income_tax"] - e["provident_fund"] - e["loan_installment"]))])
    bals = L.leave_balances(c, e["id"], D.today(c).year)
    lb = table(["Type", "Quota", "Used", "Pending", "Remaining"],
               [[b["name"], b["quota"] or "—", b["used"], b["pending"], "—" if b["remaining"] is None else b["remaining"]] for b in bals], (1, 2, 3, 4))
    edit_btn = link_btn("Edit", "/employees/%s/edit" % eid, "primary") if req.has_role("emp_edit") else ""
    return page(req, e["name"], H(Raw('<div class="two">'), card("Profile", left, edit_btn),
                                  card("Salary (monthly)", right), Raw("</div>"), payslips_card(req, e),
                                  card("Leave balance %d" % D.today(c).year, lb), access_cards(req, e)))


def payslips_card(req, e):
    """Payslip history + preview for one employee (needs the payroll-view right)."""
    if not req.has_role("pay_view"):
        return ""
    c = req.conn
    rows = [[Raw('<a href="/payslip/%d">%s</a>' % (r["id"], esc(r["month"]))), status_badge(r["status"]), D.fmt(r["gross"]), D.fmt(r["net"]),
             Raw('<a class="btn ghost sm" href="/payslip/%d">View</a>' % r["id"])]
            for r in c.execute("SELECT p.id, p.gross, p.net, r.month, r.status FROM payslips p JOIN payroll_runs r ON r.id=p.run_id "
                               "WHERE p.emp_id=? ORDER BY r.month DESC", (e["id"],))]
    prev = Raw('<form class="bar" method="get" action="/employees/%d/payslip-preview">%s<button class="btn primary">Preview payslip</button></form>'
               % (e["id"], inp("Month", "month", D.today(c).strftime("%Y-%m"), "month")))
    note = Raw('<p class="muted">Preview shows what the payslip would be from the current salary and attendance, even before payroll is run. '
               'The employee sees a payslip in their own login once payroll for that month is posted.</p>')
    return card("Payslips", H(prev, note, table(["Month", "Status", "Gross", "Net", ""], rows, (2, 3), "No payroll has been run for this employee yet.")))


@route("GET", r"/employees/(?P<eid>\d+)/edit",EMPE)
def emp_edit(req, eid):
    e = get_emp(req, eid)
    v = dict(e)
    for k, _ in MONEY_FIELDS:
        v[k] = D.plain(e[k])
    base = e["basic"] + e["house_rent"] + e["medical"]
    b, h, _m = L.split_salary(base)
    v["_auto"] = "1" if base and e["basic"] == b and e["house_rent"] == h else ""
    v["_pf"] = "1" if e["provident_fund"] > 0 else ""
    return page(req, "Edit employee", emp_form_html(req, v, "/employees/%s/edit" % eid, "%s — %s" % (e["code"], e["name"])))


@route("POST", r"/employees/(?P<eid>\d+)/edit",EMPE)
def emp_update(req, eid):
    e = get_emp(req, eid)
    try:
        v, o = read_emp_form(req)
    except ValueError as ex:
        req.flash(str(ex), "err")
        return page(req, "Edit employee", emp_form_html(req, raw_vals(req), "/employees/%s/edit" % eid, e["name"]), 400)
    cols = EMP_FIELDS + [m[0] for m in MONEY_FIELDS]
    req.conn.execute("UPDATE employees SET %s WHERE id=?" % ",".join(k + "=?" for k in cols), [o[k] for k in cols] + [e["id"]])
    if o["status"] == "inactive":
        req.conn.execute("UPDATE users SET active=0 WHERE employee_id=?", (e["id"],))
        req.conn.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE employee_id=?)", (e["id"],))
        if not e["exit_date"]:
            req.conn.execute("UPDATE employees SET exit_date=? WHERE id=?", (D.today(req.conn).isoformat(), e["id"]))
    else:
        req.conn.execute("UPDATE employees SET exit_date=NULL WHERE id=?", (e["id"],))
    D.audit(req.conn, req.user["username"], "employee_updated", e["code"])
    req.flash("Employee updated.")
    return redirect("/employees/%s" % eid)


# ------------------------------------------------ login access + exit
TEMP_CHARS = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def temp_password():
    return "".join(secrets.choice(TEMP_CHARS) for _ in range(10))


def access_cards(req, e):
    c = req.conn
    out = []
    u = c.execute("SELECT * FROM users WHERE employee_id=?", (e["id"],)).fetchone()
    if req.role == "admin":
        base = "/employees/%d/login" % e["id"]
        if u is None:
            if e["status"] == "active":
                body = post_form(req, base + "/create", H(inp("Username", "username", e["code"].lower(), required=True, hint="3-30 letters, digits, . _ -"),
                                                         Raw(btn("Create login"))), "form")
                hint = Raw('<p class="muted">The employee can then see only: own leave (apply/balance), own attendance and own payslips. A temporary password is generated for you to hand over.</p>')
                out.append(card("Login access", H(hint, body)))
            else:
                out.append(card("Login access", Raw('<p class="muted">Employee has left; no login.</p>')))
        else:
            state = badge("Active", "green") if u["active"] else badge("Disabled", "gray")
            kv = Raw("<dl class='kv'><dt>Username</dt><dd>%s</dd><dt>Status</dt><dd>%s</dd></dl>" % (esc(u["username"]), state))
            btns = H(post_form(req, base + "/reset", Raw(btn("Reset password", "ghost")), "inline", confirm="Generate a new temporary password? The old one stops working."), " ",
                     post_form(req, base + "/toggle", Raw(btn("Disable login" if u["active"] else "Enable login", "danger" if u["active"] else "ghost")), "inline"))
            out.append(card("Login access", H(kv, btns)))
    if not req.has_role("emp_edit"):
        return H(*out)
    if e["status"] == "active":
        body = post_form(req, "/employees/%d/exit" % e["id"], H(inp("Last working day", "exit_date", D.today(c).isoformat(), "date", True),
                                                               Raw(btn("Mark as left", "danger"))), "form",
                         confirm="Mark this employee as left? Their login will be disabled immediately and pending leave cancelled.")
        out.append(card("Employee exit", H(Raw('<p class="muted">Disables the login at once, cancels pending leave and keeps all records. Final-month salary is paid up to the last working day.</p>'), body)))
    else:
        body = post_form(req, "/employees/%d/rejoin" % e["id"], Raw(btn("Re-activate employee", "ghost")), "inline", confirm="Re-activate this employee?")
        out.append(card("Employee exit", H(Raw("<p>Left on <b>%s</b>.</p>" % esc(e["exit_date"] or "—")), body)))
    return H(*out)


def credentials_page(req, e, username, pw, title):
    body = Raw('<div class="slip"><h2>%s</h2><dl class="kv"><dt>Employee</dt><dd>%s (%s)</dd><dt>Username</dt><dd><b>%s</b></dd>'
               '<dt>Temporary password</dt><dd><b style="font-size:20px;letter-spacing:1px">%s</b></dd></dl>'
               '<p class="flash err">Write this down or print it now. It is shown only once. The employee must choose a new password at first login.</p>'
               '<p class="noprint"><button class="btn ghost" onclick="window.print()">Print</button> %s</p></div>'
               % (esc(title), esc(e["name"]), esc(e["code"]), esc(username), esc(pw), link_btn("Back to employee", "/employees/%d" % e["id"])))
    return page(req, title, body)


@route("POST", r"/employees/(?P<eid>\d+)/login/create", ("admin",))
def login_create(req, eid):
    c = req.conn
    e = get_emp(req, eid)
    un = req.f("username")
    if e["status"] != "active":
        return fail(req, "This employee has left.", "/employees/%s" % eid)
    if c.execute("SELECT 1 FROM users WHERE employee_id=?", (e["id"],)).fetchone():
        return fail(req, "This employee already has a login.", "/employees/%s" % eid)
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", un):
        return fail(req, "Username must be 3-30 characters: letters, digits, dot, underscore or dash.", "/employees/%s" % eid)
    if c.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
        return fail(req, "That username is already taken.", "/employees/%s" % eid)
    pw = temp_password()
    c.execute("INSERT INTO users(username,pw_hash,role,employee_id,must_change,created) VALUES(?,?,'employee',?,1,?)",
              (un, D.hash_password(pw), e["id"], D.now_local(c).strftime("%Y-%m-%d %H:%M")))
    D.audit(c, req.user["username"], "login_created", "%s for %s" % (un, e["code"]))
    return credentials_page(req, e, un, pw, "Login created")


@route("POST", r"/employees/(?P<eid>\d+)/login/reset", ("admin",))
def login_reset(req, eid):
    c = req.conn
    e = get_emp(req, eid)
    u = c.execute("SELECT * FROM users WHERE employee_id=?", (e["id"],)).fetchone()
    if not u or not u["active"]:
        return fail(req, "No active login to reset.", "/employees/%s" % eid)
    pw = temp_password()
    c.execute("UPDATE users SET pw_hash=?, must_change=1 WHERE id=?", (D.hash_password(pw), u["id"]))
    c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    D.audit(c, req.user["username"], "password_reset", u["username"])
    return credentials_page(req, e, u["username"], pw, "Password reset")


@route("POST", r"/employees/(?P<eid>\d+)/login/toggle", ("admin",))
def login_toggle(req, eid):
    c = req.conn
    e = get_emp(req, eid)
    u = c.execute("SELECT * FROM users WHERE employee_id=?", (e["id"],)).fetchone()
    if not u:
        return fail(req, "No login exists.", "/employees/%s" % eid)
    if not u["active"] and e["status"] != "active":
        return fail(req, "Re-activate the employee first.", "/employees/%s" % eid)
    c.execute("UPDATE users SET active=1-active WHERE id=?", (u["id"],))
    c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    D.audit(c, req.user["username"], "login_toggled", u["username"])
    req.flash("Login %s." % ("disabled" if u["active"] else "enabled"))
    return redirect("/employees/%s" % eid)


@route("POST", r"/employees/(?P<eid>\d+)/exit",EMPE)
def emp_exit(req, eid):
    c = req.conn
    e = get_emp(req, eid)
    try:
        d = D.parse_date(req.f("exit_date"))
    except ValueError:
        return fail(req, "Enter a valid last working day.", "/employees/%s" % eid)
    if e["status"] != "active":
        return fail(req, "Employee has already left.", "/employees/%s" % eid)
    if d < D.parse_date(e["join_date"]):
        return fail(req, "Last working day cannot be before the joining date.", "/employees/%s" % eid)
    c.execute("UPDATE employees SET status='inactive', exit_date=? WHERE id=?", (d.isoformat(), e["id"]))
    c.execute("UPDATE users SET active=0 WHERE employee_id=?", (e["id"],))
    c.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE employee_id=?)", (e["id"],))
    n = c.execute("UPDATE leave_requests SET status='cancelled', remarks='Employee left' WHERE emp_id=? AND status='pending'", (e["id"],)).rowcount
    D.audit(c, req.user["username"], "employee_exit", "%s on %s" % (e["code"], d))
    req.flash("%s marked as left on %s. Login disabled%s." % (e["name"], d, (", %d pending leave request(s) cancelled" % n) if n else ""))
    return redirect("/employees/%s" % eid)


@route("POST", r"/employees/(?P<eid>\d+)/rejoin",EMPE)
def emp_rejoin(req, eid):
    e = get_emp(req, eid)
    req.conn.execute("UPDATE employees SET status='active', exit_date=NULL WHERE id=?", (e["id"],))
    D.audit(req.conn, req.user["username"], "employee_reactivated", e["code"])
    req.flash("Employee re-activated. Their login (if any) stays disabled until the admin enables it.")
    return redirect("/employees/%s" % eid)


# ---------------------------------------------------------- attendance
def loc_dist(loc):
    parts = (loc or "").split(",")
    return int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None


def times_cell(c, r):
    if not r or not r["check_in"]:
        return "—"
    out = "<b>%s</b>" % esc(r["check_in"])
    if r["check_out"]:
        out += " → <b>%s</b> · %s" % (esc(r["check_out"]), esc(L.worked(r["check_in"], r["check_out"])))
    else:
        out += " → <span class='muted'>not yet</span>"
    if L.is_late(c, r["check_in"]):
        out += " " + str(badge("Late", "amber"))
    extra = []
    if r["via"] == "mobile":
        extra.append("mobile")
    d = loc_dist(r["in_loc"])
    if d is not None:
        extra.append("%d m from office" % d)
    if extra:
        out += "<br><small class='muted'>%s</small>" % esc(" · ".join(extra))
    return Raw(out)


@route("GET", r"/attendance",ATTV)
def attendance(req):
    c = req.conn
    ds = req.q("date") or D.today(c).isoformat()
    try:
        d = D.parse_date(ds)
    except ValueError:
        return fail(req, "Invalid date.", "/attendance")
    emps = c.execute("SELECT * FROM employees WHERE status='active' AND join_date<=? ORDER BY code", (ds,)).fetchall()
    rec = {r["emp_id"]: r for r in c.execute("SELECT * FROM attendance WHERE adate=?", (ds,))}
    off = L.WD[d.weekday()] in L.weekly_off(c)
    rows = []
    for e in emps:
        cur = rec[e["id"]]["status"] if e["id"] in rec else ""
        radios = "".join('<label><input type="radio" name="st_%d" value="%s"%s> %s</label> ' % (e["id"], k, " checked" if cur == k else "", esc(v))
                         for k, v in D.ATT_STATUS.items())
        rows.append([e["code"], e["name"], Raw('<span class="seg">%s</span>' % radios), times_cell(c, rec.get(e["id"]))])
    warn = '<p class="flash err">%s is a weekly off day.</p>' % L.WD[d.weekday()] if off else ""
    dp = Raw('<form class="bar" method="get">%s<button class="btn ghost">Go</button> %s</form>' % (inp("Date", "date", ds, "date"), link_btn("Monthly report", "/attendance/report")))
    hint = Raw('<p class="muted">Employees mark their own time from the phone (Check in / out). Use the buttons below only for people without a phone or to correct a day. '
               'After a day ends, <b>Mark no-shows absent</b> turns everyone without a record into Absent.</p>')
    form = post_form(req, "/attendance", H(Raw('<input type="hidden" name="date" value="%s">' % esc(ds)), table(["Code", "Name", "Status", "Check in / out"], rows, empty="No active employees on this date."),
                                           Raw("<p>%s</p>" % btn("Save selected"))))
    close = ""
    if d < D.today(c) and not off:
        close = post_form(req, "/attendance/close-day", H(Raw('<input type="hidden" name="date" value="%s">' % esc(ds)), Raw(btn("Mark no-shows absent", "danger"))), "inline",
                          confirm="Mark everyone without a record on %s as Absent?" % ds)
    return page(req, "Attendance", H(Raw(warn), card("Daily attendance — %s" % ds, H(dp, hint, form, close))))


@route("POST", r"/attendance/close-day",ATTM)
def attendance_close(req):
    c = req.conn
    ds = req.f("date")
    try:
        d = D.parse_date(ds)
    except ValueError:
        return fail(req, "Invalid date.", "/attendance")
    if d >= D.today(c):
        return fail(req, "Only days that have ended can be closed.", "/attendance?date=" + ds)
    if L.WD[d.weekday()] in L.weekly_off(c):
        return fail(req, "That day is a weekly off.", "/attendance?date=" + ds)
    n = 0
    for e in c.execute("SELECT id FROM employees WHERE status='active' AND join_date<=? AND (exit_date IS NULL OR exit_date>=?)", (ds, ds)).fetchall():
        if not c.execute("SELECT 1 FROM attendance WHERE emp_id=? AND adate=?", (e["id"], ds)).fetchone():
            c.execute("INSERT INTO attendance(emp_id,adate,status,via) VALUES(?,?,'A','auto')", (e["id"], ds))
            n += 1
    D.audit(c, req.user["username"], "attendance_closed", "%s (%d absent)" % (ds, n))
    req.flash("%d employee(s) with no record marked Absent." % n)
    return redirect("/attendance?date=" + ds)


@route("POST", r"/attendance",ATTM)
def attendance_save(req):
    c = req.conn
    ds = req.f("date")
    try:
        D.parse_date(ds)
    except ValueError:
        return fail(req, "Invalid date.", "/attendance")
    n = 0
    for e in c.execute("SELECT id FROM employees WHERE status='active' AND join_date<=?", (ds,)).fetchall():
        st = req.f("st_%d" % e["id"])
        if st in D.ATT_STATUS:
            c.execute("INSERT INTO attendance(emp_id,adate,status) VALUES(?,?,?) ON CONFLICT(emp_id,adate) DO UPDATE SET status=excluded.status",
                      (e["id"], ds, st))
            n += 1
    D.audit(c, req.user["username"], "attendance_saved", "%s (%d)" % (ds, n))
    req.flash("Attendance saved for %d employees." % n)
    return redirect("/attendance?date=" + ds)


@route("GET", r"/attendance/report",ATTV)
def attendance_report(req):
    c = req.conn
    month = req.q("month") or D.today(c).strftime("%Y-%m")
    try:
        first, last = D.month_bounds(month)
    except (ValueError, IndexError):
        return fail(req, "Invalid month.", "/attendance/report")
    rows = []
    for e in c.execute("SELECT * FROM employees WHERE status='active' ORDER BY code"):
        cnt = {r[0]: r[1] for r in c.execute("SELECT status,COUNT(*) FROM attendance WHERE emp_id=? AND adate BETWEEN ? AND ? GROUP BY status",
                                             (e["id"], first.isoformat(), last.isoformat()))}
        late = sum(1 for r in c.execute("SELECT check_in FROM attendance WHERE emp_id=? AND adate BETWEEN ? AND ? AND check_in IS NOT NULL",
                                        (e["id"], first.isoformat(), last.isoformat())) if L.is_late(c, r[0]))
        rows.append([e["code"], e["name"], cnt.get("P", 0), cnt.get("A", 0), cnt.get("L", 0), cnt.get("UL", 0), late])
    bar = Raw('<form class="bar" method="get">%s<button class="btn ghost">Show</button></form>' % inp("Month", "month", month, "month"))
    return page(req, "Monthly attendance", card(month, H(bar, table(["Code", "Name", "Present", "Absent", "Paid leave", "Unpaid leave", "Late days"], rows, (2, 3, 4, 5, 6)))))


# --------------------------------------------------------------- leave
def create_leave(req, emp_id, type_id, start, end, reason):
    c = req.conn
    try:
        s, e = D.parse_date(start), D.parse_date(end)
    except ValueError:
        raise ValueError("Enter valid start and end dates.")
    if e < s:
        raise ValueError("End date cannot be before start date.")
    if (e - s).days > 120:
        raise ValueError("Leave period is too long.")
    lt = c.execute("SELECT * FROM leave_types WHERE id=?", (type_id,)).fetchone()
    if not lt:
        raise ValueError("Choose a leave type.")
    days = len(L.working_dates(s, e, L.weekly_off(c)))
    if days == 0:
        raise ValueError("The selected dates are all weekly off days.")
    if c.execute("SELECT 1 FROM leave_requests WHERE emp_id=? AND status IN ('pending','approved') AND start_date<=? AND end_date>=?",
                 (emp_id, e.isoformat(), s.isoformat())).fetchone():
        raise ValueError("This period overlaps another pending/approved leave.")
    if lt["days_per_year"]:
        bal = [b for b in L.leave_balances(c, emp_id, s.year) if b["id"] == lt["id"]][0]
        if days > bal["remaining"]:
            raise ValueError("Not enough balance: %d day(s) requested, %d remaining in %s." % (days, max(bal["remaining"], 0), lt["name"]))
        if s.year != e.year:
            raise ValueError("Split leave that crosses into the next year into two requests.")
    c.execute("INSERT INTO leave_requests(emp_id,type_id,start_date,end_date,days,reason,created) VALUES(?,?,?,?,?,?,?)",
              (emp_id, lt["id"], s.isoformat(), e.isoformat(), days, reason, D.now_local(c).strftime("%Y-%m-%d %H:%M")))
    return days


def apply_form(req, emp_id, action):
    types = [(t["id"], t["name"] + ("" if t["paid"] else " (unpaid)")) for t in req.conn.execute("SELECT * FROM leave_types ORDER BY id")]
    today = D.today(req.conn).isoformat()
    inner = H(Raw('<input type="hidden" name="emp_id" value="%s">' % emp_id) if emp_id else "", sel("Leave type", "type_id", types, required=True),
              inp("From", "start", today, "date", True), inp("To", "end", today, "date", True), inp("Reason", "reason"), Raw(btn("Submit request")))
    return post_form(req, action, inner, "form")


@route("GET", r"/leave",LEAVE)
def leave_list(req):
    c = req.conn
    st = req.q("status", "pending")
    sql = ("SELECT r.*, e.name ename, e.code ecode, t.name tname FROM leave_requests r JOIN employees e ON e.id=r.emp_id "
           "JOIN leave_types t ON t.id=r.type_id")
    args = []
    if st != "all":
        sql += " WHERE r.status=?"
        args.append(st)
    rows = []
    for r in c.execute(sql + " ORDER BY r.id DESC LIMIT 300", args):
        act = ""
        if r["status"] == "pending":
            act = post_form(req, "/leave/%d/decide" % r["id"], H(
                Raw('<input name="remarks" placeholder="Remarks" style="width:140px">'),
                Raw('<button class="btn primary sm" name="d" value="approve">Approve</button> <button class="btn danger sm" name="d" value="reject">Reject</button>')), "row")
        elif r["status"] == "approved":
            act = post_form(req, "/leave/%d/decide" % r["id"], Raw('<button class="btn danger sm" name="d" value="cancel">Cancel</button>'), "row", confirm="Cancel this approved leave?")
        rows.append([r["ecode"] + " " + r["ename"], r["tname"], "%s → %s" % (r["start_date"], r["end_date"]), r["days"], r["reason"] or "—",
                     status_badge(r["status"]), r["remarks"] or "", act])
    flt = Raw('<form class="bar" method="get">%s<button class="btn ghost">Filter</button></form>'
              % sel("Status", "status", [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("cancelled", "Cancelled"), ("all", "All")], st))
    emps = [(e["id"], "%s — %s" % (e["code"], e["name"])) for e in c.execute("SELECT * FROM employees WHERE status='active' ORDER BY code")]
    types = [(t["id"], t["name"]) for t in c.execute("SELECT * FROM leave_types ORDER BY id")]
    nb = post_form(req, "/leave/new", H(sel("Employee", "emp_id", emps, required=True), sel("Leave type", "type_id", types, required=True),
                                        inp("From", "start", D.today(c).isoformat(), "date", True), inp("To", "end", D.today(c).isoformat(), "date", True),
                                        inp("Reason", "reason"), Raw(btn("Record request"))), "form")
    return page(req, "Leave requests", H(card("Requests", H(flt, table(["Employee", "Type", "Period", "Days", "Reason", "Status", "Remarks", ""], rows, (3,)))),
                                         card("Record leave on behalf of an employee", nb)))


@route("POST", r"/leave/new",LEAVE)
def leave_new(req):
    try:
        days = create_leave(req, int(req.f("emp_id") or 0), int(req.f("type_id") or 0), req.f("start"), req.f("end"), req.f("reason"))
    except ValueError as e:
        return fail(req, str(e), "/leave")
    req.flash("Leave request recorded (%d days). Approve it below." % days)
    return redirect("/leave")


@route("POST", r"/leave/(?P<lid>\d+)/decide",LEAVE)
def leave_decide(req, lid):
    c = req.conn
    r = c.execute("SELECT r.*, t.paid FROM leave_requests r JOIN leave_types t ON t.id=r.type_id WHERE r.id=?", (lid,)).fetchone()
    if not r:
        abort(404, "Request not found.")
    d, remarks = req.f("d"), req.f("remarks")
    now = D.now_local(c).strftime("%Y-%m-%d %H:%M")
    s, e = D.parse_date(r["start_date"]), D.parse_date(r["end_date"])
    if d in ("approve", "reject") and r["status"] == "pending":
        new = "approved" if d == "approve" else "rejected"
        c.execute("UPDATE leave_requests SET status=?, decided_by=?, decided_at=?, remarks=? WHERE id=?", (new, req.user["id"], now, remarks, r["id"]))
        if new == "approved":
            code = "L" if r["paid"] else "UL"
            for day in L.working_dates(s, e, L.weekly_off(c)):
                c.execute("INSERT INTO attendance(emp_id,adate,status,note) VALUES(?,?,?,?) ON CONFLICT(emp_id,adate) DO UPDATE SET status=excluded.status, note=excluded.note",
                          (r["emp_id"], day.isoformat(), code, "leave #%d" % r["id"]))
    elif d == "cancel" and r["status"] == "approved":
        c.execute("UPDATE leave_requests SET status='cancelled', decided_by=?, decided_at=?, remarks=? WHERE id=?", (req.user["id"], now, remarks, r["id"]))
        c.execute("DELETE FROM attendance WHERE emp_id=? AND adate BETWEEN ? AND ? AND note=?", (r["emp_id"], s.isoformat(), e.isoformat(), "leave #%d" % r["id"]))
    else:
        return fail(req, "This request can no longer be changed that way.", "/leave")
    D.audit(c, req.user["username"], "leave_" + d, "#%s" % lid)
    req.flash("Leave request updated.")
    return redirect("/leave")


# ----------------------------------------------------- employee portal
def my_emp(req):
    if not req.user["employee_id"]:
        abort(403, "Your account is not linked to an employee record.")
    return get_emp(req, req.user["employee_id"])


@route("GET", r"/my/leave", ALL)
def my_leave(req):
    e = my_emp(req)
    c = req.conn
    bals = L.leave_balances(c, e["id"], D.today(c).year)
    bt = table(["Type", "Quota", "Used", "Pending", "Remaining"], [[b["name"], b["quota"] or "—", b["used"], b["pending"], "—" if b["remaining"] is None else b["remaining"]] for b in bals], (1, 2, 3, 4))
    rows = []
    for r in c.execute("SELECT r.*, t.name tname FROM leave_requests r JOIN leave_types t ON t.id=r.type_id WHERE r.emp_id=? ORDER BY r.id DESC LIMIT 100", (e["id"],)):
        act = post_form(req, "/my/leave/%d/cancel" % r["id"], Raw('<button class="btn danger sm">Withdraw</button>'), "row", confirm="Withdraw this request?") if r["status"] == "pending" else ""
        rows.append([r["tname"], "%s → %s" % (r["start_date"], r["end_date"]), r["days"], status_badge(r["status"]), r["remarks"] or "", act])
    return page(req, "My leave", H(card("Balance %d" % D.today(c).year, bt), card("Apply for leave", apply_form(req, None, "/my/leave")),
                                   card("My requests", table(["Type", "Period", "Days", "Status", "Remarks", ""], rows, (2,)))))


@route("POST", r"/my/leave", ALL)
def my_leave_post(req):
    e = my_emp(req)
    try:
        days = create_leave(req, e["id"], int(req.f("type_id") or 0), req.f("start"), req.f("end"), req.f("reason"))
    except ValueError as ex:
        return fail(req, str(ex), "/my/leave")
    req.flash("Request for %d day(s) sent for approval." % days)
    return redirect("/my/leave")


@route("POST", r"/my/leave/(?P<lid>\d+)/cancel", ALL)
def my_leave_cancel(req, lid):
    e = my_emp(req)
    n = req.conn.execute("UPDATE leave_requests SET status='cancelled' WHERE id=? AND emp_id=? AND status='pending'", (lid, e["id"])).rowcount
    req.flash("Request withdrawn." if n else "Nothing to withdraw.", "ok" if n else "err")
    return redirect("/my/leave")


def office_point(c):
    try:
        return float(D.get_setting(c, "office_lat")), float(D.get_setting(c, "office_lng"))
    except ValueError:
        return None


def today_text(c, emp_id):
    """(record, short status text) for today."""
    r = c.execute("SELECT * FROM attendance WHERE emp_id=? AND adate=?", (emp_id, D.today(c).isoformat())).fetchone()
    if r and r["status"] in ("L", "UL"):
        return r, "On leave today"
    if r and r["check_in"] and r["check_out"]:
        return r, "Checked in %s, out %s (%s)" % (r["check_in"], r["check_out"], L.worked(r["check_in"], r["check_out"]))
    if r and r["check_in"]:
        return r, "Checked in at %s" % r["check_in"]
    return r, "Not checked in yet"


CHECK_JS = """<script>(function(){var f=document.getElementById('chk'),b=document.getElementById('chkbtn'),m=document.getElementById('chkmsg'),need=NEED;
if(!b)return;b.addEventListener('click',function(){b.disabled=true;m.textContent='Getting your location...';
if(!navigator.geolocation){if(need){m.textContent='This browser cannot share location.';b.disabled=false;return}f.submit();return}
navigator.geolocation.getCurrentPosition(function(p){f.elements['lat'].value=p.coords.latitude;f.elements['lng'].value=p.coords.longitude;f.elements['acc'].value=Math.round(p.coords.accuracy);f.submit()},
function(){if(need){m.textContent='Location is required. Allow location for this app/site in your phone settings and try again.';b.disabled=false}else{f.submit()}},
{enableHighAccuracy:true,timeout:20000,maximumAge:0})})})();</script>"""


@route("GET", r"/my/attendance", ALL)
def my_att(req):
    e = my_emp(req)
    c = req.conn
    now = D.now_local(c)
    month = req.q("month") or now.strftime("%Y-%m")
    try:
        first, last = D.month_bounds(month)
    except (ValueError, IndexError):
        return fail(req, "Invalid month.", "/my/attendance")
    rec, status = today_text(c, e["id"])
    need = D.get_setting(c, "geofence") == "1"
    onleave = bool(rec and rec["status"] in ("L", "UL"))
    if onleave or (rec and rec["check_in"] and rec["check_out"]):
        action = Raw('<p class="muted">%s</p>' % ("Enjoy your leave." if onleave else "Done for today. See you tomorrow."))
    else:
        act, label = ("out", "Check OUT") if rec and rec["check_in"] else ("in", "Check IN")
        action = post_form(req, "/my/attendance/check", Raw(
            '<input type="hidden" name="act" value="%s"><input type="hidden" name="lat"><input type="hidden" name="lng"><input type="hidden" name="acc">'
            '<button type="button" id="chkbtn" class="btn primary big">%s</button><p id="chkmsg" class="muted" style="margin:8px 0 0">%s</p>'
            % (act, label, "Your location is checked against the office." if need else "Your location is recorded with the time.")), "")
        action = Raw(str(action).replace('<form ', '<form id="chk" ', 1) + CHECK_JS.replace("NEED", "true" if need else "false"))
    late = " " + str(badge("Late", "amber")) if rec and L.is_late(c, rec["check_in"]) else ""
    today_card = card("Today — %s" % now.strftime("%a %d %b, %H:%M"), H(Raw('<p style="font-size:18px;margin:0 0 12px"><b>%s</b>%s</p>' % (esc(status), late)), action))
    rows = []
    for r in c.execute("SELECT * FROM attendance WHERE emp_id=? AND adate BETWEEN ? AND ? ORDER BY adate DESC", (e["id"], first.isoformat(), last.isoformat())):
        rows.append([r["adate"], L.WD[D.parse_date(r["adate"]).weekday()], D.ATT_STATUS.get(r["status"], r["status"]), r["check_in"] or "—", r["check_out"] or "—",
                     L.worked(r["check_in"], r["check_out"]) or "—", badge("Late", "amber") if L.is_late(c, r["check_in"]) else ""])
    bar = Raw('<form class="bar" method="get">%s<button class="btn ghost">Show</button></form>' % inp("Month", "month", month, "month"))
    return page(req, "My attendance", H(today_card, card(month, H(bar, table(["Date", "Day", "Status", "In", "Out", "Hours", ""], rows)))))


@route("POST", r"/my/attendance/check", ALL)
def my_check(req):
    e = my_emp(req)
    c = req.conn
    back_to = "/my/attendance"
    if e["status"] != "active":
        return fail(req, "Your employment record is not active.", back_to)
    act = req.f("act")
    if act not in ("in", "out"):
        return fail(req, "Invalid request.", back_to)
    # ---- location rules
    loc = None
    try:
        lat, lng, acc = float(req.f("lat")), float(req.f("lng")), float(req.f("acc") or 0)
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            loc = (lat, lng, acc)
    except ValueError:
        pass
    office = office_point(c)
    dist = None
    if D.get_setting(c, "geofence") == "1":
        if not office:
            return fail(req, "Office location is not set yet. Please ask the administrator.", back_to)
        if not loc:
            return fail(req, "Location is required to mark attendance. Allow location access and try again.", back_to)
        if loc[2] > 150:
            return fail(req, "GPS is not accurate enough (%d m). Go near a window or outdoors and try again." % loc[2], back_to)
        dist = L.distance_m(loc[0], loc[1], office[0], office[1])
        try:
            radius = int(D.get_setting(c, "office_radius", "200"))
        except ValueError:
            radius = 200
        if dist > radius:
            return fail(req, "You are %d m away from the office. Marking is allowed within %d m." % (dist, radius), back_to)
    elif loc and office:
        dist = L.distance_m(loc[0], loc[1], office[0], office[1])
    loc_text = ("%.5f,%.5f,%s" % (loc[0], loc[1], int(dist) if dist is not None else "")) if loc else None
    # ---- attendance rules (time always comes from the server, never from the phone)
    now = D.now_local(c)
    today, t = now.date().isoformat(), now.strftime("%H:%M")
    r = c.execute("SELECT * FROM attendance WHERE emp_id=? AND adate=?", (e["id"], today)).fetchone()
    if r and r["status"] in ("L", "UL"):
        return fail(req, "You are on approved leave today.", back_to)
    if act == "in":
        if r and r["check_in"]:
            return fail(req, "You already checked in today at %s." % r["check_in"], back_to)
        c.execute("INSERT INTO attendance(emp_id,adate,status,check_in,in_loc,via) VALUES(?,?,'P',?,?,'mobile') "
                  "ON CONFLICT(emp_id,adate) DO UPDATE SET status='P', check_in=excluded.check_in, in_loc=excluded.in_loc, via='mobile'", (e["id"], today, t, loc_text))
        msg = "Checked in at %s." % t + (" You are late." if L.is_late(c, t) else "")
    else:
        if not r or not r["check_in"]:
            return fail(req, "Check in first.", back_to)
        if r["check_out"]:
            return fail(req, "You already checked out today at %s." % r["check_out"], back_to)
        c.execute("UPDATE attendance SET check_out=?, out_loc=? WHERE id=?", (t, loc_text, r["id"]))
        msg = "Checked out at %s. Worked %s." % (t, L.worked(r["check_in"], t))
    D.audit(c, req.user["username"], "check_" + act, "%s %s%s" % (today, t, (" %dm" % dist) if dist is not None else ""))
    req.flash(msg)
    return redirect(back_to)


@route("GET", r"/privacy")
def privacy(req):
    c = req.conn
    co = D.get_setting(c, "company_name")
    mail = D.get_setting(c, "contact_email")
    body = ('<div class="slip" style="margin:24px auto"><h2>Privacy policy</h2><p class="muted">%s HRMS</p>'
            '<p>This app is used by employees and administrators of <b>%s</b> to manage attendance, leave and salary records.</p>'
            '<h3>What we collect</h3><ul><li>Employee profile: name, CNIC, contact details, job and bank details, salary.</li>'
            '<li>Attendance: check-in and check-out time. When you tap Check in / Check out, your phone location at that moment is recorded to confirm you are at the workplace. '
            'Location is <b>not</b> tracked in the background or at any other time.</li><li>Leave requests and payslips.</li></ul>'
            '<h3>How it is used</h3><p>Only to run payroll, attendance and leave for your employer. Data is not sold and is not shared with advertisers.</p>'
            '<h3>Who can see it</h3><p>You see your own records. Authorised HR, accounts and administrator staff of your employer see employee records needed for their work.</p>'
            '<h3>Security and retention</h3><p>Access needs a personal login; passwords are stored hashed; the site uses HTTPS. Records are kept as long as your employer needs them for employment and legal reasons.</p>'
            '<h3>Contact</h3><p>For questions or correction/deletion requests contact your employer%s.</p></div>'
            % (esc(co), esc(co), (" at <b>%s</b>" % esc(mail)) if mail else "'s HR department"))
    return bare_page("Privacy policy", body)



"""Payroll runs, payslips, bank sheet."""
from . import db as D
from . import logic as L
from .web import (H, Raw, Response, abort, btn, card, esc, inp, link_btn, page, post_form, redirect, route, sel, status_badge, table)
from .views_core import fail, ALL

PAY, FIN, HRR = ("pay_view",), ("pay_post",), ("pay_prepare",)


def get_run(req, rid):
    r = req.conn.execute("SELECT * FROM payroll_runs WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404, "Payroll run not found.")
    return r


@route("GET", r"/payroll", PAY)
def payroll_list(req):
    c = req.conn
    rows = []
    for r in c.execute("SELECT * FROM payroll_runs ORDER BY month DESC"):
        t = L.run_totals(c, r["id"])
        rows.append([Raw('<a href="/payroll/%d">%s</a>' % (r["id"], esc(r["month"]))), status_badge(r["status"]), t["n"], D.fmt(t["gross"]), D.fmt(t["net"])])
    new = ""
    if req.has_role("pay_prepare"):
        new = post_form(req, "/payroll/new", H(inp("Month", "month", D.today(c).strftime("%Y-%m"), "month", True), Raw(btn("Create payroll run"))), "form")
        new = card("Start a new month", new)
    return page(req, "Payroll", H(card("Payroll runs", table(["Month", "Status", "Employees", "Gross", "Net payable"], rows, (2, 3, 4), "No payroll run yet.")), new))


@route("POST", r"/payroll/new", HRR)
def payroll_new(req):
    c = req.conn
    month = req.f("month")
    try:
        D.month_bounds(month)
        if len(month) != 7:
            raise ValueError
    except (ValueError, IndexError):
        return fail(req, "Choose a valid month.", "/payroll")
    if c.execute("SELECT 1 FROM payroll_runs WHERE month=?", (month,)).fetchone():
        return fail(req, "A payroll run for %s already exists." % month, "/payroll")
    rid = c.execute("INSERT INTO payroll_runs(month,created) VALUES(?,?)", (month, D.now_local(c).strftime("%Y-%m-%d %H:%M"))).lastrowid
    n = L.generate_run(c, rid, month)
    D.audit(c, req.user["username"], "payroll_created", month)
    req.flash("Payroll for %s created with %d employees. Review it, then post." % (month, n))
    return redirect("/payroll/%d" % rid)


@route("GET", r"/payroll/(?P<rid>\d+)", PAY)
def payroll_view(req, rid):
    c = req.conn
    run = get_run(req, rid)
    t = L.run_totals(c, run["id"])
    draft = run["status"] == "draft"
    rows = []
    for s in c.execute("SELECT p.*, e.name, e.code FROM payslips p JOIN employees e ON e.id=p.emp_id WHERE p.run_id=? ORDER BY e.code", (run["id"],)):
        ded = s["income_tax"] + s["provident_fund"] + s["loan"] + s["other_deduction"]
        if draft and req.has_role("pay_prepare"):
            adj = post_form(req, "/payroll/%d/slip/%d" % (run["id"], s["id"]), Raw(
                '<input name="bonus" value="%s" style="width:90px" title="Bonus"> <input name="other" value="%s" style="width:90px" title="Other deduction"> '
                '<input name="note" value="%s" placeholder="Note" style="width:110px"> <button class="btn ghost sm">Save</button>' % (D.plain(s["bonus"]), D.plain(s["other_deduction"]), esc(s["note"] or ""))), "row")
        else:
            adj = ""
        rows.append([s["code"], Raw('<a href="/payslip/%d">%s</a>' % (s["id"], esc(s["name"]))), D.fmt(s["gross"]), "%d / %s" % (s["absent_days"], D.fmt(s["absent_deduction"])),
                     D.fmt(s["bonus"]), D.fmt(ded), D.fmt(s["net"]), adj])
    foot = Raw('<p><b>Totals:</b> Gross %s · Absence deduction %s · Bonus %s · Tax %s · PF %s · Loan %s · Other %s · <b>Net %s</b></p>'
               % tuple(esc(D.fmt(x)) for x in (t["gross"], t["absd"], t["bonus"], t["tax"], t["pf"], t["loan"], t["od"], t["net"])))
    acts = []
    if draft:
        if req.has_role("pay_prepare"):
            acts.append(post_form(req, "/payroll/%d/regen" % run["id"], Raw(btn("Recalculate", "ghost")), "inline", confirm="Recalculate from current salaries and attendance? Bonus/deduction entries are kept."))
            acts.append(post_form(req, "/payroll/%d/delete" % run["id"], Raw(btn("Delete run", "danger")), "inline", confirm="Delete this draft run?"))
        if req.has_role("pay_post"):
            acts.append(post_form(req, "/payroll/%d/post" % run["id"], Raw(btn("Post to accounts")), "inline", confirm="Post salary journal to accounts?"))
    if run["status"] == "posted":
        accs = [(a["id"], a["name"]) for a in c.execute("SELECT * FROM accounts WHERE code IN ('1001','1002') ORDER BY code")]
        if req.has_role("pay_post"):
            acts.append(post_form(req, "/payroll/%d/pay" % run["id"], H(sel("Pay from", "acc", accs, required=True), inp("Payment date", "pdate", D.today(c).isoformat(), "date", True), Raw(btn("Mark as paid"))), "bar"))
            if req.role == "admin":
                acts.append(post_form(req, "/payroll/%d/revert" % run["id"], Raw(btn("Revert posting", "danger")), "inline", confirm="Reverse the journal voucher and reopen as draft?"))
    acts.append(link_btn("Bank sheet (CSV)", "/payroll/%d/csv" % run["id"]))
    vlinks = ""
    for lbl, vid in (("Journal voucher", run["post_voucher_id"]), ("Payment voucher", run["pay_voucher_id"])):
        if vid and req.has_role("acc_view"):
            vlinks += ' <a href="/accounts/vouchers/%d">%s</a>' % (vid, lbl)
    head = Raw('<p>Status: %s%s</p>' % (status_badge(run["status"]), vlinks))
    tbl = table(["Code", "Employee", "Gross", "Absent days / ded.", "Bonus", "Deductions", "Net", "Adjust (bonus · other ded.)" if draft else ""], rows, (2, 4, 5, 6), "No employees in this run.")
    return page(req, "Payroll — " + run["month"], H(head, card("Payslips", H(tbl, foot)), Raw('<div class="row noprint">'), *acts, Raw("</div>")))


@route("POST", r"/payroll/(?P<rid>\d+)/slip/(?P<sid>\d+)", HRR)
def slip_adjust(req, rid, sid):
    c = req.conn
    run = get_run(req, rid)
    if run["status"] != "draft":
        return fail(req, "Only draft payroll can be adjusted.", "/payroll/%s" % rid)
    s = c.execute("SELECT * FROM payslips WHERE id=? AND run_id=?", (sid, rid)).fetchone()
    if not s:
        abort(404, "Payslip not found.")
    try:
        bonus, od = D.to_paisa(req.f("bonus")), D.to_paisa(req.f("other"))
        if bonus < 0 or od < 0:
            raise ValueError("Amounts cannot be negative.")
    except ValueError as e:
        return fail(req, str(e), "/payroll/%s" % rid)
    net = s["gross"] - s["absent_deduction"] + bonus - (s["income_tax"] + s["provident_fund"] + s["loan"] + od)
    c.execute("UPDATE payslips SET bonus=?, other_deduction=?, note=?, net=? WHERE id=?", (bonus, od, req.f("note")[:120], net, s["id"]))
    req.flash("Payslip updated.")
    return redirect("/payroll/%s" % rid)


@route("POST", r"/payroll/(?P<rid>\d+)/regen", HRR)
def run_regen(req, rid):
    run = get_run(req, rid)
    if run["status"] != "draft":
        return fail(req, "Only draft payroll can be recalculated.", "/payroll/%s" % rid)
    n = L.generate_run(req.conn, run["id"], run["month"])
    req.flash("Recalculated %d payslips." % n)
    return redirect("/payroll/%s" % rid)


@route("POST", r"/payroll/(?P<rid>\d+)/delete", HRR)
def run_delete(req, rid):
    run = get_run(req, rid)
    if run["status"] != "draft":
        return fail(req, "Only draft payroll can be deleted.", "/payroll/%s" % rid)
    req.conn.execute("DELETE FROM payroll_runs WHERE id=?", (run["id"],))
    D.audit(req.conn, req.user["username"], "payroll_deleted", run["month"])
    req.flash("Draft payroll deleted.")
    return redirect("/payroll")


@route("POST", r"/payroll/(?P<rid>\d+)/post", FIN)
def run_post(req, rid):
    run = get_run(req, rid)
    if run["status"] != "draft":
        return fail(req, "Already posted.", "/payroll/%s" % rid)
    try:
        vid = L.post_run(req.conn, run, req.user["id"])
    except ValueError as e:
        return fail(req, str(e), "/payroll/%s" % rid)
    D.audit(req.conn, req.user["username"], "payroll_posted", run["month"])
    req.flash("Posted to accounts as a journal voucher.")
    return redirect("/payroll/%s" % rid)


@route("POST", r"/payroll/(?P<rid>\d+)/pay", FIN)
def run_pay(req, rid):
    c = req.conn
    run = get_run(req, rid)
    if run["status"] != "posted":
        return fail(req, "Payroll must be posted first (and not already paid).", "/payroll/%s" % rid)
    acc = c.execute("SELECT * FROM accounts WHERE id=? AND code IN ('1001','1002')", (req.f("acc"),)).fetchone()
    try:
        D.parse_date(req.f("pdate"))
    except ValueError:
        return fail(req, "Enter a valid payment date.", "/payroll/%s" % rid)
    if not acc:
        return fail(req, "Choose Cash or Bank.", "/payroll/%s" % rid)
    try:
        L.pay_run(c, run, acc["id"], req.f("pdate"), req.user["id"])
    except ValueError as e:
        return fail(req, str(e), "/payroll/%s" % rid)
    D.audit(c, req.user["username"], "payroll_paid", run["month"])
    req.flash("Salary marked as paid; payment voucher created.")
    return redirect("/payroll/%s" % rid)


@route("POST", r"/payroll/(?P<rid>\d+)/revert", ("admin",))
def run_revert(req, rid):
    c = req.conn
    run = get_run(req, rid)
    if run["status"] != "posted":
        return fail(req, "Only a posted (unpaid) run can be reverted.", "/payroll/%s" % rid)
    c.execute("UPDATE payroll_runs SET status='draft', post_voucher_id=NULL WHERE id=?", (run["id"],))
    c.execute("DELETE FROM vouchers WHERE id=?", (run["post_voucher_id"],))
    D.audit(c, req.user["username"], "payroll_reverted", run["month"])
    req.flash("Posting reverted; run is a draft again.")
    return redirect("/payroll/%s" % rid)


@route("GET", r"/payroll/(?P<rid>\d+)/csv", PAY)
def run_csv(req, rid):
    run = get_run(req, rid)
    def q(x):
        return '"%s"' % str(x or "").replace('"', '""')
    lines = ["Code,Name,Bank,Account,Net"]
    for s in req.conn.execute("SELECT p.net, e.* FROM payslips p JOIN employees e ON e.id=p.emp_id WHERE p.run_id=? ORDER BY e.code", (run["id"],)):
        lines.append(",".join([q(s["code"]), q(s["name"]), q(s["bank_name"]), q(s["bank_account"]), D.plain(s["net"])]))
    return Response("\n".join(lines) + "\n", ctype="text/csv; charset=utf-8",
                    headers=[("Content-Disposition", 'attachment; filename="salary-%s.csv"' % run["month"])])


def slip_page(req, info, s, banner=""):
    """Render one payslip. info: employee fields + month; s: the amounts (a payslip row or compute_slip result)."""
    c = req.conn
    cur = D.get_setting(c, "currency")
    earn = [("Basic salary", s["basic"]), ("House rent", s["house_rent"]), ("Medical allowance", s["medical"]), ("Conveyance", s["conveyance"]),
            ("Other allowance", s["other_allow"]), ("Bonus", s["bonus"])]
    ded = [("Absence (%d day(s))" % s["absent_days"], s["absent_deduction"]), ("Income tax", s["income_tax"]), ("Provident fund", s["provident_fund"]),
           ("Loan installment", s["loan"]), ("Other deductions", s["other_deduction"])]
    te = s["gross"] + s["bonus"]
    td = s["absent_deduction"] + s["income_tax"] + s["provident_fund"] + s["loan"] + s["other_deduction"]
    er = [[k, D.fmt(v)] for k, v in earn if v]
    dr = [[k, D.fmt(v)] for k, v in ded if v]
    body = Raw('%s<div class="slip"><div class="hd"><div><h2>%s</h2><div class="muted">Salary slip — %s</div></div><div style="text-align:right"><b>%s</b><br>%s</div></div>'
               '<dl class="kv"><dt>Department</dt><dd>%s</dd><dt>Designation</dt><dd>%s</dd><dt>CNIC</dt><dd>%s</dd><dt>Bank</dt><dd>%s %s</dd></dl><br>'
               '<div class="two"><div><h3>Earnings</h3>%s<p><b>Total: %s</b></p></div><div><h3>Deductions</h3>%s<p><b>Total: %s</b></p></div></div>'
               '<div style="margin-top:14px;border:2px solid #17212b;border-radius:8px;padding:12px 16px;background:#eaf6f3;display:flex;justify-content:space-between;'
               'align-items:center;gap:12px;flex-wrap:wrap;-webkit-print-color-adjust:exact;print-color-adjust:exact"><div><b>NET SALARY</b> <span class="muted">(Earnings − Deductions)</span>'
               '<div style="font-size:13px;margin-top:2px">%s</div></div><div style="font-size:28px;font-weight:800">%s %s</div></div>%s</div>'
               '<p class="noprint"><button class="btn primary" onclick="window.print()">Print</button></p>'
               % (banner, esc(D.get_setting(c, "company_name")), esc(info["month"]), esc(info["name"]), esc(info["code"]), esc(info["dept"] or "—"), esc(info["desig"] or "—"),
                  esc(info["cnic"] or "—"), esc(info["bank_name"] or ""), esc(info["bank_account"] or ""), table(["Item", cur], er, (1,)), esc(D.fmt(te)),
                  table(["Item", cur], dr, (1,), "No deductions."), esc(D.fmt(td)), esc(D.amount_in_words(s["net"])), esc(cur), esc(D.fmt(s["net"])),
                  ('<p class="muted">Note: %s</p>' % esc(s["note"])) if s["note"] else ""))
    return page(req, "Payslip", body)


EMP_INFO_SQL = ("e.name, e.code, e.cnic, e.bank_name, e.bank_account, d.name dept, g.name desig "
                "FROM employees e LEFT JOIN departments d ON d.id=e.dept_id LEFT JOIN designations g ON g.id=e.desig_id")


@route("GET", r"/payslip/(?P<sid>\d+)", ALL)
def payslip(req, sid):
    c = req.conn
    s = c.execute("SELECT p.*, r.month, r.status rstatus, e.name, e.code, e.cnic, e.bank_name, e.bank_account, d.name dept, g.name desig "
                  "FROM payslips p JOIN payroll_runs r ON r.id=p.run_id JOIN employees e ON e.id=p.emp_id "
                  "LEFT JOIN departments d ON d.id=e.dept_id LEFT JOIN designations g ON g.id=e.desig_id WHERE p.id=?", (sid,)).fetchone()
    if not s:
        abort(404, "Payslip not found.")
    if not req.has_role("pay_view"):
        if s["emp_id"] != req.user["employee_id"] or s["rstatus"] == "draft":
            abort(403, "You cannot view this payslip.")
    banner = ""
    if s["rstatus"] == "draft":
        banner = '<p class="flash err noprint">This payroll is still a draft. The employee cannot see this payslip until it is posted.</p>'
    return slip_page(req, s, s, banner)


@route("GET", r"/employees/(?P<eid>\d+)/payslip-preview", PAY)
def payslip_preview(req, eid):
    c = req.conn
    e = c.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone()
    if not e:
        abort(404, "Employee not found.")
    month = req.q("month") or D.today(c).strftime("%Y-%m")
    try:
        first, last = D.month_bounds(month)
        if len(month) != 7:
            raise ValueError
    except (ValueError, IndexError):
        return fail(req, "Choose a valid month.", "/employees/%s" % eid)
    if D.parse_date(e["join_date"]) > last:
        return fail(req, "%s had not joined by %s." % (e["name"], month), "/employees/%s" % eid)
    s = L.compute_slip(c, e, month)
    info = c.execute("SELECT " + EMP_INFO_SQL + " WHERE e.id=?", (e["id"],)).fetchone()
    banner = '<p class="flash err noprint"><b>PREVIEW</b> — calculated from the current salary and attendance. Not a final payslip.</p>'
    return slip_page(req, dict(info, month=month), s, banner)


@route("GET", r"/my/payslips", ALL)
def my_payslips(req):
    if not req.user["employee_id"]:
        abort(403, "Your account is not linked to an employee record.")
    rows = [[Raw('<a href="/payslip/%d">%s</a>' % (r["id"], esc(r["month"]))), D.fmt(r["gross"]), D.fmt(r["net"]), status_badge(r["status"])]
            for r in req.conn.execute("SELECT p.id, p.gross, p.net, r.month, r.status FROM payslips p JOIN payroll_runs r ON r.id=p.run_id "
                                      "WHERE p.emp_id=? AND r.status IN ('posted','paid') ORDER BY r.month DESC", (req.user["employee_id"],))]
    return page(req, "My payslips", card("Payslips", table(["Month", "Gross", "Net", "Status"], rows, (1, 2), "No payslip published yet.")))

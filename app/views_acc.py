"""Accounts: vouchers, chart of accounts, ledger, trial balance, financial reports."""
from . import db as D
from .web import (H, Raw, abort, btn, card, esc, inp, link_btn, page, post_form, redirect, route, sel, table, badge)
from .views_core import fail

ACCV, ACCW, ACCC = ("acc_view",), ("acc_voucher",), ("acc_chart",)
VTYPES = [("JV", "Journal Voucher"), ("CPV", "Cash Payment"), ("CRV", "Cash Receipt"), ("BPV", "Bank Payment"), ("BRV", "Bank Receipt")]
TYPE_NAME = dict(VTYPES)
ATYPES = [("asset", "Asset"), ("liability", "Liability"), ("equity", "Equity"), ("income", "Income"), ("expense", "Expense")]


def acc_options(c, only_active=True):
    q = "SELECT * FROM accounts" + (" WHERE active=1" if only_active else "") + " ORDER BY code"
    return [(a["id"], "%s — %s" % (a["code"], a["name"])) for a in c.execute(q)]


def valid_date(s):
    try:
        D.parse_date(s)
        return True
    except ValueError:
        return False


# ------------------------------------------------------------- vouchers
@route("GET", r"/accounts/vouchers", ACCV)
def vouchers(req):
    c = req.conn
    vt, fr, to = req.q("vtype"), req.q("from"), req.q("to")
    sql = "SELECT v.*, (SELECT COALESCE(SUM(debit),0) FROM voucher_lines WHERE voucher_id=v.id) amt FROM vouchers v WHERE 1=1"
    args = []
    if vt in TYPE_NAME:
        sql += " AND v.vtype=?"
        args.append(vt)
    if fr and valid_date(fr):
        sql += " AND v.vdate>=?"
        args.append(fr)
    if to and valid_date(to):
        sql += " AND v.vdate<=?"
        args.append(to)
    rows = [[Raw('<a href="/accounts/vouchers/%d">%s</a>' % (v["id"], esc(v["vno"]))), v["vdate"], TYPE_NAME.get(v["vtype"], v["vtype"]), v["narration"] or "", D.fmt(v["amt"])]
            for v in c.execute(sql + " ORDER BY v.vdate DESC, v.id DESC LIMIT 300", args)]
    flt = Raw('<form class="bar" method="get">%s%s%s<button class="btn ghost">Filter</button></form>'
              % (sel("Type", "vtype", VTYPES, vt, blank="All"), inp("From", "from", fr, "date"), inp("To", "to", to, "date")))
    return page(req, "Vouchers", card("Voucher register", H(flt, table(["No.", "Date", "Type", "Narration", "Amount"], rows, (4,))), (link_btn("+ New voucher", "/accounts/vouchers/new", "primary") if req.has_role("acc_voucher") else "")))


def cash_accounts(c):
    """Cash / bank accounts are the active assets with codes 1001-1099."""
    return c.execute("SELECT * FROM accounts WHERE atype='asset' AND active=1 AND code LIKE '10__' ORDER BY code").fetchall()


GROUP_ORDER = {
    "payment": ["expense", "liability", "asset", "equity", "income"],
    "receipt": ["income", "liability", "equity", "asset", "expense"],
}
GROUP_NAME = {"expense": "Expenses", "income": "Income", "liability": "Liabilities", "asset": "Assets", "equity": "Equity"}


def head_select(c, kind):
    accs = c.execute("SELECT * FROM accounts WHERE active=1 ORDER BY code").fetchall()
    out = ['<select name="acc"><option value="">— choose account —</option>']
    for t in GROUP_ORDER[kind]:
        items = [a for a in accs if a["atype"] == t]
        if items:
            out.append('<optgroup label="%s">%s</optgroup>' % (GROUP_NAME[t], "".join('<option value="%d">%s — %s</option>' % (a["id"], esc(a["code"]), esc(a["name"])) for a in items)))
    out.append("</select>")
    return "".join(out)


VOUCHER_JS = """<script>(function(){var t=document.getElementById('tot'),c=document.getElementById('cashsel');
function f(n){return n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}
function upd(){var s=0;document.querySelectorAll('input[name=amt]').forEach(function(i){s+=parseFloat(i.value)||0});
t.textContent=f(s)+' '+(KIND=='payment'?'will be paid out of':'will be received into')+' '+c.options[c.selectedIndex].text}
document.querySelectorAll('input[name=amt]').forEach(function(i){i.addEventListener('input',upd)});c.addEventListener('change',upd);upd()})();</script>"""


@route("GET", r"/accounts/vouchers/new", ACCW)
def voucher_new(req):
    c = req.conn
    kind = "receipt" if req.q("kind") == "receipt" else "payment"
    cash = cash_accounts(c)
    tabs = Raw('<div class="row" style="margin-bottom:14px">%s %s %s</div>' % (
        link_btn("Payment (money going out)", "/accounts/vouchers/new?kind=payment", "primary" if kind == "payment" else "ghost"),
        link_btn("Receipt (money coming in)", "/accounts/vouchers/new?kind=receipt", "primary" if kind == "receipt" else "ghost"),
        link_btn("Journal (advanced)", "/accounts/vouchers/journal")))
    label = "Paid from" if kind == "payment" else "Received in"
    head_label = "Expense / account paid" if kind == "payment" else "Income / account received for"
    rows = "".join('<tr><td>%s</td><td><input name="amt" type="number" step="0.01" min="0" inputmode="decimal" style="text-align:right" placeholder="0.00"></td>'
                   '<td><input name="memo" placeholder="Details (optional)"></td></tr>' % head_select(c, kind) for _ in range(6))
    inner = H(Raw('<input type="hidden" name="kind" value="%s">' % kind),
              Raw('<div class="form">'), Raw('<label class="fld"><span>%s *</span><select name="cash" id="cashsel" required>%s</select></label>'
                                              % (label, "".join('<option value="%d">%s — %s</option>' % (a["id"], esc(a["code"]), esc(a["name"])) for a in cash))),
              inp("Date", "vdate", D.today(c).isoformat(), "date", True), inp("Narration (optional)", "narration", ""), Raw("</div>"),
              Raw("<div class='tw'><table><thead><tr><th>%s</th><th class='num'>Amount</th><th>Details</th></tr></thead><tbody>%s</tbody></table></div>" % (head_label, rows)),
              Raw('<p><b id="tot"></b></p><p class="muted">The Cash/Bank side is added for you automatically; just pick the account and amount. '
                  'Use more than one row if the payment covers several heads.</p><p>%s %s</p>' % (btn("Save voucher"), link_btn("Cancel", "/accounts/vouchers"))))
    return page(req, "New %s" % kind, H(tabs, card("Voucher", post_form(req, "/accounts/vouchers/new", inner)), Raw(VOUCHER_JS.replace("KIND", '"%s"' % kind))))


@route("POST", r"/accounts/vouchers/new", ACCW)
def voucher_create(req):
    c = req.conn
    kind = req.f("kind")
    vdate = req.f("vdate")
    back = "/accounts/vouchers/new?kind=%s" % ("receipt" if kind == "receipt" else "payment")
    if kind not in ("payment", "receipt") or not valid_date(vdate):
        return fail(req, "Choose payment or receipt and a valid date.", back)
    cash = c.execute("SELECT * FROM accounts WHERE id=? AND atype='asset' AND active=1 AND code LIKE '10__'", (req.f("cash"),)).fetchone()
    if not cash:
        return fail(req, "Choose the Cash or Bank account.", back)
    accs, amts, memos = req.fl("acc"), req.fl("amt"), req.fl("memo")
    lines, total, names = [], 0, []
    try:
        for i, a in enumerate(accs):
            amt = D.to_paisa(amts[i] if i < len(amts) else "")
            if not a and not amt:
                continue
            if not a:
                raise ValueError("Row %d has an amount but no account." % (i + 1))
            if amt <= 0:
                raise ValueError("Row %d: enter an amount greater than zero." % (i + 1))
            head = c.execute("SELECT * FROM accounts WHERE id=? AND active=1", (a,)).fetchone()
            if not head:
                raise ValueError("Row %d: invalid account." % (i + 1))
            if head["id"] == cash["id"]:
                raise ValueError("Row %d: the account cannot be the same as %s." % (i + 1, cash["name"]))
            memo = memos[i] if i < len(memos) else ""
            lines.append((head["id"], amt, 0, memo) if kind == "payment" else (head["id"], 0, amt, memo))
            total += amt
            names.append(head["name"])
        if not lines:
            raise ValueError("Add at least one account and amount.")
        lines.append((cash["id"], 0, total, "") if kind == "payment" else (cash["id"], total, 0, ""))
        vtype = ("C" if cash["code"] == "1001" else "B") + ("PV" if kind == "payment" else "RV")
        narration = req.f("narration") or ("%s: %s" % ("Payment" if kind == "payment" else "Receipt", ", ".join(dict.fromkeys(names))))
        vid = D.create_voucher(c, vtype, vdate, narration, lines, req.user["id"], None)
    except ValueError as e:
        return fail(req, str(e), back)
    D.audit(c, req.user["username"], "voucher_created", "%s %s" % (kind, vid))
    req.flash("Voucher saved (%s %s). Cash/Bank entry was added automatically." % (D.fmt(total), "paid" if kind == "payment" else "received"))
    return redirect("/accounts/vouchers/%d" % vid)


@route("GET", r"/accounts/vouchers/journal", ACCW)
def journal_new(req):
    c = req.conn
    opts = acc_options(c)
    lines = []
    for i in range(8):
        lines.append(Raw('<tr><td>%s</td><td><input name="debit" type="number" step="0.01" min="0" style="text-align:right"></td>'
                         '<td><input name="credit" type="number" step="0.01" min="0" style="text-align:right"></td><td><input name="memo"></td></tr>'
                         % sel("", "acc", opts, blank="— account —")))
    inner = H(Raw('<div class="form">'), sel("Type", "vtype", VTYPES, required=True), inp("Date", "vdate", D.today(c).isoformat(), "date", True),
              inp("Narration", "narration", ""), Raw("</div><div class='tw'><table><thead><tr><th>Account</th><th class='num'>Debit</th><th class='num'>Credit</th><th>Memo</th></tr></thead><tbody>"),
              *lines, Raw("</tbody></table></div><p class='muted'>Total debits must equal total credits. Leave unused rows empty.</p><p>%s %s</p>" % (btn("Save voucher"), link_btn("Cancel", "/accounts/vouchers"))))
    return page(req, "Journal voucher", card("Journal voucher (advanced: you enter both sides)", post_form(req, "/accounts/vouchers/journal", inner)))


@route("POST", r"/accounts/vouchers/journal", ACCW)
def journal_create(req):
    c = req.conn
    vt = req.f("vtype")
    vdate = req.f("vdate")
    if vt not in TYPE_NAME or not valid_date(vdate):
        return fail(req, "Choose a voucher type and a valid date.", "/accounts/vouchers/journal")
    accs, deb, cre, memo = req.fl("acc"), req.fl("debit"), req.fl("credit"), req.fl("memo")
    lines = []
    try:
        for i, a in enumerate(accs):
            d = D.to_paisa(deb[i] if i < len(deb) else "")
            cr = D.to_paisa(cre[i] if i < len(cre) else "")
            if not a:
                if d or cr:
                    raise ValueError("Row %d has an amount but no account." % (i + 1))
                continue
            if not c.execute("SELECT 1 FROM accounts WHERE id=? AND active=1", (a,)).fetchone():
                raise ValueError("Row %d: invalid account." % (i + 1))
            lines.append((int(a), d, cr, memo[i] if i < len(memo) else ""))
        vid = D.create_voucher(c, vt, vdate, req.f("narration"), lines, req.user["id"], None)
    except ValueError as e:
        return fail(req, str(e), "/accounts/vouchers/journal")
    D.audit(c, req.user["username"], "voucher_created", str(vid))
    req.flash("Voucher saved.")
    return redirect("/accounts/vouchers/%d" % vid)


@route("GET", r"/accounts/vouchers/(?P<vid>\d+)", ACCV)
def voucher_view(req, vid):
    c = req.conn
    v = c.execute("SELECT * FROM vouchers WHERE id=?", (vid,)).fetchone()
    if not v:
        abort(404, "Voucher not found.")
    rows = [[a["code"] + " — " + a["name"], l["memo"] or "", D.fmt(l["debit"]) if l["debit"] else "", D.fmt(l["credit"]) if l["credit"] else ""]
            for l in c.execute("SELECT * FROM voucher_lines WHERE voucher_id=? ORDER BY id", (v["id"],))
            for a in [c.execute("SELECT * FROM accounts WHERE id=?", (l["account_id"],)).fetchone()]]
    td = c.execute("SELECT COALESCE(SUM(debit),0) FROM voucher_lines WHERE voucher_id=?", (v["id"],)).fetchone()[0]
    foot = Raw("<p><b>Total: %s</b></p>" % esc(D.fmt(td)))
    acts = ""
    if v["source"]:
        acts = Raw('<span class="muted">System voucher (%s) — manage it from the Payroll page.</span>' % esc(v["source"]))
    elif req.has_role("acc_voucher"):
        acts = post_form(req, "/accounts/vouchers/%d/delete" % v["id"], Raw(btn("Delete voucher", "danger")), "inline", confirm="Delete this voucher permanently?")
    info = Raw("<p>%s · %s · <b>%s</b><br>%s</p>" % (esc(TYPE_NAME.get(v["vtype"])), esc(v["vdate"]), esc(v["vno"]), esc(v["narration"] or "")))
    return page(req, "Voucher " + v["vno"], H(info, card("Entries", H(table(["Account", "Memo", "Debit", "Credit"], rows, (2, 3)), foot), acts),
                                                 Raw('<p class="noprint"><button class="btn ghost" onclick="window.print()">Print</button></p>')))


@route("POST", r"/accounts/vouchers/(?P<vid>\d+)/delete", ACCW)
def voucher_delete(req, vid):
    v = req.conn.execute("SELECT * FROM vouchers WHERE id=?", (vid,)).fetchone()
    if not v:
        abort(404, "Voucher not found.")
    if v["source"]:
        return fail(req, "System vouchers cannot be deleted here.", "/accounts/vouchers/%s" % vid)
    req.conn.execute("DELETE FROM vouchers WHERE id=?", (v["id"],))
    D.audit(req.conn, req.user["username"], "voucher_deleted", v["vno"])
    req.flash("Voucher %s deleted." % v["vno"])
    return redirect("/accounts/vouchers")


# ----------------------------------------------------------------- chart
def dr_cr(p):
    return "%s %s" % (D.fmt(abs(p)), "Dr" if p >= 0 else "Cr") if p else "0.00"


@route("GET", r"/accounts/chart", ACCV)
def chart(req):
    c = req.conn
    rows = []
    for a in c.execute("SELECT * FROM accounts ORDER BY code"):
        bal = D.account_balance(c, a["id"])
        act = ""
        if not a["system"] and req.has_role("acc_chart"):
            act = post_form(req, "/accounts/chart/%d/toggle" % a["id"], Raw('<button class="btn ghost sm">%s</button>' % ("Disable" if a["active"] else "Enable")), "inline")
        rows.append([a["code"], Raw('<a href="/accounts/ledger?acc=%d">%s</a>' % (a["id"], esc(a["name"]))), a["atype"].title(), dr_cr(bal),
                     badge("Active", "green") if a["active"] else badge("Disabled", "gray"), act])
    new = post_form(req, "/accounts/chart/new", H(inp("Code", "code", "", required=True, hint="e.g. 5010"), inp("Name", "name", "", required=True),
                                                  sel("Type", "atype", ATYPES, required=True), Raw(btn("Add account"))), "form")
    return page(req, "Chart of accounts", H(card("Accounts", table(["Code", "Name", "Type", "Balance", "Status", ""], rows, (3,))), card("Add an account", new) if req.has_role("acc_chart") else ""))


@route("POST", r"/accounts/chart/new", ACCC)
def chart_new(req):
    code, name, at = req.f("code"), req.f("name"), req.f("atype")
    if not code or not name or at not in dict(ATYPES):
        return fail(req, "Code, name and type are required.", "/accounts/chart")
    if req.conn.execute("SELECT 1 FROM accounts WHERE code=?", (code,)).fetchone():
        return fail(req, "Account code %s already exists." % code, "/accounts/chart")
    req.conn.execute("INSERT INTO accounts(code,name,atype) VALUES(?,?,?)", (code, name, at))
    D.audit(req.conn, req.user["username"], "account_created", code)
    req.flash("Account added.")
    return redirect("/accounts/chart")


@route("POST", r"/accounts/chart/(?P<aid>\d+)/toggle", ACCC)
def chart_toggle(req, aid):
    a = req.conn.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
    if not a or a["system"]:
        return fail(req, "System accounts cannot be disabled.", "/accounts/chart")
    req.conn.execute("UPDATE accounts SET active=1-active WHERE id=?", (aid,))
    return redirect("/accounts/chart")


# ---------------------------------------------------------------- ledger
@route("GET", r"/accounts/ledger", ACCV)
def ledger(req):
    c = req.conn
    acc = req.q("acc")
    today = D.today(c)
    fr = req.q("from") or today.replace(month=1, day=1).isoformat()
    to = req.q("to") or today.isoformat()
    bar = Raw('<form class="bar" method="get">%s%s%s<button class="btn ghost">Show</button></form>'
              % (sel("Account", "acc", acc_options(c, False), acc, blank="— choose —"), inp("From", "from", fr, "date"), inp("To", "to", to, "date")))
    body = bar
    a = c.execute("SELECT * FROM accounts WHERE id=?", (acc,)).fetchone() if acc.isdigit() else None
    if a and valid_date(fr) and valid_date(to):
        day_before = (D.parse_date(fr).toordinal() - 1)
        import datetime
        opening = D.account_balance(c, a["id"], datetime.date.fromordinal(day_before).isoformat())
        bal = opening
        rows = [["", "", "Opening balance", "", "", dr_cr(opening)]]
        td = tc = 0
        for l in c.execute("SELECT l.*, v.vno, v.vdate, v.narration, v.id vid FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
                           "WHERE l.account_id=? AND v.vdate BETWEEN ? AND ? ORDER BY v.vdate, v.id, l.id", (a["id"], fr, to)):
            bal += l["debit"] - l["credit"]
            td += l["debit"]
            tc += l["credit"]
            rows.append([l["vdate"], Raw('<a href="/accounts/vouchers/%d">%s</a>' % (l["vid"], esc(l["vno"]))), l["memo"] or l["narration"] or "",
                         D.fmt(l["debit"]) if l["debit"] else "", D.fmt(l["credit"]) if l["credit"] else "", dr_cr(bal)])
        rows.append(["", "", "Period total / closing", D.fmt(td), D.fmt(tc), dr_cr(bal)])
        body = H(bar, table(["Date", "Voucher", "Particulars", "Debit", "Credit", "Balance"], rows, (3, 4, 5)))
        title = "%s — %s" % (a["code"], a["name"])
    else:
        title = "Ledger"
        if acc:
            req.flash("Choose an account and valid dates.", "err")
    return page(req, "Ledger", card(title, body))


# ---------------------------------------------------------- trial balance
@route("GET", r"/accounts/trial", ACCV)
def trial(req):
    c = req.conn
    asof = req.q("asof") or D.today(c).isoformat()
    if not valid_date(asof):
        return fail(req, "Invalid date.", "/accounts/trial")
    rows = []
    tdr = tcr = 0
    for a in c.execute("SELECT * FROM accounts ORDER BY code"):
        b = D.account_balance(c, a["id"], asof)
        if not b:
            continue
        dr, cr = (b, 0) if b > 0 else (0, -b)
        tdr += dr
        tcr += cr
        rows.append([a["code"], a["name"], a["atype"].title(), D.fmt(dr) if dr else "", D.fmt(cr) if cr else ""])
    rows.append(["", "", "TOTAL", D.fmt(tdr), D.fmt(tcr)])
    ok = badge("Balanced", "green") if tdr == tcr else badge("OUT OF BALANCE", "red")
    bar = Raw('<form class="bar" method="get">%s<button class="btn ghost">Show</button></form>' % inp("As of", "asof", asof, "date"))
    return page(req, "Trial balance", card("As of %s" % asof, H(bar, table(["Code", "Account", "Type", "Debit", "Credit"], rows, (3, 4)), ok)))


# --------------------------------------------------------------- reports
def sums(c, atype, fr, to):
    out = []
    for a in c.execute("SELECT * FROM accounts WHERE atype=? ORDER BY code", (atype,)):
        r = c.execute("SELECT COALESCE(SUM(l.debit),0), COALESCE(SUM(l.credit),0) FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
                      "WHERE l.account_id=? AND v.vdate BETWEEN ? AND ?", (a["id"], fr, to)).fetchone()
        amt = (r[1] - r[0]) if atype in ("income",) else (r[0] - r[1])
        if amt:
            out.append((a["name"], amt))
    return out


@route("GET", r"/accounts/reports", ACCV)
def reports(req):
    c = req.conn
    today = D.today(c)
    fr = req.q("from") or today.replace(day=1).isoformat()
    to = req.q("to") or today.isoformat()
    if not (valid_date(fr) and valid_date(to)):
        return fail(req, "Invalid dates.", "/accounts/reports")
    inc, exp = sums(c, "income", fr, to), sums(c, "expense", fr, to)
    ti, te = sum(x[1] for x in inc), sum(x[1] for x in exp)
    pl_rows = [["INCOME", ""]] + [[n, D.fmt(a)] for n, a in inc] + [["Total income", D.fmt(ti)], ["EXPENSES", ""]] + [[n, D.fmt(a)] for n, a in exp] + \
              [["Total expenses", D.fmt(te)], ["NET PROFIT / (LOSS)", D.fmt(ti - te)]]
    # balance sheet as of `to`
    beginning = "0001-01-01"
    assets = [(n, a) for n, a in ((x["name"], D.account_balance(c, x["id"], to)) for x in c.execute("SELECT * FROM accounts WHERE atype='asset' ORDER BY code")) if a]
    liabs = [(n, -a) for n, a in ((x["name"], D.account_balance(c, x["id"], to)) for x in c.execute("SELECT * FROM accounts WHERE atype='liability' ORDER BY code")) if a]
    eq = [(n, -a) for n, a in ((x["name"], D.account_balance(c, x["id"], to)) for x in c.execute("SELECT * FROM accounts WHERE atype='equity' ORDER BY code")) if a]
    profit = sum(x[1] for x in sums(c, "income", beginning, to)) - sum(x[1] for x in sums(c, "expense", beginning, to))
    eq.append(("Retained earnings (profit to date)", profit))
    ta, tl, tq = sum(x[1] for x in assets), sum(x[1] for x in liabs), sum(x[1] for x in eq)
    bs_rows = [["ASSETS", ""]] + [[n, D.fmt(a)] for n, a in assets] + [["Total assets", D.fmt(ta)], ["LIABILITIES", ""]] + [[n, D.fmt(a)] for n, a in liabs] + \
              [["Total liabilities", D.fmt(tl)], ["EQUITY", ""]] + [[n, D.fmt(a)] for n, a in eq] + [["Total equity", D.fmt(tq)], ["Liabilities + equity", D.fmt(tl + tq)]]
    bar = Raw('<form class="bar" method="get">%s%s<button class="btn ghost">Show</button></form>' % (inp("From", "from", fr, "date"), inp("To", "to", to, "date")))
    return page(req, "Financial reports", H(bar, Raw('<div class="two">'), card("Income statement %s to %s" % (fr, to), table(["", "Amount"], pl_rows, (1,))),
                                            card("Balance sheet as of %s" % to, table(["", "Amount"], bs_rows, (1,))), Raw("</div>")))

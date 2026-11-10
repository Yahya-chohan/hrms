"""Business rules: leave days/balances and payroll calculation + accounting posting."""
import datetime
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from . import db as D

WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def weekly_off(conn):
    return {x.strip().title()[:3] for x in D.get_setting(conn, "weekly_off").split(",") if x.strip()}


def working_dates(start, end, off):
    out = []
    d = start
    while d <= end:
        if WD[d.weekday()] not in off:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def leave_balances(conn, emp_id, year):
    rows = []
    for lt in conn.execute("SELECT * FROM leave_types ORDER BY id").fetchall():
        def total(status):
            return conn.execute(
                "SELECT COALESCE(SUM(days),0) FROM leave_requests WHERE emp_id=? AND type_id=? "
                "AND status=? AND substr(start_date,1,4)=?",
                (emp_id, lt["id"], status, str(year)),
            ).fetchone()[0]
        used, pend = total("approved"), total("pending")
        quota = lt["days_per_year"]
        rows.append({
            "id": lt["id"], "name": lt["name"], "paid": lt["paid"], "quota": quota,
            "used": used, "pending": pend,
            "remaining": (quota - used - pend) if quota else None,
        })
    return rows


# ------------------------------------------------------- salary split + tax
# Pakistan salaried-individual slabs, tax year 2027 (1 Jul 2026 - 30 Jun 2027).
# Each row: (annual income above this limit in Rs, fixed tax at the limit in Rs, rate % on the excess).
# Update this table when a new Finance Act changes the slabs.
TAX_YEAR = "2026-27"
TAX_SLABS = [
    (0, 0, 0), (600000, 0, 1), (1200000, 6000, 11), (2200000, 116000, 20),
    (3200000, 316000, 25), (4100000, 541000, 29), (5600000, 976000, 32), (7000000, 1424000, 35),
]
SPLIT = (50, 30, 20)  # basic, house rent, medical (% of total salary)


def split_salary(total):
    basic = total * SPLIT[0] // 100
    house = total * SPLIT[1] // 100
    return basic, house, total - basic - house


def annual_tax(taxable):
    """taxable annual income in paisa -> annual tax in paisa."""
    tax = 0
    for lower, fixed, rate in TAX_SLABS:
        if taxable > lower * 100:
            tax = fixed * 100 + ((taxable - lower * 100) * rate + 50) // 100
    return tax


def pf_percent(conn):
    """Employee provident fund % of basic (standard is 8.333%, i.e. 1/12)."""
    try:
        p = Decimal(D.get_setting(conn, "pf_percent", "8.333"))
        return p if 0 <= p <= 100 else Decimal("8.333")
    except InvalidOperation:
        return Decimal("8.333")


def fmt_pct(p):
    return format(p.normalize(), "f")


def pf_amount(conn, basic):
    return int((Decimal(basic) * pf_percent(conn) / 100).quantize(Decimal(1), ROUND_HALF_UP))


def monthly_tax(conn, gross, basic, medical):
    """Monthly income tax for a salary. Medical allowance is exempt up to 10% of basic (setting)."""
    exempt = min(medical, basic // 10) if D.get_setting(conn, "medical_exempt", "1") == "1" else 0
    return (annual_tax((gross - exempt) * 12) + 6) // 12


# ------------------------------------------------------------------ payroll
def month_days(conn):
    try:
        v = int(D.get_setting(conn, "month_days", "30"))
    except ValueError:
        v = 30
    return max(1, min(31, v))


def compute_slip(conn, emp, month, bonus=0, other_ded=0, note=""):
    first, last = D.month_bounds(month)
    md = month_days(conn)
    gross = emp["basic"] + emp["house_rent"] + emp["medical"] + emp["conveyance"] + emp["other_allow"]
    join = D.parse_date(emp["join_date"])
    start = max(first, join)
    absent = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE emp_id=? AND adate BETWEEN ? AND ? AND status IN ('A','UL')",
        (emp["id"], start.isoformat(), last.isoformat()),
    ).fetchone()[0]
    exit_d = D.parse_date(emp["exit_date"]) if emp["exit_date"] else None
    end = min(last, exit_d) if exit_d else last
    if start == first and end == last:
        partial = 0  # worked the whole calendar month
    else:  # joined or left during the month: pay only the days worked (on the salary-month basis)
        partial = max(0, md - max(0, (end - start).days + 1))
    absent_days = min(md, absent + partial)
    absent_ded = (gross * absent_days + md // 2) // md
    deductions = emp["income_tax"] + emp["provident_fund"] + emp["loan_installment"] + other_ded
    net = gross - absent_ded + bonus - deductions
    return {
        "basic": emp["basic"], "house_rent": emp["house_rent"], "medical": emp["medical"],
        "conveyance": emp["conveyance"], "other_allow": emp["other_allow"], "gross": gross,
        "absent_days": absent_days, "absent_deduction": absent_ded, "bonus": bonus,
        "income_tax": emp["income_tax"], "provident_fund": emp["provident_fund"],
        "loan": emp["loan_installment"], "other_deduction": other_ded, "note": note, "net": net,
    }


SLIP_COLS = ["basic", "house_rent", "medical", "conveyance", "other_allow", "gross", "absent_days",
             "absent_deduction", "bonus", "income_tax", "provident_fund", "loan", "other_deduction", "note", "net"]


def save_slip(conn, run_id, emp_id, s):
    conn.execute(
        "INSERT INTO payslips(run_id,emp_id,%s) VALUES(?,?,%s)" % (",".join(SLIP_COLS), ",".join("?" * len(SLIP_COLS))),
        [run_id, emp_id] + [s[c] for c in SLIP_COLS],
    )


def generate_run(conn, run_id, month):
    """(Re)build all payslips of a draft run, keeping manual bonus/deduction adjustments."""
    old = {r["emp_id"]: r for r in conn.execute("SELECT * FROM payslips WHERE run_id=?", (run_id,)).fetchall()}
    conn.execute("DELETE FROM payslips WHERE run_id=?", (run_id,))
    first, last = D.month_bounds(month)
    emps = conn.execute(
        "SELECT * FROM employees WHERE join_date<=? AND (status='active' OR (exit_date IS NOT NULL AND exit_date>=?)) ORDER BY code",
        (last.isoformat(), first.isoformat())
    ).fetchall()
    for e in emps:
        o = old.get(e["id"])
        s = compute_slip(conn, e, month, o["bonus"] if o else 0, o["other_deduction"] if o else 0, o["note"] if o else "")
        save_slip(conn, run_id, e["id"], s)
    return len(emps)


def run_totals(conn, run_id):
    r = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(gross),0) gross, COALESCE(SUM(absent_deduction),0) absd, COALESCE(SUM(bonus),0) bonus, "
        "COALESCE(SUM(income_tax),0) tax, COALESCE(SUM(provident_fund),0) pf, COALESCE(SUM(loan),0) loan, "
        "COALESCE(SUM(other_deduction),0) od, COALESCE(SUM(net),0) net FROM payslips WHERE run_id=?",
        (run_id,),
    ).fetchone()
    return r


def post_run(conn, run, user_id):
    """Create the salary journal voucher (draft -> posted)."""
    t = run_totals(conn, run["id"])
    if t["n"] == 0:
        raise ValueError("The run has no payslips.")
    if conn.execute("SELECT COUNT(*) FROM payslips WHERE run_id=? AND net<0", (run["id"],)).fetchone()[0]:
        raise ValueError("Some payslips have a negative net salary. Fix the deductions before posting.")
    earned = t["gross"] - t["absd"] + t["bonus"]
    A = lambda c: D.account_id(conn, c)
    lines = [
        (A("5001"), earned, 0, "Salaries for " + run["month"]),
        (A("2002"), 0, t["tax"], "Income tax withheld"),
        (A("2003"), 0, t["pf"], "Provident fund"),
        (A("1200"), 0, t["loan"], "Loan recovery"),
        (A("2004"), 0, t["od"], "Other deductions"),
        (A("2001"), 0, t["net"], "Net salary payable"),
    ]
    last = D.month_bounds(run["month"])[1].isoformat()
    vid = D.create_voucher(conn, "JV", last, "Salary for %s" % run["month"], lines, user_id, "payroll:%d" % run["id"])
    conn.execute("UPDATE payroll_runs SET status='posted', post_voucher_id=? WHERE id=?", (vid, run["id"]))
    return vid


def pay_run(conn, run, pay_account_id, pay_date, user_id):
    t = run_totals(conn, run["id"])
    lines = [
        (D.account_id(conn, "2001"), t["net"], 0, "Salary paid " + run["month"]),
        (pay_account_id, 0, t["net"], "Salary paid " + run["month"]),
    ]
    code = conn.execute("SELECT code FROM accounts WHERE id=?", (pay_account_id,)).fetchone()["code"]
    vt = "CPV" if code == "1001" else "BPV"
    vid = D.create_voucher(conn, vt, pay_date, "Salary payment for %s" % run["month"], lines, user_id, "payroll:%d" % run["id"])
    conn.execute("UPDATE payroll_runs SET status='paid', pay_voucher_id=? WHERE id=?", (vid, run["id"]))
    return vid


# ------------------------------------------------------- mobile check-in/out
def distance_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres (haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _mins(hhmm):
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def is_late(conn, check_in):
    """True if check_in ('HH:MM') is after office start + grace minutes."""
    if not check_in:
        return False
    try:
        grace = int(D.get_setting(conn, "late_grace", "15"))
        return _mins(check_in) > _mins(D.get_setting(conn, "office_start", "09:00")) + grace
    except ValueError:
        return False


def worked(check_in, check_out):
    if not check_in or not check_out:
        return ""
    try:
        m = _mins(check_out) - _mins(check_in)
    except ValueError:
        return ""
    return "%dh %02dm" % (m // 60, m % 60) if m >= 0 else ""

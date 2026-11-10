"""Database layer: schema, seed data, money helpers, accounting core."""
import datetime
import hashlib
import hmac
import os
import secrets
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("HRMS_DB", os.path.join(BASE_DIR, "data", "hrms.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS designations(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS employees(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  father_name TEXT, cnic TEXT, phone TEXT, email TEXT, address TEXT, dob TEXT, gender TEXT,
  join_date TEXT NOT NULL,
  dept_id INTEGER REFERENCES departments(id),
  desig_id INTEGER REFERENCES designations(id),
  status TEXT NOT NULL DEFAULT 'active',
  bank_name TEXT, bank_account TEXT,
  basic INTEGER NOT NULL DEFAULT 0,
  house_rent INTEGER NOT NULL DEFAULT 0,
  medical INTEGER NOT NULL DEFAULT 0,
  conveyance INTEGER NOT NULL DEFAULT 0,
  other_allow INTEGER NOT NULL DEFAULT 0,
  income_tax INTEGER NOT NULL DEFAULT 0,
  provident_fund INTEGER NOT NULL DEFAULT 0,
  loan_installment INTEGER NOT NULL DEFAULT 0,
  exit_date TEXT,
  created TEXT
);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  employee_id INTEGER REFERENCES employees(id),
  active INTEGER NOT NULL DEFAULT 1,
  must_change INTEGER NOT NULL DEFAULT 0,
  perms TEXT,
  created TEXT
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL,
  expires REAL NOT NULL,
  flash TEXT
);
CREATE TABLE IF NOT EXISTS leave_types(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  days_per_year INTEGER NOT NULL DEFAULT 0,
  paid INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS leave_requests(
  id INTEGER PRIMARY KEY,
  emp_id INTEGER NOT NULL REFERENCES employees(id),
  type_id INTEGER NOT NULL REFERENCES leave_types(id),
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  days INTEGER NOT NULL,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  decided_by INTEGER REFERENCES users(id),
  decided_at TEXT,
  remarks TEXT,
  created TEXT
);
CREATE TABLE IF NOT EXISTS attendance(
  id INTEGER PRIMARY KEY,
  emp_id INTEGER NOT NULL REFERENCES employees(id),
  adate TEXT NOT NULL,
  status TEXT NOT NULL,
  check_in TEXT, check_out TEXT, note TEXT,
  in_loc TEXT, out_loc TEXT, via TEXT,
  UNIQUE(emp_id, adate)
);
CREATE TABLE IF NOT EXISTS payroll_runs(
  id INTEGER PRIMARY KEY,
  month TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  created TEXT,
  post_voucher_id INTEGER REFERENCES vouchers(id),
  pay_voucher_id INTEGER REFERENCES vouchers(id)
);
CREATE TABLE IF NOT EXISTS payslips(
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
  emp_id INTEGER NOT NULL REFERENCES employees(id),
  basic INTEGER, house_rent INTEGER, medical INTEGER, conveyance INTEGER, other_allow INTEGER,
  gross INTEGER,
  absent_days INTEGER DEFAULT 0,
  absent_deduction INTEGER DEFAULT 0,
  bonus INTEGER DEFAULT 0,
  income_tax INTEGER DEFAULT 0,
  provident_fund INTEGER DEFAULT 0,
  loan INTEGER DEFAULT 0,
  other_deduction INTEGER DEFAULT 0,
  note TEXT,
  net INTEGER,
  UNIQUE(run_id, emp_id)
);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  atype TEXT NOT NULL,
  system INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS vouchers(
  id INTEGER PRIMARY KEY,
  vno TEXT UNIQUE NOT NULL,
  seq INTEGER NOT NULL,
  vtype TEXT NOT NULL,
  vdate TEXT NOT NULL,
  narration TEXT,
  created_by INTEGER REFERENCES users(id),
  created TEXT,
  source TEXT
);
CREATE TABLE IF NOT EXISTS voucher_lines(
  id INTEGER PRIMARY KEY,
  voucher_id INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  debit INTEGER NOT NULL DEFAULT 0,
  credit INTEGER NOT NULL DEFAULT 0,
  memo TEXT
);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY,
  ts TEXT, username TEXT, action TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_att_date ON attendance(adate);
CREATE INDEX IF NOT EXISTS ix_lines_acc ON voucher_lines(account_id);
CREATE INDEX IF NOT EXISTS ix_leave_emp ON leave_requests(emp_id);
"""

DEFAULT_ACCOUNTS = [
    ("1001", "Cash in Hand", "asset"),
    ("1002", "Bank Account", "asset"),
    ("1100", "Accounts Receivable", "asset"),
    ("1200", "Employee Loans Receivable", "asset"),
    ("2001", "Salary Payable", "liability"),
    ("2002", "Income Tax Payable", "liability"),
    ("2003", "Provident Fund Payable", "liability"),
    ("2004", "Other Deductions Payable", "liability"),
    ("2100", "Accounts Payable", "liability"),
    ("3001", "Owner's Capital", "equity"),
    ("4001", "Sales / Service Income", "income"),
    ("4002", "Other Income", "income"),
    ("5001", "Salaries Expense", "expense"),
    ("5002", "Rent Expense", "expense"),
    ("5003", "Utilities Expense", "expense"),
    ("5004", "Office Expenses", "expense"),
    ("5005", "Travel Expense", "expense"),
    ("5006", "Miscellaneous Expense", "expense"),
]
SYSTEM_CODES = {"1001", "1002", "1200", "2001", "2002", "2003", "2004", "5001"}

DEFAULT_SETTINGS = {
    "company_name": "My Company",
    "currency": "PKR",
    "weekly_off": "Sun",
    "month_days": "30",
    "office_start": "09:00",
    "tz_offset": "5",
    "medical_exempt": "1",
    "pf_percent": "8.333",
    "geofence": "0",
    "office_lat": "",
    "office_lng": "",
    "office_radius": "200",
    "late_grace": "15",
    "contact_email": "",
}

ROLES = ("admin", "hr", "accountant", "employee")

# Access rights. Admin has everything; every other user gets the rights ticked for them (role = starting preset).
# Every logged-in employee can always use the self-service pages (own leave, attendance + check-in, payslips).
PERM_GROUPS = [
    ("People", [
        ("emp_view", "View employees and their profile / salary"),
        ("emp_edit", "Add and edit employees, mark exit"),
        ("att_view", "View attendance and reports"),
        ("att_mark", "Mark attendance / close the day"),
        ("leave_manage", "See, approve and reject leave requests"),
    ]),
    ("Payroll", [
        ("pay_view", "View payroll runs and everyone's payslips"),
        ("pay_prepare", "Create / recalculate / adjust payroll"),
        ("pay_post", "Post payroll to accounts and mark as paid"),
    ]),
    ("Accounts", [
        ("acc_view", "View vouchers, ledger, trial balance, reports"),
        ("acc_voucher", "Create and delete vouchers"),
        ("acc_chart", "Add / disable accounts in the chart"),
    ]),
]
PERM_LABELS = {k: v for _, items in PERM_GROUPS for k, v in items}
PERM_KEYS = list(PERM_LABELS)
PRESETS = {
    "admin": PERM_KEYS,
    "hr": ["emp_view", "emp_edit", "att_view", "att_mark", "leave_manage", "pay_view", "pay_prepare"],
    "accountant": ["pay_view", "pay_post", "acc_view", "acc_voucher", "acc_chart"],
    "employee": [],
}
PERM_NEEDS = {"emp_edit": ["emp_view"], "att_mark": ["att_view"], "pay_prepare": ["pay_view"], "pay_post": ["pay_view"],
              "acc_voucher": ["acc_view"], "acc_chart": ["acc_view"]}


def close_perms(keys):
    """Keep only known keys and add the view rights that the chosen rights depend on."""
    out = {k for k in keys if k in PERM_LABELS}
    for k in list(out):
        out.update(PERM_NEEDS.get(k, []))
    return out


def user_perms(user):
    if user["role"] == "admin":
        return set(PERM_KEYS)
    if user["perms"] is None:  # users created before access rights existed: use the role's preset
        return set(PRESETS.get(user["role"], []))
    return close_perms(user["perms"].split(","))
ATT_STATUS = {"P": "Present", "A": "Absent", "L": "Paid Leave", "UL": "Unpaid Leave"}


# ---------------------------------------------------------------- helpers
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def to_paisa(text):
    s = (text or "").replace(",", "").strip()
    if s == "":
        return 0
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError("Invalid amount: %s" % text)
    if not d.is_finite():
        raise ValueError("Invalid amount: %s" % text)
    return int((d * 100).quantize(Decimal(1), ROUND_HALF_UP))


def fmt(p):
    p = int(p or 0)
    sign = "-" if p < 0 else ""
    p = abs(p)
    return "%s%s.%02d" % (sign, format(p // 100, ","), p % 100)


def plain(p):
    p = int(p or 0)
    sign = "-" if p < 0 else ""
    p = abs(p)
    return "%s%d.%02d" % (sign, p // 100, p % 100)


_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen "
         "Eighteen Nineteen").split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_1000(n):
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + ((" " + _ONES[n % 10]) if n % 10 else ""))
    elif n > 0:
        out.append(_ONES[n])
    return " ".join(out)


def amount_in_words(p, currency="Rupees"):
    """paisa -> 'Eighty Four Thousand Rupees Only' (Pakistani lakh/crore system)."""
    p = int(p or 0)
    rupees, paisa = divmod(abs(p), 100)
    parts = []
    for unit, name in ((10000000, "Crore"), (100000, "Lakh"), (1000, "Thousand")):
        q, rupees_left = divmod(rupees, unit)
        if q:
            parts.append(_below_1000(q) + " " + name)
        rupees = rupees_left
    if rupees:
        parts.append(_below_1000(rupees))
    words = " ".join(parts) or "Zero"
    out = "%s %s" % (words, currency)
    if paisa:
        out += " and %s Paisa" % _below_1000(paisa)
    return ("Minus " if p < 0 else "") + out + " Only"


def hash_password(pw):
    salt = secrets.token_hex(16)
    it = 200000
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), it).hex()
    return "pbkdf2$%d$%s$%s" % (it, salt, h)


def check_password(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def get_setting(conn, key, default=""):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r is None:
        return DEFAULT_SETTINGS.get(key, default)
    return r["value"]


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def now_local(conn):
    try:
        off = float(get_setting(conn, "tz_offset", "5"))
    except ValueError:
        off = 5.0
    utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return utc + datetime.timedelta(hours=off)


def today(conn):
    return now_local(conn).date()


def parse_date(s):
    return datetime.date.fromisoformat((s or "").strip())


def month_bounds(month):
    """month 'YYYY-MM' -> (first_date, last_date)."""
    y, m = int(month[:4]), int(month[5:7])
    first = datetime.date(y, m, 1)
    nxt = datetime.date(y + (m == 12), (m % 12) + 1, 1)
    return first, nxt - datetime.timedelta(days=1)


def audit(conn, username, action, detail=""):
    ts = now_local(conn).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO audit_log(ts,username,action,detail) VALUES(?,?,?,?)",
        (ts, username or "-", action, detail),
    )


# ------------------------------------------------------------ accounting
def account_id(conn, code):
    r = conn.execute("SELECT id FROM accounts WHERE code=?", (code,)).fetchone()
    if r is None:
        raise ValueError("Account %s is missing from the chart of accounts." % code)
    return r["id"]


def create_voucher(conn, vtype, vdate, narration, lines, user_id=None, source=None):
    """lines: list of (account_id, debit, credit, memo) in paisa. Raises ValueError."""
    lines = [l for l in lines if l[1] or l[2]]
    if len(lines) < 2:
        raise ValueError("A voucher needs at least two lines.")
    td = tc = 0
    for _, d, c, _m in lines:
        if d < 0 or c < 0 or (d and c):
            raise ValueError("Each line must have either a debit or a credit amount.")
        td += d
        tc += c
    if td != tc:
        raise ValueError("Debits (%s) and credits (%s) are not equal." % (fmt(td), fmt(tc)))
    if td == 0:
        raise ValueError("Voucher total cannot be zero.")
    seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM vouchers WHERE vtype=?", (vtype,)).fetchone()[0]
    vno = "%s-%05d" % (vtype, seq)
    cur = conn.execute(
        "INSERT INTO vouchers(vno,seq,vtype,vdate,narration,created_by,created,source) VALUES(?,?,?,?,?,?,?,?)",
        (vno, seq, vtype, vdate, narration, user_id, now_local(conn).strftime("%Y-%m-%d %H:%M"), source),
    )
    vid = cur.lastrowid
    for a, d, c, m in lines:
        conn.execute(
            "INSERT INTO voucher_lines(voucher_id,account_id,debit,credit,memo) VALUES(?,?,?,?,?)",
            (vid, a, d, c, m),
        )
    return vid


def account_balance(conn, acc_id, upto=None):
    """Debit-minus-credit balance."""
    sql = (
        "SELECT COALESCE(SUM(l.debit),0)-COALESCE(SUM(l.credit),0) FROM voucher_lines l "
        "JOIN vouchers v ON v.id=l.voucher_id WHERE l.account_id=?"
    )
    args = [acc_id]
    if upto:
        sql += " AND v.vdate<=?"
        args.append(upto)
    return conn.execute(sql, args).fetchone()[0]


# ------------------------------------------------------------------ init
def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    if "exit_date" not in [r[1] for r in conn.execute("PRAGMA table_info(employees)")]:  # upgrade older databases
        conn.execute("ALTER TABLE employees ADD COLUMN exit_date TEXT")
    if "perms" not in [r[1] for r in conn.execute("PRAGMA table_info(users)")]:
        conn.execute("ALTER TABLE users ADD COLUMN perms TEXT")
    att_cols = [r[1] for r in conn.execute("PRAGMA table_info(attendance)")]
    for col in ("in_loc", "out_loc", "via"):
        if col not in att_cols:
            conn.execute("ALTER TABLE attendance ADD COLUMN %s TEXT" % col)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
        for code, name, atype in DEFAULT_ACCOUNTS:
            conn.execute(
                "INSERT INTO accounts(code,name,atype,system) VALUES(?,?,?,?)",
                (code, name, atype, 1 if code in SYSTEM_CODES else 0),
            )
    if conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 0:
        for n in ("Administration", "Human Resources", "Finance", "IT", "Sales", "Operations"):
            conn.execute("INSERT INTO departments(name) VALUES(?)", (n,))
    if conn.execute("SELECT COUNT(*) FROM designations").fetchone()[0] == 0:
        for n in ("Manager", "Assistant Manager", "Officer", "Executive", "Clerk", "Driver", "Office Boy"):
            conn.execute("INSERT INTO designations(name) VALUES(?)", (n,))
    if conn.execute("SELECT COUNT(*) FROM leave_types").fetchone()[0] == 0:
        for n, d, p in (("Annual Leave", 14, 1), ("Casual Leave", 10, 1), ("Sick Leave", 8, 1), ("Unpaid Leave", 0, 0)):
            conn.execute("INSERT INTO leave_types(name,days_per_year,paid) VALUES(?,?,?)", (n, d, p))
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        if os.environ.get("HRMS_SECURE") == "1" and not os.environ.get("HRMS_ADMIN_PASSWORD"):
            conn.close()
            raise RuntimeError("Live mode (HRMS_SECURE=1): set HRMS_ADMIN_PASSWORD to a strong password before the first start.")
        conn.execute(
            "INSERT INTO users(username,pw_hash,role,must_change,created) VALUES(?,?,?,?,?)",
            ("admin", hash_password(os.environ.get("HRMS_ADMIN_PASSWORD") or "Admin@123"), "admin", 1, now_local(conn).strftime("%Y-%m-%d %H:%M")),
        )
    conn.commit()
    conn.close()

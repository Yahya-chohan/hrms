# HRMS — HR, Leave, Payroll & Accounts

Ek hi software mein: employee record, attendance, leave, salary/payroll (payslip), aur double-entry accounts.
**Kuch install karne ki zaroorat nahi** — sirf Python 3.8+ chahiye (koi pip package nahi). Data SQLite file mein save hota hai.

## Chalane ka tareeqa
```
python3 run.py                  # sirf is computer par:  http://127.0.0.1:8000
python3 run.py --host 0.0.0.0   # office network ke dusre computers bhi khol sakte hain
python3 run.py --port 9000      # port badalne ke liye
```
Browser mein kholein, pehla login: **admin / Admin@123** (pehli dafa password badalna lazmi hai).

## Roles
| Role | Kya kar sakta hai |
|---|---|
| **Admin** | Sab kuch + users, departments, designations, leave types, settings, audit log, backup |
| **HR** | Employees, attendance, leave approve/reject, payroll banana/recalculate |
| **Accountant** | Vouchers, chart of accounts, ledger, trial balance, reports, payroll post/pay |
| **Employee** | Apni leave apply, balance, attendance, payslip dekhna |

## Roz ka kaam (shuru karne ka order)
1. **Admin → Settings**: company ka naam, weekly off (Sun ya Sat,Sun), mahine ke din (default 30), timezone (Pakistan = 5).
2. **Employees → New**: sirf **Total monthly salary** likhein. "Auto" ticked ho to basic 50% / house rent 30% / medical 20% khud ban jata hai
   aur income tax FBR slabs (tax year 2026-27) ke mutabiq khud calculate hota hai. Naya department/designation wahin type karke bhi ban jata hai.
   Conveyance / Other allowance alag se likhein: **Gross salary** khud ban jata hai (total + allowances) aur tax us gross par lagta hai.
   Provident fund basic ka 8.333% khud lagta hai (Admin → Settings mein % badal sakte hain, ya form mein PF ka tick hata dein).
   Auto band karein to sab amounts haath se likh sakte hain.
3. **Employee ka login**: Employees → us ka naam → **Login access → Create login** (sirf Admin). Username aur ek temporary password milta hai jo aap employee ko dete hain;
   wo pehli dafa login par apna password khud rakhta hai. Employee sirf apni leave, attendance aur payslip dekh sakta hai (baqi sab 403 "not allowed").
   Employee chhode to us ke page par **Employee exit → Mark as left**: login usi waqt band, pending leave cancel, record mehfooz, aakhri mahine ki salary kaam ke dino ki.
4. **Attendance**: employee phone se **Check IN / OUT** karta hai (office ki location ke andar; Admin → Settings mein set hoti hai). Din ke baad **Mark no-shows absent**.
   HR sirf un ki hazri lagata hai jin ke paas phone nahi. Approved leave khud attendance mein "Leave" lag jati hai. **iPhone/Android/Play Store ki guide: `MOBILE_APP.md`.**
5. **Leave Requests**: employees apply karte hain, HR approve/reject karta hai. Balance aur overlap khud check hota hai.
6. **Payroll → Create payroll run (mahina)**: sab payslips khud bante hain. Bonus / extra deduction har slip par add karein.
   - Absent deduction = (gross ÷ mahine ke din) × absent/unpaid-leave din.
   - **Post to accounts** (Accountant): Salary expense, tax/PF/loan payable ka journal voucher ban jata hai.
   - **Mark as paid**: Cash ya Bank se payment voucher. *Bank sheet (CSV)* bank transfer ke liye download hoti hai.
7. **Accounts**: Cash/Bank receipts-payments ke vouchers, Ledger, Trial Balance, Income Statement, Balance Sheet.

## Zaroori baatein
- **Backup**: Admin → Backup se database download karein (regular karein). Restore: server band karke `data/hrms.db` replace karein.
- Amounts paisa mein integer store hote hain, isliye hisab mein rounding ka farq nahi aata.
- Internet par (public) chalane se pehle HTTPS (reverse proxy) lagwayen; yeh built-in server sirf office/LAN ke liye hai.
- **Income tax**: salaried slabs tax year 2026-27 (1 Jul 2026 se) `app/logic.py` mein `TAX_SLABS` mein hain; naya budget aaye to wahan badal dein.
  Medical allowance basic ke 10% tak exempt maani jati hai (Admin → Settings mein band kar sakte hain, agar company free medical deti hai).
  Tax mahana = saalana tax ÷ 12. Yeh andaza hai, filing se pehle apne tax consultant se confirm karein.
- Vouchers delete ho sakte hain (audit log mein record rehta hai); payroll ke vouchers sirf Payroll page se (Revert) badalte hain.
- Test chalane ke liye alag database: `HRMS_DB=/tmp/test.db python3 run.py`.

## Files
`run.py` (start) · `app/db.py` (database + accounting) · `app/logic.py` (leave & payroll rules) · `app/views_*.py` (screens) · `app/web.py` (server, login, security, design)


## Website par live karna (online hosting)
**Poori guide `DEPLOY.md` mein hai** (PythonAnywhere, VPS + domain, cPanel). Mukhtasar:
Yeh app **ek chalta hua server + ek database file (data/hrms.db)** use karti hai, isliye hosting aisi chahiye jahan:
- Python program musalsal chalta rahe, aur
- `data/` folder **hamesha ke liye (persistent disk)** save rahe.

**Vercel / Netlify jaisi serverless hosting is version ke liye munasib nahi** (wahan file save nahi rehti, har request ke baad data ud jata hai).
Munasib options: Render, Railway, Fly.io (persistent disk/volume ke saath), ya koi VPS (DigitalOcean, Contabo, Hetzner...).

Server par yeh environment variables set karein:
| Variable | Matlab |
|---|---|
| `HRMS_ADMIN_PASSWORD` | Pehle admin ka password (pehli dafa database banne par). Zaroor set karein, warna default `Admin@123` sab ko maloom hai |
| `HRMS_SECURE=1` | HTTPS par cookie ko Secure banata hai (live site par zaroor lagayen) |
| `HRMS_DB` | Database file ka path, jo persistent disk par ho (jaise `/data/hrms.db`) |
| `PORT` | Hosting khud set karti hai; tab server khud `0.0.0.0` par sunta hai |

Start command: `python run.py`. HTTPS aksar hosting khud deti hai. Live hone ke baad pehle `admin` se login karke password badlein aur **Backup** regular download karein.

## Access control (Admin → Users & Access)
Each login has a list of rights: employees view/edit, attendance view/mark, leave manage,
payroll view/prepare/post, accounts view/vouchers/chart. Quick presets: HR, Accountant, Employee.
Tick/untick boxes for custom combinations (e.g. a staff member who can only create vouchers).
Anyone linked to an employee record always has self-service: own check-in/out, leave, payslips
(payslips become visible after payroll is posted).

## Vouchers
Accounts → Vouchers → New: choose Payment or Receipt, pick Cash/Bank once, then only the head
(expense / income) and amount. The Cash/Bank side is added automatically. "Journal (advanced)"
is the full two-sided entry.

## Payslips
Employee page → Payslips card: history of posted slips and a preview for any month.
The NET SALARY (take-home) is shown in the employee form, employee page and every payslip.

## Upgrading
Replace the program folder but keep data/hrms.db; the database migrates itself on start.

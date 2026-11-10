# HRMS ko website par live karna

Is software ko **aisi hosting chahiye jahan Python program chalta rahe aur `data` folder hamesha save rahe**.
Vercel / Netlify is ke liye munasib nahi (wahan data save nahi rehta).

Teen rasteh hain. Tinon mein pehle `hrms.zip` ke andar ka folder (jis mein `run.py` aur `wsgi.py` hain) upload karna hota hai.

| Raasta | Kharcha | Kis ke liye |
|---|---|---|
| A. PythonAnywhere | Free plan hai (paid behtar) | Sab se aasan, bina server seekhe. Free par subdomain `aap.pythonanywhere.com` milta hai |
| B. VPS (Ubuntu) + apna domain | ~$5-6 mahana | Asli company ke liye sab se behtar, poora control, apna domain (jaise hr.aapkidomain.com) |
| C. cPanel hosting (jo website ke saath mili) | Aap ka maujooda plan | Agar cPanel mein **"Setup Python App"** ka option ho |

> Asli employees ka salary/CNIC/bank data hai, isliye **paid plan ya VPS** rakhein aur free plan sirf aazmane ke liye.

## Pehle yeh 3 cheezein (har raaste mein)
| Setting (Environment variable) | Kya likhna hai |
|---|---|
| `HRMS_ADMIN_PASSWORD` | Admin ka **mazboot** password. Iske baghair live server start hi nahi hoga (default password sab ko maloom hai) |
| `HRMS_SECURE` | `1` (HTTPS par cookie mehfooz karta hai) |
| `HRMS_DB` | Database file ka path jo hamesha save rahe, jaise `/home/aap/hrms-data/hrms.db` |

Pehli dafa login `admin` + wohi password; phir foran apna password badal lein (system khud kahega).

## A. PythonAnywhere
1. pythonanywhere.com par account banayein. **Files** tab mein `hrms.zip` upload karein, phir **Consoles → Bash** mein: `unzip hrms.zip` (folder `~/hrms` banega) aur `mkdir ~/hrms-data`.
2. **Web** tab → *Add a new web app* → *Manual configuration* → Python 3.10 ya naya chunein.
3. Web tab mein WSGI configuration file kholein, andar ka saara text hata kar yeh likhein (apna username daalein):
```python
import os, sys
sys.path.insert(0, "/home/APNA_USERNAME/hrms")
os.environ["HRMS_ADMIN_PASSWORD"] = "yahan-mazboot-password"
os.environ["HRMS_SECURE"] = "1"
os.environ["HRMS_DB"] = "/home/APNA_USERNAME/hrms-data/hrms.db"
from wsgi import application
```
4. **Reload** dabayen, phir `https://APNA_USERNAME.pythonanywhere.com` kholein.
5. Free plan par app ko samay samay wahan "extend/Run until" karna padta hai, aur plan ki shartein khud check kar lein.

## B. VPS (Ubuntu 22.04 / 24.04) + domain
Domain ka **A record** server ke IP par laga dein (jaise `hr.aapkidomain.com`). Server par (root ya sudo se):
```bash
apt update && apt install -y python3 unzip sqlite3
adduser --system --group --home /opt/hrms hrms
mkdir -p /var/lib/hrms && chown hrms:hrms /var/lib/hrms
# hrms.zip ko /opt/ mein upload karke:
cd /opt && unzip hrms.zip && chown -R hrms:hrms /opt/hrms
```
`/etc/systemd/system/hrms.service` banayein:
```ini
[Unit]
Description=HRMS
After=network.target

[Service]
User=hrms
WorkingDirectory=/opt/hrms
Environment=HRMS_ADMIN_PASSWORD=yahan-mazboot-password
Environment=HRMS_SECURE=1
Environment=HRMS_TRUST_PROXY=1
Environment=HRMS_DB=/var/lib/hrms/hrms.db
Environment=HOST=127.0.0.1
Environment=PORT=8000
ExecStart=/usr/bin/python3 /opt/hrms/run.py
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
systemctl daemon-reload && systemctl enable --now hrms
```
HTTPS ke liye Caddy (khud certificate le leta hai). Caddy ko Caddy ki official guide (caddyserver.com/docs/install) se install karein, phir `/etc/caddy/Caddyfile`:
```
hr.aapkidomain.com {
    reverse_proxy 127.0.0.1:8000
}
```
`systemctl reload caddy`. Phir `https://hr.aapkidomain.com` kholein. Pehli dafa chalne ke baad service file se `HRMS_ADMIN_PASSWORD` wali line hata dena behtar hai.

Roz ki backup (raat 2 baje):
```bash
mkdir -p /var/backups/hrms && (crontab -l 2>/dev/null; echo '0 2 * * * sqlite3 /var/lib/hrms/hrms.db ".backup /var/backups/hrms/hrms-$(date +\%F).db"') | crontab -
```
Server ke bahar bhi backup rakhein (Admin → Backup se download).

## C. cPanel
**Setup Python App** mein: Python 3.9+ chunein, application root = upload kiya hua folder, startup file `wsgi.py`, entry point `application`, aur upar wale 3 environment variables add karein. Phir *Restart*. (Har cPanel host par yeh option nahi hota; na ho to raasta A ya B.)

## Live hone ke baad
- `admin` se login karke password badlein.
- Admin → Settings: company name, weekly off, PF %, tax options check karein.
- Employees add karein aur har ek ke page se **Create login** karke username + temporary password unhein dein.
- Employee chhod jaye to us ke page par **Mark as left**: login usi lamhe band, record mehfooz.
- Backup regular lein.

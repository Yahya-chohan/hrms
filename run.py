#!/usr/bin/env python3
"""Start the HRMS server:  python3 run.py [--host 127.0.0.1] [--port 8000]"""
import argparse
import os
from http.server import ThreadingHTTPServer

from app import db
from app import views_core, views_pay, views_acc, views_admin  # noqa: F401  (register routes)
from app.web import Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"), help="use 0.0.0.0 to allow other computers on the network")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    a = ap.parse_args()
    db.init_db()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("HRMS running at http://%s:%d   (first login: admin / Admin@123)" % (a.host, a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

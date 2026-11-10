"""WSGI entry point for hosts that run Python web apps (PythonAnywhere, cPanel "Setup Python App", gunicorn...).

    gunicorn -w 2 -b 127.0.0.1:8000 wsgi:application
"""
from app import db
from app import views_core, views_pay, views_acc, views_admin  # noqa: F401  (register routes)
from app.web import MAX_BODY, Response, client_ip, dispatch

db.init_db()

_REASONS = {200: "OK", 303: "See Other", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
            413: "Payload Too Large", 500: "Internal Server Error"}


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    if method not in ("GET", "POST"):
        resp = Response("Method not allowed", 405)
    else:
        path = environ.get("PATH_INFO", "/").encode("latin-1", "replace").decode("utf-8", "replace")
        qs = environ.get("QUERY_STRING", "")
        raw = path + ("?" + qs if qs else "")
        try:
            n = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY:
            resp = Response("Request too large", 413)
        else:
            body = environ["wsgi.input"].read(n) if n else b""
            ip = client_ip(environ.get("HTTP_X_FORWARDED_FOR"), environ.get("REMOTE_ADDR", ""))
            resp = dispatch(method, raw, body, environ.get("HTTP_COOKIE", ""), ip)
    headers = [("Content-Type", resp.ctype), ("Content-Length", str(len(resp.body))), ("X-Frame-Options", "DENY"),
               ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin")] + list(resp.headers)
    if not any(k.lower() == "cache-control" for k, _ in resp.headers):
        headers.append(("Cache-Control", "no-store"))
    start_response("%d %s" % (resp.status, _REASONS.get(resp.status, "OK")), headers)
    return [resp.body]

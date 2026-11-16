"""Authentication, sessions, CSRF, rate limiting and security headers."""

import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from pathlib import Path

from flask import g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from studio.store import now

ROLES = ("admin", "editor", "viewer")
COMMON = {"password", "12345678", "123456789", "qwertyui", "password1", "iloveyou", "11111111", "admin123", "letmein1"}
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


def secret_key(root):
    env = os.getenv("SECRET_KEY")
    if env:
        return env
    p = Path(root) / ".cache" / "secret_key"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return p.read_text().strip()


class RateLimiter:
    def __init__(self):
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key, limit, window):
        t = time.monotonic()
        with self.lock:
            dq = self.hits[key]
            while dq and dq[0] < t - window:
                dq.popleft()
            if len(dq) >= limit:
                return False, int(window - (t - dq[0])) + 1
            dq.append(t)
            if len(self.hits) > 5000:
                for k in [k for k, v in self.hits.items() if not v]:
                    self.hits.pop(k, None)
            return True, 0


limiter = RateLimiter()


def client_ip():
    return request.remote_addr or "?"


def validate_password(pw):
    if len(pw) < 8:
        return "Use at least 8 characters."
    if pw.lower() in COMMON or pw.isdigit():
        return "That password is too easy to guess."
    return None


def audit(store, action, target="", detail="", user=None):
    u = user if user is not None else getattr(g, "user", None)
    store.x("INSERT INTO audit(ts,user_id,user_email,action,target,detail,ip) VALUES(?,?,?,?,?,?,?)",
            (now(), u["id"] if u else None, u["email"] if u else None, action, str(target)[:120], str(detail)[:300],
             client_ip() if request else None))


def public_user(u):
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]}


def register(store, email, name, password, role=None):
    email = (email or "").strip().lower()
    name = (name or "").strip()[:60] or email.split("@")[0]
    if not EMAIL.match(email):
        raise ValueError("Enter a valid email address.")
    err = validate_password(password or "")
    if err:
        raise ValueError(err)
    if store.one("SELECT id FROM users WHERE email=?", (email,)):
        raise ValueError("An account with that email already exists.")
    first = store.val("SELECT COUNT(*) FROM users", default=0) == 0
    role = "admin" if first else (role if role in ROLES else "editor")
    uid = store.x("INSERT INTO users(email,name,pw_hash,role,created_at) VALUES(?,?,?,?,?)",
                  (email, name, generate_password_hash(password), role, now()))
    return store.one("SELECT * FROM users WHERE id=?", (uid,))


def login(store, email, password):
    email = (email or "").strip().lower()
    u = store.one("SELECT * FROM users WHERE email=?", (email,))
    generic = "Incorrect email or password."
    if not u:
        generate_password_hash("x")          # keep timing similar
        raise ValueError(generic)
    if u["locked_until"] and u["locked_until"] > now():
        raise ValueError("Too many failed attempts. Try again in a few minutes.")
    if not check_password_hash(u["pw_hash"], password or ""):
        fails = (u["failed"] or 0) + 1
        lock = now() + 300 if fails >= 5 else None
        store.x("UPDATE users SET failed=?, locked_until=? WHERE id=?", (0 if lock else fails, lock, u["id"]))
        raise ValueError(generic)
    if not u["active"]:
        raise ValueError("This account is disabled.")
    store.x("UPDATE users SET failed=0, locked_until=NULL, last_login=? WHERE id=?", (now(), u["id"]))
    return u


def install(app, store):
    """Wire session loading, CSRF, headers and rate limits into the Flask app."""

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            u = store.one("SELECT * FROM users WHERE id=?", (uid,))
            if u and u["active"]:
                g.user = u
            else:
                session.clear()
        g.request_id = secrets.token_hex(4)
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.path not in ("/api/auth/login", "/api/auth/register") or session.get("csrf"):
                token = request.headers.get("X-CSRF-Token", "")
                if not session.get("csrf") or not secrets.compare_digest(token, session.get("csrf", "")):
                    return jsonify({"error": "Your session expired. Reload the page and try again."}), 403
        if request.path.startswith("/api/"):
            group = "auth" if request.path.startswith("/api/auth") else "ask" if "/ask" in request.path or "compare" in request.path else "api"
            limit, window = {"auth": (20, 300), "ask": (40, 60), "api": (400, 60)}[group]
            ok, wait = limiter.allow(f"{group}:{client_ip()}:{uid or ''}", limit, window)
            if not ok:
                r = jsonify({"error": f"Too many requests. Try again in {wait}s."})
                r.status_code = 429
                r.headers["Retry-After"] = str(wait)
                return r

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'self'; "
            "object-src 'self'; base-uri 'self'; form-action 'self'")
        rid = getattr(g, "request_id", None)
        if rid:
            resp.headers["X-Request-ID"] = rid
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


def login_required(roles=None):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **k):
            if g.user is None:
                return jsonify({"error": "Please sign in."}), 401
            if roles and g.user["role"] not in roles:
                return jsonify({"error": "You do not have permission to do that."}), 403
            return fn(*a, **k)
        return inner
    return deco

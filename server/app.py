"""GraphRAG Atlas web server.

    python server/app.py                 # http://127.0.0.1:8000
    python server/app.py --port 8080 --host 0.0.0.0

The first account you register becomes the administrator.
"""

from pathlib import Path as _P
try:                                    # read GROQ_API_KEY etc. from the project's .env
    from dotenv import load_dotenv
    load_dotenv(_P(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import argparse
import json
import logging
import os
import queue
import secrets
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory, session  # noqa: E402

import security  # noqa: E402
from studio import evaluation, neo4j_sync, observability  # noqa: E402
from studio.engine import Atlas, UserError  # noqa: E402
from studio.pipeline import DuplicateError  # noqa: E402
from studio.readers import ExtractError, MAX_BYTES  # noqa: E402
from studio.retrieval import MODES  # noqa: E402

logger = logging.getLogger("atlas.web")


def create_app(engine=None, testing=False):
    app = Flask(__name__, static_folder=str(ROOT / "web"), static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("ATLAS_HTTPS") == "1"
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 14
    app.config["TESTING"] = testing
    app.secret_key = security.secret_key(ROOT)

    atlas = engine or Atlas()
    store = atlas.store
    app.atlas = atlas
    security.install(app, store)
    admin_only = security.login_required({"admin"})
    editors = security.login_required({"admin", "editor"})
    authed = security.login_required()

    def err(message, status=400):
        return jsonify({"error": message}), status

    # ---------------------------------------------------------------- pages
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        info = atlas.info()
        return jsonify({"ok": True, **info, "needs_setup": store.val("SELECT COUNT(*) FROM users", default=0) == 0,
                        "busy": atlas.pipeline.busy()})

    # ---------------------------------------------------------------- auth
    def session_payload():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return {"user": security.public_user(g.user) if g.user else None, "csrf": session["csrf"],
                "needs_setup": store.val("SELECT COUNT(*) FROM users", default=0) == 0}

    @app.get("/api/auth/me")
    def me():
        return jsonify(session_payload())

    @app.post("/api/auth/register")
    def register():
        p = request.get_json(silent=True) or {}
        count = store.val("SELECT COUNT(*) FROM users", default=0)
        if count and os.getenv("ATLAS_OPEN_SIGNUP", "1") != "1":
            return err("Sign-ups are closed. Ask an administrator for an account.", 403)
        try:
            u = security.register(store, p.get("email"), p.get("name"), p.get("password"),
                                  role="viewer" if count else None)
        except ValueError as e:
            return err(str(e))
        session.clear()
        session["uid"] = u["id"]
        session.permanent = True
        g.user = u
        security.audit(store, "register", u["email"], f"role={u['role']}")
        return jsonify(session_payload())

    @app.post("/api/auth/login")
    def login():
        p = request.get_json(silent=True) or {}
        try:
            u = security.login(store, p.get("email"), p.get("password"))
        except ValueError as e:
            security.audit(store, "login_failed", (p.get("email") or "")[:80])
            return err(str(e), 401)
        session.clear()
        session["uid"] = u["id"]
        session.permanent = True
        g.user = u
        security.audit(store, "login", u["email"])
        return jsonify(session_payload())

    @app.post("/api/auth/logout")
    def logout():
        if g.user:
            security.audit(store, "logout", g.user["email"])
        session.clear()
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- overview / examples
    @app.get("/api/overview")
    @authed
    def overview():
        return jsonify(atlas.overview(g.user))

    @app.get("/api/examples")
    @authed
    def examples():
        out = []
        docs = store.q("SELECT id, title, suggestions FROM documents WHERE builtin=0 AND status='ready' AND is_current=1 "
                       "AND archived=0 ORDER BY created_at DESC LIMIT 4")
        allowed = atlas.allowed(g.user)
        for d in docs:
            if d["id"] in allowed:
                for q in (d["suggestions"] or [])[:2]:
                    if any(o["question"] == q for o in out):
                        continue
                    out.append({"question": q, "type": "your document", "scope": "doc:" + d["id"]})
        for q in evaluation.load_questions(store):
            if len(out) >= 8:
                break
            if q["answerable"] and q["type"] in ("multi_hop", "conceptual", "cross_document") and not any(
                    o["type"] == q["type"] and len([x for x in out if x["type"] == q["type"]]) >= 2 for o in out):
                out.append({"question": q["question"], "type": q["type"].replace("_", "-"), "scope": "all"})
        out.append({"question": "What are the main themes across the documents?", "type": "overview", "scope": "all"})
        out.append({"question": "Do any documents contradict each other?", "type": "conflicts", "scope": "all"})
        return jsonify(out[:10])

    # ---------------------------------------------------------------- ask
    def ask_args(p):
        return dict(question=p.get("question"), mode=p.get("mode", "auto"), top_k=int(p.get("top_k", 5) or 5),
                    hops=int(p.get("hops", 2) or 2), scope=str(p.get("scope") or "all"), rerank=bool(p.get("rerank", True)),
                    style="concise" if p.get("style") == "concise" else "detailed")

    @app.post("/api/ask")
    @authed
    def ask():
        try:
            return jsonify(atlas.ask(g.user, **ask_args(request.get_json(silent=True) or {})))
        except (UserError, ValueError) as e:
            return err(str(e))

    @app.post("/api/ask/stream")
    @authed
    def ask_stream():
        try:
            args = ask_args(request.get_json(silent=True) or {})
        except (TypeError, ValueError):
            return err("Invalid settings.")
        if not (args["question"] or "").strip():
            return err("Type a question first.")
        if args["mode"] not in MODES:
            return err("Unknown mode.")
        user = g.user
        q = queue.Queue()

        def work():
            try:
                atlas.ask(user, emit=lambda ev, data: q.put((ev, data)), **args)
            except UserError as e:
                q.put(("error", {"message": str(e)}))
            except Exception as e:  # noqa: BLE001
                q.put(("error", {"message": friendly(e)}))
            finally:
                q.put(("done", {}))

        threading.Thread(target=work, daemon=True).start()

        def events():
            while True:
                ev, data = q.get()
                yield f"event: {ev}\ndata: {json.dumps(data, default=str)}\n\n"
                if ev == "done":
                    return

        return Response(events(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def friendly(e):
        name = type(e).__name__
        if name == "RateLimitError":
            return "The language model rate limit was reached. Wait a few seconds and ask again."
        if name in ("AuthenticationError", "PermissionDeniedError"):
            return "The language model rejected the API key. Check GROQ_API_KEY in .env."
        return f"Something went wrong ({name}). It has been logged."

    @app.post("/api/compare")
    @authed
    def compare():
        p = request.get_json(silent=True) or {}
        try:
            return jsonify(atlas.compare(g.user, p.get("question"), int(p.get("top_k", 5)), int(p.get("hops", 2)),
                                         str(p.get("scope") or "all")))
        except (UserError, ValueError) as e:
            return err(str(e))

    @app.post("/api/feedback")
    @authed
    def feedback():
        p = request.get_json(silent=True) or {}
        try:
            atlas.submit_feedback(g.user, p.get("query_id"), int(p.get("rating", 0)), p.get("tags"), p.get("comment"),
                                  p.get("chunk_marks"))
        except KeyError as e:
            return err(str(e.args[0]), 404)
        except (UserError, ValueError) as e:
            return err(str(e))
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- documents
    @app.get("/api/documents")
    @authed
    def documents():
        return jsonify(atlas.list_documents(g.user))

    @app.post("/api/documents")
    @editors
    def upload():
        files = request.files.getlist("files")
        if not files:
            return err("Choose at least one file.")
        graph = request.form.get("graph", "1") != "0"
        visibility = request.form.get("visibility", "shared")
        added, rejected = [], []
        for f in files:
            name = f.filename or "document"
            try:
                d = atlas.pipeline.add(g.user, name, f.read(MAX_BYTES + 1), graph, visibility)
                added.append({"id": d["id"], "title": d["title"], "version": d["version"]})
                security.audit(store, "upload", d["id"], name)
            except (ExtractError, DuplicateError, ValueError) as e:
                rejected.append({"name": name, "reason": str(e)})
        return jsonify({"added": added, "rejected": rejected})

    def doc_guard(doc_id, write=False):
        try:
            d = atlas._doc_for(g.user, doc_id)
        except KeyError:
            return None, err("No such document.", 404)
        if write and g.user["role"] != "admin" and (d["owner_id"] != g.user["id"] or d["builtin"] or g.user["role"] == "viewer"):
            return None, err("You can only change your own documents.", 403)
        return d, None

    @app.get("/api/documents/<doc_id>")
    @authed
    def document(doc_id):
        d, e = doc_guard(doc_id)
        return e or jsonify(atlas.document_detail(g.user, doc_id))

    @app.delete("/api/documents/<doc_id>")
    @authed
    def delete_doc(doc_id):
        d, e = doc_guard(doc_id, True)
        if e:
            return e
        try:
            atlas.pipeline.delete(doc_id)
        except RuntimeError as ex:
            return err(str(ex), 409)
        security.audit(store, "delete_document", doc_id, d["filename"])
        return jsonify({"ok": True})

    @app.post("/api/documents/<doc_id>/<action>")
    @authed
    def doc_action(doc_id, action):
        d, e = doc_guard(doc_id, True)
        if e:
            return e
        p = request.get_json(silent=True) or {}
        try:
            if action == "archive":
                atlas.pipeline.archive(doc_id)
            elif action == "restore":
                atlas.pipeline.restore(doc_id)
            elif action == "reindex":
                atlas.pipeline.reindex(doc_id)
            elif action == "visibility":
                atlas.pipeline.set_visibility(doc_id, p.get("visibility"))
            else:
                return err("Unknown action.", 404)
        except (RuntimeError, ValueError) as ex:
            return err(str(ex), 409)
        security.audit(store, f"document_{action}", doc_id)
        return jsonify({"ok": True})

    @app.get("/api/documents/<doc_id>/file")
    @authed
    def doc_file(doc_id):
        d, e = doc_guard(doc_id)
        if e:
            return e
        path = (atlas.pipeline.data_dir / d["original"][5:]) if d["original"].startswith("data:") \
            else atlas.pipeline.upload_dir / d["original"]
        if not path.exists():
            return err("The original file is no longer stored.", 404)
        mime = {"pdf": "application/pdf"}.get(d["kind"], "text/plain; charset=utf-8" if d["kind"] in ("txt", "md") else
                                              "application/octet-stream")
        resp = send_file(path, mimetype=mime, as_attachment=mime == "application/octet-stream", download_name=d["filename"],
                         conditional=True)
        resp.headers["Content-Disposition"] = (("attachment" if mime == "application/octet-stream" else "inline")
                                               + f'; filename="{d["filename"].replace(chr(34), "")}"')
        return resp

    @app.get("/api/documents/<doc_id>/pages/<int:page>")
    @authed
    def doc_page(doc_id, page):
        try:
            return jsonify(atlas.page_text(g.user, doc_id, page))
        except KeyError as e:
            return err(str(e.args[0]), 404)

    @app.get("/api/passages/<path:chunk_id>")
    @authed
    def passage(chunk_id):
        try:
            return jsonify(atlas.passage(g.user, chunk_id))
        except KeyError as e:
            return err(str(e.args[0]), 404)

    # ---------------------------------------------------------------- graph & insight
    @app.get("/api/graph")
    @authed
    def graph():
        a = request.args
        try:
            limit = max(10, min(300, int(a.get("limit", 90))))
            comm = int(a["community"]) if a.get("community") else None
        except ValueError:
            return err("Invalid parameters.")
        return jsonify(atlas.explore(g.user, limit, a.get("q"), a.get("doc"), comm, a.get("type")))

    @app.get("/api/entities/search")
    @authed
    def entity_search():
        return jsonify(atlas.entity_search(g.user, request.args.get("q", "")))

    @app.get("/api/entities/<int:eid>")
    @authed
    def entity(eid):
        try:
            return jsonify(atlas.entity_detail(g.user, eid))
        except KeyError as e:
            return err(str(e.args[0]), 404)

    @app.get("/api/graph/path")
    @authed
    def graph_path():
        try:
            p = atlas.entity_path(g.user, int(request.args["a"]), int(request.args["b"]))
        except (KeyError, ValueError):
            return err("Pick two entities.")
        return jsonify(p or {"nodes": [], "edges": []})

    @app.get("/api/resolution")
    @authed
    def resolution_view():
        return jsonify(atlas.resolution_overview(g.user))

    @app.post("/api/resolution/decide")
    @editors
    def resolution_decide():
        p = request.get_json(silent=True) or {}
        try:
            q = atlas.decide_pair(g.user, int(p["a"]), int(p["b"]), p.get("action"))
        except (KeyError, ValueError, TypeError) as e:
            return err("Invalid request." if not isinstance(e, ValueError) else str(e))
        security.audit(store, "entity_" + str(p.get("action")), f"{p.get('a')}:{p.get('b')}")
        return jsonify({"ok": True, "quality": q})

    @app.get("/api/communities")
    @authed
    def communities():
        return jsonify(atlas.list_communities(g.user))

    @app.get("/api/claims")
    @authed
    def claims():
        return jsonify(atlas.list_claims(g.user, request.args.get("q"), request.args.get("doc")))

    @app.get("/api/contradictions")
    @authed
    def contradictions():
        return jsonify(atlas.list_contradictions(g.user, request.args.get("status")))

    @app.post("/api/contradictions/<int:cid>")
    @editors
    def review(cid):
        try:
            atlas.review_contradiction(g.user, cid, (request.get_json(silent=True) or {}).get("status"))
        except KeyError as e:
            return err(str(e.args[0]), 404)
        except UserError as e:
            return err(str(e))
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- operations
    @app.get("/api/metrics")
    @authed
    def metrics():
        try:
            hours = max(1, min(24 * 90, int(request.args.get("hours", 168))))
        except ValueError:
            hours = 168
        return jsonify({**observability.metrics(store, hours), "info": atlas.info()})

    @app.get("/metrics")
    def prom():
        token = os.getenv("METRICS_TOKEN")
        ok = (token and request.headers.get("Authorization") == f"Bearer {token}") or (g.user and g.user["role"] == "admin")
        if not ok:
            return err("Forbidden.", 403)
        extra = {"documents": store.val("SELECT COUNT(*) FROM documents", default=0),
                 "entities": store.val("SELECT COUNT(*) FROM entities", default=0)}
        return Response(observability.prometheus(store, extra), mimetype="text/plain")

    @app.get("/api/queries/<qid>")
    @authed
    def query_detail(qid):
        q = store.one("SELECT * FROM queries WHERE id=?", (qid,))
        if not q or (g.user["role"] != "admin" and q["user_id"] != g.user["id"]):
            return err("No such query.", 404)
        return jsonify(q)

    @app.get("/api/history")
    @authed
    def history():
        rows = store.q("SELECT id, ts, question, mode, route, grounding, status FROM queries WHERE user_id=? AND status='ok' "
                       "ORDER BY ts DESC LIMIT 15", (g.user["id"],))
        return jsonify(rows)

    @app.get("/api/evaluation")
    @authed
    def eval_list():
        runs = evaluation.list_runs(store)
        return jsonify({"runs": runs, "questions": len(evaluation.load_questions(store)), "strategies": list(evaluation.STRATEGIES)})

    @app.post("/api/evaluation/run")
    @editors
    def eval_run():
        p = request.get_json(silent=True) or {}
        try:
            rid = evaluation.start_run(atlas, g.user["id"], p.get("strategies"), p.get("limit"), int(p.get("top_k", 5)))
        except RuntimeError as e:
            return err(str(e), 409)
        except ValueError as e:
            return err(str(e))
        security.audit(store, "evaluation_run", rid)
        return jsonify({"id": rid})

    @app.get("/api/evaluation/<int:rid>")
    @authed
    def eval_detail(rid):
        try:
            return jsonify(evaluation.run_detail(store, rid))
        except KeyError as e:
            return err(str(e.args[0]), 404)

    @app.get("/api/feedback/queue")
    @editors
    def fb_queue():
        return jsonify(atlas.feedback_queue(g.user, request.args.get("status", "new")))

    @app.post("/api/feedback/<int:fid>/resolve")
    @editors
    def fb_resolve(fid):
        try:
            atlas.resolve_feedback(g.user, fid, (request.get_json(silent=True) or {}).get("action", "done"))
        except KeyError as e:
            return err(str(e.args[0]), 404)
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- admin
    @app.get("/api/admin/users")
    @admin_only
    def users():
        return jsonify(store.q("SELECT id, email, name, role, active, created_at, last_login FROM users ORDER BY id"))

    @app.patch("/api/admin/users/<int:uid>")
    @admin_only
    def update_user(uid):
        p = request.get_json(silent=True) or {}
        u = store.one("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            return err("No such user.", 404)
        role = p.get("role", u["role"])
        active = int(bool(p.get("active", u["active"])))
        if role not in security.ROLES:
            return err("Unknown role.")
        if uid == g.user["id"] and (role != "admin" or not active):
            return err("You cannot remove your own admin access.")
        store.x("UPDATE users SET role=?, active=? WHERE id=?", (role, active, uid))
        security.audit(store, "user_update", u["email"], f"role={role} active={active}")
        return jsonify({"ok": True})

    @app.get("/api/admin/audit")
    @admin_only
    def audit_log():
        return jsonify(store.q("SELECT * FROM audit ORDER BY id DESC LIMIT 150"))

    @app.get("/api/admin/system")
    @admin_only
    def system():
        return jsonify({**atlas.info(), "db_path": str(store.path), "neo4j_configured": all(
            os.getenv(k) for k in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")),
            "users": store.val("SELECT COUNT(*) FROM users", default=0)})

    @app.post("/api/admin/neo4j-sync")
    @admin_only
    def neo4j():
        try:
            from neo4j_connection import Neo4jConnection
            with Neo4jConnection() as db:
                db.verify_connection()
                counts = neo4j_sync.sync(store, db)
        except Exception as e:  # noqa: BLE001
            return err(f"Neo4j sync failed: {type(e).__name__}: {str(e)[:200]}", 502)
        security.audit(store, "neo4j_sync", "", json.dumps(counts))
        return jsonify({"ok": True, "counts": counts})

    @app.errorhandler(413)
    def too_large(_):
        return err("That upload is too large.", 413)

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return err("Not found.", 404)
        return send_from_directory(app.static_folder, "index.html")

    @app.errorhandler(500)
    def server_error(e):
        logger.exception("unhandled error")
        return err("Something went wrong on the server. It has been logged.", 500)

    return app


def main():
    parser = argparse.ArgumentParser(description="GraphRAG Atlas")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--offline", action="store_true", help="never call the language model")
    parser.add_argument("--json-logs", action="store_true")
    args = parser.parse_args()
    if args.offline:
        os.environ["ATLAS_OFFLINE"] = "1"
    observability.configure_logging(os.getenv("LOG_LEVEL", "INFO"), args.json_logs)
    app = create_app()
    info = app.atlas.info()
    print(f"GraphRAG Atlas {info['version']}  |  {'LLM: ' + info['llm']['model'] if info['llm']['available'] else 'offline answers (set GROQ_API_KEY for the LLM)'}"
          f"  |  embeddings: {info['embedding']['model']}")
    print(f"Open http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()

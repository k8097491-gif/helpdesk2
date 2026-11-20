#!/usr/bin/env python3
"""Веб-сервер Helpdesk: REST-слой поверх пакета helpdesk + статический интерфейс.

    python3 server.py [--port 8000] [--db helpdesk.db] [--no-seed]

Только стандартная библиотека. БД SQLite однопоточная, поэтому сервер обрабатывает запросы
по одному, а периодические задачи (SLA, автозакрытие, повтор уведомлений) запускает между ними.
"""
import argparse, base64, dataclasses, json, os, re, secrets, sys, time
from datetime import datetime
from enum import Enum
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from helpdesk import (Channel, DirectoryEntry, HelpdeskError, HelpdeskSystem, Impact, InMemoryDirectory,
                      NotFound, PermissionDenied, Priority, Role, Status, Urgency, ValidationError)
from helpdesk.accounts import Accounts
from helpdesk.enums import OPEN_STATUSES, EscalationKind
from helpdesk.models import Category, Group, User
from helpdesk.rules import ALLOWED_EXTENSIONS, MAX_ATTACHMENT_BYTES, TRANSITIONS
from helpdesk.seed import seed_demo

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS: dict[str, int] = {}
MAINTENANCE_EVERY = 60


def plain(obj):
    """dataclass/enum/datetime -> JSON-совместимые значения."""
    if dataclasses.is_dataclass(obj):
        return {f.name: plain(getattr(obj, f.name)) for f in dataclasses.fields(obj) if f.name != "data"}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    return obj


def labels():
    return {n: {m.value: m.label for m in e} for n, e in
            dict(role=Role, status=Status, impact=Impact, urgency=Urgency, priority=Priority,
                 channel=Channel).items()}


class App:
    def __init__(self, hd: HelpdeskSystem):
        self.hd = hd

    # ---------------------------------------------------------------- вспомогательное
    def user(self, uid) -> User:
        return self.hd.accounts.require(uid)

    def name(self, uid):
        return None if uid is None else self.hd.accounts.get_user(uid).full_name

    def brief(self, t):
        hd = self.hd
        d = plain(t)
        d.update(requester=self.name(t.requester_id), assignee=self.name(t.assignee_id),
                 group=hd.accounts.get_group(t.group_id).name if t.group_id else None,
                 category=hd.routing.get_category(t.category_id).name, overdue=hd.sla.is_overdue(t))
        return d

    def actions(self, u: User, t, rated: bool) -> list[str]:
        out, nxt = [], TRANSITIONS[t.status]
        if t.status is not Status.CLOSED:
            out.append("comment")
        if u.role in (Role.SPECIALIST, Role.MANAGER) and t.assignee_id == u.id:
            if Status.IN_PROGRESS in nxt: out.append("start")
            if t.status is Status.IN_PROGRESS: out += ["request_info", "resolve"]
            if Status.ESCALATED in nxt: out.append("escalate")
        if u.role in (Role.DISPATCHER, Role.MANAGER) and t.status in OPEN_STATUSES:
            out += ["assign", "reclassify"]
        if t.requester_id == u.id:
            if t.status is Status.RESOLVED: out += ["confirm", "reopen"]
            if t.status is Status.CLOSED and not rated: out.append("rate")
        return out

    # ---------------------------------------------------------------- GET
    def me(self, uid):
        u = self.user(uid)
        hd = self.hd
        cats = [c for c in hd.db.query(Category, "SELECT * FROM categories ORDER BY id") if c.active]
        groups = hd.db.query(Group, "SELECT * FROM groups WHERE active = 1 ORDER BY id")
        staff = hd.db.query(User, "SELECT * FROM users WHERE active = 1 AND role IN ('SPECIALIST','MANAGER') ORDER BY id")
        return dict(user=plain(u), labels=labels(), categories=plain(cats), groups=plain(groups),
                    staff=[dict(id=s.id, name=s.full_name, group_id=s.group_id, role=s.role.value) for s in staff],
                    max_mb=MAX_ATTACHMENT_BYTES // 2**20, exts=sorted(ALLOWED_EXTENSIONS),
                    surveys=[self.brief(t) for t in hd.feedback.pending_surveys(uid)])

    def tickets(self, uid, q):
        hd, scope = self.hd, q.get("scope", ["mine"])[0]
        if scope == "queue": rows = hd.workflow.my_queue(uid)
        elif scope == "unassigned": rows = hd.workflow.unassigned_queue(uid)
        elif scope == "all":
            st = q.get("status", [""])[0]
            rows = hd.workflow.all_tickets(uid, Status(st) if st else None)
        else: rows = hd.intake.my_tickets(uid)
        text = q.get("q", [""])[0].lower().strip()
        out = [self.brief(t) for t in rows]
        return [t for t in out if not text or text in (t["title"] + t["number"] + (t["requester"] or "")).lower()]

    def ticket(self, uid, tid):
        hd, u = self.hd, self.user(uid)
        t = hd.workflow.get_ticket(uid, tid)
        fb = hd.feedback.get_for_ticket(tid)
        d = self.brief(t)
        d.update(timeline=[plain(e) for e in hd.workflow.timeline(uid, tid)], feedback=plain(fb),
                 attachments=plain(hd.store.attachments(tid)), actions=self.actions(u, t, fb is not None),
                 kb=plain(hd.kb.suggest(t.title, t.description, t.category_id)) if u.role is not Role.REQUESTER else [])
        return d

    def report(self, uid):
        a = self.hd.analytics
        return dict(queue=plain(a.queue_summary(uid)), quality=plain(a.quality(uid)),
                    overdue=[self.brief(t) for t in a.overdue(uid)],
                    workload=[dict(name=u.full_name, role=u.role.value, n=n) for u, n in a.workload(uid)])

    def admin_data(self, uid):
        hd = self.hd
        hd.accounts.require(uid, Role.ADMIN)
        return dict(users=plain(hd.db.query(User, "SELECT * FROM users ORDER BY id")),
                    categories=plain(hd.db.query(Category, "SELECT * FROM categories ORDER BY id")),
                    groups=plain(hd.db.query(Group, "SELECT * FROM groups ORDER BY id")),
                    sla=[plain(hd.sla.policy_for(p)) for p in Priority],
                    settings={k: hd.settings.get(k) for k in ("auto_close_days", "risk_ratio", "auto_assign")})

    # ---------------------------------------------------------------- POST
    def act(self, uid, tid, action, b):
        hd = self.hd
        if action == "comment": hd.workflow.add_comment(uid, tid, b.get("text"))
        elif action == "start": hd.workflow.start_work(uid, tid, b.get("note", ""))
        elif action == "request_info": hd.workflow.request_info(uid, tid, b.get("text"))
        elif action == "resolve": hd.workflow.resolve(uid, tid, b.get("text"))
        elif action == "confirm": hd.workflow.confirm_resolution(uid, tid)
        elif action == "reopen": hd.workflow.reopen(uid, tid, b.get("text"))
        elif action == "escalate":
            g = b.get("group_id")
            hd.sla.escalate(uid, tid, b.get("text"), int(g) if g else None)
        elif action == "assign":
            a = b.get("assignee_id")
            hd.routing.assign(uid, tid, int(b["group_id"]), int(a) if a else None, b.get("text", ""))
        elif action == "reclassify":
            hd.routing.reclassify(uid, tid, b.get("text"),
                                  category_id=int(b["category_id"]) if b.get("category_id") else None,
                                  impact=Impact(b["impact"]) if b.get("impact") else None,
                                  urgency=Urgency(b["urgency"]) if b.get("urgency") else None,
                                  priority=Priority(b["priority"]) if b.get("priority") else None)
        elif action == "rate": hd.feedback.rate(uid, tid, int(b.get("rating", 0)), b.get("text"))
        elif action == "attach": self.attach(uid, tid, b["file"])
        else: raise NotFound("Неизвестное действие")
        return self.ticket(uid, tid)

    def attach(self, uid, tid, f):
        try: data = base64.b64decode(f["data"], validate=True)
        except Exception: raise ValidationError("Не удалось прочитать файл")
        self.hd.intake.add_attachment(uid, tid, f["name"], data)

    def create(self, uid, b):
        files = []
        for f in b.get("files", []):
            try: files.append((f["name"], base64.b64decode(f["data"], validate=True)))
            except Exception: raise ValidationError("Не удалось прочитать файл")
        t = self.hd.intake.create_ticket(uid, b.get("title"), b.get("description"), int(b.get("category_id") or 0),
                                         Impact(b.get("impact")), Urgency(b.get("urgency")), files)
        return self.brief(t)

    def admin_op(self, uid, op, b):
        a = self.hd.admin
        g = lambda k: int(b[k]) if b.get(k) not in (None, "") else None
        if op == "group": a.create_group(uid, b.get("name"))
        elif op == "user":
            a.create_user(uid, b.get("login"), b.get("full_name"), b.get("email"), Role(b["role"]), g("group_id"),
                          Channel(b.get("channel") or "EMAIL"))
        elif op == "role": a.set_role(uid, g("user_id"), Role(b["role"]), g("group_id"))
        elif op == "deactivate": a.deactivate_user(uid, g("user_id"))
        elif op == "category": a.create_category(uid, b.get("name"), g("group_id"))
        elif op == "routing": a.set_routing(uid, g("category_id"), g("group_id"))
        elif op == "sla": a.set_sla(uid, Priority(b["priority"]), g("response"), g("resolve"))
        elif op == "setting": a.set_setting(uid, b["key"], b["value"])
        else: raise NotFound("Неизвестная операция")
        return self.admin_data(uid)

    def maintenance(self):
        hd = self.hd
        return dict(alerts=len(hd.sla.check_deadlines()), closed=len(hd.workflow.close_expired()),
                    resent=hd.notifier.retry_undelivered())


# ---------------------------------------------------------------------- HTTP
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn))
        return fn
    return deco


@route("POST", "/api/login")
def r_login(h, m, q, b):
    login = (b.get("login") or "").strip()
    if login.lower() in ("админ", "администратор"):
        login = "admin"
    user = h.app.hd.sign_in(login)
    if not user.active:
        raise PermissionDenied("Учётная запись отключена")
    token = secrets.token_urlsafe(24)
    SESSIONS[token] = user.id
    h.set_cookie = token
    return h.app.me(user.id)


@route("POST", "/api/logout")
def r_logout(h, m, q, b):
    SESSIONS.pop(h.token or "", None)
    h.set_cookie = ""
    return {}


@route("GET", "/api/me")
def r_me(h, m, q, b): return h.app.me(h.uid())


@route("GET", "/api/tickets")
def r_tickets(h, m, q, b): return h.app.tickets(h.uid(), q)


@route("POST", "/api/tickets")
def r_create(h, m, q, b): return h.app.create(h.uid(), b)


@route("GET", r"/api/tickets/(\d+)")
def r_ticket(h, m, q, b): return h.app.ticket(h.uid(), int(m[1]))


@route("POST", r"/api/tickets/(\d+)/(\w+)")
def r_act(h, m, q, b): return h.app.act(h.uid(), int(m[1]), m[2], b)


@route("GET", r"/api/attachments/(\d+)")
def r_attachment(h, m, q, b):
    a = h.app.hd.intake.get_attachment(h.uid(), int(m[1]))
    h.raw = (a.data, a.content_type, a.filename)


@route("GET", "/api/kb")
def r_kb(h, m, q, b):
    h.uid()
    return plain(h.app.hd.kb.search(q.get("q", [""])[0], limit=20))


@route("GET", "/api/kb/suggest")
def r_suggest(h, m, q, b):
    h.uid()
    cat = q.get("category_id", [""])[0]
    return plain(h.app.hd.kb.suggest(q.get("title", [""])[0], q.get("description", [""])[0], int(cat) if cat else None))


@route("POST", "/api/kb")
def r_kb_add(h, m, q, b):
    cat = b.get("category_id")
    return plain(h.app.hd.kb.add_article(h.uid(), b.get("title"), b.get("body"), b.get("keywords", ""), int(cat) if cat else None))


@route("GET", "/api/inbox")
def r_inbox(h, m, q, b): return plain(h.app.hd.notifier.inbox(h.uid())[:50])


@route("GET", "/api/report")
def r_report(h, m, q, b): return h.app.report(h.uid())


@route("GET", "/api/admin")
def r_admin(h, m, q, b): return h.app.admin_data(h.uid())


@route("POST", r"/api/admin/(\w+)")
def r_admin_op(h, m, q, b): return h.app.admin_op(h.uid(), m[1], b)


@route("POST", "/api/maintenance")
def r_maint(h, m, q, b):
    h.app.hd.accounts.require(h.uid(), Role.MANAGER, Role.ADMIN)
    return h.app.maintenance()


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "Helpdesk/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("HELPDESK_LOG"): super().log_message(fmt, *args)

    @property
    def token(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c["sid"].value if "sid" in c else None

    def uid(self):
        uid = SESSIONS.get(self.token or "")
        if uid is None:
            raise PermissionError
        return uid

    def send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra: self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, path):
        """Отдаёт файл из папки static. Неизвестный адрес -> главная страница."""
        types = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8"}
        name = os.path.basename(path)              # basename не даёт выйти из папки static через ../
        if os.path.splitext(name)[1] not in types:
            name = "index.html"
        file_path = os.path.join(HERE, "static", name)
        if not os.path.isfile(file_path):
            return self.send(404, b"Not found", "text/plain; charset=utf-8")
        with open(file_path, "rb") as f:
            self.send(200, f.read(), types[os.path.splitext(name)[1]])

    def handle_any(self, method):
        url = urlparse(self.path)
        if method == "GET" and not url.path.startswith("/api/"):
            return self.send_static(url.path)
        self.set_cookie, self.raw = None, None
        try:
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 40 * 2**20: raise ValidationError("Запрос слишком большой")
                body = json.loads(self.rfile.read(n) or b"{}")
            for m, rx, fn in ROUTES:
                match = rx.match(url.path)
                if m == method and match:
                    result = fn(self, match, parse_qs(url.query), body)
                    break
            else:
                return self.send(404, b'{"error":"Not found"}')
            if self.raw:
                data, ctype, name = self.raw
                safe = name.encode("utf-8").hex()
                return self.send(200, data, ctype if ctype.startswith(("image/", "application/pdf")) else "application/octet-stream",
                                 [("Content-Disposition", "attachment; filename*=UTF-8''" + "".join(f"%{safe[i:i+2]}" for i in range(0, len(safe), 2)))])
            extra = []
            if self.set_cookie is not None:
                extra.append(("Set-Cookie", f"sid={self.set_cookie}; Path=/; HttpOnly; SameSite=Strict" + ("" if self.set_cookie else "; Max-Age=0")))
            self.send(200, json.dumps(result, ensure_ascii=False).encode(), extra=extra)
        except PermissionError:
            self.send(401, '{"error":"Войдите в систему"}'.encode())
        except PermissionDenied as e:
            self.send(403, json.dumps({"error": str(e)}, ensure_ascii=False).encode())
        except NotFound as e:
            self.send(404, json.dumps({"error": str(e)}, ensure_ascii=False).encode())
        except (HelpdeskError, KeyError, ValueError, TypeError) as e:
            msg = str(e) if isinstance(e, HelpdeskError) else "Некорректные данные запроса"
            self.send(400, json.dumps({"error": msg}, ensure_ascii=False).encode())
        except Exception as e:  # noqa
            import traceback; traceback.print_exc()
            self.send(500, '{"error":"Внутренняя ошибка сервера"}'.encode())

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")


def build(db_path=":memory:", seed=True) -> HelpdeskSystem:
    fresh = db_path == ":memory:" or not os.path.exists(db_path)
    hd = HelpdeskSystem(db_path, directory=InMemoryDirectory([
        DirectoryEntry("student2", "Ирина Новикова", "student2@univ.example"),
        DirectoryEntry("teacher2", "Сергей Лекторов", "teacher2@univ.example")]))
    # Existing but unconfigured databases are common after an interrupted first run.
    # Complete only an empty setup; never add demo data after real tickets exist.
    needs_setup = (
        not fresh
        and hd.db.scalar("SELECT COUNT(*) FROM tickets") == 0
        and (
            hd.db.scalar("SELECT COUNT(*) FROM groups") == 0
            or hd.db.scalar("SELECT COUNT(*) FROM categories") == 0
            or hd.db.scalar("SELECT COUNT(*) FROM users WHERE role IN ('DISPATCHER','SPECIALIST','MANAGER') AND active = 1") == 0
        )
    )
    if seed and (fresh or needs_setup):
        seed_demo(hd)
    if hd.accounts.find_by_login("admin") is None:      # администратор есть всегда: войти и настроить систему
        hd.accounts.create_user("admin", "Администратор Системы", "admin@univ.example", Role.ADMIN)
    return hd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default="helpdesk.db")
    ap.add_argument("--no-seed", action="store_true", help="не создавать демо-данные и автозаполнение пустой БД")
    args = ap.parse_args()
    hd = build(args.db, not args.no_seed)
    Handler.app = App(hd)
    srv = HTTPServer((args.host, args.port), Handler)
    srv.timeout = 5
    print(f"Helpdesk: http://{args.host}:{args.port}  (БД: {args.db})")
    nxt = time.monotonic() + MAINTENANCE_EVERY
    try:
        while True:
            srv.handle_request()
            if time.monotonic() >= nxt:
                nxt = time.monotonic() + MAINTENANCE_EVERY
                try: Handler.app.maintenance()
                except Exception as e: print("maintenance:", e, file=sys.stderr)
    except KeyboardInterrupt:
        hd.close()


if __name__ == "__main__":
    main()

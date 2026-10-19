# -*- coding: utf-8 -*-
"""WorldCup Bet Arena - HTTP 服务（用户端 + 管理端）。"""
import os, sys, json, time, threading, mimetypes, secrets, socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
from core import (db, get_settings, set_setting, init_db, hash_pw, now_str, add_coins,
                  make_token, parse_token, match_dict, live_score, settle_match, open_match,
                  season_tick, build_odds, MARKET_CN, TEAM_CN, STAGE_CN, selection_cn, LOCK,
                  STATIC_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = int(os.environ.get("WC_PORT", "8848"))
START_COINS = 10000
CHECKIN_COINS = 1000
RELIEF_COINS = 500


def user_dict(u):
    return {"id": u["id"], "username": u["username"], "nickname": u["nickname"],
            "coins": u["coins"], "role": u["role"], "banned": u["banned"],
            "last_checkin": u["last_checkin"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "WorldCupArena/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---------- 基础
    def _send(self, obj, code=200, ctype="application/json; charset=utf-8"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _err(self, msg, code=400):
        self._send({"ok": False, "error": msg}, code)

    def _body(self):
        try:
            ln = int(self.headers.get("Content-Length", 0) or 0)
            if ln <= 0:
                return {}
            return json.loads(self.rfile.read(ln).decode("utf-8"))
        except Exception:
            return {}

    def _auth(self, admin=False):
        tok = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
        if not tok:
            tok = (parse_qs(urlsplit(self.path).query).get("token") or [""])[0]
        p = parse_token(tok)
        if not p:
            return None
        uid, role = p
        if admin and role != "admin":
            return None
        c = db()
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        c.close()
        if not u:
            return None
        if u["banned"] and not admin:
            return None
        return u

    def do_GET(self):
        u = urlsplit(self.path)
        try:
            if u.path.startswith("/api/"):
                return self.api_get(u.path, parse_qs(u.query))
            return self.serve_static(u.path)
        except Exception as e:
            print("[GET]", u.path, repr(e))
            self._err("server error", 500)

    def do_POST(self):
        u = urlsplit(self.path)
        try:
            return self.api_post(u.path, self._body())
        except Exception as e:
            print("[POST]", u.path, repr(e))
            self._err("server error", 500)

    def serve_static(self, path):
        if path in ("/", "/index.html"):
            fp = os.path.join(STATIC_DIR, "index.html")
        elif path in ("/admin", "/admin.html", "/admin/"):
            fp = os.path.join(STATIC_DIR, "admin.html")
        else:
            rel = path.lstrip("/").replace("..", "")
            fp = os.path.join(STATIC_DIR, rel)
        if not os.path.isfile(fp):
            self._send(b"404 Not Found", 404, "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if fp.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif fp.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif fp.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(fp, "rb") as f:
            self._send(f.read(), 200, ctype)

    # ---------- GET
    def api_get(self, path, q):
        conn = db()
        try:
            if path == "/api/state":
                return self._state(conn)
            if path == "/api/matches":
                rows = conn.execute("SELECT * FROM matches ORDER BY num").fetchall()
                return self._send({"ok": True, "data": [match_dict(r) for r in rows]})
            if path.startswith("/api/match/"):
                mid = int(path.rsplit("/", 1)[-1])
                r = conn.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
                if not r:
                    return self._err("比赛不存在", 404)
                d = match_dict(r)
                vs = conn.execute("SELECT * FROM videos WHERE (match_id=? OR match_id=0) AND enabled=1 ORDER BY match_id DESC",
                                  (mid,)).fetchall()
                d["videos"] = [dict(v) for v in vs]
                return self._send({"ok": True, "data": d})
            if path == "/api/videos":
                mid = int((q.get("match_id") or ["0"])[0])
                vs = conn.execute("SELECT * FROM videos WHERE (match_id=? OR match_id=0) AND enabled=1 ORDER BY match_id DESC",
                                  (mid,)).fetchall()
                return self._send({"ok": True, "data": [dict(v) for v in vs]})
            if path == "/api/me":
                u = self._auth()
                if not u:
                    return self._err("未登录或登录已过期", 401)
                return self._send({"ok": True, "data": user_dict(u)})
            if path == "/api/bets":
                u = self._auth()
                if not u:
                    return self._err("未登录", 401)
                rows = conn.execute("""SELECT b.*, m.team1, m.team2, m.num, m.stage, m.status mstatus,
                        m.ft1, m.ft2 FROM bets b JOIN matches m ON m.id=b.match_id
                        WHERE b.user_id=? ORDER BY b.id DESC LIMIT 200""", (u["id"],)).fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    d["market_cn"] = MARKET_CN.get(d["market"], d["market"])
                    t1 = TEAM_CN.get(d["team1"], d["team1"]); t2 = TEAM_CN.get(d["team2"], d["team2"])
                    d["team1_cn"], d["team2_cn"] = t1, t2
                    d["selection_cn"] = selection_cn(d["market"], d["selection"], t1, t2)
                    d["result"] = ("%s-%s" % (d["ft1"], d["ft2"])) if d["mstatus"] in ("finished", "settled") and d["ft1"] is not None else ""
                    out.append(d)
                return self._send({"ok": True, "data": out})
            if path == "/api/wallet":
                u = self._auth()
                if not u:
                    return self._err("未登录", 401)
                rows = conn.execute("SELECT * FROM coin_logs WHERE user_id=? ORDER BY id DESC LIMIT 200",
                                    (u["id"],)).fetchall()
                return self._send({"ok": True, "data": [dict(r) for r in rows]})
            if path == "/api/leaderboard":
                rows = conn.execute("""SELECT id,nickname,username,coins FROM users WHERE role='user'
                        ORDER BY coins DESC LIMIT 30""").fetchall()
                return self._send({"ok": True, "data": [dict(r) for r in rows]})
            if path.startswith("/api/admin/"):
                if not self._auth(admin=True):
                    return self._err("无管理员权限", 401)
                return self._admin_get(conn, path)
            return self._err("not found", 404)
        finally:
            conn.close()

    def _state(self, conn):
        s = get_settings(conn)
        num = int(s.get("current_num", 1))
        r = conn.execute("SELECT * FROM matches WHERE num=?", (num,)).fetchone()
        cur = match_dict(r) if r else None
        phase = s.get("phase", "idle")
        remain = 0
        if phase == "betting":
            remain = max(0, int(float(s.get("betting_seconds", 60)) - (time.time() - float(s.get("phase_start", 0)))))
        elif phase == "live":
            remain = max(0, int(float(s.get("live_seconds", 120)) - (time.time() - float(s.get("phase_start", 0)))))
            if r:
                cur["live"] = live_score(None, r)
                cur["score"] = {"ft": [cur["live"]["s1"], cur["live"]["s2"]]}
        elif phase == "intermission":
            remain = max(0, int(float(s.get("intermission_seconds", 4)) - (time.time() - float(s.get("phase_start", 0)))))
        return self._send({"ok": True, "data": {
            "season_status": s.get("season_status"), "auto": s.get("season_auto"),
            "phase": phase, "current_num": num, "remain": remain,
            "betting_seconds": int(float(s.get("betting_seconds", 60))),
            "live_seconds": int(float(s.get("live_seconds", 120))),
            "current": cur, "server_time": now_str(), "total_matches": 104}})

    def _admin_get(self, conn, path):
        if path == "/api/admin/overview":
            s = get_settings(conn)
            u = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(coins),0) s FROM users WHERE role='user'").fetchone()
            b = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(stake),0) st, COALESCE(SUM(payout),0) po FROM bets").fetchone()
            pend = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(stake),0) s FROM bets WHERE status='pending'").fetchone()
            today = datetime.now().strftime("%Y-%m-%d")
            t = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(stake),0) s FROM bets WHERE created_at LIKE ?",
                             (today + "%",)).fetchone()
            series = conn.execute("""SELECT substr(created_at,1,13) hr, SUM(delta) delta, COUNT(*) n
                    FROM coin_logs GROUP BY hr ORDER BY hr DESC LIMIT 48""").fetchall()
            return self._send({"ok": True, "data": {
                "users": u["c"], "coins_total": int(u["s"]), "bets": b["c"],
                "stake_total": int(b["st"]), "payout_total": int(b["po"]),
                "platform_profit": int(b["st"]) - int(b["po"]),
                "pending_bets": pend["c"], "pending_stake": int(pend["s"]),
                "today_bets": t["c"], "today_stake": int(t["s"]),
                "series": [dict(x) for x in reversed(series)],
                "season": {"status": s.get("season_status"), "phase": s.get("phase"),
                           "current_num": int(s.get("current_num", 1)), "auto": s.get("season_auto"),
                           "betting_seconds": int(float(s.get("betting_seconds", 60))),
                           "live_seconds": int(float(s.get("live_seconds", 120))),
                           "intermission_seconds": int(float(s.get("intermission_seconds", 4)))}}})
        if path == "/api/admin/users":
            rows = conn.execute("""SELECT id,username,nickname,coins,banned,created_at,role,
                    (SELECT COUNT(*) FROM bets WHERE user_id=users.id) bet_count,
                    (SELECT COALESCE(SUM(stake),0) FROM bets WHERE user_id=users.id) bet_sum
                    FROM users ORDER BY id""").fetchall()
            return self._send({"ok": True, "data": [dict(r) for r in rows]})
        if path == "/api/admin/bets":
            rows = conn.execute("""SELECT b.*, u.nickname, u.username, m.team1, m.team2, m.num
                    FROM bets b JOIN users u ON u.id=b.user_id JOIN matches m ON m.id=b.match_id
                    ORDER BY b.id DESC LIMIT 300""").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["market_cn"] = MARKET_CN.get(d["market"], d["market"])
                t1 = TEAM_CN.get(d["team1"], d["team1"]); t2 = TEAM_CN.get(d["team2"], d["team2"])
                d["team1_cn"], d["team2_cn"] = t1, t2
                d["selection_cn"] = selection_cn(d["market"], d["selection"], t1, t2)
                out.append(d)
            return self._send({"ok": True, "data": out})
        if path == "/api/admin/matches":
            rows = conn.execute("SELECT * FROM matches ORDER BY num").fetchall()
            return self._send({"ok": True, "data": [match_dict(r) for r in rows]})
        if path == "/api/admin/videos":
            rows = conn.execute("SELECT * FROM videos ORDER BY id DESC").fetchall()
            return self._send({"ok": True, "data": [dict(r) for r in rows]})
        return self._err("not found", 404)

    # ---------- POST
    def api_post(self, path, b):
        if path == "/api/register":
            return self.do_register(b)
        if path == "/api/login":
            return self.do_login(b, admin=False)
        if path == "/api/admin/login":
            return self.do_login(b, admin=True)
        if path == "/api/checkin":
            return self.do_checkin()
        if path == "/api/relief":
            return self.do_relief()
        if path == "/api/bet":
            return self.do_bet(b)
        if path == "/api/season/start":
            return self.do_season_user(b)
        if path.startswith("/api/admin/"):
            if not self._auth(admin=True):
                return self._err("无管理员权限", 401)
            return self.admin_post(path, b)
        return self._err("not found", 404)

    def do_register(self, b):
        username = (b.get("username") or "").strip()
        pw = b.get("password") or ""
        nick = (b.get("nickname") or username).strip()[:20]
        if not (3 <= len(username) <= 20) or not username.replace("_", "").isalnum():
            return self._err("账号需 3-20 位字母/数字/下划线")
        if len(pw) < 4:
            return self._err("密码至少 4 位")
        conn = db()
        try:
            if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                return self._err("账号已存在")
            salt = secrets.token_hex(8)
            cur = conn.execute("""INSERT INTO users(username,nickname,salt,pw,role,coins,created_at)
                    VALUES(?,?,?,?,?,?,?)""", (username, nick, salt, hash_pw(salt, pw), "user", START_COINS, now_str()))
            uid = cur.lastrowid
            conn.execute("INSERT INTO coin_logs(user_id,delta,balance_after,reason,created_at) VALUES(?,?,?,?,?)",
                         (uid, START_COINS, START_COINS, "新玩家注册赠送", now_str()))
            conn.commit()
            u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            return self._send({"ok": True, "data": {"token": make_token(uid, "user"), "user": user_dict(u)}})
        finally:
            conn.close()

    def do_login(self, b, admin=False):
        username = (b.get("username") or "").strip()
        pw = b.get("password") or ""
        conn = db()
        try:
            u = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not u or u["pw"] != hash_pw(u["salt"], pw):
                return self._err("账号或密码错误")
            if admin and u["role"] != "admin":
                return self._err("该账号不是管理员")
            if u["banned"]:
                return self._err("账号已被封禁")
            return self._send({"ok": True, "data": {"token": make_token(u["id"], u["role"]), "user": user_dict(u)}})
        finally:
            conn.close()

    def do_checkin(self):
        u = self._auth()
        if not u:
            return self._err("未登录", 401)
        today = datetime.now().strftime("%Y-%m-%d")
        if (u["last_checkin"] or "") == today:
            return self._err("今天已经签到过了")
        with LOCK:
            conn = db()
            try:
                bal = add_coins(conn, u["id"], CHECKIN_COINS, "每日签到")
                conn.execute("UPDATE users SET last_checkin=? WHERE id=?", (today, u["id"]))
                conn.commit()
                return self._send({"ok": True, "data": {"coins": bal, "gain": CHECKIN_COINS}})
            finally:
                conn.close()

    def do_relief(self):
        u = self._auth()
        if not u:
            return self._err("未登录", 401)
        if u["coins"] >= 200:
            return self._err("余额充足，暂不能领取救济金")
        today = datetime.now().strftime("%Y-%m-%d")
        if (u["last_relief"] or "") == today:
            return self._err("今天已经领取过救济金")
        with LOCK:
            conn = db()
            try:
                bal = add_coins(conn, u["id"], RELIEF_COINS, "破产救济金")
                conn.execute("UPDATE users SET last_relief=? WHERE id=?", (today, u["id"]))
                conn.commit()
                return self._send({"ok": True, "data": {"coins": bal, "gain": RELIEF_COINS}})
            finally:
                conn.close()

    def do_bet(self, b):
        u = self._auth()
        if not u:
            return self._err("未登录", 401)
        try:
            mid = int(b.get("match_id"))
            stake = int(b.get("stake"))
        except Exception:
            return self._err("参数错误")
        market = b.get("market") or ""
        sel = str(b.get("selection") or "")
        if market not in ("1x2", "ou25", "btts", "cs"):
            return self._err("盘口不支持")
        if stake < 10 or stake > 100000:
            return self._err("下注金额需在 10 - 100000 之间")
        with LOCK:
            conn = db()
            try:
                m = conn.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
                if not m:
                    return self._err("比赛不存在")
                if m["status"] != "open":
                    return self._err("该场已封盘，无法下注")
                s = get_settings(conn)
                if s.get("phase") != "betting" or int(s.get("current_num", 0)) != int(m["num"]):
                    return self._err("不在该场下注时段")
                odds = None
                try:
                    od = json.loads(m["odds"] or "{}")
                    if market == "1x2":
                        odds = od.get("1x2", {}).get(sel)
                    elif market == "ou25":
                        odds = od.get("ou25", {}).get(sel)
                    elif market == "btts":
                        odds = od.get("btts", {}).get(sel)
                    elif market == "cs":
                        for c in od.get("cs", []):
                            if c["sel"] == sel:
                                odds = c["odds"]
                except Exception:
                    odds = None
                if not odds:
                    return self._err("无效下注选项")
                if int(u["coins"]) < stake:
                    return self._err("金币不足")
                add_coins(conn, u["id"], -stake, "下注 第%d场 %s" % (m["num"], MARKET_CN.get(market, market)))
                conn.execute("""INSERT INTO bets(user_id,match_id,market,selection,stake,odds,status,created_at)
                        VALUES(?,?,?,?,?,?,'pending',?)""",
                             (u["id"], mid, market, sel, stake, float(odds), now_str()))
                conn.commit()
                bal = conn.execute("SELECT coins FROM users WHERE id=?", (u["id"],)).fetchone()["coins"]
                return self._send({"ok": True, "data": {"coins": bal, "odds": odds,
                                                        "potential": int(round(stake * odds))}})
            finally:
                conn.close()

    def do_season_user(self, b):
        action = b.get("action") or "start"
        with LOCK:
            conn = db()
            try:
                s = get_settings(conn)
                if action == "start":
                    if s.get("season_status") == "ended":
                        return self._err("赛季已结束，请管理员重置")
                    if s.get("season_status") != "running":
                        set_setting(conn, "season_status", "running")
                        if s.get("phase") in ("idle", "ended"):
                            open_match(conn, int(s.get("current_num", 1)))
                        elif s.get("phase") == "betting":
                            set_setting(conn, "phase_start", time.time())
                        conn.commit()
                    return self._send({"ok": True, "data": {"status": "running"}})
                return self._err("未知操作")
            finally:
                conn.close()

    # ---------- 管理端写操作
    def admin_post(self, path, b):
        with LOCK:
            conn = db()
            try:
                if path == "/api/admin/user/coins":
                    uid = int(b.get("user_id")); delta = int(b.get("delta"))
                    reason = (b.get("reason") or "管理员调整")[:60]
                    if delta == 0 or abs(delta) > 10000000:
                        return self._err("金额不合法")
                    bal = add_coins(conn, uid, delta, reason)
                    if bal is None:
                        return self._err("用户不存在")
                    conn.commit()
                    return self._send({"ok": True, "data": {"coins": bal}})
                if path == "/api/admin/user/ban":
                    uid = int(b.get("user_id")); ban = 1 if b.get("banned") else 0
                    conn.execute("UPDATE users SET banned=? WHERE id=? AND role!='admin'", (ban, uid))
                    conn.commit()
                    return self._send({"ok": True, "data": {"banned": ban}})
                if path == "/api/admin/season":
                    return self.admin_season(conn, b)
                if path == "/api/admin/match/settle":
                    mid = int(b.get("match_id"))
                    r = settle_match(conn, mid)
                    set_setting(conn, "phase", "intermission")
                    set_setting(conn, "phase_start", time.time())
                    conn.commit()
                    return self._send({"ok": True, "data": r})
                if path == "/api/admin/videos":
                    mid = int(b.get("match_id") or 0)
                    title = (b.get("title") or "官方视频源")[:60]
                    kind = b.get("kind") if b.get("kind") in ("embed", "hls", "mp4") else "embed"
                    url = (b.get("url") or "").strip()
                    if not url.startswith("http"):
                        return self._err("地址需以 http(s) 开头")
                    conn.execute("INSERT INTO videos(match_id,title,kind,url,enabled) VALUES(?,?,?,?,1)",
                                 (mid, title, kind, url))
                    conn.commit()
                    return self._send({"ok": True})
                if path == "/api/admin/videos/delete":
                    conn.execute("DELETE FROM videos WHERE id=?", (int(b.get("id")),))
                    conn.commit()
                    return self._send({"ok": True})
                if path == "/api/admin/settings":
                    for k in ("betting_seconds", "live_seconds", "intermission_seconds"):
                        if k in b:
                            v = max(5, min(3600, int(b[k])))
                            set_setting(conn, k, v)
                    if "auto" in b:
                        set_setting(conn, "season_auto", "1" if b["auto"] else "0")
                    conn.commit()
                    return self._send({"ok": True})
                return self._err("not found", 404)
            finally:
                conn.close()

    def admin_season(self, conn, b):
        action = b.get("action")
        s = get_settings(conn)
        num = int(s.get("current_num", 1))
        if action == "start":
            set_setting(conn, "season_status", "running")
            if s.get("phase") in ("idle", "ended"):
                open_match(conn, 1)
            elif s.get("phase") == "betting":
                set_setting(conn, "phase_start", time.time())
            elif s.get("phase") == "live":
                set_setting(conn, "phase_start", time.time() - float(s.get("live_seconds", 120)) * 0.4)
        elif action == "pause":
            set_setting(conn, "season_status", "paused")
        elif action == "open":
            open_match(conn, num)
        elif action == "kickoff":
            conn.execute("UPDATE matches SET status='live' WHERE num=?", (num,))
            set_setting(conn, "phase", "live"); set_setting(conn, "phase_start", time.time())
        elif action == "settle":
            m = conn.execute("SELECT id FROM matches WHERE num=?", (num,)).fetchone()
            if m:
                settle_match(conn, m["id"])
            set_setting(conn, "phase", "intermission"); set_setting(conn, "phase_start", time.time())
        elif action == "next":
            nxt = num + 1
            if nxt > 104:
                set_setting(conn, "season_status", "ended"); set_setting(conn, "phase", "ended")
            else:
                open_match(conn, nxt)
        elif action == "goto":
            nxt = max(1, min(104, int(b.get("num") or 1)))
            conn.execute("UPDATE matches SET status='upcoming' WHERE num>?", (nxt,))
            open_match(conn, nxt)
        elif action == "reset":
            conn.execute("DELETE FROM bets")
            conn.execute("DELETE FROM coin_logs")
            conn.execute("UPDATE users SET coins=? WHERE role='user'", (START_COINS,))
            conn.execute("UPDATE matches SET status='upcoming', odds=NULL, settled=0, live_start=NULL")
            set_setting(conn, "season_status", "idle"); set_setting(conn, "phase", "idle")
            set_setting(conn, "current_num", "1"); set_setting(conn, "phase_start", 0)
        conn.commit()
        return self._send({"ok": True, "data": {"action": action}})


def main():
    init_db()
    stop = threading.Event()
    t = threading.Thread(target=season_tick, args=(stop,), daemon=True)
    t.start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print("=" * 58)
    print("  WorldCup Bet Arena  |  2026 世界杯模拟竞猜（虚拟金币）")
    print("  用户端: http://127.0.0.1:%d" % PORT)
    print("  管理端: http://127.0.0.1:%d/admin   默认 admin / admin123" % PORT)
    print("  数据目录: %s" % core.DATA_DIR)
    print("=" * 58)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop.set()
        srv.shutdown()


if __name__ == "__main__":
    main()

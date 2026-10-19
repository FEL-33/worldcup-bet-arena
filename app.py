# -*- coding: utf-8 -*-
"""WorldCup Bet Arena - HTTP 服务（用户端 + 管理端）。"""
import os, sys, json, re, time, threading, mimetypes, secrets, socket, urllib.request
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


# ---------------- 赔率：读取 / 校验 / 写回 ----------------
ODDS_SEL = {"1x2": ("home", "draw", "away"), "ou25": ("over", "under"), "btts": ("yes", "no")}
ODDS_MIN, ODDS_MAX = 1.01, 200.0
CS_RE = re.compile(r"^(\d{1,2})-(\d{1,2})$")


def _odds_num(x):
    """把任意输入转成合法赔率浮点数；不合法返回 None。"""
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v < ODDS_MIN or v > ODDS_MAX:
        return None
    return round(v, 2)


def odds_parse(raw):
    """把 matches.odds 的 JSON 文本归一化成四盘口结构，坏数据安全降级为空。"""
    try:
        od = json.loads(raw or "{}")
        if not isinstance(od, dict):
            od = {}
    except Exception:
        od = {}
    out = {"1x2": {}, "ou25": {}, "btts": {}, "cs": []}
    for mk, sels in ODDS_SEL.items():
        cur = od.get(mk)
        cur = cur if isinstance(cur, dict) else {}
        for s in sels:
            v = _odds_num(cur.get(s))
            out[mk][s] = v if v else 0.0
    cs = od.get("cs")
    if isinstance(cs, list):
        seen = set()
        for c in cs:
            if not isinstance(c, dict):
                continue
            sel = str(c.get("sel") or "").strip()
            if not CS_RE.match(sel) or sel in seen:
                continue
            v = _odds_num(c.get("odds"))
            if not v:
                continue
            seen.add(sel)
            out["cs"].append({"sel": sel, "odds": v})
    return out


def odds_validate(data, base=None):
    """校验管理端提交的赔率，返回 (normalized, err)。
    仅接受白名单盘口与选项；赔率需落在 1.01-200；正确比分最多 30 项且不得重复。
    提交中缺失的盘口沿用 base（当前库内值），避免部分提交把其他盘口清空。
    """
    if not isinstance(data, dict):
        return None, "赔率数据格式错误"
    base = base or {"1x2": {}, "ou25": {}, "btts": {}, "cs": []}
    res = {"1x2": {}, "ou25": {}, "btts": {}, "cs": []}
    for mk in ("1x2", "ou25", "btts"):
        cur = data.get(mk)
        if cur is None:
            for s in ODDS_SEL[mk]:
                v = _odds_num((base.get(mk) or {}).get(s))
                if not v:
                    return None, "缺少盘口「%s」" % MARKET_CN.get(mk, mk)
                res[mk][s] = v
            continue
        if not isinstance(cur, dict):
            return None, "盘口「%s」数据格式错误" % MARKET_CN.get(mk, mk)
        for s in ODDS_SEL[mk]:
            v = _odds_num(cur.get(s))
            if not v:
                return None, "盘口「%s」选项 %s 的赔率需在 %.2f-%.0f 之间" % (
                    MARKET_CN.get(mk, mk), s, ODDS_MIN, ODDS_MAX)
            res[mk][s] = v
    cs = data.get("cs")
    if cs is None:
        res["cs"] = list(base.get("cs") or [])
        if not res["cs"]:
            return None, "正确比分盘口不能为空"
        return res, None
    if not isinstance(cs, list) or not cs:
        return None, "正确比分盘口不能为空"
    if len(cs) > 30:
        return None, "正确比分选项最多 30 个"
    seen = set()
    for c in cs:
        if not isinstance(c, dict):
            return None, "正确比分选项格式错误"
        sel = str(c.get("sel") or "").strip()
        m = CS_RE.match(sel)
        if not m:
            return None, "正确比分选项「%s」格式不合法（应为 主-客 如 2-1）" % sel
        if int(m.group(1)) > 20 or int(m.group(2)) > 20:
            return None, "正确比分「%s」超出范围（单队进球上限 20）" % sel
        if sel in seen:
            return None, "正确比分选项重复：%s" % sel
        v = _odds_num(c.get("odds"))
        if not v:
            return None, "正确比分「%s」的赔率需在 %.2f-%.0f 之间" % (sel, ODDS_MIN, ODDS_MAX)
        seen.add(sel)
        res["cs"].append({"sel": sel, "odds": v})
    return res, None


def odds_margin(od):
    """盘口水位（1/赔率之和），用于管理端提示抽水比例。"""
    try:
        return round(sum(1.0 / od["1x2"][s] for s in ODDS_SEL["1x2"] if od["1x2"].get(s)), 4)
    except Exception:
        return 0.0


def odds_row(conn, mid):
    """读取单场赔率行；不存在返回 None。"""
    r = conn.execute("SELECT id,num,team1,team2,stage,status,odds,odds_source,odds_updated_at "
                     "FROM matches WHERE id=?", (mid,)).fetchone()
    if not r:
        return None
    return r


def odds_out(r):
    od = odds_parse(r["odds"])
    return {"match_id": r["id"], "num": r["num"],
            "team1_cn": TEAM_CN.get(r["team1"], r["team1"]),
            "team2_cn": TEAM_CN.get(r["team2"], r["team2"]),
            "stage_cn": STAGE_CN.get(r["stage"], r["stage"]),
            "status": r["status"], "odds": od,
            "margin": odds_margin(od),
            "source": r["odds_source"] or "model",
            "updated_at": r["odds_updated_at"] or "",
            "markets": [{"key": k, "name": MARKET_CN.get(k, k)} for k in ODDS_SEL] +
                       [{"key": "cs", "name": MARKET_CN.get("cs", "正确比分")}]}


def probe_source(url, kind, timeout=5.0):
    """探测视频源可达性：取前 2KB，判断状态码 / Content-Type / 是否 HLS 清单。"""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, method="GET", headers={
            "User-Agent": "Mozilla/5.0 (WorldCupArena/1.0)", "Range": "bytes=0-2047",
            "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "") or ""
            xfo = (r.headers.get("X-Frame-Options") or "").upper()
            csp = (r.headers.get("Content-Security-Policy") or "").lower()
            body = r.read(2048) or b""
            code = r.getcode()
        playlist = body.lstrip()[:7].upper() == b"#EXTM3U"
        blocked = ("DENY" in xfo) or ("SAMEORIGIN" in xfo) or ("frame-ancestors" in csp)
        return {"reachable": True, "status": code, "content_type": ctype,
                "is_playlist": playlist, "embeddable": (not blocked),
                "kind_ok": (kind == "hls") == playlist or kind in ("embed", "link"),
                "elapsed_ms": int((time.time() - t0) * 1000),
                "note": ("HLS 清单可读" if playlist else ("内容可读" if code < 400 else "状态异常"))
                        + ("；该站禁止 iframe 内嵌，建议用 link 类型" if blocked else "")}
    except Exception as e:
        return {"reachable": False, "error": str(e)[:160],
                "elapsed_ms": int((time.time() - t0) * 1000)}


def mask_url(u):
    """把 URL 的主机名中间部分打码，保留协议与后缀，用于默认隐藏真实域名。"""
    u = (u or "").strip()
    if not u:
        return ""
    try:
        p = urlsplit(u)
        host = p.hostname or ""
    except Exception:
        return "*" * 8
    if not host:
        return "*" * 8
    parts = host.split(".")
    first = parts[0]
    keep = 2 if len(first) > 5 else 1
    mhost = first[:keep] + "*" * max(3, len(first) - keep)
    if len(parts) > 1:
        mhost += "." + ".".join(parts[1:])
    port = (":%s" % p.port) if p.port else ""
    path = "" if p.path in ("", "/") else "/***"
    return "%s://%s%s%s" % (p.scheme or "https", mhost, port, path)


def get_lan_ip():
    """取本机对外可用的局域网 IPv4（不实际发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


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

    def _client_platform(self, q=None):
        """判定请求端类型：pc / mobile。优先取 ?platform=，否则按 User-Agent。"""
        q = q or {}
        pf = ((q.get("platform") or [""])[0] or "").strip().lower()
        if pf in ("pc", "mobile"):
            return pf
        ua = (self.headers.get("User-Agent") or "").lower()
        return "mobile" if any(k in ua for k in ("mobile", "android", "iphone", "ipad", "harmonyos")) else "pc"

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
        uid, role, ver = p
        if admin and role != "admin":
            return None
        c = db()
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        c.close()
        if not u:
            return None
        if int(u["token_ver"] or 0) != ver:
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
                vs = conn.execute("""SELECT * FROM videos WHERE (match_id=? OR match_id=0) AND enabled=1
                        AND IFNULL(platform,'all') IN ('all', ?)
                        ORDER BY IFNULL(is_live,0) DESC, match_id DESC, id ASC""",
                                  (mid, self._client_platform(q))).fetchall()
                d["videos"] = [dict(v) for v in vs]
                return self._send({"ok": True, "data": d})
            if path == "/api/videos":
                mid = int((q.get("match_id") or ["0"])[0])
                vs = conn.execute("""SELECT * FROM videos WHERE (match_id=? OR match_id=0) AND enabled=1
                        AND IFNULL(platform,'all') IN ('all', ?)
                        ORDER BY IFNULL(is_live,0) DESC, match_id DESC, id ASC""",
                                  (mid, self._client_platform(q))).fetchall()
                return self._send({"ok": True, "data": [dict(v) for v in vs]})
            if path == "/api/video/play":
                return self._video_play(conn, q)
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
                return self._admin_get(conn, path, q)
            return self._err("not found", 404)
        finally:
            conn.close()

    def _video_play(self, conn, q):
        """视频画面接口：返回当前（或指定）场次可直接渲染的播放配置。
        参数：match_id / num（都可省略，默认当前场次）。
        排序：本场专属源优先，其次全局源；仅返回 enabled=1。
        """
        s = get_settings(conn)
        cur_num = int(s.get("current_num", 1))
        try:
            mid = int((q.get("match_id") or ["0"])[0] or 0)
            num = int((q.get("num") or ["0"])[0] or 0)
        except ValueError:
            return self._err("参数不合法")
        if mid:
            r = conn.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
        elif num:
            r = conn.execute("SELECT * FROM matches WHERE num=?", (num,)).fetchone()
        else:
            r = conn.execute("SELECT * FROM matches WHERE num=?", (cur_num,)).fetchone()
        if not r:
            return self._err("比赛不存在", 404)
        # 客户端类型：优先取显式参数 platform，否则按 User-Agent 判定
        pf = self._client_platform(q)
        rows = conn.execute("""SELECT id,match_id,title,kind,url,enabled,platform,is_live FROM videos
                WHERE (match_id=? OR match_id=0) AND enabled=1
                  AND IFNULL(platform,'all') IN ('all', ?)
                ORDER BY IFNULL(is_live,0) DESC, (match_id=?) DESC,
                         (IFNULL(platform,'all')='all') ASC, id ASC""",
                            (r["id"], pf, r["id"])).fetchall()
        sources = []
        for v in rows:
            d = dict(v)
            d["scope"] = "match" if d["match_id"] else "global"
            d["platform"] = d.get("platform") or "all"
            d["is_live"] = int(d.get("is_live") or 0)
            sources.append(d)
        live_now = (s.get("phase") == "live" and int(s.get("current_num", 1)) == int(r["num"]))
        has_live = any(x["is_live"] for x in sources)
        primary = sources[0] if sources else None
        return self._send({"ok": True, "data": {
            "match": {"id": r["id"], "num": r["num"],
                      "team1_cn": TEAM_CN.get(r["team1"], r["team1"]),
                      "team2_cn": TEAM_CN.get(r["team2"], r["team2"]),
                      "status": r["status"], "phase": s.get("phase"),
                      "is_current": int(r["num"]) == cur_num},
            "mode": primary["kind"] if primary else "none",
            "primary": primary, "sources": sources, "source_count": len(sources),
            "has_live_source": has_live,
            "live_sources": [x for x in sources if x["is_live"]],
            "replay_sources": [x for x in sources if not x["is_live"]],
            "live": bool(live_now), "autoplay": bool(live_now or (primary and primary["is_live"])),
            "refresh_ms": 10000 if live_now else 30000,
            "hls_js": "https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js",
            "platform": pf,
            "notice": "视频源由管理员配置，仅支持持有合法授权的 embed / HLS / MP4 源；link 类型为官方开放页跳转入口"}})

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

    def _admin_get(self, conn, path, q=None):
        q = q or {}
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
        if path == "/api/admin/odds":
            try:
                mid = int((q.get("match_id") or ["0"])[0] or 0)
            except (TypeError, ValueError):
                mid = 0
            if mid:
                r = odds_row(conn, mid)
                if not r:
                    return self._err("比赛不存在", 404)
                return self._send({"ok": True, "data": odds_out(r)})
            rows = conn.execute("""SELECT id,num,team1,team2,stage,status,odds,odds_source,odds_updated_at
                    FROM matches ORDER BY num""").fetchall()
            out = []
            for r in rows:
                d = odds_out(r)
                d.pop("markets", None)
                out.append(d)
            return self._send({"ok": True, "data": {
                "matches": out, "count": len(out),
                "markets": [{"key": k, "name": MARKET_CN.get(k, k)}
                            for k in ("1x2", "ou25", "btts", "cs")]}})
        if path == "/api/admin/videos":
            rows = conn.execute("""SELECT id,match_id,title,kind,url,enabled,platform,is_live,created_at
                    FROM videos ORDER BY enabled DESC, is_live DESC, id DESC""").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["platform"] = d.get("platform") or "all"
                d["is_live"] = int(d.get("is_live") or 0)
                out.append(d)
            return self._send({"ok": True, "data": out})
        if path == "/api/admin/access":
            s = get_settings(conn)
            port = s.get("port") or os.environ.get("WC_PORT") or "8848"
            real = (s.get("public_base") or "").strip()
            masked_cfg = (s.get("access_masked") or "1") == "1"
            reveal = (q.get("reveal") or ["0"])[0] == "1"
            show_real = reveal or not masked_cfg
            if reveal and real:
                try:
                    logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
                    os.makedirs(logdir, exist_ok=True)
                    with open(os.path.join(logdir, "access.log"), "a", encoding="utf-8") as f:
                        f.write("%s REVEAL public_base ip=%s ua=%s\n" % (
                            now_str(), self.client_address[0],
                            (self.headers.get("User-Agent") or "")[:80]))
                except Exception:
                    pass
            return self._send({"ok": True, "data": {
                "local": "http://127.0.0.1:%s" % port,
                "lan": "http://%s:%s" % (get_lan_ip(), port),
                "public_base": real if show_real else mask_url(real),
                "public_base_masked": mask_url(real),
                "has_public": bool(real),
                "reveal": show_real,
                "mask_on": masked_cfg,
                "port": port,
                "tunnel_cmd": "tools\\cloudflared.exe tunnel --url http://127.0.0.1:%s" % port,
            }})
        if path == "/api/admin/videos/test":
            vid = int((q.get("id") or ["0"])[0] or 0)
            row = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
            if not row:
                return self._err("视频源不存在", 404)
            return self._send({"ok": True, "data": probe_source(row["url"], row["kind"])})
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
            return self._send({"ok": True, "data": {"token": make_token(uid, "user", u["token_ver"]), "user": user_dict(u)}})
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
            return self._send({"ok": True, "data": {"token": make_token(u["id"], u["role"], u["token_ver"]), "user": user_dict(u)}})
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
                if path == "/api/admin/password":
                    return self.admin_change_password(conn, b)
                if path == "/api/admin/odds":
                    return self.admin_odds(conn, b)
                if path == "/api/admin/videos":
                    mid = int(b.get("match_id") or 0)
                    title = (b.get("title") or "官方视频源")[:60]
                    kind = b.get("kind") if b.get("kind") in ("embed", "hls", "mp4", "link") else "embed"
                    platform = b.get("platform") if b.get("platform") in ("all", "pc", "mobile") else "all"
                    url = (b.get("url") or "").strip()
                    if not url.startswith("http"):
                        return self._err("地址需以 http(s) 开头")
                    is_live = 1 if b.get("is_live") else 0
                    conn.execute("""INSERT INTO videos(match_id,title,kind,url,enabled,platform,created_at,is_live)
                            VALUES(?,?,?,?,1,?,?,?)""", (mid, title, kind, url, platform, now_str(), is_live))
                    conn.commit()
                    return self._send({"ok": True, "data": {"is_live": is_live}})
                if path == "/api/admin/videos/update":
                    vid = int(b.get("id") or 0)
                    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
                    if not row:
                        return self._err("视频源不存在", 404)
                    mid = int(b["match_id"]) if b.get("match_id") is not None else int(row["match_id"])
                    title = (b.get("title") or row["title"] or "").strip()[:60] or "官方视频源"
                    kind = b.get("kind") if b.get("kind") in ("embed", "hls", "mp4", "link") else row["kind"]
                    platform = b.get("platform") if b.get("platform") in ("all", "pc", "mobile") \
                        else (row["platform"] or "all")
                    url = (b.get("url") or row["url"] or "").strip()
                    if not url.startswith("http"):
                        return self._err("地址需以 http(s) 开头")
                    enabled = (1 if b["enabled"] else 0) if "enabled" in b else int(row["enabled"] or 0)
                    is_live = (1 if b["is_live"] else 0) if "is_live" in b else int(row["is_live"] or 0)
                    conn.execute("""UPDATE videos SET match_id=?, title=?, kind=?, url=?, enabled=?, platform=?,
                            is_live=? WHERE id=?""", (max(0, mid), title, kind, url, enabled, platform, is_live, vid))
                    conn.commit()
                    return self._send({"ok": True, "data": {"id": vid, "enabled": enabled,
                                                            "platform": platform, "is_live": is_live}})
                if path == "/api/admin/videos/toggle":
                    vid = int(b.get("id") or 0)
                    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
                    if not row:
                        return self._err("视频源不存在", 404)
                    enabled = (1 if b["enabled"] else 0) if "enabled" in b else (0 if row["enabled"] else 1)
                    conn.execute("UPDATE videos SET enabled=? WHERE id=?", (enabled, vid))
                    conn.commit()
                    return self._send({"ok": True, "data": {"id": vid, "enabled": enabled}})
                if path == "/api/admin/videos/delete":
                    conn.execute("DELETE FROM videos WHERE id=?", (int(b.get("id")),))
                    conn.commit()
                    return self._send({"ok": True})
                if path == "/api/admin/access":
                    if "mask" in b:
                        set_setting(conn, "access_masked", "1" if b.get("mask") else "0")
                    if "public_base" in b:
                        base = (b.get("public_base") or "").strip()
                        if "*" in base:
                            return self._err("检测到打码内容，请先点「显示真实地址」再修改")
                        if base and not base.startswith("http"):
                            return self._err("公网地址需以 http(s) 开头")
                        set_setting(conn, "public_base", base.rstrip("/"))
                    conn.commit()
                    s2 = get_settings(conn)
                    real2 = (s2.get("public_base") or "").strip()
                    masked2 = (s2.get("access_masked") or "1") == "1"
                    return self._send({"ok": True, "data": {
                        "public_base": mask_url(real2) if masked2 else real2,
                        "public_base_masked": mask_url(real2),
                        "mask_on": masked2, "has_public": bool(real2)}})
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

    def admin_odds(self, conn, b):
        """管理端赔率维护。
        action=save   保存手工调整后的赔率（四盘口全部校验后写回 matches.odds）
        action=recalc 按模型重算该场赔率并覆盖
        """
        try:
            mid = int(b.get("match_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        r = odds_row(conn, mid)
        if not r:
            return self._err("比赛不存在", 404)
        action = (b.get("action") or "save").strip().lower()
        if action not in ("save", "recalc"):
            return self._err("未知操作：仅支持 save / recalc")
        if action == "recalc":
            od = build_odds(r["team1"], r["team2"], r["stage"])
            conn.execute("""UPDATE matches SET odds=?, odds_source='model', odds_updated_at=?
                    WHERE id=?""", (json.dumps(od, ensure_ascii=False), now_str(), mid))
            conn.commit()
            self._odds_log("RECALC", mid, r["num"], "model")
            return self._send({"ok": True, "data": odds_out(odds_row(conn, mid))})
        data = b.get("odds")
        if data is None:
            data = {k: b[k] for k in ("1x2", "ou25", "btts", "cs") if k in b}
        if not data:
            return self._err("未提交赔率数据")
        od, err = odds_validate(data, odds_parse(r["odds"]))
        if err:
            return self._err(err)
        conn.execute("""UPDATE matches SET odds=?, odds_source='manual', odds_updated_at=?
                WHERE id=?""", (json.dumps(od, ensure_ascii=False), now_str(), mid))
        conn.commit()
        self._odds_log("SAVE", mid, r["num"], "manual")
        return self._send({"ok": True, "data": odds_out(odds_row(conn, mid))})

    def _odds_log(self, action, mid, num, source):
        """赔率改动留痕（仅追加文本日志，失败不影响主流程）。"""
        try:
            u = self._auth(admin=True) or {}
            logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            os.makedirs(logdir, exist_ok=True)
            with open(os.path.join(logdir, "odds.log"), "a", encoding="utf-8") as f:
                f.write("%s %s match=%s num=%s source=%s by=%s ip=%s\n" % (
                    now_str(), action, mid, num, source,
                    u.get("username", "?"), self.client_address[0]))
        except Exception:
            pass

    def admin_change_password(self, conn, b):
        """管理员改密：校验原密码 → 强度校验 → 换盐重哈希 → token_ver+1（旧会话全部失效）。"""
        u = self._auth(admin=True)
        if not u:
            return self._err("无管理员权限", 401)
        old = b.get("old_password") or ""
        new = b.get("new_password") or ""
        if "confirm_password" in b and b.get("confirm_password") != new:
            return self._err("两次输入的新密码不一致")
        if not old:
            return self._err("请输入原密码")
        if u["pw"] != hash_pw(u["salt"], old):
            return self._err("原密码不正确")
        if not (8 <= len(new) <= 64):
            return self._err("新密码需 8-64 位")
        if new == old:
            return self._err("新密码不能与原密码相同")
        weak = {"admin123", "12345678", "123456789", "password", "admin@123", "qwerty123"}
        if new.lower() in weak:
            return self._err("新密码过于常见，请更换")
        if not any(c.isalpha() for c in new) or not any(c.isdigit() for c in new):
            return self._err("新密码需同时包含字母和数字")
        salt = secrets.token_hex(8)
        conn.execute("UPDATE users SET salt=?, pw=?, token_ver=COALESCE(token_ver,0)+1 WHERE id=?",
                     (salt, hash_pw(salt, new), u["id"]))
        set_setting(conn, "admin_pw_changed_at", now_str())
        conn.commit()
        return self._send({"ok": True, "data": {"relogin": True, "changed_at": now_str(),
                                                "username": u["username"]}})

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

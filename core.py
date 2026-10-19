# -*- coding: utf-8 -*-
"""核心数据层：真实世界杯数据、模拟赔率引擎、虚拟金币账户、赛季回放引擎。"""
import os, json, math, re, sqlite3, hashlib, hmac, time, threading, secrets
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
STATIC_DIR = os.path.join(BASE, "static")
DB_PATH = os.path.join(DATA_DIR, "arena.db")
RAW_PATH = os.path.join(DATA_DIR, "matches_raw.json")
SECRET_PATH = os.path.join(DATA_DIR, "secret.key")

LOCK = threading.RLock()

TEAM_CN = {
    "Algeria": "阿尔及利亚", "Argentina": "阿根廷", "Australia": "澳大利亚", "Austria": "奥地利",
    "Belgium": "比利时", "Bosnia & Herzegovina": "波黑", "Brazil": "巴西", "Canada": "加拿大",
    "Cape Verde": "佛得角", "Colombia": "哥伦比亚", "Croatia": "克罗地亚", "Curaçao": "库拉索",
    "Czech Republic": "捷克", "DR Congo": "刚果民主共和国", "Ecuador": "厄瓜多尔", "Egypt": "埃及",
    "England": "英格兰", "France": "法国", "Germany": "德国", "Ghana": "加纳", "Haiti": "海地",
    "Iran": "伊朗", "Iraq": "伊拉克", "Ivory Coast": "科特迪瓦", "Japan": "日本", "Jordan": "约旦",
    "Mexico": "墨西哥", "Morocco": "摩洛哥", "Netherlands": "荷兰", "New Zealand": "新西兰",
    "Norway": "挪威", "Panama": "巴拿马", "Paraguay": "巴拉圭", "Portugal": "葡萄牙", "Qatar": "卡塔尔",
    "Saudi Arabia": "沙特阿拉伯", "Scotland": "苏格兰", "Senegal": "塞内加尔", "South Africa": "南非",
    "South Korea": "韩国", "Spain": "西班牙", "Sweden": "瑞典", "Switzerland": "瑞士",
    "Tunisia": "突尼斯", "Turkey": "土耳其", "USA": "美国", "Uruguay": "乌拉圭", "Uzbekistan": "乌兹别克斯坦",
}
FLAG = {
    "Algeria": "🇩🇿", "Argentina": "🇦🇷", "Australia": "🇦🇺", "Austria": "🇦🇹", "Belgium": "🇧🇪",
    "Bosnia & Herzegovina": "🇧🇦", "Brazil": "🇧🇷", "Canada": "🇨🇦", "Cape Verde": "🇨🇻",
    "Colombia": "🇨🇴", "Croatia": "🇭🇷", "Curaçao": "🇨🇼", "Czech Republic": "🇨🇿",
    "DR Congo": "🇨🇩", "Ecuador": "🇪🇨", "Egypt": "🇪🇬", "England": "🏴", "France": "🇫🇷",
    "Germany": "🇩🇪", "Ghana": "🇬🇭", "Haiti": "🇭🇹", "Iran": "🇮🇷", "Iraq": "🇮🇶",
    "Ivory Coast": "🇨🇮", "Japan": "🇯🇵", "Jordan": "🇯🇴", "Mexico": "🇲🇽", "Morocco": "🇲🇦",
    "Netherlands": "🇳🇱", "New Zealand": "🇳🇿", "Norway": "🇳🇴", "Panama": "🇵🇦",
    "Paraguay": "🇵🇾", "Portugal": "🇵🇹", "Qatar": "🇶🇦", "Saudi Arabia": "🇸🇦",
    "Scotland": "🏴", "Senegal": "🇸🇳", "South Africa": "🇿🇦", "South Korea": "🇰🇷",
    "Spain": "🇪🇸", "Sweden": "🇸🇪", "Switzerland": "🇨🇭", "Tunisia": "🇹🇳", "Turkey": "🇹🇷",
    "USA": "🇺🇸", "Uruguay": "🇺🇾", "Uzbekistan": "🇺🇿",
}
# 模拟实力参数：仅驱动虚拟盘口，不代表真实排名或真实赔率
POWER = {
    "Spain": 93, "Argentina": 92, "France": 91, "England": 89, "Brazil": 88, "Portugal": 88,
    "Netherlands": 87, "Germany": 86, "Belgium": 85, "Croatia": 84, "Uruguay": 84, "Morocco": 84,
    "Colombia": 83, "Japan": 82, "Mexico": 82, "USA": 81, "Senegal": 81, "Switzerland": 81,
    "Austria": 81, "Norway": 80, "Turkey": 80, "Ecuador": 80, "South Korea": 80, "Sweden": 79,
    "Algeria": 78, "Ivory Coast": 78, "Canada": 78, "Australia": 77, "Iran": 77, "Egypt": 77,
    "Ghana": 76, "Czech Republic": 76, "Paraguay": 76, "Scotland": 75, "Tunisia": 75,
    "Bosnia & Herzegovina": 74, "South Africa": 73, "Panama": 71, "Saudi Arabia": 71,
    "Iraq": 70, "Jordan": 70, "Qatar": 70, "Uzbekistan": 71, "DR Congo": 71,
    "Cape Verde": 68, "New Zealand": 68, "Curaçao": 66, "Haiti": 65,
}
STAGE_CN = {"group": "小组赛", "r32": "1/16 决赛", "r16": "1/8 决赛", "qf": "1/4 决赛",
            "sf": "半决赛", "third": "三四名决赛", "final": "决赛"}
MARKET_CN = {"1x2": "胜平负", "ou25": "大小球 2.5", "btts": "双方进球", "cs": "正确比分"}


def normalize_stage(round_name):
    r = (round_name or "").lower()
    if r.startswith("matchday"):
        return "group"
    if "round of 32" in r:
        return "r32"
    if "round of 16" in r:
        return "r16"
    if "quarter" in r:
        return "qf"
    if "semi" in r:
        return "sf"
    if "third" in r:
        return "third"
    if "final" in r:
        return "final"
    return "group"


def parse_minute(m):
    m = str(m)
    if "+" in m:
        a, b = m.split("+", 1)
        try:
            return int(re.sub(r"\D", "", a) or 0) + int(re.sub(r"\D", "", b) or 0)
        except Exception:
            return 90
    try:
        return int(re.sub(r"\D", "", m) or 0)
    except Exception:
        return 0


def parse_kickoff(date_str, time_str):
    try:
        y, mo, d = [int(x) for x in date_str.split("-")]
        m = re.match(r"(\d{1,2}):(\d{2})\s*UTC([+-]\d+)", time_str or "12:00 UTC+0")
        if not m:
            raise ValueError
        hh, mm, tz = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(y, mo, d, hh, mm, tzinfo=timezone(timedelta(hours=tz))).timestamp()
    except Exception:
        try:
            y, mo, d = [int(x) for x in date_str.split("-")]
            return datetime(y, mo, d, 12, 0, tzinfo=timezone.utc).timestamp()
        except Exception:
            return 0.0


# ------------------------------ 赔率引擎
def _pois(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def to_odds(p):
    p = max(min(p, 0.985), 0.002)
    return round(max(1.02, min(60.0, 0.92 / p)), 2)


def build_odds(t1, t2, stage):
    p1, p2 = POWER.get(t1, 74), POWER.get(t2, 74)
    base = 1.30
    l1 = max(0.32, min(3.40, base * math.exp((p1 - p2) / 26.0)))
    l2 = max(0.32, min(3.40, base * math.exp((p2 - p1) / 26.0)))
    if stage == "qf":
        l1 *= 0.94; l2 *= 0.94
    if stage in ("sf", "final", "third"):
        l1 *= 0.90; l2 *= 0.90
    mat = [[_pois(i, l1) * _pois(j, l2) for j in range(8)] for i in range(8)]
    ph = sum(mat[i][j] for i in range(8) for j in range(8) if i > j)
    pd = sum(mat[i][j] for i in range(8) for j in range(8) if i == j)
    pa = sum(mat[i][j] for i in range(8) for j in range(8) if i < j)
    over = sum(mat[i][j] for i in range(8) for j in range(8) if i + j >= 3)
    btts = sum(mat[i][j] for i in range(8) for j in range(8) if i > 0 and j > 0)
    cs = []
    for i in range(6):
        for j in range(6):
            cs.append({"sel": "%d-%d" % (i, j), "p": mat[i][j]})
    cs.sort(key=lambda x: -x["p"])
    return {
        "1x2": {"home": to_odds(ph), "draw": to_odds(pd), "away": to_odds(pa)},
        "ou25": {"over": to_odds(over), "under": to_odds(1 - over)},
        "btts": {"yes": to_odds(btts), "no": to_odds(1 - btts)},
        "cs": [{"sel": c["sel"], "odds": to_odds(c["p"])} for c in cs[:8]],
        "meta": {"power_home": p1, "power_away": p2,
                 "exp_home": round(l1, 2), "exp_away": round(l2, 2),
                 "p_home": round(ph, 4), "p_draw": round(pd, 4), "p_away": round(pa, 4)},
    }


def judge(market, selection, f1, f2):
    if market == "1x2":
        res = "home" if f1 > f2 else ("draw" if f1 == f2 else "away")
        return "win" if selection == res else "lose"
    if market == "ou25":
        total = f1 + f2
        if selection == "over":
            return "win" if total >= 3 else "lose"
        return "win" if total <= 2 else "lose"
    if market == "btts":
        yes = f1 > 0 and f2 > 0
        return "win" if (selection == "yes" and yes) or (selection == "no" and not yes) else "lose"
    if market == "cs":
        return "win" if selection == "%d-%d" % (f1, f2) else "lose"
    return "lose"


def selection_cn(market, sel, t1, t2):
    if market == "1x2":
        return {"home": t1 + " 胜", "draw": "平局", "away": t2 + " 胜"}.get(sel, sel)
    if market == "ou25":
        return "大球 2.5" if sel == "over" else "小球 2.5"
    if market == "btts":
        return "双方都进球" if sel == "yes" else "至少一方不进球"
    return "比分 " + str(sel)


# ------------------------------ 数据库
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def hash_pw(salt, pw):
    return hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_settings(conn):
    return {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM settings")}


def set_setting(conn, k, v):
    conn.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (k, str(v)))


def load_secret():
    if os.path.exists(SECRET_PATH):
        return open(SECRET_PATH, "rb").read()
    s = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
    open(SECRET_PATH, "wb").write(s)
    return s


SECRET = load_secret()


def make_token(uid, role, ver=0):
    """签发 token；ver 为密码版本号，改密后旧 token 立即失效。"""
    exp = int(time.time()) + 86400 * 7
    payload = "%s.%s.%s.%s" % (uid, role, exp, int(ver or 0))
    sig = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return payload + "." + sig


def parse_token(tok):
    """返回 (uid, role, ver)；旧格式 token（无版本段）按版本 0 兼容。"""
    try:
        parts = tok.split(".")
        if len(parts) == 5:
            uid, role, exp, ver, sig = parts
            payload = "%s.%s.%s.%s" % (uid, role, exp, ver)
        elif len(parts) == 4:
            uid, role, exp, sig = parts
            ver = "0"
            payload = "%s.%s.%s" % (uid, role, exp)
        else:
            return None
        if hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32] != sig:
            return None
        if int(exp) < time.time():
            return None
        return int(uid), role, int(ver)
    except Exception:
        return None


def add_coins(conn, uid, delta, reason):
    u = conn.execute("SELECT coins FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return None
    bal = max(0, int(u["coins"]) + int(delta))
    conn.execute("UPDATE users SET coins=? WHERE id=?", (bal, uid))
    conn.execute("INSERT INTO coin_logs(user_id,delta,balance_after,reason,created_at) VALUES(?,?,?,?,?)",
                 (uid, int(delta), bal, reason, now_str()))
    return bal


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      nickname TEXT,
      salt TEXT NOT NULL,
      pw TEXT NOT NULL,
      role TEXT DEFAULT 'user',
      coins INTEGER DEFAULT 0,
      banned INTEGER DEFAULT 0,
      last_checkin TEXT,
      last_relief TEXT,
      created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS matches(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      num INTEGER, round TEXT, stage TEXT, group_name TEXT,
      date TEXT, time TEXT, venue TEXT,
      team1 TEXT, team2 TEXT,
      ft1 INTEGER, ft2 INTEGER, ht1 INTEGER, ht2 INTEGER, et1 INTEGER, et2 INTEGER,
      goals TEXT, odds TEXT, kickoff REAL,
      status TEXT DEFAULT 'upcoming',
      settled INTEGER DEFAULT 0,
      live_start REAL,
      odds_source TEXT DEFAULT 'model',
      odds_updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS bets(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER, match_id INTEGER,
      market TEXT, selection TEXT, stake INTEGER, odds REAL,
      status TEXT DEFAULT 'pending', payout INTEGER DEFAULT 0,
      created_at TEXT, settled_at TEXT
    );
    CREATE TABLE IF NOT EXISTS coin_logs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER, delta INTEGER, balance_after INTEGER,
      reason TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS videos(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      match_id INTEGER DEFAULT 0,
      title TEXT, kind TEXT, url TEXT, enabled INTEGER DEFAULT 1,
      is_live INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE INDEX IF NOT EXISTS idx_bets_match ON bets(match_id);
    CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(user_id);
    """)
    # 轻量迁移：老库补列，保证改密后旧 token 失效
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "token_ver" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN token_ver INTEGER DEFAULT 0")
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "created_at" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN created_at TEXT")
    # 轻量迁移：视频源增加「客户端类型」维度（all=通用 / pc=仅电脑端 / mobile=仅手机端）
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "platform" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN platform TEXT DEFAULT 'all'")
    # 轻量迁移：视频源增加「直播 / 回放」标记（1=直播源，用户端置顶并标注「直播中」）
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "is_live" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN is_live INTEGER DEFAULT 0")
    # 轻量迁移：比赛赔率记录来源与更新时间（model=模型生成 / manual=管理员手工调整）
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(matches)")}
    if "odds_source" not in mcols:
        conn.execute("ALTER TABLE matches ADD COLUMN odds_source TEXT DEFAULT 'model'")
    if "odds_updated_at" not in mcols:
        conn.execute("ALTER TABLE matches ADD COLUMN odds_updated_at TEXT")
    defaults = {
        "season_status": "idle", "season_auto": "1", "current_num": "1",
        "phase": "idle", "phase_start": "0", "betting_seconds": "30",
        "live_seconds": "60", "intermission_seconds": "3",
        "public_base": "", "access_masked": "1",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)", (k, v))

    if conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"] == 0:
        import_data(conn)
    if not conn.execute("SELECT id FROM users WHERE role='admin'").fetchone():
        salt = secrets.token_hex(8)
        conn.execute("INSERT INTO users(username,nickname,salt,pw,role,coins,created_at) VALUES(?,?,?,?,?,?,?)",
                     ("admin", "赛事管理员", salt, hash_pw(salt, "admin123"), "admin", 0, now_str()))
        print("[init] admin / admin123")
    conn.commit()
    conn.close()


def import_data(conn):
    with open(RAW_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)["matches"]
    rows = []
    for m in raw:
        sc = m.get("score") or {}
        ft = sc.get("ft") or [0, 0]
        ht = sc.get("ht") or [None, None]
        et = sc.get("et") or [None, None]
        g = {"g1": m.get("goals1") or [], "g2": m.get("goals2") or []}
        rows.append((
            m.get("round", ""), normalize_stage(m.get("round", "")), m.get("group", ""),
            m.get("date", ""), m.get("time", ""), m.get("ground", ""),
            m.get("team1", ""), m.get("team2", ""),
            int(ft[0]), int(ft[1]),
            ht[0], ht[1], et[0], et[1],
            json.dumps(g, ensure_ascii=False), parse_kickoff(m.get("date", ""), m.get("time", "")),
        ))
    rows.sort(key=lambda r: r[-1])
    for i, r in enumerate(rows, 1):
        conn.execute("""INSERT INTO matches(num,round,stage,group_name,date,time,venue,team1,team2,
            ft1,ft2,ht1,ht2,et1,et2,goals,kickoff) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (i,) + r)
    conn.commit()
    print("[init] imported %d real matches" % len(rows))


def match_dict(r, reveal=True):
    d = {"id": r["id"], "num": r["num"], "round": r["round"], "stage": r["stage"],
         "stage_cn": STAGE_CN.get(r["stage"], r["stage"]), "group": r["group_name"],
         "date": r["date"], "time": r["time"], "venue": r["venue"],
         "team1": r["team1"], "team2": r["team2"],
         "team1_cn": TEAM_CN.get(r["team1"], r["team1"]), "team2_cn": TEAM_CN.get(r["team2"], r["team2"]),
         "flag1": FLAG.get(r["team1"], ""), "flag2": FLAG.get(r["team2"], ""),
         "status": r["status"], "settled": r["settled"]}
    if r["odds"]:
        try:
            d["odds"] = json.loads(r["odds"])
        except Exception:
            d["odds"] = None
    if reveal and r["status"] in ("finished", "settled"):
        d["score"] = {"ft": [r["ft1"], r["ft2"]], "ht": [r["ht1"], r["ht2"]], "et": [r["et1"], r["et2"]]}
        try:
            d["goals"] = json.loads(r["goals"])
        except Exception:
            d["goals"] = {"g1": [], "g2": []}
    elif r["status"] == "live":
        lv = live_score(conn_override=None, row=r)
        d["live"] = lv
        d["score"] = {"ft": [lv["s1"], lv["s2"]]}
    return d


def live_score(conn_override, row):
    try:
        c = conn_override or db()
        s = get_settings(c)
        if conn_override is None:
            c.close()
        start = float(s.get("phase_start", 0) or 0)
        dur = float(s.get("live_seconds", 120) or 120)
    except Exception:
        start, dur = 0.0, 120.0
    elapsed = max(0.0, time.time() - start)
    ratio = min(1.0, elapsed / dur)
    vm = ratio * 96.0
    try:
        goals = json.loads(row["goals"])
    except Exception:
        goals = {"g1": [], "g2": []}
    s1 = sum(1 for g in goals.get("g1", []) if parse_minute(g.get("minute", 0)) <= vm)
    s2 = sum(1 for g in goals.get("g2", []) if parse_minute(g.get("minute", 0)) <= vm)
    return {"s1": s1, "s2": s2, "minute": min(96, int(vm)), "ratio": round(ratio, 3)}


# ------------------------------ 赛季引擎
def open_match(conn, num):
    m = conn.execute("SELECT * FROM matches WHERE num=?", (num,)).fetchone()
    if not m:
        return False
    odds = m["odds"] or json.dumps(build_odds(m["team1"], m["team2"], m["stage"]), ensure_ascii=False)
    conn.execute("UPDATE matches SET status='open', odds=?, live_start=NULL WHERE id=?", (odds, m["id"]))
    set_setting(conn, "current_num", num)
    set_setting(conn, "phase", "betting")
    set_setting(conn, "phase_start", time.time())
    return True


def settle_match(conn, mid):
    m = conn.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
    if not m:
        return {"settled": 0}
    if m["settled"]:
        return {"settled": 0, "already": True}
    f1, f2 = int(m["ft1"]), int(m["ft2"])
    bets = conn.execute("SELECT * FROM bets WHERE match_id=? AND status='pending'", (mid,)).fetchall()
    n_win = n_lose = n_push = 0
    payout_sum = 0
    for b in bets:
        r = judge(b["market"], b["selection"], f1, f2)
        if r == "win":
            payout = int(round(b["stake"] * b["odds"])); n_win += 1
        elif r == "push":
            payout = int(b["stake"]); n_push += 1
        else:
            payout = 0; n_lose += 1
        conn.execute("UPDATE bets SET status=?, payout=?, settled_at=? WHERE id=?",
                     (r, payout, now_str(), b["id"]))
        if payout:
            add_coins(conn, b["user_id"], payout, "注单#%d 结算返还" % b["id"])
        payout_sum += payout
    conn.execute("UPDATE matches SET status='finished', settled=1 WHERE id=?", (mid,))
    return {"settled": len(bets), "win": n_win, "lose": n_lose, "push": n_push,
            "payout": payout_sum, "score": [f1, f2]}


def season_tick(stop_event=None):
    while not (stop_event and stop_event.is_set()):
        try:
            with LOCK:
                conn = db()
                s = get_settings(conn)
                if s.get("season_status") == "running" and s.get("season_auto", "1") == "1":
                    now = time.time()
                    phase = s.get("phase", "idle")
                    start = float(s.get("phase_start", 0) or 0)
                    if phase == "betting":
                        if now - start >= float(s.get("betting_seconds", 60)):
                            conn.execute("UPDATE matches SET status='live' WHERE num=?", (int(s["current_num"]),))
                            set_setting(conn, "phase", "live"); set_setting(conn, "phase_start", now)
                    elif phase == "live":
                        if now - start >= float(s.get("live_seconds", 120)):
                            m = conn.execute("SELECT id FROM matches WHERE num=?", (int(s["current_num"]),)).fetchone()
                            if m:
                                settle_match(conn, m["id"])
                            set_setting(conn, "phase", "intermission"); set_setting(conn, "phase_start", now)
                    elif phase == "intermission":
                        if now - start >= float(s.get("intermission_seconds", 4)):
                            nxt = int(s["current_num"]) + 1
                            if nxt > 104:
                                set_setting(conn, "season_status", "ended")
                                set_setting(conn, "phase", "ended")
                            else:
                                open_match(conn, nxt)
                    conn.commit()
                conn.close()
        except Exception as e:
            print("[tick]", e)
        time.sleep(1)

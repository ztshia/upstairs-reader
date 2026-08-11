"""
RSS 阅读器后端 — SQLite + Flask
依赖: Flask, feedparser, requests
运行: pip install -r requirements.txt && python app.py
"""

import sqlite3
import feedparser
import requests
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, g, render_template
from threading import Thread, Lock
import os
import sys
import inspect
import html
import hashlib
import time
import zhconv

# 定时刷新控制
_refresh_lock = Lock()
_last_refresh = 0
_REFRESH_INTERVAL = 30 * 60  # 30分钟刷新一次所有订阅源

app = Flask(__name__)

# ── 禁止缓存 HTML（开发阶段）──
@app.after_request
def no_cache(response):
    if response.content_type and ("html" in response.content_type or "css" in response.content_type):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ── Jinja2 繁简转换过滤器 ───────────────────────────────────────────

@app.template_filter('t2s')
def traditional_to_simplified(text):
    """繁体转简体（渲染时转换，不存库）"""
    if not text:
        return ''
    return zhconv.convert(text, 'zh-cn')

# Resolve DB path relative to this script file (works even when cwd differs from script dir)
def _get_script_dir():
    return os.path.dirname(os.path.abspath(inspect.getfile(sys.modules[__name__])))
DB_NAME = os.environ.get("DB_PATH", os.path.join(_get_script_dir(), "rss.db"))
app.config["DATABASE"] = DB_NAME

# ── 工具函数 ────────────────────────────────────────────────────

def _parse_rfc_time(s: str) -> datetime:
    """解析 RFC 2822 格式时间（含 / 不含时区偏移）→ 始终返回 UTC"""
    if not s:
        return datetime.now(timezone.utc)
    # 优先 email.utils（正确处理时区）
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        pass
    # fallback: 手动解析（需强制标为 UTC）
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S +0000",
        "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)  # 无时区信息时强制 UTC
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _auto_refresh_all():
    """后台定时刷新所有订阅源（每 30 分钟一次）"""
    global _last_refresh
    print("[auto-refresh] 线程开始运行")
    
    # 首次刷新：通过 HTTP API 触发（避免子线程 SQLite 阻塞问题）
    print("[auto-refresh] 等待 60 秒后执行首次刷新...")
    time.sleep(60)  # 等服务完全启动后再刷新
    
    try:
        import urllib.request
        print("[auto-refresh] 通过 API 触发刷新...")
        req = urllib.request.Request("http://127.0.0.1:5321/api/feeds/refresh-all")
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[auto-refresh] 刷新 API 响应: {resp.status}")
        _last_refresh = time.time()
    except Exception as e:
        print(f"[auto-refresh] 首次刷新失败: {e}")
    
    # 后续定时刷新
    while True:
        print(f"[auto-refresh] 等待 {_REFRESH_INTERVAL} 秒...")
        time.sleep(_REFRESH_INTERVAL)
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:5321/api/feeds/refresh-all")
            with urllib.request.urlopen(req, timeout=30) as resp:
                print(f"[auto-refresh] 定时刷新完成: {resp.status}")
            _last_refresh = time.time()
        except Exception as e:
            print(f"[auto-refresh] 定时刷新失败: {e}")


# ── 数据库初始化 ────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_NAME)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """建库 + 建表"""
    db = sqlite3.connect(DB_NAME)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT    NOT NULL UNIQUE,
            password_md5 TEXT    NOT NULL,
            api_key      TEXT    NOT NULL,
            display_name TEXT    DEFAULT NULL,
            avatar       TEXT    DEFAULT NULL,
            created_at   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS groups (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feeds (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT    NOT NULL UNIQUE,
            title       TEXT,
            description TEXT,
            icon_url    TEXT,
            site_url    TEXT,
            group_id    INTEGER DEFAULT NULL,
            added_at    TEXT    NOT NULL,
            updated_at  TEXT,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_id     INTEGER NOT NULL,
            guid        TEXT    NOT NULL,
            title       TEXT,
            link        TEXT,
            author      TEXT,
            content     TEXT,
            summary     TEXT,
            published   TEXT,
            is_read     INTEGER DEFAULT 0,
            is_starred  INTEGER DEFAULT 0,
            fetched_at  TEXT,
            FOREIGN KEY (feed_id) REFERENCES feeds(id) ON DELETE CASCADE,
            UNIQUE(feed_id, guid)
        );

        CREATE INDEX IF NOT EXISTS idx_items_feed   ON items(feed_id);
        CREATE INDEX IF NOT EXISTS idx_items_read   ON items(is_read);
        CREATE INDEX IF NOT EXISTS idx_items_pub    ON items(published DESC);
        CREATE INDEX IF NOT EXISTS idx_feeds_group  ON feeds(group_id);

        CREATE TABLE IF NOT EXISTS user_items (
            user_id    INTEGER NOT NULL,
            item_id    INTEGER NOT NULL,
            is_read    INTEGER DEFAULT 0,
            is_starred INTEGER DEFAULT 0,
            note       TEXT    DEFAULT NULL,
            PRIMARY KEY (user_id, item_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_user_items_user    ON user_items(user_id);
        CREATE INDEX IF NOT EXISTS idx_user_items_starred ON user_items(user_id, is_starred);
        CREATE INDEX IF NOT EXISTS idx_user_items_read    ON user_items(user_id, is_read);

        CREATE TABLE IF NOT EXISTS site_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    
    # 插入默认配置：0 为关闭注册，1 为开放注册
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('allow_registration', '0')")
    
    # AI总结功能默认配置
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('ai_enabled', '0')")
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('ai_api_url', '')")
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('ai_api_key', '')")
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('ai_model', '')")
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('ai_min_length', '500')")
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('ai_provider', 'aliyun')")
    db.execute("INSERT OR IGNORE INTO site_settings (key, value) VALUES ('ai_providers_json', '{}')")
    # 迁移：给旧表添加 site_url / created_on_time 列
    try:
        db.execute("ALTER TABLE feeds ADD COLUMN site_url TEXT")
    except Exception:
        pass  # 列已存在
    try:
        db.execute("ALTER TABLE items ADD COLUMN created_on_time INT")
    except Exception:
        pass  # 列已存在
    try:
        db.execute("ALTER TABLE items ADD COLUMN note TEXT")
    except Exception:
        pass  # 列已存在
    try:
        db.execute("ALTER TABLE users ADD COLUMN default_view TEXT DEFAULT 'list'")
    except Exception:
        pass  # 列已存在
    try:
        db.execute("ALTER TABLE users ADD COLUMN open_registration INTEGER DEFAULT 0")
    except Exception:
        pass  # 列已存在
    
    # 创建文章摘要缓存表
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS article_summaries (
                item_id INTEGER PRIMARY KEY,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                audio_status TEXT DEFAULT NULL,  -- pending | ready | error
                audio_path TEXT DEFAULT NULL,   -- 本地文件路径
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
        """)
    except Exception:
        pass  # 表已存在

    # 迁移：将 items 表中的全局 is_read/is_starred/note 迁移到 user_items
    # 分配给最早注册的用户（通常是管理员/所有者）
    try:
        count = db.execute("SELECT COUNT(*) FROM user_items").fetchone()[0]
        if count == 0:
            # 检查是否有全局星标/已读/笔记数据需要迁移
            has_data = db.execute("SELECT COUNT(*) FROM items WHERE is_read=1 OR is_starred=1 OR (note IS NOT NULL AND note != '')").fetchone()[0]
            if has_data > 0:
                # 找到最早注册的用户作为数据归属者
                owner = db.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
                if owner:
                    owner_id = owner[0]
                    db.execute("""
                        INSERT OR IGNORE INTO user_items (user_id, item_id, is_read, is_starred, note)
                        SELECT ?, id, is_read, is_starred, note FROM items
                    """, (owner_id,))
                    print(f"[migration] 已将 {has_data} 条全局数据迁移到 user_items (user_id={owner_id})")
    except Exception as e:
        print(f"[migration] user_items 迁移失败（可忽略）: {e}")

    db.commit()
    db.close()


# ── 辅助函数 ───────────────────────────────────────────────────

def _current_user_id():
    """从请求头 X-API-Key 或 query param k 获取当前用户 ID，未认证返回 None"""
    api_key = request.headers.get("X-API-Key", "") or request.args.get("k", "")
    if not api_key:
        return None
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE api_key=?", (api_key,)).fetchone()
    return user[0] if user else None


def _get_user_item_state(db, user_id, item_id):
    """获取用户对某篇文章的阅读/收藏/笔记状态，无记录则返回默认值"""
    row = db.execute(
        "SELECT is_read, is_starred, note FROM user_items WHERE user_id=? AND item_id=?",
        (user_id, item_id)
    ).fetchone()
    if row:
        return {"is_read": row[0], "is_starred": row[1], "note": row[2] or ""}
    return {"is_read": 0, "is_starred": 0, "note": ""}


def _ensure_user_item(db, user_id, item_id):
    """确保 user_items 中有该用户的记录，无则插入默认行"""
    db.execute(
        "INSERT OR IGNORE INTO user_items (user_id, item_id, is_read, is_starred, note) VALUES (?, ?, 0, 0, NULL)",
        (user_id, item_id)
    )


<<<<<<< HEAD

def _is_admin(db, user_id):
    """检查是否管理员（第一个注册的用户即为管理员）"""
    first_user = db.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
    return first_user and first_user[0] == user_id

=======
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6
def _extract_favicon_from_page(url):
    """从网站首页 HTML 的 <link rel="icon"> 提取真实 favicon URL"""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code != 200:
            return ""
        import re
        match = re.search(r'<link[^>]+rel=["\'](?:shortcut\s+)?icon["\'][^>]+href=["\']([^"\'>]+)["\']', r.text, re.I)
        if not match:
            match = re.search(r'<link[^>]+href=["\']([^"\'>]+)["\'][^>]+rel=["\'](?:shortcut\s+)?icon["\']', r.text, re.I)
        if match:
            href = match.group(1)
            if href.startswith("//"):
                from urllib.parse import urlparse
                o = urlparse(url)
                href = f"{o.scheme}:{href}"
            elif href.startswith("/"):
                from urllib.parse import urlparse
                o = urlparse(url)
                href = f"{o.scheme}://{o.netloc}{href}"
            elif not href.startswith("http"):
                href = url.rstrip("/") + "/" + href
            return href
    except Exception:
        pass
    return ""


def _build_content(entry):
    """构建文章正文：原 content + 图片型 enclosure 内联"""
    content = (entry.get("content") or [{}])[0].get("value", "") or entry.get("summary", "")
    for enc in getattr(entry, "enclosures", []):
        enc_type = enc.get("type", "")
        enc_url = enc.get("href", "") or enc.get("url", "")
        if enc_type.startswith("image/") and enc_url:
            content += f'<img src="{enc_url}" alt="" loading="lazy">'
    return content


<<<<<<< HEAD
def _normalize_guid(guid: str) -> str:
    """规范化 GUID，去重别名域名等造成的假差异"""
    g = html.unescape(guid)
    # Telegram: t.me == telegram.me
    g = g.replace("//telegram.me/", "//t.me/")
    return g


=======
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6
def fetch_feed(url: str) -> dict:
    """抓取并解析 RSS/Atom，返回 feed 元信息和条目列表"""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)

    feed_info = parsed.feed or {}

    icon_url = ""
    site_url = ""
    if feed_info.get("icon"):
        icon_url = feed_info["icon"]
    elif feed_info.get("logo"):
        icon_url = feed_info["logo"]
    # 从 feed 的 link 提取源站首页（对 RSSHub 等聚合源特别重要）
    if feed_info.get("link"):
        try:
            from urllib.parse import urlparse
            o = urlparse(feed_info["link"])
            site_url = f"{o.scheme}://{o.netloc}"
            # favicon：从网站首页 <link rel=icon> 提取真实路径
            if not icon_url and site_url:
                icon_url = _extract_favicon_from_page(site_url)
            if not icon_url:
                icon_url = f"{o.scheme}://{o.netloc}/favicon.ico"
        except Exception:
            pass

    return {
        "title":       feed_info.get("title", url),
        "description": feed_info.get("subtitle", ""),
        "icon_url":    icon_url,
        "site_url":    site_url,
        "entries": [
            {
                "guid":      _normalize_guid(e.get("id") or e.get("link") or hashlib.md5(e.get("title","").encode()).hexdigest()),
                "title":     e.get("title", "(无标题)"),
                "link":      e.get("link", ""),
                "author":    e.get("author", ""),
                "content":   _build_content(e),
                "summary":   e.get("summary", ""),
                "published": e.get("published") or e.get("updated", ""),
            }
            for e in parsed.entries
        ],
    }


def background_refresh(feed_id: int, url: str):
    """后台线程：抓取 feed 并入库"""
    db = sqlite3.connect(DB_NAME)
    db.row_factory = sqlite3.Row
    try:
        data = fetch_feed(url)
        now = datetime.now(timezone.utc).isoformat()
        # custom_title survives refresh — only overwrite the raw feed title
        db.execute(
            "UPDATE feeds SET title=?, description=?, icon_url=?, site_url=?, updated_at=? WHERE id=?",
            (data["title"], data["description"], data["icon_url"], data["site_url"], now, feed_id)
        )
        for item in data["entries"]:
            pub_ts = int(_parse_rfc_time(item.get("published", "")).timestamp())
            db.execute("""
                INSERT OR IGNORE INTO items
                (feed_id, guid, title, link, author, content, summary, published, fetched_at, created_on_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (feed_id, item["guid"], item["title"], item["link"],
                  item["author"], item["content"], item["summary"],
                  item["published"], now, pub_ts))
        db.commit()
    except Exception as e:
        print(f"[refresh feed {feed_id}] error: {e}")
    finally:
        db.close()


# ── 路由 ───────────────────────────────────────────────────────

@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/newspaper")
def newspaper():
    return render_template("newspaper.html")


@app.route("/people/<username>")
def people_page(username):
    """个人主页"""
    db = get_db()
    user = db.execute(
        "SELECT id, username, display_name, avatar, created_at FROM users WHERE username=?",
        (username,)
    ).fetchone()
    if not user:
        return render_template("index.html"), 404
    return render_template("people.html", profile_user=dict(user))



@app.route("/api/user/<username>/profile", methods=["GET"])
def get_public_profile(username):
    """获取用户公开资料（个人信息+收藏+笔记）"""
    db = get_db()
    user = db.execute(
        "SELECT id, username, display_name, avatar, created_at FROM users WHERE username=?",
        (username,)
    ).fetchone()
    if not user:
        return jsonify({"error": "用户不存在"}), 404

    uid = user[0]
    display_name = user[2] or user[1]

    # 收藏数
    starred_count = db.execute(
        "SELECT COUNT(*) FROM user_items WHERE user_id=? AND is_starred=1", (uid,)
    ).fetchone()[0]

    # 笔记数
    notes_count = db.execute(
        "SELECT COUNT(*) FROM user_items WHERE user_id=? AND note IS NOT NULL AND note != ''",
        (uid,)
    ).fetchone()[0]

    # 收藏的文章（分页）
    limit = min(request.args.get("limit", 20, type=int), 50)
    offset = request.args.get("offset", 0, type=int)
    tab = request.args.get("tab", "starred")  # starred or notes

    items = []
    total = 0
    if tab == "starred":
        items = db.execute("""
            SELECT i.id, i.title, i.link, i.author, i.content, i.summary, i.published, i.created_on_time,
                   COALESCE(f.custom_title, f.title) AS feed_title, f.icon_url AS feed_icon, f.site_url AS feed_site_url,
                   ui.is_read, ui.is_starred
            FROM user_items ui
            JOIN items i ON i.id=ui.item_id
            JOIN feeds f ON f.id=i.feed_id
            WHERE ui.user_id=? AND ui.is_starred=1
            ORDER BY i.created_on_time DESC
            LIMIT ? OFFSET ?
        """, (uid, limit, offset)).fetchall()
        total = starred_count
    elif tab == "notes":
        items = db.execute("""
            SELECT i.id, i.title, i.link, i.author, i.content, i.summary, i.published, i.created_on_time,
                   COALESCE(f.custom_title, f.title) AS feed_title, f.icon_url AS feed_icon, f.site_url AS feed_site_url,
                   ui.is_read, ui.is_starred, ui.note
            FROM user_items ui
            JOIN items i ON i.id=ui.item_id
            JOIN feeds f ON f.id=i.feed_id
            WHERE ui.user_id=? AND ui.note IS NOT NULL AND ui.note != '' AND ui.note != '[]'
            ORDER BY i.created_on_time DESC
            LIMIT ? OFFSET ?
        """, (uid, limit, offset)).fetchall()
        total = notes_count

    return jsonify({
        "username": user[1],
        "display_name": display_name,
        "avatar": user[3] or "",
        "created_at": user[4],
        "starred_count": starred_count,
        "notes_count": notes_count,
        "items": [dict(r) for r in items],
        "total": total,
        "limit": limit,
        "offset": offset,
        "tab": tab,
    })


# ── Groups API ─────────────────────────────────────────────────

@app.route("/api/groups", methods=["GET"])
def list_groups():
    db = get_db()
    groups = db.execute("SELECT * FROM groups ORDER BY sort_order, id").fetchall()
    return jsonify([dict(r) for r in groups])


@app.route("/api/groups", methods=["POST"])
def create_group():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    max_order = db.execute("SELECT MAX(sort_order) FROM groups").fetchone()[0] or 0
    cur = db.execute("INSERT INTO groups (name, sort_order, created_at) VALUES (?, ?, ?)",
                     (name, max_order + 1, now))
    db.commit()
    return jsonify({"id": cur.lastrowid, "name": name, "sort_order": max_order + 1, "created_at": now}), 201


@app.route("/api/groups/<int:group_id>", methods=["PUT"])
def update_group(group_id):
    name = request.json.get("name")
    if name is not None:
        name = name.strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    db = get_db()
    db.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/groups/<int:group_id>", methods=["DELETE"])
def delete_group(group_id):
    db = get_db()
    # Feeds in this group get ungrouped (group_id = NULL)
    db.execute("UPDATE feeds SET group_id=NULL WHERE group_id=?", (group_id,))
    db.execute("DELETE FROM groups WHERE id=?", (group_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/groups/<int:group_id>/feeds", methods=["PUT"])
def set_feed_group():
    """批量将一组 feeds 移入/移出分组"""
    feed_ids = request.json.get("feed_ids", [])
    group_id = request.json.get("group_id")  # None to remove from group
    db = get_db()
    if feed_ids:
        placeholders = ",".join("?" * len(feed_ids))
        db.execute(f"UPDATE feeds SET group_id=? WHERE id IN ({placeholders})",
                   [group_id, *feed_ids])
    db.commit()
    return jsonify({"ok": True})


# ── Feeds API ──────────────────────────────────────────────────

@app.route("/api/feeds", methods=["GET"])
def list_feeds():
    db = get_db()
    user_id = _current_user_id()
    if user_id:
        feeds = db.execute(
            "SELECT f.*, COUNT(i.id) AS item_count, "
            "SUM(COALESCE(ui.is_read, i.is_read)) AS read_count "
            "FROM feeds f LEFT JOIN items i ON i.feed_id=f.id "
            "LEFT JOIN user_items ui ON ui.user_id=? AND ui.item_id=i.id "
            "GROUP BY f.id ORDER BY f.added_at DESC", (user_id,)
        ).fetchall()
    else:
        feeds = db.execute(
            "SELECT f.*, COUNT(i.id) AS item_count, "
            "SUM(i.is_read) AS read_count "
            "FROM feeds f LEFT JOIN items i ON i.feed_id=f.id "
            "GROUP BY f.id ORDER BY f.added_at DESC"
        ).fetchall()
    result = []
    for r in feeds:
        d = dict(r)
        if d.get("custom_title"):
            d["title"] = d["custom_title"]
        result.append(d)
    return jsonify(result)


def _row(r):
    """sqlite3.Row → plain dict"""
    return dict(r)


@app.route("/api/feeds/<int:feed_id>", methods=["GET"])
def get_feed(feed_id):
    db = get_db()
    user_id = _current_user_id()
    if user_id:
        row = db.execute(
            "SELECT f.*, COUNT(i.id) AS item_count, "
            "SUM(COALESCE(ui.is_read, i.is_read)) AS read_count "
            "FROM feeds f LEFT JOIN items i ON i.feed_id=f.id "
            "LEFT JOIN user_items ui ON ui.user_id=? AND ui.item_id=i.id "
            "WHERE f.id=? GROUP BY f.id", (user_id, feed_id)
        ).fetchone()
    else:
        row = db.execute(
            "SELECT f.*, COUNT(i.id) AS item_count, "
            "SUM(i.is_read) AS read_count "
            "FROM feeds f LEFT JOIN items i ON i.feed_id=f.id "
            "WHERE f.id=? GROUP BY f.id", (feed_id,)
        ).fetchone()
    if not row:
        return jsonify({"error": "feed not found"}), 404
    d = dict(row)
    if d.get("custom_title"):
        d["title"] = d["custom_title"]
    return jsonify(d)


@app.route("/api/feeds", methods=["POST"])
def add_feed():
    url = request.json.get("url", "").strip()
    group_id = request.json.get("group_id")
    if not url:
        return jsonify({"error": "url is required"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM feeds WHERE url=?", (url,)).fetchone()
    if existing:
        return jsonify({"error": "feed already exists"}), 409

    try:
        data = fetch_feed(url)
    except Exception as e:
        return jsonify({"error": f"fetch failed: {e}"}), 400

    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT INTO feeds (url, title, description, icon_url, site_url, group_id, added_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (url, data["title"], data["description"], data["icon_url"], data["site_url"], group_id, now, now)
    )
    feed_id = cur.lastrowid

    for item in data["entries"]:
        pub_ts = int(_parse_rfc_time(item.get("published", "")).timestamp())
        db.execute("""
            INSERT OR IGNORE INTO items
            (feed_id, guid, title, link, author, content, summary, published, fetched_at, created_on_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (feed_id, item["guid"], item["title"], item["link"],
              item["author"], item["content"], item["summary"],
              item["published"], now, pub_ts))
    db.commit()
    return jsonify({"id": feed_id, "title": data["title"]}), 201


@app.route("/api/feeds/<int:feed_id>", methods=["PUT"])
def update_feed(feed_id):
    """更新 feed 的分组或标题"""
    db = get_db()
    data = request.json or {}
    if "title" in data:
        db.execute("UPDATE feeds SET custom_title=? WHERE id=?", (data["title"], feed_id))
    if "group_id" in data:
        db.execute("UPDATE feeds SET group_id=? WHERE id=?", (data["group_id"], feed_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/feeds/<int:feed_id>", methods=["DELETE"])
def delete_feed(feed_id):
    db = get_db()
    db.execute("DELETE FROM items WHERE feed_id=?", (feed_id,))  # 级联删除孤立文章
    db.execute("DELETE FROM feeds WHERE id=?", (feed_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/feeds/<int:feed_id>/refresh", methods=["POST"])
def refresh_feed(feed_id):
    db = get_db()
    feed = db.execute("SELECT id, url FROM feeds WHERE id=?", (feed_id,)).fetchone()
    if not feed:
        return jsonify({"error": "not found"}), 404
    try:
        data = fetch_feed(feed["url"])
        now = datetime.now().isoformat()
        db.execute(
            "UPDATE feeds SET title=?, description=?, icon_url=?, site_url=?, updated_at=? WHERE id=?",
            (data["title"], data["description"], data["icon_url"], data["site_url"], now, feed_id)
        )
        # 也刷新文章
        Thread(target=background_refresh, args=(feed_id, feed["url"]), daemon=True).start()
        return jsonify({"ok": True, "icon_url": data["icon_url"], "site_url": data["site_url"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/feeds/refresh-all", methods=["GET", "POST"])
def refresh_all_feeds():
    """刷新所有订阅源"""
    db = get_db()
    feeds = db.execute("SELECT id, url FROM feeds").fetchall()
    count = 0
    for feed in feeds:
        Thread(target=background_refresh, args=(feed["id"], feed["url"]), daemon=True).start()
        count += 1
    return jsonify({"ok": True, "count": count})


# ── Items API ──────────────────────────────────────────────────

@app.route("/api/items", methods=["GET"])
def list_items():
    db = get_db()
    user_id = _current_user_id()
    feed_id   = request.args.get("feed_id", type=int)
    group_id  = request.args.get("group_id", type=int)
    search    = request.args.get("q", "")
    unread    = request.args.get("unread", "")
    limit     = min(request.args.get("limit", 50, type=int), 200)
    offset    = request.args.get("offset", 0, type=int)

    if user_id:
        # 已登录：用 LEFT JOIN user_items 获取用户级别的阅读/收藏/笔记状态
        sql = """
            SELECT i.id, i.feed_id, i.guid, i.title, i.link, i.author, i.content, i.summary, i.published, i.fetched_at, i.created_on_time,
                   COALESCE(f.custom_title, f.title) AS feed_title, f.icon_url AS feed_icon, f.site_url AS feed_site_url, f.url AS feed_url,
                   COALESCE(ui.is_read, i.is_read) AS is_read,
                   COALESCE(ui.is_starred, i.is_starred) AS is_starred,
                   COALESCE(ui.note, i.note) AS note,
                   COALESCE(s.audio_status, '') AS audio_status
            FROM items i
            JOIN feeds f ON f.id=i.feed_id
            LEFT JOIN user_items ui ON ui.user_id=? AND ui.item_id=i.id
            LEFT JOIN article_summaries s ON s.item_id=i.id
            WHERE 1=1
        """
        args = [user_id]
    else:
        sql = """
            SELECT i.id, i.feed_id, i.guid, i.title, i.link, i.author, i.content, i.summary, i.published, i.fetched_at, i.created_on_time,
                   COALESCE(f.custom_title, f.title) AS feed_title, f.icon_url AS feed_icon, f.site_url AS feed_site_url, f.url AS feed_url,
                   '' AS audio_status
            FROM items i
            JOIN feeds f ON f.id=i.feed_id
            WHERE 1=1
        """
        args = []

    if feed_id:
        sql += " AND i.feed_id=?"
        args.append(feed_id)
    if group_id:
        sql += " AND f.group_id=?"
        args.append(group_id)
    if search:
        sql += " AND (i.title LIKE ? OR i.content LIKE ?)"
        args.extend([f"%{search}%", f"%{search}%"])
    if unread == "1":
        if user_id:
            sql += " AND COALESCE(ui.is_read, i.is_read)=0"
        else:
            sql += " AND i.is_read=0"
    sql += " ORDER BY i.created_on_time DESC, i.published DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])

    items = db.execute(sql, args).fetchall()
    result = [dict(r) for r in items]
    return jsonify({
        "items": result,
        "limit": limit,
        "offset": offset,
    })


@app.route("/api/newspaper/random-items", methods=["GET"])
def newspaper_random_items():
    """返回随机选取的新闻（正文>2000字），供 newspaper.html 使用"""
    import re
    from datetime import datetime, timedelta
    db = get_db()
    limit = min(request.args.get("limit", 50, type=int), 200)
    days = request.args.get("days", 0, type=int)  # 0=不限制天数
    max_items = request.args.get("max_items", 0, type=int)  # 0=不限制数量

    # 日期过滤在客户端进行（避免数据库日期格式不一致问题）
    # 先取更多条目（Python层做精确字数过滤）
    rows = db.execute("""
        SELECT i.id, i.title, i.content, i.summary, i.published, i.created_on_time,
               COALESCE(f.custom_title, f.title) AS feed_title
        FROM items i
        JOIN feeds f ON f.id=i.feed_id
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit * 3,)).fetchall()

    items = []
    seen_ids = set()
    for row in rows:
        if len(items) >= limit:
            break
        item = dict(row)
        item_id = item['id']
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        # 精确字数：去除HTML标签后判断
        raw = (item.get('content') or '') or (item.get('summary') or '')
        text = re.sub(r'<[^>]+>', '', raw)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) < 2000:
            continue
        # 摘要和音频状态
        summary_row = db.execute(
            "SELECT summary, audio_status, audio_path FROM article_summaries WHERE item_id=?",
            (item_id,)
        ).fetchone()
        item['summary'] = summary_row[0] if summary_row else None
        item['audio_status'] = summary_row[1] if summary_row else None
        item['has_audio'] = bool(summary_row and summary_row[1] == 'ready' and summary_row[2]) if summary_row else False
        # 提取文章首图作为封面
        content_html = item.get('content') or ''
        img_match = re.search(r'<img[^>]+src=["\']([^"\'>]+)["\']', content_html)
        item['cover_url'] = img_match.group(1) if img_match else None
        items.append(item)

    # 限制最大数量
    if max_items > 0:
        items = items[:max_items]

    return jsonify({"items": items})


@app.route("/api/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    db = get_db()
    user_id = _current_user_id()
    if user_id:
        item = db.execute(
            """SELECT i.id, i.feed_id, i.guid, i.title, i.link, i.author, i.content, i.summary, i.published, i.fetched_at, i.created_on_time,
                      COALESCE(f.custom_title, f.title) AS feed_title,
                      COALESCE(ui.is_read, i.is_read) AS is_read,
                      COALESCE(ui.is_starred, i.is_starred) AS is_starred,
                      COALESCE(ui.note, i.note) AS note
               FROM items i
               JOIN feeds f ON f.id=i.feed_id
               LEFT JOIN user_items ui ON ui.user_id=? AND ui.item_id=i.id
               WHERE i.id=?""",
            (user_id, item_id)
        ).fetchone()
    else:
        item = db.execute(
            "SELECT i.*, COALESCE(f.custom_title, f.title) AS feed_title FROM items i JOIN feeds f ON f.id=i.feed_id WHERE i.id=?",
            (item_id,)
        ).fetchone()
    if not item:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(item))


@app.route("/api/items/<int:item_id>/read", methods=["PUT"])
def mark_item_read(item_id):
    """标记文章已读/未读"""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401
    is_read = request.json.get("is_read", 1)
    db = get_db()
    _ensure_user_item(db, user_id, item_id)
    db.execute("UPDATE user_items SET is_read=? WHERE user_id=? AND item_id=?", (is_read, user_id, item_id))
    db.commit()
    return jsonify({"ok": True, "is_read": is_read})


@app.route("/api/items/<int:item_id>/note", methods=["PUT"])
def set_item_note(item_id):
    """设置文章笔记"""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401
    note = request.json.get("note", "")
    # 笔记清空时存 NULL 而非空字符串或空数组 "[]"
    if not note or note in ("", "[]"):
        note = None
    db = get_db()
    _ensure_user_item(db, user_id, item_id)
    db.execute("UPDATE user_items SET note=? WHERE user_id=? AND item_id=?", (note, user_id, item_id))
    db.commit()
    return jsonify({"ok": True, "note": note or ""})


@app.route("/api/items/notes", methods=["GET"])
def list_notes():
    """返回所有写过笔记的文章，支持分页"""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401
    db = get_db()
    limit  = min(request.args.get("limit", 50, type=int), 200)
    offset = request.args.get("offset", 0, type=int)
    items = db.execute("""
        SELECT i.id, i.title, i.link, i.author, i.content, i.summary, i.published, i.created_on_time, i.feed_id,
                   COALESCE(f.custom_title, f.title) AS feed_title, f.icon_url AS feed_icon, f.site_url AS feed_site_url, f.url AS feed_url,
                   ui.is_read, ui.is_starred, ui.note
        FROM user_items ui
        JOIN items i ON i.id=ui.item_id
        JOIN feeds f ON f.id=i.feed_id
        WHERE ui.user_id=? AND ui.note IS NOT NULL AND ui.note != '' AND ui.note != '[]'
        ORDER BY i.created_on_time DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    total = db.execute(
        "SELECT COUNT(*) FROM user_items WHERE user_id=? AND note IS NOT NULL AND note != ''",
        (user_id,)
    ).fetchone()[0]
    return jsonify({"items": [dict(r) for r in items], "total": total})


@app.route("/api/items/<int:item_id>/star",  methods=["PUT"])
def mark_item_star(item_id):
    """收藏/取消收藏文章。收藏时自动触发后台 TTS 合成。"""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401
    is_starred = request.json.get("is_starred", 1)
    db = get_db()
    _ensure_user_item(db, user_id, item_id)
    db.execute("UPDATE user_items SET is_starred=? WHERE user_id=? AND item_id=?", (is_starred, user_id, item_id))

    if is_starred == 1:
        # 收藏时：触发后台任务，确保摘要存在并合成 TTS
        # 注意：必须传递 app 实例并在子线程中创建 app context
        import threading
        def _bg_task(item_id):
            import logging
            logger = logging.getLogger()
            try:
                with app.app_context():
                    _ensure_summary_and_tts(item_id)
            except Exception:
                logger.exception(f"[BG] Unhandled error in background TTS task for item {item_id}")
        t = threading.Thread(target=_bg_task, args=(item_id,), daemon=True)
        t.start()

    db.commit()
    return jsonify({"ok": True, "is_starred": is_starred})


@app.route("/api/feeds/<int:feed_id>/read-all", methods=["PUT"])
def mark_feed_read(feed_id):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401
    db = get_db()
    # 为该 feed 下所有文章确保 user_items 记录存在，然后标记已读
    item_ids = db.execute("SELECT id FROM items WHERE feed_id=?", (feed_id,)).fetchall()
    for row in item_ids:
        _ensure_user_item(db, user_id, row[0])
    db.execute("UPDATE user_items SET is_read=1 WHERE user_id=? AND item_id IN (SELECT id FROM items WHERE feed_id=?)", (user_id, feed_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/items/starred", methods=["GET"])
def list_starred():
    """返回所有收藏文章，支持分页"""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401
    db = get_db()
    limit  = min(request.args.get("limit", 50, type=int), 200)
    offset = request.args.get("offset", 0, type=int)
    items = db.execute("""
        SELECT i.id, i.title, i.link, i.author, i.content, i.summary, i.published, i.created_on_time, i.feed_id,
                   COALESCE(f.custom_title, f.title) AS feed_title, f.icon_url AS feed_icon, f.site_url AS feed_site_url, f.url AS feed_url,
                   ui.is_read, ui.is_starred, ui.note,
                   s.audio_status, s.audio_path
        FROM user_items ui
        JOIN items i ON i.id=ui.item_id
        JOIN feeds f ON f.id=i.feed_id
        LEFT JOIN article_summaries s ON s.item_id=i.id
        WHERE ui.user_id=? AND ui.is_starred=1
        ORDER BY i.created_on_time DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    return jsonify({"items": [dict(r) for r in items], "limit": limit, "offset": offset})


@app.route("/api/stats", methods=["GET"])
def stats():
    user_id = _current_user_id()
    db = get_db()
    r = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM feeds)           AS feed_count,
            (SELECT COUNT(*) FROM items)            AS item_count,
            COALESCE((SELECT COUNT(*) FROM user_items WHERE user_id=? AND is_read=0), (SELECT COUNT(*) FROM items WHERE is_read=0))  AS unread_count,
            COALESCE((SELECT COUNT(*) FROM user_items WHERE user_id=? AND is_starred=1), (SELECT COUNT(*) FROM items WHERE is_starred=1)) AS starred_count
    """, (user_id, user_id)).fetchone()
    return jsonify(dict(r))


# ── 用户管理 API ───────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    """注册用户"""
    db = get_db()
    # 从 site_settings 读取是否开放注册
    setting = db.execute("SELECT value FROM site_settings WHERE key='allow_registration'").fetchone()
    if not setting or setting[0] != '1':
        return jsonify({"error": "暂停注册，请联系管理员"}), 403
    username = request.json.get("username", "").strip().lower()
    password = request.json.get("password", "")
    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码至少 4 位"}), 400

    # 生成密码 MD5 和 API Key
    password_md5 = hashlib.md5(password.encode()).hexdigest()
    api_key = hashlib.md5(f"{username}:{password}".encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, password_md5, api_key, created_at) VALUES (?, ?, ?, ?)",
            (username, password_md5, api_key, now)
        )
        db.commit()
        return jsonify({"ok": True, "api_key": api_key}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "用户名已存在"}), 409


@app.route("/api/auth/login", methods=["POST"])
def login():
    """登录验证"""
    username = request.json.get("username", "").strip().lower()
    password = request.json.get("password", "")
    password_md5 = hashlib.md5(password.encode()).hexdigest()

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE username=? AND password_md5=?",
        (username, password_md5)
    ).fetchone()
    if not user:
        return jsonify({"error": "用户名或密码错误"}), 401
<<<<<<< HEAD
    is_admin = bool(_is_admin(db, user["id"]))
    return jsonify({"ok": True, "api_key": user["api_key"], "username": user["username"],
                         "display_name": user["display_name"] or user["username"],
                         "avatar": user["avatar"] or "",
                         "is_admin": is_admin})
=======
    return jsonify({"ok": True, "api_key": user["api_key"], "username": user["username"],
                         "display_name": user["display_name"] or user["username"],
                         "avatar": user["avatar"] or ""})
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6




@app.route("/api/user/profile", methods=["PUT"])
def update_profile():
    """更新头像和显示名"""
    username = request.json.get("username", "")
    api_key = request.headers.get("X-API-Key", "") or request.json.get("api_key", "")
    if not username or not api_key:
        return jsonify({"error": "缺少参数"}), 400

    db = get_db()
    user = db.execute("SELECT id FROM users WHERE username=? AND api_key=?", (username, api_key)).fetchone()
    if not user:
        return jsonify({"error": "认证失败"}), 401

    display_name = request.json.get("display_name", "").strip()
    avatar = request.json.get("avatar", "").strip()
    display_name = display_name if display_name else username

    db.execute("UPDATE users SET display_name=?, avatar=? WHERE id=?", (display_name, avatar, user[0]))
    db.commit()
    return jsonify({"ok": True, "display_name": display_name, "avatar": avatar})


@app.route("/api/user/settings", methods=["PUT"])
def update_user_settings():
    """更新用户偏好设置"""
    username = request.json.get("username", "")
    api_key = request.headers.get("X-API-Key", "") or request.json.get("api_key", "")
    if not username or not api_key:
        return jsonify({"error": "缺少参数"}), 400

    db = get_db()
    user = db.execute("SELECT id FROM users WHERE username=? AND api_key=?", (username, api_key)).fetchone()
    if not user:
        return jsonify({"error": "认证失败"}), 401

    default_view = request.json.get("default_view", "list")
    if default_view not in ("list", "grid"):
        return jsonify({"error": "无效视图"}), 400

    db.execute("UPDATE users SET default_view=? WHERE id=?", (default_view, user[0]))
    db.commit()
    return jsonify({"ok": True, "default_view": default_view})


@app.route("/api/settings/registration", methods=["GET"])
def get_registration_setting():
    """获取是否开放注册状态"""
    db = get_db()
    row = db.execute("SELECT value FROM site_settings WHERE key='allow_registration'").fetchone()
    allow = row[0] == '1' if row else False
    return jsonify({"allow": allow})

@app.route("/api/settings/registration", methods=["PUT"])
def update_registration_setting():
<<<<<<< HEAD
    """修改注册开放状态（需要管理员权限）"""
=======
    """修改注册开放状态（仅限 zhengtu 账号）"""
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401

    db = get_db()
    user = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
<<<<<<< HEAD
    if not _is_admin(db, user_id):
=======
    if not user or user["username"] != "zhengtu":
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6
        return jsonify({"error": "无权限，仅管理员可修改"}), 403

    allow = request.json.get("allow", False)
    new_value = '1' if allow else '0'
    db.execute("UPDATE site_settings SET value=? WHERE key='allow_registration'", (new_value,))
    db.commit()
    return jsonify({"ok": True, "allow": allow})


# ── AI设置 API ─────────────────────────────────────────────────────

@app.route("/api/settings/ai", methods=["GET"])
def get_ai_settings():
    """获取AI总结设置"""
    db = get_db()
    keys = ['ai_enabled', 'ai_api_url', 'ai_api_key', 'ai_model', 'ai_min_length', 'ai_provider', 'ai_providers_json']
    settings = {}
    for key in keys:
        row = db.execute("SELECT value FROM site_settings WHERE key=?", (key,)).fetchone()
        settings[key] = row[0] if row else ''
    # 不返回完整的API key，只返回掩码版本
    if settings.get('ai_api_key'):
        key = settings['ai_api_key']
        if len(key) > 8:
            settings['ai_api_key_masked'] = key[:4] + '****' + key[-4:]
        else:
            settings['ai_api_key_masked'] = '****'
    else:
        settings['ai_api_key_masked'] = ''
    return jsonify(settings)


@app.route("/api/settings/ai", methods=["PUT"])
def update_ai_settings():
<<<<<<< HEAD
    """修改AI总结设置（需要管理员权限）"""
=======
    """修改AI总结设置（仅限 zhengtu 账号）"""
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401

    db = get_db()
    user = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
<<<<<<< HEAD
    if not _is_admin(db, user_id):
=======
    if not user or user["username"] != "zhengtu":
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6
        return jsonify({"error": "无权限，仅管理员可修改"}), 403

    data = request.json or {}
    
    # 更新各个设置项
    if 'ai_enabled' in data:
        db.execute("UPDATE site_settings SET value=? WHERE key='ai_enabled'", 
                   ('1' if data['ai_enabled'] else '0',))
    if 'ai_min_length' in data:
        db.execute("UPDATE site_settings SET value=? WHERE key='ai_min_length'", 
                   (str(data['ai_min_length']),))
    
    # 处理厂商相关配置
    provider = data.get('ai_provider', 'aliyun')
    db.execute("UPDATE site_settings SET value=? WHERE key='ai_provider'", (provider,))
    
    # 保存当前厂商的配置到 ai_providers_json
    import json
    providers_json = db.execute("SELECT value FROM site_settings WHERE key='ai_providers_json'").fetchone()
    providers = json.loads(providers_json[0] if providers_json and providers_json[0] else '{}')
    
    # 更新当前厂商的配置
    providers[provider] = {
        'api_url': data.get('ai_api_url', ''),
        'api_key': data.get('ai_api_key', ''),
        'model': data.get('ai_model', ''),
    }
    db.execute("UPDATE site_settings SET value=? WHERE key='ai_providers_json'", (json.dumps(providers),))
    
    # 同时更新旧字段保持兼容（当前激活的厂商配置）
    if 'ai_api_url' in data:
        db.execute("UPDATE site_settings SET value=? WHERE key='ai_api_url'", (data['ai_api_url'],))
    if 'ai_api_key' in data and not data['ai_api_key'].startswith('****'):
        db.execute("UPDATE site_settings SET value=? WHERE key='ai_api_key'", (data['ai_api_key'],))
    if 'ai_model' in data:
        db.execute("UPDATE site_settings SET value=? WHERE key='ai_model'", (data['ai_model'],))
    
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/settings/ai/test", methods=["POST"])
def test_ai_connection():
    """测试AI连接是否正常"""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401

    db = get_db()
    user = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
<<<<<<< HEAD
    if not _is_admin(db, user_id):
=======
    if not user or user["username"] != "zhengtu":
>>>>>>> 36e641ef38fda4836716bbe5e7b1c0a137a509b6
        return jsonify({"error": "无权限，仅管理员可测试"}), 403

    # 获取当前设置
    api_url_row = db.execute("SELECT value FROM site_settings WHERE key='ai_api_url'").fetchone()
    api_key_row = db.execute("SELECT value FROM site_settings WHERE key='ai_api_key'").fetchone()
    model_row = db.execute("SELECT value FROM site_settings WHERE key='ai_model'").fetchone()
    
    api_url = api_url_row[0] if api_url_row else ''
    api_key = api_key_row[0] if api_key_row else ''
    model = model_row[0] if model_row else ''
    
    # 如果请求中提供了新的设置，使用新设置测试
    data = request.json or {}
    if data.get('api_url'):
        api_url = data['api_url']
    if data.get('api_key'):
        api_key = data['api_key']
    if data.get('model'):
        model = data['model']
    
    if not api_url or not api_key or not model:
        return jsonify({"ok": False, "error": "请填写完整的API设置"}), 400
    
    # 发送测试请求
    try:
        test_response = requests.post(
            api_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": "Hello, this is a test. Please respond with 'OK'."}
                ],
                "max_tokens": 10
            },
            timeout=30
        )
        
        if test_response.status_code == 200:
            return jsonify({"ok": True, "message": "连接成功"})
        else:
            return jsonify({"ok": False, "error": f"API返回错误: {test_response.status_code}"}), 400
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "连接超时"}), 400
    except requests.exceptions.ConnectionError:
        return jsonify({"ok": False, "error": "无法连接到API地址"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/ai/summarize/<int:item_id>", methods=["POST"])
def summarize_article(item_id):
    """生成文章摘要"""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401

    db = get_db()
    
    # 检查AI功能是否开启
    ai_enabled_row = db.execute("SELECT value FROM site_settings WHERE key='ai_enabled'").fetchone()
    if not ai_enabled_row or ai_enabled_row[0] != '1':
        return jsonify({"error": "AI功能未开启"}), 403
    
    # 获取文章内容
    item = db.execute("SELECT id, title, content, summary FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return jsonify({"error": "文章不存在"}), 404
    
    # 检查是否有缓存的摘要
    cached = db.execute("SELECT summary FROM article_summaries WHERE item_id=?", (item_id,)).fetchone()
    if cached:
        return jsonify({"ok": True, "summary": cached[0], "cached": True})
    
    # 获取AI设置
    api_url_row = db.execute("SELECT value FROM site_settings WHERE key='ai_api_url'").fetchone()
    api_key_row = db.execute("SELECT value FROM site_settings WHERE key='ai_api_key'").fetchone()
    model_row = db.execute("SELECT value FROM site_settings WHERE key='ai_model'").fetchone()
    min_length_row = db.execute("SELECT value FROM site_settings WHERE key='ai_min_length'").fetchone()
    
    api_url = api_url_row[0] if api_url_row else ''
    api_key = api_key_row[0] if api_key_row else ''
    model = model_row[0] if model_row else ''
    min_length = int(min_length_row[0]) if min_length_row and min_length_row[0].isdigit() else 500
    
    if not api_url or not api_key or not model:
        return jsonify({"error": "AI设置不完整"}), 400
    
    # 提取纯文本内容
    content = item['content'] or item['summary'] or ''
    # 简单的HTML标签去除
    import re
    text_content = re.sub(r'<[^>]+>', '', content)
    text_content = re.sub(r'\s+', ' ', text_content).strip()
    
    # 检查字数
    if len(text_content) < min_length:
        return jsonify({"error": f"文章字数不足{min_length}字，无需总结"}), 400
    
    # 构建总结提示词
    prompt = f"""请对以下文章进行总结，要求：
1. 用简体中文
2. 总结约400字左右的一段话，不要换行，不要带markdown
3. 提取核心观点和关键信息
4. 保持客观中立
5. 引号要使用中文直角引号「」

文章标题：{item['title'] or '无标题'}

文章内容：
{text_content[:8000]}

请生成总结："""
    
    # 调用AI API
    try:
        response = requests.post(
            api_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 800,
                "temperature": 0.7
            },
            timeout=60
        )
        
        if response.status_code != 200:
            return jsonify({"error": f"API调用失败: {response.status_code}"}), 500
        
        result = response.json()
        # 兼容OpenAI格式的响应
        if 'choices' in result and len(result['choices']) > 0:
            summary = result['choices'][0]['message']['content']
        elif 'response' in result:
            summary = result['response']
        else:
            return jsonify({"error": "无法解析API响应"}), 500
        
        # 缓存摘要
        now = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT OR REPLACE INTO article_summaries (item_id, summary, created_at) VALUES (?, ?, ?)",
                   (item_id, summary, now))
        db.commit()
        
        # 摘要生成完成后，后台自动合成TTS音频
        import threading
        def _bg_tts(iid, stxt):
            try:
                with app.app_context():
                    _do_tts_for_item(iid, stxt)
            except Exception:
                import logging
                logging.getLogger().exception(f"[BG TTS] Error for item {iid}")
        t = threading.Thread(target=_bg_tts, args=(item_id, summary), daemon=True)
        t.start()
        
        return jsonify({"ok": True, "summary": summary, "cached": False})
        
    except requests.exceptions.Timeout:
        return jsonify({"error": "API调用超时"}), 500
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "无法连接到API"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


    """更新头像和显示名"""
    username = request.json.get("username", "")
    api_key = request.headers.get("X-API-Key", "") or request.json.get("api_key", "")
    if not username or not api_key:
        return jsonify({"error": "缺少参数"}), 400

    db = get_db()
    user = db.execute("SELECT id FROM users WHERE username=? AND api_key=?", (username, api_key)).fetchone()
    if not user:
        return jsonify({"error": "认证失败"}), 401

    display_name = request.json.get("display_name", "").strip()
    avatar = request.json.get("avatar", "").strip()
    display_name = display_name if display_name else username

    db.execute("UPDATE users SET display_name=?, avatar=? WHERE id=?", (display_name, avatar, user[0]))
    db.commit()
    return jsonify({"ok": True, "display_name": display_name, "avatar": avatar})


@app.route("/api/tts/speak", methods=["GET", "POST"])
def tts_speak():
    """TTS 语音合成（无需登录，RSS 阅读器本身已有登录保护）"""
    import logging
    logging.basicConfig(filename='/tmp/tts_debug.log', level=logging.INFO)
    logger = logging.getLogger()
    try:
        if request.method == "POST":
            data = request.get_json()
            if data is None:
                logger.error("TTS: get_json() returned None")
                return "Invalid JSON", 400
            text = (data.get("text") or "").strip()
        else:
            text = request.args.get("text", "").strip()
        logger.info(f"TTS request text length: {len(text) if text else 0}, text[:50]: {text[:50] if text else 'EMPTY'}")
        if not text:
            return "No text", 400
    except Exception as e:
        logger.error(f"TTS parse error: {e}")
        return str(e), 500

    # 取 TTS 配置（小米 MiMo）
    try:
        db = get_db()
        api_key_row = db.execute("SELECT value FROM site_settings WHERE key='tts_api_key'").fetchone()
        api_url_row = db.execute("SELECT value FROM site_settings WHERE key='tts_api_url'").fetchone()
        logger.info(f"DB query done: key_exists={bool(api_key_row)}, url_exists={bool(api_url_row)}")
        if not api_key_row or not api_key_row[0]:
            logger.error("TTS API Key 未配置")
            return "TTS API Key 未配置，请在设置中填写", 400
    except Exception as e:
        logger.error(f"TTS DB error: {type(e).__name__}: {e}")
        return str(e), 500

    import requests
    import base64
    try:
        # 小米 MiMo TTS 通过 chat/completions 调用，audio 在 message 中
        logger.info(f"Calling MiMo API: url={api_url_row[0]}, key_len={len(api_key_row[0])}, text_len={len(text)}")
        r = requests.post(
            api_url_row[0].rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key_row[0]}",
                "Content-Type": "application/json"
            },
            json={
                "model": "mimo-v2.5-tts",
                "messages": [
                    {"role": "user", "content": "请朗读。"},
                    {"role": "assistant", "content": text}
                ]
            },
            timeout=60  # 增加到60秒
        )
        logger.info(f"MiMo API response: status={r.status_code}, size={len(r.text)}")
        if r.status_code != 200:
            logger.error(f"MiMo API error: {r.text[:200]}")
            return f"TTS error: {r.text[:200]}", 502
        data = r.json()
        audio_b64 = data["choices"][0]["message"]["audio"]["data"]
        logger.info(f"Audio decoded: {len(audio_b64)} base64 chars")
        audio_bytes = base64.b64decode(audio_b64)
        logger.info(f"Audio bytes: {len(audio_bytes)} bytes")
        from flask import make_response
        resp = make_response(audio_bytes)
        resp.headers["Content-Type"] = "audio/wav"
        resp.headers["Content-Disposition"] = "inline; filename=tts.wav"
        return resp
    except Exception as e:
        logger.error(f"TTS API error: {type(e).__name__}: {e}")
        return str(e), 500


# ── 后台 TTS 合成（收藏触发）───────────────────────────────────────────────


def _generate_summary_for_item(item_id):
    """为指定文章生成 AI 摘要（后台用），返回摘要文本或 None"""
    import re
    db = get_db()
    item = db.execute(
        "SELECT id, title, content, summary FROM items WHERE id=?", (item_id,)
    ).fetchone()
    if not item:
        return None
    content = item["content"] or item["summary"] or ""
    text_content = re.sub(r'<[^>]+>', '', content)
    text_content = re.sub(r'\s+', ' ', text_content).strip()
    if len(text_content) < 500:
        return None
    prompt = f"""请对以下文章进行总结，要求：
1. 用简体中文
2. 总结约400字左右的一段话，不要换行，不要带markdown
3. 提取核心观点和关键信息
4. 保持客观中立
5. 引号要使用中文直角引号「」

文章标题：{item['title'] or '无标题'}

文章内容：
{text_content[:8000]}

请生成总结："""
    try:
        api_url_row = db.execute("SELECT value FROM site_settings WHERE key='ai_api_url'").fetchone()
        api_key_row = db.execute("SELECT value FROM site_settings WHERE key='ai_api_key'").fetchone()
        model_row   = db.execute("SELECT value FROM site_settings WHERE key='ai_model'").fetchone()
        if not api_url_row or not api_key_row or not model_row:
            return None
        api_url = api_url_row[0]
        api_key = api_key_row[0]
        model   = model_row[0]
        import requests
        r = requests.post(api_url, headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                          json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 800, "temperature": 0.7},
                          timeout=60)
        if r.status_code != 200:
            return None
        result = r.json()
        summary = None
        if "choices" in result and len(result["choices"]) > 0:
            summary = result["choices"][0]["message"]["content"]
        elif "response" in result:
            summary = result["response"]
        if summary:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT OR REPLACE INTO article_summaries (item_id, summary, created_at) VALUES (?, ?, ?)",
                       (item_id, summary, now))
            db.commit()
        return summary
    except Exception as e:
        import logging
        logging.getLogger().error(f"Summary generation failed for item {item_id}: {e}")
        return None


def _do_tts_for_item(item_id, summary_text=None):
    """调用 MiMo TTS 合成音频，保存到本地文件"""
    import logging
    import requests
    import base64
    import os
    logger = logging.getLogger()
    db = get_db()

    if summary_text is None:
        row = db.execute("SELECT summary FROM article_summaries WHERE item_id=?", (item_id,)).fetchone()
        if not row or not row[0]:
            logger.error(f"[TTS] No summary for item {item_id}")
            db.execute("UPDATE article_summaries SET audio_status='error' WHERE item_id=?", (item_id,))
            db.commit()
            return
        summary_text = row[0]

    api_key_row = db.execute("SELECT value FROM site_settings WHERE key='tts_api_key'").fetchone()
    api_url_row = db.execute("SELECT value FROM site_settings WHERE key='tts_api_url'").fetchone()
    if not api_key_row or not api_key_row[0] or not api_url_row or not api_url_row[0]:
        logger.error(f"[TTS] API not configured for item {item_id}")
        db.execute("UPDATE article_summaries SET audio_status='error' WHERE item_id=?", (item_id,))
        db.commit()
        return

    try:
        r = requests.post(
            api_url_row[0].rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key_row[0]}", "Content-Type": "application/json"},
            json={"model": "mimo-v2.5-tts",
                  "messages": [{"role": "user", "content": "请朗读。"}, {"role": "assistant", "content": summary_text}]},
            timeout=60
        )
        if r.status_code != 200:
            logger.error(f"[TTS] API error for item {item_id}: {r.text[:200]}")
            db.execute("UPDATE article_summaries SET audio_status='error' WHERE item_id=?", (item_id,))
            db.commit()
            return
        data = r.json()
        audio_b64 = data["choices"][0]["message"]["audio"]["data"]
        audio_bytes = base64.b64decode(audio_b64)

        # 保存到本地文件
        audio_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_cache")
        os.makedirs(audio_dir, exist_ok=True)
        audio_path = os.path.join(audio_dir, f"{item_id}.wav")
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        db.execute("UPDATE article_summaries SET audio_status='ready', audio_path=? WHERE item_id=?", (audio_path, item_id))
        db.commit()
        logger.info(f"[TTS] Success for item {item_id}: {len(audio_bytes)} bytes")
    except Exception as e:
        logger.error(f"[TTS] Failed for item {item_id}: {e}")
        db.execute("UPDATE article_summaries SET audio_status='error' WHERE item_id=?", (item_id,))
        db.commit()


def _ensure_summary_and_tts(item_id):
    """后台任务：确保摘要存在，然后合成 TTS。如果已有 ready 音频则跳过。"""
    import logging
    logger = logging.getLogger()
    logger.info(f"[BG] Starting background task for item {item_id}")
    db = get_db()
    # 快速检查：如果已有 ready 音频且文件存在，直接跳过
    existing = db.execute("SELECT audio_status, audio_path FROM article_summaries WHERE item_id=?", (item_id,)).fetchone()
    if existing and existing[0] == 'ready' and existing[1] and os.path.exists(existing[1]):
        logger.info(f"[BG] Item {item_id} already has ready audio, skipping TTS")
        return
    row = db.execute("SELECT summary FROM article_summaries WHERE item_id=?", (item_id,)).fetchone()
    if not row or not row[0]:
        summary_text = _generate_summary_for_item(item_id)
        if not summary_text:
            logger.error(f"[BG] Could not generate summary for item {item_id}")
            db.execute("INSERT OR REPLACE INTO article_summaries (item_id, summary, audio_status, created_at)\n                             VALUES (?, '', 'error', datetime('now'))", (item_id,))
            db.commit()
            return
    else:
        summary_text = row[0]
    # 先标记 pending，让前端显示加载状态
    db.execute("UPDATE article_summaries SET audio_status='pending' WHERE item_id=?", (item_id,))
    db.commit()
    _do_tts_for_item(item_id, summary_text)


@app.route("/api/items/<int:item_id>/audio", methods=["GET"])
def serve_item_audio(item_id):
    """返回已缓存的音频文件（无需登录，RSS 阅读器本身已有登录保护）"""
    db = get_db()
    row = db.execute("SELECT audio_status, audio_path FROM article_summaries WHERE item_id=?", (item_id,)).fetchone()
    if not row or not row[0]:
        return "No audio", 404
    status, audio_path = row[0], row[1]
    if status != "ready" or not audio_path or not os.path.exists(audio_path):
        return f"Audio not ready (status={status})", 404
    from flask import make_response
    with open(audio_path, "rb") as f:
        data = f.read()
    resp = make_response(data)
    resp.headers["Content-Type"] = "audio/wav"
    resp.headers["Content-Disposition"] = f"inline; filename=audio_{item_id}.wav"
    return resp


@app.route("/api/items/<int:item_id>/tts", methods=["POST"])
def trigger_tts(item_id):
    """手动触发TTS合成（前端无缓存时调用），后台执行并保存音频文件"""
    import threading
    import logging
    logger = logging.getLogger()

    # 检查是否已有缓存
    db = get_db()
    row = db.execute("SELECT audio_status, audio_path FROM article_summaries WHERE item_id=?", (item_id,)).fetchone()
    if row and row[0] == 'ready' and row[1] and os.path.exists(row[1]):
        return jsonify({"ok": True, "status": "ready"})

    # 获取摘要文本
    summary_row = db.execute("SELECT summary FROM article_summaries WHERE item_id=?", (item_id,)).fetchone()
    if not summary_row or not summary_row[0]:
        return jsonify({"ok": False, "error": "无摘要，请先生成AI摘要"}), 400

    # 标记pending
    if not row:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT OR REPLACE INTO article_summaries (item_id, summary, audio_status, created_at) VALUES (?, ?, 'pending', ?)",
                   (item_id, summary_row[0], now))
    else:
        db.execute("UPDATE article_summaries SET audio_status='pending' WHERE item_id=?", (item_id,))
    db.commit()

    # 后台合成
    def _bg(iid):
        try:
            with app.app_context():
                _do_tts_for_item(iid)
        except Exception:
            logger.exception(f"[TTS] Error for item {iid}")
    threading.Thread(target=_bg, args=(item_id,), daemon=True).start()

    return jsonify({"ok": True, "status": "pending"})


@app.route("/api/ai/summary-status/<int:item_id>", methods=["GET"])
def get_summary_status(item_id):
    """检查文章是否有缓存的AI摘要"""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "未登录"}), 401
    
    db = get_db()
    cached = db.execute("SELECT summary FROM article_summaries WHERE item_id=?", (item_id,)).fetchone()
    
    if cached:
        return jsonify({"ok": True, "has_summary": True, "summary": cached[0]})
    else:
        return jsonify({"ok": True, "has_summary": False})


@app.route("/api/user/me", methods=["GET"])
def get_me():
    """获取当前用户信息"""
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        return jsonify({"error": "未登录"}), 401

    db = get_db()
    user = db.execute("SELECT id, username, display_name, avatar, created_at, COALESCE(default_view,'list'), COALESCE(open_registration,0) FROM users WHERE api_key=?", (api_key,)).fetchone()
    if not user:
        return jsonify({"error": "用户不存在"}), 404

    return jsonify({
        "id": user[0], "username": user[1],
        "display_name": user[2] or user[1],
        "avatar": user[3] or "",
        "created_at": user[4],
        "default_view": user[5] if len(user) > 5 else "list",
        "open_registration": bool(user[6] if len(user) > 6 else 0)
    })

@app.route("/api/auth/change-password", methods=["POST"])
def change_password():
    """修改密码"""
    username = request.json.get("username", "").strip().lower()
    old_password = request.json.get("old_password", "")
    new_password = request.json.get("new_password", "")
    if not all([username, old_password, new_password]):
        return jsonify({"error": "参数不完整"}), 400

    old_md5 = hashlib.md5(old_password.encode()).hexdigest()
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE username=? AND password_md5=?",
        (username, old_md5)
    ).fetchone()
    if not user:
        return jsonify({"error": "原密码错误"}), 401

    new_md5 = hashlib.md5(new_password.encode()).hexdigest()
    new_api_key = hashlib.md5(f"{username}:{new_password}".encode()).hexdigest()
    db.execute(
        "UPDATE users SET password_md5=?, api_key=? WHERE id=?",
        (new_md5, new_api_key, user["id"])
    )
    db.commit()
    return jsonify({"ok": True, "api_key": new_api_key})



@app.route("/api/user/avatar", methods=["POST"])
def upload_avatar():
    """上传头像"""
    if "avatar" not in request.files:
        return jsonify({"error": "没有文件"}), 400
    file = request.files["avatar"]
    if file.filename == "":
        return jsonify({"error": "没有选择文件"}), 400
    
    username = request.form.get("username", "").strip().lower()
    api_key = request.form.get("api_key", "")
    
    # Verify auth
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=? AND api_key=?", (username, api_key)).fetchone()
    if not user:
        return jsonify({"error": "认证失败"}), 401
    
    # Save file
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return jsonify({"error": "不支持的图片格式"}), 400
    
    filename = secure_filename(f"{username}{ext}")
    filepath = os.path.join(app.root_path, "static", "avatars", filename)
    file.save(filepath)
    
    avatar_url = f"/static/avatars/{filename}"
    db.execute("UPDATE users SET avatar=? WHERE id=?", (avatar_url, user["id"]))
    db.commit()
    
    return jsonify({"ok": True, "avatar": avatar_url})

# ── Fever API ────────────────────────────────────────────────────
# ── 繁简转换 API ─────────────────────────────────────────────────────

@app.route("/api/translate", methods=["POST"])
def api_translate():
    """繁<→简转换 API（不存库，按需转换）"""
    data = request.get_json()
    text = data.get("text", "")
    direction = data.get("direction", "t2s")  # t2s 或 s2t
    if not text:
        return jsonify({"result": ""})
    target = "zh-cn" if direction == "t2s" else "zh-hant"
    return jsonify({"result": zhconv.convert(text, target)})


# ── OPML 导入导出 ────────────────────────────────────────────────────

@app.route("/api/opml/export", methods=["GET"])
def opml_export():
    """导出 OPML 2.0 文件，涵盖所有订阅源与分组"""
    db = get_db()
    groups  = [_row(r) for r in db.execute("SELECT * FROM groups ORDER BY sort_order, id").fetchall()]
    feeds   = [_row(r) for r in db.execute("SELECT * FROM feeds ORDER BY title, url").fetchall()]

    def escape_xml(s):
        if not s:
            return ""
        return (str(s)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))

    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        '  <head>',
        '    <title>階上悦读 — 订阅导出</title>',
        '    <dateCreated>' + now + '</dateCreated>',
        '    <dateModified>' + now + '</dateModified>',
        '  </head>',
        '  <body>',
    ]

    # 按分组输出 outline
    for group in groups:
        group_feeds = [f for f in feeds if f["group_id"] == group["id"]]
        if not group_feeds:
            continue
        lines.append(f'    <outline text="{escape_xml(group["name"])}" title="{escape_xml(group["name"])}">')
        for f in group_feeds:
            title = f["title"] or f["url"]
            lines.append(
                f'      <outline type="rss" text="{escape_xml(title)}" '
                f'title="{escape_xml(title)}" '
                f'xmlUrl="{escape_xml(f["url"])}" '
                f'htmlUrl="{escape_xml(f.get("site_url") or "")}"/>'
            )
        lines.append('    </outline>')

    # 未分组 feed
    ungrouped = [f for f in feeds if not f["group_id"]]
    for f in ungrouped:
        title = f["title"] or f["url"]
        lines.append(
            f'    <outline type="rss" text="{escape_xml(title)}" '
            f'title="{escape_xml(title)}" '
            f'xmlUrl="{escape_xml(f["url"])}" '
            f'htmlUrl="{escape_xml(f.get("site_url") or "")}"/>'
        )

    lines += ['  </body>', '</opml>']

    response = app.make_response('\n'.join(lines))
    response.headers['Content-Type']        = 'application/xml; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=upstairs-rss.opml'
    return response


@app.route("/api/opml/import", methods=["POST"])
def opml_import():
    """解析 OPML 并批量导入订阅源（按需创建分组）"""
    xml_data = None

    # 支持 raw XML body 或 multipart file
    if request.content_type and 'application/xml' in request.content_type:
        xml_data = request.get_data(as_text=True)
    elif 'file' in request.files:
        xml_data = request.files['file'].read().decode('utf-8', errors='replace')
    else:
        data = request.get_json(silent=True)
        if data and 'xml' in data:
            xml_data = data['xml']

    if not xml_data:
        return jsonify({"error": "No OPML data provided"}), 400

    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        return jsonify({"error": f"XML 解析失败: {e}"}), 400

    db = get_db()
    imported = 0
    skipped  = 0
    errors   = []

    def ensure_group(name):
        row = db.execute("SELECT id FROM groups WHERE name=?", (name,)).fetchone()
        if row:
            return row[0]
        now = datetime.now(timezone.utc).isoformat()
        max_order = db.execute("SELECT MAX(sort_order) FROM groups").fetchone()[0] or 0
        cur = db.execute("INSERT INTO groups (name, sort_order, created_at) VALUES (?, ?, ?)",
                         (name, max_order + 1, now))
        db.commit()
        return cur.lastrowid

    def parse_outlines(outlines, group_name=None):
        nonlocal imported, skipped, errors
        for outline in outlines:
            # 嵌套的 outline → 分组
            children = list(outline)
            if children:  # 含有子节点，视作分组
                node_text  = outline.get('text') or outline.get('title') or ''
                node_title = outline.get('title') or ''
                grp = ensure_group(node_text or node_title)
                parse_outlines(children, grp)
                continue

            # 单个 feed outline
            xml_url  = outline.get('xmlUrl') or outline.get('xmlurl')
            if not xml_url:
                continue

            existing = db.execute("SELECT id FROM feeds WHERE url=?", (xml_url,)).fetchone()
            if existing:
                skipped += 1
                continue

            text    = outline.get('text') or outline.get('title') or xml_url
            html_url = outline.get('htmlUrl') or outline.get('htmlurl') or ''
            gid     = group_name  # int or None

            try:
                data = fetch_feed(xml_url)
            except Exception as e:
                errors.append(f"抓取失败 {xml_url}: {e}")
                data = {
                    "title":       text,
                    "description": "",
                    "icon_url":    "",
                    "site_url":    html_url,
                    "entries":     [],
                }

            now   = datetime.now(timezone.utc).isoformat()
            cur   = db.execute(
                "INSERT INTO feeds (url, title, description, icon_url, site_url, group_id, added_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (xml_url, data["title"], data["description"], data["icon_url"],
                 data["site_url"] or html_url, gid, now, now)
            )
            feed_id = cur.lastrowid

            for item in data["entries"]:
                pub_ts = int(_parse_rfc_time(item.get("published", "")).timestamp())
                db.execute("""
                    INSERT OR IGNORE INTO items
                    (feed_id, guid, title, link, author, content, summary, published, fetched_at, created_on_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (feed_id, item["guid"], item["title"], item["link"],
                      item["author"], item["content"], item["summary"],
                      item["published"], now, pub_ts))

            imported += 1

    body = root.find('.//body') or root
    parse_outlines(list(body))
    db.commit()

    return jsonify({
        "ok":       True,
        "imported": imported,
        "skipped":  skipped,
        "errors":   errors,
    })


# Fever API 协议：https://feedafever.com/api

FEVER_SECRET = "fever-secret-key"  # 可改为配置文件


def fever_auth(api_key: str) -> dict:
    """验证 Fever API key，返回用户信息或 None"""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE api_key=?", (api_key,)).fetchone()
    return dict(user) if user else None


def fever_auth_basic() -> dict:
    """验证 HTTP Basic Auth，返回用户信息或 None
    兼容两种格式：
    1. Authorization: Basic base64(username:api_key) — api_key 已是 md5 哈希
    2. Authorization: Basic base64(username:password) — 需计算 md5(username:password)
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return None
    try:
        import base64
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, secret = decoded.split(":", 1)
        db = get_db()
        # 先尝试直接匹配 api_key（API Key 模式）
        user = db.execute("SELECT * FROM users WHERE username=? AND api_key=?",
                          (username, secret)).fetchone()
        if user:
            return dict(user)
        # 再尝试 md5(username:secret) 匹配（原始密码模式）
        computed_key = hashlib.md5(f"{username}:{secret}".encode()).hexdigest()
        user = db.execute("SELECT * FROM users WHERE username=? AND api_key=?",
                          (username, computed_key)).fetchone()
        return dict(user) if user else None
    except Exception:
        return None


@app.route("/fever", methods=["GET", "POST"], strict_slashes=False)
def fever_endpoint():
    """
    Fever API 统一端点
    请求格式：/fever?api_key=xxx[&feeds][&groups][&items][&unread_item_ids]...
    """
    # 支持三种认证方式：
    # 1. URL 参数 api_key
    # 2. POST body: api_key
    # 3. HTTP Basic Auth: Authorization: Basic base64(username:api_key)
    print(f"[FEVER] {request.method} {request.url} args={dict(request.args)} form={dict(request.form)} headers_auth={request.headers.get('Authorization','NONE')[:50]}")
    api_key = request.args.get("api_key") or request.form.get("api_key") or ""
    if api_key:
        user = fever_auth(api_key)
        print(f"[FEVER] api_key auth: key={api_key[:8]}... user={'found' if user else 'NOT FOUND'}")
    else:
        user = fever_auth_basic()
        print(f"[FEVER] basic auth: user={'found' if user else 'NOT FOUND'}")
    if not user:
        print(f"[FEVER] AUTH FAILED")
        return jsonify({"auth": 0})

    result = {"api_version": 3, "auth": 1}

    # 获取最后刷新时间
    db = get_db()
    last_refresh = db.execute("SELECT MAX(updated_at) FROM feeds").fetchone()[0]
    result["last_refresh_on_time"] = int(datetime.fromisoformat(last_refresh).timestamp()) if last_refresh else 0

    # feeds
    # feeds - always include for Reeder compatibility
    feeds = db.execute("SELECT id, title, custom_title, url, icon_url, site_url, updated_at FROM feeds").fetchall()
    result["feeds"] = [{"id": f["id"], "title": f["custom_title"] or f["title"] or f["url"], "url": f["url"], "site_url": f["site_url"] or "", "is_spark": 0, "last_updated_on_time": int(datetime.fromisoformat(f["updated_at"]).timestamp()) if f["updated_at"] else 0} for f in feeds]

    # feeds_groups - Fever API format: one object per group with comma-separated feed_ids
    fg_rows = db.execute(
        "SELECT group_id, GROUP_CONCAT(id) as feed_ids FROM feeds WHERE group_id IS NOT NULL GROUP BY group_id"
    ).fetchall()
    result["feeds_groups"] = [{"group_id": fg["group_id"], "feed_ids": fg["feed_ids"]} for fg in fg_rows]

    # groups - always include for Reeder compatibility
    groups = db.execute("""
        SELECT g.id, g.name,
               COUNT(f.id) as feeds_count
        FROM groups g
        LEFT JOIN feeds f ON f.group_id = g.id
        GROUP BY g.id
        ORDER BY g.sort_order, g.id
    """).fetchall()
    result["groups"] = [{"id": g["id"], "title": g["name"], "feeds_count": g["feeds_count"]} for g in groups]

    # favicons
    if "favicons" in request.args or "favicons" in request.form:
        feeds = db.execute("SELECT id, icon_url FROM feeds").fetchall()
        result["favicons"] = [{"id": f["id"], "data": f["icon_url"] or ""} for f in feeds]

    # items
    if "items" in request.args or "items" in request.form:
        since_id = request.args.get("since_id", request.form.get("since_id", 0), type=int)
        max_id = request.args.get("max_id", request.form.get("max_id", 0), type=int)
        with_ids = request.args.get("with_ids", request.form.get("with_ids", ""))

        sql = "SELECT i.*, COALESCE(ui.is_read, i.is_read) AS user_is_read, COALESCE(ui.is_starred, i.is_starred) AS user_is_starred FROM items i LEFT JOIN user_items ui ON ui.user_id=? AND ui.item_id=i.id WHERE 1=1"
        args = [user["id"]]
        if since_id:
            sql += " AND i.id > ?"
            args.append(since_id)
        if max_id:
            sql += " AND i.id < ?"
            args.append(max_id)
        if with_ids:
            ids = [int(x) for x in with_ids.split(",") if x.strip().isdigit()]
            if ids:
                sql += f" AND i.id IN ({','.join('?'*len(ids))})"
                args.extend(ids)
        sql += " ORDER BY i.id DESC LIMIT 50"

        items = db.execute(sql, args).fetchall()
        result["items"] = [
            {
                "id": item["id"],
                "feed_id": item["feed_id"],
                "title": item["title"],
                "url": item["link"],
                "author": item["author"],
                "html": item["content"],
                "created_on_time": item["created_on_time"] or 0,
                "is_read": item["user_is_read"],
                "is_saved": item["user_is_starred"],
            }
            for item in items
        ]

    # unread_item_ids - only when requested
    if "unread_item_ids" in request.args or "unread_item_ids" in request.form:
        unread_ids = db.execute("SELECT i.id FROM items i LEFT JOIN user_items ui ON ui.user_id=? AND ui.item_id=i.id WHERE COALESCE(ui.is_read, i.is_read)=0", (user["id"],)).fetchall()
        result["unread_item_ids"] = ",".join(str(r[0]) for r in unread_ids)

    # saved_item_ids - only when requested
    if "saved_item_ids" in request.args or "saved_item_ids" in request.form:
        saved_ids = db.execute("SELECT i.id FROM items i LEFT JOIN user_items ui ON ui.user_id=? AND ui.item_id=i.id WHERE COALESCE(ui.is_starred, i.is_starred)=1", (user["id"],)).fetchall()
        result["saved_item_ids"] = ",".join(str(r[0]) for r in saved_ids)

    # 标记操作
    mark = request.args.get("mark", request.form.get("mark"))
    as_what = request.args.get("as", request.form.get("as"))
    item_id = request.args.get("id", request.form.get("id", ""))
    before = request.args.get("before", request.form.get("before", ""))

    if mark and as_what and item_id:
        uid = user["id"]
        if mark == "item":
            iid = int(item_id)
            _ensure_user_item(db, uid, iid)
            if as_what == "read":
                db.execute("UPDATE user_items SET is_read=1 WHERE user_id=? AND item_id=?", (uid, iid))
            elif as_what == "unread":
                db.execute("UPDATE user_items SET is_read=0 WHERE user_id=? AND item_id=?", (uid, iid))
            elif as_what == "saved":
                db.execute("UPDATE user_items SET is_starred=1 WHERE user_id=? AND item_id=?", (uid, iid))
            elif as_what == "unsaved":
                db.execute("UPDATE user_items SET is_starred=0 WHERE user_id=? AND item_id=?", (uid, iid))
            db.commit()
        elif mark == "feed":
            if as_what == "read":
                feed_items = db.execute("SELECT id FROM items WHERE feed_id=?", (int(item_id),)).fetchall()
                for fi in feed_items:
                    _ensure_user_item(db, uid, fi[0])
                db.execute("UPDATE user_items SET is_read=1 WHERE user_id=? AND item_id IN (SELECT id FROM items WHERE feed_id=?)", (uid, int(item_id)))
                db.commit()
        elif mark == "group":
            if as_what == "read":
                group_items = db.execute("SELECT i.id FROM items i JOIN feeds f ON f.id=i.feed_id WHERE f.group_id=?", (int(item_id),)).fetchall()
                for gi in group_items:
                    _ensure_user_item(db, uid, gi[0])
                db.execute("UPDATE user_items SET is_read=1 WHERE user_id=? AND item_id IN (SELECT i.id FROM items i JOIN feeds f ON f.id=i.feed_id WHERE f.group_id=?)", (uid, int(item_id)))
                db.commit()

    return jsonify(result)


# ── 启动 ───────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    # 主线程测试查询
    print("[debug] 主线程测试查询...")
    try:
        db = sqlite3.connect(DB_NAME)
        feeds = db.execute("SELECT COUNT(*) FROM feeds").fetchone()
        db.close()
        print(f"[debug] 主线程查询成功: {feeds[0]} 个订阅源")
    except Exception as e:
        print(f"[debug] 主线程查询失败: {e}")
    # 启动定时刷新守护线程
    refresh_thread = Thread(target=_auto_refresh_all, daemon=True)
    refresh_thread.start()
    print("RSS Reader Demo started at http://localhost:5321")
    print(f"[debug] 刷新线程已启动: {refresh_thread.is_alive()}")
    app.run(host="0.0.0.0", port=5321, debug=False)

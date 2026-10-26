"""CUVAS Newsletter — monthly newsletter system.

One document per month. News is appended below previous news in the
same monthly edition, numbered sequentially in addition order.
At month end the edition is archived and the new month starts at 1.
PDF is generated with ReportLab in CUVAS indigo + academic green
branding (maroon for dates, orange only as a small highlight),
official CUVAS logo on top, Volume / Issue / Month on the cover.
News images rotate through 4 layouts, one per news in order,
cycling back to layout 1 after layout 4.
DB: Postgres when DATABASE_URL is set (Vercel + Neon), else SQLite.
Images are stored in the DB (BYTEA / BLOB), never on the filesystem,
so they survive the same way on both backends.
"""
import base64
import hashlib
import io
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

from flask import (Flask, abort, redirect, render_template, request,
                   Response, session, url_for)

try:
    from cuvas_logo import CUVAS_LOGO_B64
except Exception:
    CUVAS_LOGO_B64 = ""

APP_NAME = "CUVAS Newsletter"
UNI_NAME = "Cholistan University of Veterinary and Animal Sciences, Bahawalpur"
UNI_SHORT = "CUVAS"
INDIGO = "#2e3192"
BLUE = "#18335A"
BLUE2 = "#315D93"
PURPLE = "#7255A7"
GREEN = "#15803d"
GREEN_BRIGHT = "#3fb877"
MAROON = "#8b1e2d"
ORANGE = "#ea580c"
# Kept for any old reference; the live theme now uses GREEN.
GOLD = GREEN
CATEGORIES = ["University News", "Academic Activities", "Research",
              "Events", "Achievements", "Student Activities",
              "Announcements", "Other"]
# Editorial section order for the PDF / preview (cover story excluded
# from its section list only on the cover page; it still appears in
# full inside its section, so no content is lost).
SECTION_ORDER = [
    ("University News", "University Highlights"),
    ("Academic Activities", "Academic Activities"),
    ("Research", "Research"),
    ("Achievements", "Achievements"),
    ("Events", "Events"),
    ("Announcements", "Announcements"),
    ("Student Activities", "Student Activities"),
    ("Other", "Other News"),
]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cuvas-newsletter-dev-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30 MB total per form
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
IS_PG = bool(DATABASE_URL)
# Vercel's filesystem is read-only except /tmp, so a SQLite file in the
# project folder fails there with "unable to open database file" (the
# 500 seen on the first Vercel deploy). On Vercel without Postgres,
# SQLite lives in /tmp as a demo fallback only — it is NOT permanent.
# Permanent storage on Vercel is Postgres via Vercel Storage -> Neon,
# which sets DATABASE_URL (same pattern as MY CUVAS).
if os.environ.get("VERCEL"):
    LOCAL_DB = "/tmp/cuvas_newsletter.db"
else:
    LOCAL_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "cuvas_newsletter.db")
ADMIN_USER = "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@123")
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# ---------------------------------------------------------------- DB
_pg_conn = None


def _sql(q):
    """Convert ? placeholders to %s for Postgres, with the MY CUVAS
    stray-% guard: no literal % may remain in SQL text."""
    if not IS_PG:
        return q
    out = q.replace("?", "%s")
    # Every % in the output must belong to a %s placeholder.
    tmp = out.replace("%s", "")
    assert "%" not in tmp, "stray % in SQL text"
    return out


def get_conn():
    global _pg_conn
    if IS_PG:
        import psycopg
        if _pg_conn is None or _pg_conn.closed:
            _pg_conn = psycopg.connect(DATABASE_URL)
        return _pg_conn
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _rollback(conn):
    # A failed Postgres query leaves the shared connection in an
    # aborted-transaction state; without a rollback every later query
    # on that serverless instance fails too (a likely cause of the
    # "first save errored, then it worked" report).
    try:
        conn.rollback()
    except Exception:
        pass


def q_one(query, params=()):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_sql(query), params)
        row = cur.fetchone()
    except Exception:
        _rollback(conn)
        raise
    if not IS_PG:
        conn.close()
    return row


def q_all(query, params=()):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_sql(query), params)
        rows = cur.fetchall()
    except Exception:
        _rollback(conn)
        raise
    if not IS_PG:
        conn.close()
    return rows


def q_exec(query, params=()):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_sql(query), params)
        conn.commit()
    except Exception:
        _rollback(conn)
        raise
    last = None
    try:
        last = cur.lastrowid
    except Exception:
        pass
    if not IS_PG:
        conn.close()
    return last


def q_insert_returning_id(query, params=()):
    """Insert and return the new row id without a second lookup query.
    Postgres: query must end with RETURNING id. SQLite: lastrowid."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(_sql(query), params)
        if IS_PG:
            new_id = cur.fetchone()[0]
        else:
            new_id = cur.lastrowid
        conn.commit()
    except Exception:
        _rollback(conn)
        raise
    if not IS_PG:
        conn.close()
    return new_id


def _ensure_columns(conn, cur):
    """Additive-only migrations. Existing tables, rows and columns are
    never dropped or altered; new columns are nullable with defaults,
    and old rows are backfilled so old data keeps working."""
    if IS_PG:
        specs = [
            ("news", "category", "TEXT DEFAULT 'University News'"),
            ("news", "summary", "TEXT"),
            ("news", "venue", "TEXT"),
            ("news", "department", "TEXT"),
            ("news", "participants", "TEXT"),
            ("news", "source_credit", "TEXT"),
            ("news", "is_draft", "INTEGER DEFAULT 0"),
            ("news_images", "caption", "TEXT"),
            ("news_images", "is_cover", "INTEGER DEFAULT 0"),
            ("editions", "stage", "TEXT DEFAULT 'Draft'"),
        ]
        for tbl, col, ddl in specs:
            cur.execute(
                "ALTER TABLE %s ADD COLUMN IF NOT EXISTS %s %s"
                % (tbl, col, ddl))
        cur.execute("UPDATE news SET category = 'University News' "
                    "WHERE category IS NULL")
        cur.execute("UPDATE news SET is_draft = 0 WHERE is_draft IS NULL")
        cur.execute("UPDATE news_images SET is_cover = 0 "
                    "WHERE is_cover IS NULL")
        cur.execute("UPDATE news_images SET is_cover = 1 WHERE position = 0 "
                    "AND NOT EXISTS (SELECT 1 FROM news_images n2 "
                    "WHERE n2.news_id = news_images.news_id "
                    "AND n2.is_cover = 1)")
        cur.execute("UPDATE editions SET stage = 'Archived' "
                    "WHERE status = 'archived' AND stage = 'Draft'")
    else:
        cur.execute("PRAGMA table_info(news)")
        news_cols = {r[1] for r in cur.fetchall()}
        cur.execute("PRAGMA table_info(news_images)")
        img_cols = {r[1] for r in cur.fetchall()}
        cur.execute("PRAGMA table_info(editions)")
        ed_cols = {r[1] for r in cur.fetchall()}
        adds = [
            ("news", news_cols, "category", "TEXT DEFAULT 'University News'"),
            ("news", news_cols, "summary", "TEXT"),
            ("news", news_cols, "venue", "TEXT"),
            ("news", news_cols, "department", "TEXT"),
            ("news", news_cols, "participants", "TEXT"),
            ("news", news_cols, "source_credit", "TEXT"),
            ("news", news_cols, "is_draft", "INTEGER DEFAULT 0"),
            ("news_images", img_cols, "caption", "TEXT"),
            ("news_images", img_cols, "is_cover", "INTEGER DEFAULT 0"),
            ("editions", ed_cols, "stage", "TEXT DEFAULT 'Draft'"),
        ]
        for tbl, cols, col, ddl in adds:
            if col not in cols:
                cur.execute("ALTER TABLE %s ADD COLUMN %s %s"
                            % (tbl, col, ddl))
        cur.execute("UPDATE news SET category = 'University News' "
                    "WHERE category IS NULL")
        cur.execute("UPDATE news SET is_draft = 0 WHERE is_draft IS NULL")
        cur.execute("UPDATE news_images SET is_cover = 0 "
                    "WHERE is_cover IS NULL")
        cur.execute("UPDATE news_images SET is_cover = 1 WHERE position = 0 "
                    "AND NOT EXISTS (SELECT 1 FROM news_images n2 "
                    "WHERE n2.news_id = news_images.news_id "
                    "AND n2.is_cover = 1)")
        cur.execute("UPDATE editions SET stage = 'Archived' "
                    "WHERE status = 'archived' AND stage = 'Draft'")


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    if IS_PG:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS editions (
                id SERIAL PRIMARY KEY,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                closed_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id SERIAL PRIMARY KEY,
                edition_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_day TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS news_images (
                id SERIAL PRIMARY KEY,
                news_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                filename TEXT NOT NULL,
                mime TEXT NOT NULL,
                data BYTEA NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS version_log (
                id SERIAL PRIMARY KEY,
                edition_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                action TEXT NOT NULL,
                news_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS edition_cover_images (
                id SERIAL PRIMARY KEY,
                edition_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                filename TEXT NOT NULL,
                mime TEXT NOT NULL,
                data BYTEA NOT NULL
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS editions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                closed_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edition_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_day TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS news_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                filename TEXT NOT NULL,
                mime TEXT NOT NULL,
                data BLOB NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS version_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edition_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                action TEXT NOT NULL,
                news_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS edition_cover_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edition_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                filename TEXT NOT NULL,
                mime TEXT NOT NULL,
                data BLOB NOT NULL
            )
        """)
    _ensure_columns(conn, cur)
    conn.commit()
    if not IS_PG:
        conn.close()


# ---------------------------------------------------------------- dates
def karachi_today():
    tz = timezone(timedelta(hours=5))
    return datetime.now(tz).date()


def month_label(year, month):
    return date(year, month, 1).strftime("%B %Y")


def month_slug(year, month):
    return date(year, month, 1).strftime("%B-%Y")


def day_name_for(d):
    return d.strftime("%A")


def parse_event_date(s):
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


# ---------------------------------------------------------------- editions
def edition_number(ed):
    """Sequential edition count by creation order (1-based)."""
    rows = q_all("SELECT id FROM editions ORDER BY id ASC")
    for i, r in enumerate(rows, start=1):
        rid = r[0] if IS_PG else r["id"]
        eid = ed[0] if IS_PG else ed["id"]
        if rid == eid:
            return i
    return 1


def volume_issue(ed):
    n = edition_number(ed)
    volume = (n - 1) // 12 + 1
    issue = (n - 1) % 12 + 1
    return volume, issue


def ed_dict(row):
    if row is None:
        return None
    if IS_PG:
        keys = ["id", "year", "month", "status", "version",
                "created_at", "closed_at", "stage"]
        d = dict(zip(keys, row))
    else:
        d = dict(row)
    # stage may be absent on a row read before migration; derive it.
    if not d.get("stage"):
        d["stage"] = "Archived" if d.get("status") == "archived" else "Draft"
    d["label"] = month_label(d["year"], d["month"])
    v, iss = volume_issue(row)
    d["volume"] = v
    d["issue"] = iss
    d["volume_line"] = "Volume %d • Issue %d • %s" % (v, iss, d["label"])
    return d


def create_edition(year, month):
    # Never create a duplicate edition for the same month.
    existing = q_one("SELECT * FROM editions WHERE year = ? AND month = ? "
                     "ORDER BY id ASC", (year, month))
    if existing:
        return ed_dict(existing)
    now = datetime.now(timezone.utc).isoformat()
    if IS_PG:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(_sql(
            "INSERT INTO editions (year, month, status, version, "
            "created_at) VALUES (?, ?, 'open', 0, ?) RETURNING id"),
            (year, month, now))
        new_id = cur.fetchone()[0]
        conn.commit()
    else:
        new_id = q_exec(
            "INSERT INTO editions (year, month, status, version, "
            "created_at) VALUES (?, ?, 'open', 0, ?)",
            (year, month, now))
    row = q_one("SELECT * FROM editions WHERE id = ?", (new_id,))
    return ed_dict(row)


def get_or_create_current_edition():
    """Auto-rollover: an open edition from a PAST month is archived
    and a fresh open edition for the current month is created.
    An open edition for the current month, or for a future month
    created by a manual Close Month, is returned as-is and never
    auto-archived."""
    init_db()
    today = karachi_today()
    rows = q_all("SELECT * FROM editions WHERE status = 'open' "
                 "ORDER BY id DESC")
    for row in rows:
        ed = ed_dict(row)
        if (ed["year"], ed["month"]) >= (today.year, today.month):
            return ed
        # Past-month open edition: archive it.
        now = datetime.now(timezone.utc).isoformat()
        q_exec("UPDATE editions SET status = 'archived', closed_at = ? "
               "WHERE id = ?", (now, ed["id"]))
    return create_edition(today.year, today.month)


def get_edition(eid):
    row = q_one("SELECT * FROM editions WHERE id = ?", (eid,))
    return ed_dict(row)


def list_editions():
    rows = q_all("SELECT * FROM editions ORDER BY id DESC")
    return [ed_dict(r) for r in rows]


def fmt_event_date(s):
    try:
        return parse_event_date(s).strftime("%d %B %Y")
    except Exception:
        return s


def news_for_edition(eid, light=False, include_drafts=False):
    """Published items only by default (public pages + PDF + preview).
    Admin passes include_drafts=True. Serial numbers count published
    items only, in addition order; a draft has number=None and its
    unique record id stays separate from any serial."""
    init_db()
    if include_drafts:
        rows = q_all("SELECT * FROM news WHERE edition_id = ? "
                     "ORDER BY created_at ASC, id ASC", (eid,))
    else:
        rows = q_all("SELECT * FROM news WHERE edition_id = ? "
                     "AND is_draft = 0 ORDER BY created_at ASC, id ASC",
                     (eid,))
    out = []
    for r in rows:
        if IS_PG:
            keys = ["id", "edition_id", "title", "body", "event_date",
                    "event_day", "created_at", "category", "summary",
                    "venue", "department", "participants",
                    "source_credit", "is_draft"]
            d = dict(zip(keys, r))
        else:
            d = dict(r)
        d.setdefault("category", "University News")
        if d.get("category") not in CATEGORIES:
            d["category"] = "University News"
        d["is_draft"] = bool(d.get("is_draft"))
        d["event_date_display"] = fmt_event_date(d["event_date"])
        if light:
            d["images"] = images_meta_for_news(d["id"])
        else:
            d["images"] = images_for_news(d["id"])
        out.append(d)
    serial = 0
    for d in out:
        if d["is_draft"]:
            d["number"] = None
        else:
            serial += 1
            d["number"] = serial
    return out


def editorial_groups(items):
    """Cover story = first published item; the rest grouped by
    category in SECTION_ORDER. The cover story still appears in full
    inside its own section (the cover page is a teaser only)."""
    published = [it for it in items if not it.get("is_draft")]
    cover = published[0] if published else None
    groups = []
    for cat, title in SECTION_ORDER:
        rows = [it for it in published if it.get("category") == cat]
        if rows:
            groups.append((title, rows))
    return cover, groups


def images_meta_for_news(nid):
    rows = q_all("SELECT id, news_id, position, filename, mime, "
                 "caption, is_cover FROM news_images WHERE news_id = ? "
                 "ORDER BY position ASC, id ASC", (nid,))
    out = []
    for r in rows:
        if IS_PG:
            out.append(dict(zip(["id", "news_id", "position", "filename",
                                 "mime", "caption", "is_cover"], r)))
        else:
            out.append(dict(r))
    return out


def cover_images_meta_for_edition(eid):
    rows = q_all("SELECT id, edition_id, position, filename, mime "
                 "FROM edition_cover_images WHERE edition_id = ? "
                 "ORDER BY position ASC, id ASC", (eid,))
    out = []
    for r in rows:
        if IS_PG:
            out.append(dict(zip(["id", "edition_id", "position",
                                 "filename", "mime"], r)))
        else:
            out.append(dict(r))
    return out


def cover_images_for_edition(eid):
    rows = q_all("SELECT * FROM edition_cover_images WHERE edition_id = ? "
                 "ORDER BY position ASC, id ASC", (eid,))
    out = []
    for r in rows:
        if IS_PG:
            d = dict(zip(["id", "edition_id", "position", "filename",
                          "mime", "data"], r))
            if isinstance(d["data"], memoryview):
                d["data"] = bytes(d["data"])
        else:
            d = dict(r)
        out.append(d)
    return out


def images_for_news(nid):
    rows = q_all("SELECT * FROM news_images WHERE news_id = ? "
                 "ORDER BY position ASC, id ASC", (nid,))
    out = []
    for r in rows:
        if IS_PG:
            keys = ["id", "news_id", "position", "filename", "mime",
                    "data", "caption", "is_cover"]
            d = dict(zip(keys, r))
            if isinstance(d["data"], memoryview):
                d["data"] = bytes(d["data"])
        else:
            d = dict(r)
        out.append(d)
    return out


def bump_version(eid, action):
    row_c = q_one("SELECT COUNT(*) FROM news WHERE edition_id = ? "
                  "AND is_draft = 0", (eid,))
    count = row_c[0] if IS_PG else row_c[0]
    row = q_one("SELECT version FROM editions WHERE id = ?", (eid,))
    ver = (row[0] if IS_PG else row["version"]) + 1
    q_exec("UPDATE editions SET version = ? WHERE id = ?", (ver, eid))
    now = datetime.now(timezone.utc).isoformat()
    q_exec("INSERT INTO version_log (edition_id, version, action, "
           "news_count, created_at) VALUES (?, ?, ?, ?, ?)",
           (eid, ver, action, count, now))
    return ver


def close_edition(eid):
    ed = get_edition(eid)
    now = datetime.now(timezone.utc).isoformat()
    q_exec("UPDATE editions SET status = 'archived', closed_at = ? "
           "WHERE id = ?", (now, eid))
    # Next month starts immediately, even if the calendar month has
    # not changed yet (admin closed early at month end).
    y, m = ed["year"], ed["month"] + 1
    if m == 13:
        y, m = y + 1, 1
    return create_edition(y, m)


# ---------------------------------------------------------------- images
def magic_mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def logo_bytes():
    if not CUVAS_LOGO_B64:
        return None
    try:
        return base64.b64decode(CUVAS_LOGO_B64)
    except Exception:
        return None


def cropped_image(data, ratio_w, ratio_h, max_w=1200):
    """Centre-crop to ratio_w:ratio_h, resize, return JPEG bytes."""
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    target = ratio_w / ratio_h
    cur = w / h
    if cur > target:
        nw = int(h * target)
        x = (w - nw) // 2
        im = im.crop((x, 0, x + nw, h))
    else:
        nh = int(w / target)
        y = (h - nh) // 2
        im = im.crop((0, y, w, y + nh))
    if im.size[0] > max_w:
        im = im.resize((max_w, int(im.size[1] * max_w / im.size[0])),
                       Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def logo_image_bytes(max_w=420):
    data = logo_bytes()
    if not data:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        if im.size[0] > max_w:
            im = im.resize((max_w, int(im.size[1] * max_w / im.size[0])),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return data


# ---------------------------------------------------------------- PDF
def build_pdf(ed, items, covers=None):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image as RLImage, PageBreak,
                                    Paragraph, Spacer, Table, TableStyle)
    from reportlab.platypus.flowables import HRFlowable

    blue = colors.HexColor(BLUE)
    blue2 = colors.HexColor(BLUE2)
    purple = colors.HexColor(PURPLE)
    light = colors.HexColor("#f4f5f7")
    grey = colors.HexColor("#555555")
    page_w, page_h = A4
    content_w = 174 * mm

    s_banner = ParagraphStyle("tb", fontName="Helvetica-Bold", fontSize=13,
                              leading=16, textColor=colors.white)
    s_num = ParagraphStyle("nb", fontName="Helvetica-Bold", fontSize=20,
                           leading=20, textColor=colors.white,
                           alignment=TA_CENTER)
    s_body = ParagraphStyle("b", fontName="Helvetica", fontSize=10,
                            leading=14.5, alignment=TA_JUSTIFY,
                            textColor=colors.HexColor("#222222"),
                            spaceAfter=2.5 * mm)
    s_date = ParagraphStyle("d", fontName="Helvetica-Bold", fontSize=9,
                            leading=12, textColor=purple, spaceAfter=2 * mm)
    s_summary = ParagraphStyle("sm", fontName="Helvetica-Oblique",
                               fontSize=10, leading=14, alignment=TA_JUSTIFY,
                               textColor=grey, spaceAfter=3 * mm)
    s_meta = ParagraphStyle("mt", fontName="Helvetica", fontSize=8.5,
                            leading=11, textColor=grey, spaceAfter=2 * mm)
    s_caption = ParagraphStyle("cp", fontName="Helvetica-Oblique",
                               fontSize=8, leading=10, alignment=TA_CENTER,
                               textColor=grey, spaceAfter=2 * mm)
    s_cover_uni = ParagraphStyle("cu", fontName="Helvetica-Bold",
                                 fontSize=11, leading=14,
                                 alignment=TA_CENTER, textColor=blue)
    s_chronicle = ParagraphStyle("cb", fontName="Times-Bold",
                                 fontSize=40, leading=42,
                                 alignment=TA_CENTER, textColor=blue)
    s_cover_sub = ParagraphStyle("cs", fontName="Helvetica", fontSize=11,
                                 leading=14, alignment=TA_CENTER,
                                 textColor=grey)
    s_vol = ParagraphStyle("v", fontName="Helvetica-Bold", fontSize=10,
                           leading=13, alignment=TA_CENTER,
                           textColor=colors.white)
    s_section = ParagraphStyle("sec", fontName="Times-Bold", fontSize=18,
                               leading=21, textColor=blue, spaceAfter=1 * mm)
    s_headline = ParagraphStyle("hl", fontName="Times-Bold", fontSize=14,
                                leading=17, textColor=blue)
    s_toc_num = ParagraphStyle("tn", fontName="Helvetica-Bold",
                               fontSize=14, leading=14,
                               textColor=colors.white, alignment=TA_CENTER)
    s_cover_label = ParagraphStyle("cl", fontName="Helvetica-Bold",
                                   fontSize=10, leading=13, textColor=purple)

    def header_footer(canvas, doc):
        canvas.saveState()
        if doc.page > 1:
            canvas.setFillColor(blue)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(18 * mm, page_h - 13 * mm, "CUVAS CHRONICLE")
            canvas.setFont("Helvetica", 8)
            canvas.drawRightString(page_w - 18 * mm, page_h - 13 * mm,
                                   ed["volume_line"])
            canvas.setStrokeColor(purple)
            canvas.setLineWidth(1.2)
            canvas.line(18 * mm, page_h - 15 * mm,
                        page_w - 18 * mm, page_h - 15 * mm)
        canvas.setStrokeColor(purple)
        canvas.setLineWidth(1)
        canvas.line(18 * mm, 15 * mm, page_w - 18 * mm, 15 * mm)
        canvas.setFillColor(grey)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(18 * mm, 11 * mm, UNI_NAME)
        canvas.drawRightString(page_w - 18 * mm, 11 * mm,
                               "Page %d" % doc.page)
        canvas.restoreState()

    def full_image(im_data, ratio=(16, 9)):
        data = cropped_image(im_data, ratio[0], ratio[1])
        w = content_w
        h = w * ratio[1] / ratio[0]
        return RLImage(io.BytesIO(data), width=w, height=h)

    def small_image(im_data, w_mm, ratio=(4, 3)):
        data = cropped_image(im_data, ratio[0], ratio[1])
        w = w_mm * mm
        return RLImage(io.BytesIO(data), width=w,
                       height=w * ratio[1] / ratio[0])

    def date_line_for(it):
        try:
            edate = parse_event_date(it["event_date"])
            return "%s, %s" % (it["event_day"] or day_name_for(edate),
                               edate.strftime("%d %B %Y"))
        except Exception:
            return "%s - %s" % (it["event_day"], it["event_date"])

    def cover_image_of(it):
        for im in it["images"]:
            if im.get("is_cover"):
                return im
        return it["images"][0] if it["images"] else None

    published = [it for it in items if not it.get("is_draft")]
    cover_story, groups = editorial_groups(published)
    story = []

    # ---- COVER / MASTHEAD ----
    lb = logo_image_bytes()
    if lb:
        try:
            img = RLImage(io.BytesIO(lb), width=62 * mm, height=31 * mm)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 3 * mm))
        except Exception:
            pass
    story.append(Paragraph(UNI_NAME, s_cover_uni))
    story.append(Spacer(1, 2 * mm))
    story.append(HRFlowable(width="100%", thickness=1.2, color=purple,
                            spaceAfter=4 * mm, spaceBefore=2 * mm))
    story.append(Paragraph("CUVAS Chronicle", s_chronicle))
    story.append(Paragraph("News and Highlights from Across the University",
                           s_cover_sub))
    story.append(Spacer(1, 4 * mm))
    vol_tbl = Table([[Paragraph(ed["volume_line"], s_vol)]],
                    colWidths=[110 * mm])
    vol_tbl.hAlign = "CENTER"
    vol_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), blue),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    story.append(vol_tbl)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Version %d" % ed["version"],
                           ParagraphStyle("vv", parent=s_cover_sub,
                                          fontName="Helvetica-Bold",
                                          textColor=blue)))
    story.append(Spacer(1, 5 * mm))
    if cover_story is not None:
        story.append(Paragraph("COVER STORY", s_cover_label))
        story.append(Spacer(1, 2 * mm))
        ci = cover_image_of(cover_story)
        if ci:
            try:
                story.append(full_image(ci["data"]))
                if ci.get("caption"):
                    story.append(Paragraph(ci["caption"].replace("&", "&amp;"),
                                           s_caption))
                story.append(Spacer(1, 2 * mm))
            except Exception:
                pass
        story.append(Paragraph(cover_story["title"], s_headline))
        story.append(Paragraph("%s  |  %s" % (date_line_for(cover_story),
                                              cover_story.get("category") or
                                              "University News"), s_date))
        summ = (cover_story.get("summary") or "").strip()
        if not summ:
            summ = cover_story["body"][:400].strip()
        if summ:
            story.append(Paragraph(summ.replace("&", "&amp;"), s_summary))
    elif covers:
        try:
            if len(covers) == 1:
                story.append(full_image(covers[0]["data"]))
            elif len(covers) == 2:
                cells = [small_image(c["data"], 86) for c in covers]
                t = Table([cells], colWidths=[86 * mm, 86 * mm])
                t.setStyle(TableStyle([
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
                story.append(t)
            else:
                rows = []
                for k in (0, 2):
                    rows.append([small_image(c["data"], 86)
                                 for c in covers[k:k + 2]])
                t = Table(rows, colWidths=[86 * mm, 86 * mm])
                t.setStyle(TableStyle([
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
                story.append(t)
        except Exception:
            pass
    story.append(PageBreak())

    # ---- CONTENTS ----
    story.append(Paragraph("Contents", ParagraphStyle(
        "ch", fontName="Times-Bold", fontSize=26, leading=28,
        textColor=blue)))
    story.append(HRFlowable(width="100%", thickness=1.2, color=purple,
                            spaceAfter=4 * mm, spaceBefore=2 * mm))
    info = Table([[Paragraph(
        "%s<br/>%d news item(s) &nbsp;|&nbsp; Version %d"
        % (ed["volume_line"], len(published), ed["version"]),
        ParagraphStyle("ib", fontName="Helvetica", fontSize=10,
                       leading=14, textColor=blue))]],
        colWidths=[content_w])
    info.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), light),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    story.append(info)
    story.append(Spacer(1, 5 * mm))
    if published:
        toc_rows = []
        for it in published:
            badge = Table([[Paragraph("%02d" % it["number"], s_toc_num)]],
                          colWidths=[12 * mm], rowHeights=[12 * mm])
            badge.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), blue),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ROUNDEDCORNERS", [3, 3, 3, 3]),
            ]))
            text_cell = Paragraph(
                "%s<br/><font size=8 color='#666666'>%s  |  %s</font>"
                % (it["title"], date_line_for(it),
                   it.get("category") or "University News"),
                ParagraphStyle("tc", fontName="Helvetica-Bold",
                               fontSize=11, leading=14, textColor=blue))
            toc_rows.append([badge, text_cell])
        toc = Table(toc_rows, colWidths=[15 * mm, 159 * mm])
        toc.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, light),
        ]))
        story.append(toc)
    else:
        story.append(Paragraph("No news has been added to this edition yet.",
                               s_cover_sub))
    story.append(PageBreak())

    # ---- SECTIONS + NEWS (4 rotating image layouts) ----
    for sec_title, sec_items in groups:
        story.append(Paragraph(sec_title, s_section))
        story.append(HRFlowable(width="100%", thickness=1.2, color=purple,
                                spaceAfter=4 * mm, spaceBefore=1 * mm))
        for it in sec_items:
            banner = Table(
                [[Paragraph("%02d" % it["number"], s_num),
                  Paragraph(it["title"], s_banner)]],
                colWidths=[16 * mm, 158 * mm])
            banner.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), blue),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("ROUNDEDCORNERS", [4, 4, 4, 4]),
            ]))
            story.append(banner)
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph("%s  |  %s" % (date_line_for(it),
                                                  it.get("category") or
                                                  "University News"),
                                   s_date))
            meta_bits = []
            if it.get("venue"):
                meta_bits.append("Venue: %s" % it["venue"])
            if it.get("department"):
                meta_bits.append("Department: %s" % it["department"])
            if it.get("participants"):
                meta_bits.append("Participants: %s" % it["participants"])
            if it.get("source_credit"):
                meta_bits.append("Source: %s" % it["source_credit"])
            if meta_bits:
                story.append(Paragraph("  |  ".join(meta_bits)
                                       .replace("&", "&amp;"), s_meta))
            summ = (it.get("summary") or "").strip()
            if summ:
                story.append(Paragraph(summ.replace("&", "&amp;"), s_summary))
            paras = [p.strip() for p in it["body"].split("\n") if p.strip()]
            imgs = it["images"]
            layout = (it["number"] - 1) % 4 + 1

            def _para(txt):
                return Paragraph(txt.replace("&", "&amp;"), s_body)

            def _add_full(im):
                try:
                    story.append(full_image(im["data"]))
                    if im.get("caption"):
                        story.append(Paragraph(im["caption"].replace("&", "&amp;"),
                                               s_caption))
                    story.append(Spacer(1, 2 * mm))
                except Exception:
                    pass

            def _cell(im):
                try:
                    parts = [small_image(im["data"], 86)]
                    if im.get("caption"):
                        parts.append(Paragraph(im["caption"].replace("&", "&amp;"),
                                               s_caption))
                    return parts
                except Exception:
                    return [Paragraph("", s_body)]

            def _add_grid(grid_imgs):
                if not grid_imgs:
                    return
                try:
                    if len(grid_imgs) == 1:
                        _add_full(grid_imgs[0])
                    elif len(grid_imgs) == 2:
                        tbl = Table([[_cell(c) for c in grid_imgs]],
                                    colWidths=[86 * mm, 86 * mm])
                        tbl.setStyle(TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 2),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
                        story.append(tbl)
                        story.append(Spacer(1, 2 * mm))
                    elif len(grid_imgs) == 3:
                        _add_full(grid_imgs[0])
                        tbl = Table([[_cell(c) for c in grid_imgs[1:]]],
                                    colWidths=[86 * mm, 86 * mm])
                        tbl.setStyle(TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 2),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
                        story.append(tbl)
                        story.append(Spacer(1, 2 * mm))
                    else:
                        rows = []
                        for k in (0, 2):
                            rows.append([_cell(c) for c in grid_imgs[k:k + 2]])
                        tbl = Table(rows, colWidths=[86 * mm, 86 * mm])
                        tbl.setStyle(TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 2),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                            ("TOPPADDING", (0, 0), (-1, -1), 2),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
                        story.append(tbl)
                        story.append(Spacer(1, 2 * mm))
                except Exception:
                    pass

            if not imgs:
                for para in paras:
                    story.append(_para(para))
            elif layout == 1:
                _add_full(imgs[0])
                remaining = imgs[1:]
                if remaining:
                    end_img = remaining[-1]
                    middles = remaining[:-1]
                    insert_at = {}
                    for j, im in enumerate(middles):
                        pos = int(len(paras) * (j + 1) / (len(middles) + 1))
                        if pos >= len(paras):
                            pos = len(paras) - 1
                        if pos < 0:
                            pos = 0
                        while pos in insert_at and pos < len(paras) - 1:
                            pos += 1
                        insert_at[pos] = im
                    side_used = False
                    i = 0
                    while i < len(paras):
                        story.append(_para(paras[i]))
                        if i in insert_at:
                            im = insert_at[i]
                            try:
                                if not side_used and i + 1 < len(paras):
                                    side = Table(
                                        [[_para(paras[i + 1]),
                                          small_image(im["data"], 58)]],
                                        colWidths=[112 * mm, 62 * mm])
                                    side.setStyle(TableStyle([
                                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                        ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                                    ]))
                                    story.append(side)
                                    story.append(Spacer(1, 1 * mm))
                                    side_used = True
                                    i += 1
                                else:
                                    _add_full(im)
                            except Exception:
                                pass
                        i += 1
                    _add_full(end_img)
            elif layout == 2:
                for para in paras:
                    story.append(_para(para))
                _add_grid(imgs)
            elif layout == 3:
                if paras:
                    try:
                        side = Table(
                            [[_para(paras[0]), small_image(imgs[0]["data"], 58)]],
                            colWidths=[112 * mm, 62 * mm])
                        side.setStyle(TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 2),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                        ]))
                        story.append(side)
                        story.append(Spacer(1, 2 * mm))
                    except Exception:
                        story.append(_para(paras[0]))
                    for para in paras[1:]:
                        story.append(_para(para))
                _add_grid(imgs[1:])
            else:
                _add_full(imgs[0])
                rest = imgs[1:]
                last_img = rest[-1] if rest else None
                middles = rest[:-1] if rest else []
                half = (len(paras) + 1) // 2
                for para in paras[:half]:
                    story.append(_para(para))
                _add_grid(middles)
                for para in paras[half:]:
                    story.append(_para(para))
                if last_img is not None:
                    _add_full(last_img)
            story.append(Spacer(1, 3 * mm))
            story.append(HRFlowable(width="100%", thickness=0.6, color=light,
                                    spaceAfter=5 * mm, spaceBefore=1 * mm))

    from reportlab.platypus import SimpleDocTemplate
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm,
                            rightMargin=18 * mm, topMargin=20 * mm,
                            bottomMargin=18 * mm,
                            title="CUVAS Chronicle - %s" % ed["label"],
                            author=UNI_SHORT)
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    buf.seek(0)
    return buf.getvalue()


def pdf_filename(ed):
    return ("CUVAS-Newsletter-%s-v%d.pdf"
            % (month_slug(ed["year"], ed["month"]), ed["version"]))


# ---------------------------------------------------------------- auth
def is_admin():
    return bool(session.get("admin"))


def require_admin():
    if not is_admin():
        abort(403)


# ---------------------------------------------------------------- routes
@app.route("/brand/cuvas-logo.png")
def brand_logo():
    data = logo_bytes()
    if not data:
        abort(404)
    return Response(data, mimetype="image/png")


@app.route("/image/news/<int:img_id>")
def serve_news_image(img_id):
    row = q_one("SELECT mime, data FROM news_images WHERE id = ?",
                (img_id,))
    if not row:
        abort(404)
    mime = row[0] if IS_PG else row["mime"]
    data = row[1] if IS_PG else row["data"]
    if isinstance(data, memoryview):
        data = bytes(data)
    return Response(data, mimetype=mime,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.route("/image/cover/<int:img_id>")
def serve_cover_image(img_id):
    row = q_one("SELECT mime, data FROM edition_cover_images "
                "WHERE id = ?", (img_id,))
    if not row:
        abort(404)
    mime = row[0] if IS_PG else row["mime"]
    data = row[1] if IS_PG else row["data"]
    if isinstance(data, memoryview):
        data = bytes(data)
    return Response(data, mimetype=mime,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.route("/")
def home():
    ed = get_or_create_current_edition()
    items = news_for_edition(ed["id"], light=True)
    return render_template("home.html", ed=ed, items=items,
                           editions=list_editions(), uni=UNI_NAME)


@app.route("/edition/<int:eid>")
def view_edition(eid):
    ed = get_edition(eid)
    if not ed:
        abort(404)
    items = news_for_edition(eid, light=True)
    return render_template("edition.html", ed=ed, items=items,
                           editions=list_editions(), uni=UNI_NAME)


@app.route("/edition/<int:eid>/preview")
def edition_preview(eid):
    ed = get_edition(eid)
    if not ed:
        abort(404)
    items = news_for_edition(eid)
    cover, groups = editorial_groups(items)
    covers = cover_images_for_edition(eid)
    return render_template("preview.html", ed=ed, items=items,
                           cover=cover, groups=groups, covers=covers,
                           editions=list_editions(), uni=UNI_NAME)


@app.route("/edition/<int:eid>/pdf")
def edition_pdf(eid):
    ed = get_edition(eid)
    if not ed:
        abort(404)
    items = news_for_edition(eid)
    covers = cover_images_for_edition(eid)
    if not covers:
        # Fallback cover collage: first image of the first news items.
        for it in items:
            if it["images"]:
                covers.append(it["images"][0])
            if len(covers) >= 4:
                break
    data = build_pdf(ed, items, covers)
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition":
                             "attachment; filename=%s" % pdf_filename(ed)})


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    err = ""
    if request.method == "POST":
        if (request.form.get("username") == ADMIN_USER and
                request.form.get("password") == ADMIN_PASSWORD):
            session["admin"] = True
            # Remember me is functional: permanent session (30 days)
            # when ticked, browser-session only when not ticked.
            session.permanent = bool(request.form.get("remember_me"))
            return redirect(url_for("admin_home"))
        err = "Wrong username or password."
    try:
        ed = get_or_create_current_edition()
    except Exception:
        ed = None
    return render_template("login.html", err=err, ed=ed)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("home"))


@app.route("/admin")
def admin_home():
    if not is_admin():
        return redirect(url_for("admin_login"))
    # Month selector: ?edition=<id> shows that edition on the dashboard;
    # default is the current open edition.
    sel = request.args.get("edition", type=int)
    if sel:
        ed = get_edition(sel)
        if not ed:
            ed = get_or_create_current_edition()
    else:
        ed = get_or_create_current_edition()
    all_items = news_for_edition(ed["id"], light=True, include_drafts=True)
    # Search / category / sort (server-side, on real rows only).
    q = (request.args.get("q") or "").strip().lower()
    cat = request.args.get("category") or ""
    sort = request.args.get("sort") or "newest"
    items = all_items
    if q:
        items = [it for it in items
                 if q in it["title"].lower() or q in it["body"].lower()
                 or q in (it.get("summary") or "").lower()]
    if cat and cat in CATEGORIES:
        items = [it for it in items if it.get("category") == cat]
    if sort == "oldest":
        items = list(reversed(items))
    elif sort == "event_new":
        items = sorted(items, key=lambda x: x["event_date"], reverse=True)
    elif sort == "event_old":
        items = sorted(items, key=lambda x: x["event_date"])
    elif sort == "title":
        items = sorted(items, key=lambda x: x["title"].lower())
    # else "newest": addition order reversed (latest first in table).
    if sort == "newest":
        items = list(reversed(items))
    save_err = request.args.get("error", "")
    msg = request.args.get("msg", "")
    cover_count = 0
    cover_meta = []
    try:
        cover_meta = cover_images_meta_for_edition(ed["id"])
        cover_count = len(cover_meta)
    except Exception:
        cover_count = 0
        cover_meta = []
    logs = q_all("SELECT * FROM version_log WHERE edition_id = ? "
                 "ORDER BY id DESC LIMIT 20", (ed["id"],))
    log_list = []
    for r in logs:
        if IS_PG:
            log_list.append(dict(zip(
                ["id", "edition_id", "version", "action", "news_count",
                 "created_at"], r)))
        else:
            log_list.append(dict(r))
    # Summary cards (real data only).
    total_all = q_one("SELECT COUNT(*) FROM news")[0]
    archived_count = q_one("SELECT COUNT(*) FROM editions "
                           "WHERE status = 'archived'")[0]
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    week_count = 0
    for row in q_all("SELECT created_at FROM news"):
        raw = row[0] if IS_PG else row["created_at"]
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= week_ago:
                week_count += 1
        except Exception:
            pass
    published_count = len([it for it in all_items if not it["is_draft"]])
    draft_count = len([it for it in all_items if it["is_draft"]])
    # Monthly publication progress: 3 honest readiness steps.
    steps = [("News added", published_count > 0),
             ("Cover image set", cover_count > 0),
             ("Edition versioned (PDF ready)", ed["version"] > 0)]
    progress = int(100 * sum(1 for _, ok in steps if ok) / len(steps))
    return render_template("admin.html", ed=ed, items=items,
                           all_items=all_items, logs=log_list,
                           editions=list_editions(), save_err=save_err,
                           msg=msg, cover_count=cover_count,
                           cover_meta=cover_meta, categories=CATEGORIES,
                           q=q, cat=cat, sort=sort,
                           total_all=total_all,
                           archived_count=archived_count,
                           week_count=week_count,
                           published_count=published_count,
                           draft_count=draft_count,
                           progress=progress, steps=steps)


@app.route("/admin/news", methods=["POST"])
def admin_add_news():
    if not is_admin():
        return redirect(url_for("admin_login"))
    ed = get_or_create_current_edition()
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    event_date = (request.form.get("event_date") or "").strip()
    event_day = (request.form.get("event_day") or "").strip()
    category = request.form.get("category") or "University News"
    if category not in CATEGORIES:
        category = "University News"
    summary = (request.form.get("summary") or "").strip()
    venue = (request.form.get("venue") or "").strip()
    department = (request.form.get("department") or "").strip()
    participants = (request.form.get("participants") or "").strip()
    source_credit = (request.form.get("source_credit") or "").strip()
    action = request.form.get("action") or "publish"
    is_draft = 1 if action == "draft" else 0
    if not title or not body or not event_date:
        return redirect(url_for("admin_home", error="missing"))
    try:
        d = parse_event_date(event_date)
    except Exception:
        return redirect(url_for("admin_home", error="date"))
    if not event_day:
        event_day = day_name_for(d)
    now = datetime.now(timezone.utc).isoformat()
    try:
        if IS_PG:
            nid = q_insert_returning_id(
                "INSERT INTO news (edition_id, title, body, event_date, "
                "event_day, created_at, category, summary, venue, "
                "department, participants, source_credit, is_draft) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "RETURNING id",
                (ed["id"], title, body, event_date, event_day, now,
                 category, summary, venue, department, participants,
                 source_credit, is_draft))
        else:
            nid = q_insert_returning_id(
                "INSERT INTO news (edition_id, title, body, event_date, "
                "event_day, created_at, category, summary, venue, "
                "department, participants, source_credit, is_draft) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ed["id"], title, body, event_date, event_day, now,
                 category, summary, venue, department, participants,
                 source_credit, is_draft))
        files = request.files.getlist("images")
        captions = request.form.getlist("captions")
        try:
            cover_idx = int(request.form.get("cover_index") or 0)
        except Exception:
            cover_idx = 0
        pos = 0
        for f in files:
            if pos >= 4 or not f or not f.filename:
                continue
            data = f.read()
            if not data or len(data) > MAX_IMAGE_BYTES:
                continue
            mime = magic_mime(data)
            if not mime:
                continue
            cap = captions[pos].strip() if pos < len(captions) else ""
            q_exec("INSERT INTO news_images (news_id, position, filename, "
                   "mime, data, caption, is_cover) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (nid, pos, f.filename, mime, data, cap,
                    1 if pos == cover_idx else 0))
            pos += 1
        if not is_draft:
            bump_version(ed["id"], "News added: %s" % title[:80])
            return redirect(url_for("admin_home", msg="added"))
        return redirect(url_for("admin_home", msg="draft"))
    except Exception:
        return redirect(url_for(
            "admin_home",
            error="save_failed_check_list"))


@app.route("/admin/cover", methods=["POST"])
def admin_cover():
    """Cover-page images for the current edition (up to 4).
    Uploading replaces the previous cover images. If none are set,
    the PDF cover falls back to the first images of the news items."""
    if not is_admin():
        return redirect(url_for("admin_login"))
    ed = get_or_create_current_edition()
    try:
        q_exec("DELETE FROM edition_cover_images WHERE edition_id = ?",
               (ed["id"],))
        files = request.files.getlist("cover_images")
        pos = 0
        for f in files:
            if pos >= 4 or not f or not f.filename:
                continue
            data = f.read()
            if not data or len(data) > MAX_IMAGE_BYTES:
                continue
            mime = magic_mime(data)
            if not mime:
                continue
            q_exec("INSERT INTO edition_cover_images (edition_id, "
                   "position, filename, mime, data) "
                   "VALUES (?, ?, ?, ?, ?)",
                   (ed["id"], pos, f.filename, mime, data))
            pos += 1
        bump_version(ed["id"], "Cover images updated")
    except Exception:
        return redirect(url_for("admin_home",
                                error="save_failed_check_list"))
    return redirect(url_for("admin_home"))


@app.route("/admin/news/<int:nid>/edit", methods=["GET", "POST"])
def admin_edit_news(nid):
    if not is_admin():
        return redirect(url_for("admin_login"))
    row = q_one("SELECT * FROM news WHERE id = ?", (nid,))
    if not row:
        abort(404)
    if IS_PG:
        item = dict(zip(["id", "edition_id", "title", "body", "event_date",
                         "event_day", "created_at", "category", "summary",
                         "venue", "department", "participants",
                         "source_credit", "is_draft"], row))
    else:
        item = dict(row)
    item.setdefault("category", "University News")
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        event_date = (request.form.get("event_date") or "").strip()
        event_day = (request.form.get("event_day") or "").strip()
        category = request.form.get("category") or "University News"
        if category not in CATEGORIES:
            category = "University News"
        summary = (request.form.get("summary") or "").strip()
        venue = (request.form.get("venue") or "").strip()
        department = (request.form.get("department") or "").strip()
        participants = (request.form.get("participants") or "").strip()
        source_credit = (request.form.get("source_credit") or "").strip()
        if title and body and event_date:
            try:
                d = parse_event_date(event_date)
                if not event_day:
                    event_day = day_name_for(d)
                q_exec("UPDATE news SET title = ?, body = ?, "
                       "event_date = ?, event_day = ?, category = ?, "
                       "summary = ?, venue = ?, department = ?, "
                       "participants = ?, source_credit = ? WHERE id = ?",
                       (title, body, event_date, event_day, category,
                        summary, venue, department, participants,
                        source_credit, nid))
                if not item.get("is_draft"):
                    bump_version(item["edition_id"], "News edited: %s"
                                 % title[:80])
            except Exception:
                pass
        return redirect(url_for("admin_home"))
    item["images"] = images_meta_for_news(nid)
    return render_template("edit.html", item=item, categories=CATEGORIES,
                           ed=get_edition(item["edition_id"]))


@app.route("/admin/news/<int:nid>/publish", methods=["POST"])
def admin_publish_news(nid):
    if not is_admin():
        return redirect(url_for("admin_login"))
    row = q_one("SELECT * FROM news WHERE id = ?", (nid,))
    if not row:
        abort(404)
    eid = row[1] if IS_PG else row["edition_id"]
    title = row[2] if IS_PG else row["title"]
    q_exec("UPDATE news SET is_draft = 0 WHERE id = ?", (nid,))
    bump_version(eid, "Draft published: %s" % title[:80])
    return redirect(url_for("admin_home", msg="published"))


@app.route("/admin/edition-stage", methods=["POST"])
def admin_edition_stage():
    if not is_admin():
        return redirect(url_for("admin_login"))
    ed = get_or_create_current_edition()
    stage = request.form.get("stage") or ""
    if stage in ("Draft", "Ready for Review", "Published") and \
            ed["status"] != "archived":
        q_exec("UPDATE editions SET stage = ? WHERE id = ?",
               (stage, ed["id"]))
    return redirect(url_for("admin_home"))


@app.route("/admin/news/<int:nid>/delete", methods=["POST"])
def admin_delete_news(nid):
    if not is_admin():
        return redirect(url_for("admin_login"))
    row = q_one("SELECT * FROM news WHERE id = ?", (nid,))
    if not row:
        abort(404)
    eid = row[1] if IS_PG else row["edition_id"]
    title = row[2] if IS_PG else row["title"]
    q_exec("DELETE FROM news_images WHERE news_id = ?", (nid,))
    q_exec("DELETE FROM news WHERE id = ?", (nid,))
    bump_version(eid, "News deleted: %s" % title[:80])
    return redirect(url_for("admin_home"))


@app.route("/admin/close-month", methods=["POST"])
def admin_close_month():
    if not is_admin():
        return redirect(url_for("admin_login"))
    ed = get_or_create_current_edition()
    close_edition(ed["id"])
    return redirect(url_for("admin_home"))


@app.errorhandler(403)
def forbidden(_e):
    return render_template("login.html",
                           err="Please sign in as admin first."), 403


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5056, debug=True)

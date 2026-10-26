"""CUVAS Newsletter — monthly newsletter system.

One document per month. News is appended below previous news in the
same monthly edition, numbered sequentially in addition order.
At month end the edition is archived and the new month starts at 1.
PDF is generated with ReportLab in CUVAS indigo + gold branding,
official CUVAS logo on top, Volume / Issue / Month on the cover.
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
GOLD = "#c9a227"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cuvas-newsletter-dev-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30 MB total per form

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


def q_one(query, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_sql(query), params)
    row = cur.fetchone()
    if not IS_PG:
        conn.close()
    return row


def q_all(query, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_sql(query), params)
    rows = cur.fetchall()
    if not IS_PG:
        conn.close()
    return rows


def q_exec(query, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_sql(query), params)
    conn.commit()
    last = None
    try:
        last = cur.lastrowid
    except Exception:
        pass
    if not IS_PG:
        conn.close()
    return last


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
                "created_at", "closed_at"]
        d = dict(zip(keys, row))
    else:
        d = dict(row)
    d["label"] = month_label(d["year"], d["month"])
    v, iss = volume_issue(row)
    d["volume"] = v
    d["issue"] = iss
    d["volume_line"] = "Volume %d • Issue %d • %s" % (v, iss, d["label"])
    return d


def create_edition(year, month):
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


def news_for_edition(eid):
    rows = q_all("SELECT * FROM news WHERE edition_id = ? "
                 "ORDER BY created_at ASC, id ASC", (eid,))
    out = []
    for r in rows:
        if IS_PG:
            keys = ["id", "edition_id", "title", "body", "event_date",
                    "event_day", "created_at"]
            d = dict(zip(keys, r))
        else:
            d = dict(r)
        d["images"] = images_for_news(d["id"])
        out.append(d)
    for i, d in enumerate(out, start=1):
        d["number"] = i
    return out


def images_for_news(nid):
    rows = q_all("SELECT * FROM news_images WHERE news_id = ? "
                 "ORDER BY position ASC, id ASC", (nid,))
    out = []
    for r in rows:
        if IS_PG:
            keys = ["id", "news_id", "position", "filename", "mime",
                    "data"]
            d = dict(zip(keys, r))
            if isinstance(d["data"], memoryview):
                d["data"] = bytes(d["data"])
        else:
            d = dict(r)
        out.append(d)
    return out


def bump_version(eid, action):
    count = len(news_for_edition(eid))
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
def build_pdf(ed, items):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image as RLImage, PageBreak,
                                    Paragraph, Spacer, Table, TableStyle)
    from reportlab.lib.utils import ImageReader

    indigo = colors.HexColor(INDIGO)
    dark = colors.HexColor("#252775")
    gold = colors.HexColor(GOLD)
    light = colors.HexColor("#eef0fa")
    grey = colors.HexColor("#444444")
    page_w, page_h = A4

    s_title = ParagraphStyle("t", fontName="Helvetica-Bold",
                             fontSize=13, leading=16, textColor=indigo,
                             spaceAfter=2 * mm)
    s_body = ParagraphStyle("b", fontName="Helvetica", fontSize=10,
                            leading=14.5, alignment=TA_JUSTIFY,
                            textColor=colors.HexColor("#222222"),
                            spaceAfter=2.5 * mm)
    s_date = ParagraphStyle("d", fontName="Helvetica-Bold", fontSize=9,
                            leading=12, textColor=gold, spaceAfter=1 * mm)
    s_cover_uni = ParagraphStyle("cu", fontName="Helvetica-Bold",
                                 fontSize=11, leading=14,
                                 alignment=TA_CENTER, textColor=indigo)
    s_cover_big = ParagraphStyle("cb", fontName="Helvetica-Bold",
                                 fontSize=38, leading=40,
                                 alignment=TA_CENTER, textColor=indigo)
    s_cover_sub = ParagraphStyle("cs", fontName="Helvetica", fontSize=11,
                                 leading=14, alignment=TA_CENTER,
                                 textColor=grey)
    s_vol = ParagraphStyle("v", fontName="Helvetica-Bold", fontSize=10,
                           leading=13, alignment=TA_CENTER,
                           textColor=colors.white)
    s_toc = ParagraphStyle("toc", fontName="Helvetica", fontSize=10,
                           leading=14, textColor=colors.HexColor("#222222"))
    s_num = ParagraphStyle("n", fontName="Helvetica-Bold", fontSize=22,
                           leading=22, textColor=colors.white,
                           alignment=TA_CENTER)

    def header_footer(canvas, doc):
        canvas.saveState()
        if doc.page > 1:
            canvas.setFillColor(indigo)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(18 * mm, page_h - 13 * mm,
                              "CUVAS NEWSLETTER")
            canvas.setFont("Helvetica", 8)
            canvas.drawRightString(page_w - 18 * mm, page_h - 13 * mm,
                                   ed["volume_line"])
            canvas.setStrokeColor(gold)
            canvas.setLineWidth(1.2)
            canvas.line(18 * mm, page_h - 15 * mm,
                        page_w - 18 * mm, page_h - 15 * mm)
        canvas.setStrokeColor(gold)
        canvas.setLineWidth(1)
        canvas.line(18 * mm, 15 * mm, page_w - 18 * mm, 15 * mm)
        canvas.setFillColor(grey)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(18 * mm, 11 * mm, UNI_NAME)
        canvas.drawRightString(page_w - 18 * mm, 11 * mm,
                               "Page %d" % doc.page)
        canvas.restoreState()

    story = []
    # Cover header: official logo, university name, NEWSLETTER, volume.
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
    story.append(Paragraph("NEWSLETTER", s_cover_big))
    story.append(Paragraph("News and Highlights from Across the University",
                           s_cover_sub))
    story.append(Spacer(1, 4 * mm))
    vol_tbl = Table([[Paragraph(ed["volume_line"], s_vol)]],
                    colWidths=[110 * mm])
    vol_tbl.hAlign = "CENTER"
    vol_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), indigo),
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
                                          textColor=indigo)))
    if items:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("In this issue", ParagraphStyle(
            "ih", fontName="Helvetica-Bold", fontSize=13, leading=16,
            textColor=indigo, spaceAfter=3 * mm)))
        toc_rows = []
        for it in items:
            toc_rows.append([Paragraph("%02d" % it["number"], ParagraphStyle(
                "tn", fontName="Helvetica-Bold", fontSize=10, leading=13,
                textColor=indigo)),
                Paragraph(it["title"], s_toc)])
        toc = Table(toc_rows, colWidths=[12 * mm, 150 * mm])
        toc.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, light),
        ]))
        story.append(toc)
    else:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph("No news has been added to this edition yet.",
                               s_cover_sub))
    story.append(PageBreak())

    content_w = 174 * mm
    for it in items:
        # Number badge + title + date/day
        badge = Table([[Paragraph("%02d" % it["number"], s_num)]],
                      colWidths=[14 * mm], rowHeights=[14 * mm])
        badge.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), indigo),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        head = Table([[badge, Paragraph(it["title"].upper(), s_title)]],
                     colWidths=[16 * mm, 158 * mm])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
        story.append(head)
        try:
            edate = parse_event_date(it["event_date"])
            date_line = "%s, %s" % (it["event_day"] or
                                    day_name_for(edate),
                                    edate.strftime("%d %B %Y"))
        except Exception:
            date_line = "%s — %s" % (it["event_day"], it["event_date"])
        story.append(Paragraph(date_line, s_date))
        for para in it["body"].split("\n"):
            para = para.strip()
            if para:
                story.append(Paragraph(para.replace("&", "&amp;"), s_body))
        imgs = it["images"]
        if imgs:
            story.append(Spacer(1, 1 * mm))
            if len(imgs) == 1:
                data = cropped_image(imgs[0]["data"], 16, 9)
                story.append(RLImage(io.BytesIO(data), width=content_w,
                                     height=content_w * 9 / 16))
            elif len(imgs) == 2:
                cells = []
                for im in imgs:
                    data = cropped_image(im["data"], 4, 3)
                    cells.append(RLImage(io.BytesIO(data), width=86 * mm,
                                         height=64.5 * mm))
                t = Table([cells], colWidths=[86 * mm, 86 * mm],
                          spaceBefore=1 * mm, spaceAfter=1 * mm)
                t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 2),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
                story.append(t)
            elif len(imgs) == 3:
                data = cropped_image(imgs[0]["data"], 16, 9)
                story.append(RLImage(io.BytesIO(data), width=content_w,
                                     height=content_w * 9 / 16))
                cells = []
                for im in imgs[1:]:
                    data = cropped_image(im["data"], 4, 3)
                    cells.append(RLImage(io.BytesIO(data), width=86 * mm,
                                         height=64.5 * mm))
                t = Table([cells], colWidths=[86 * mm, 86 * mm],
                          spaceBefore=2 * mm, spaceAfter=1 * mm)
                t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 2),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
                story.append(t)
            else:
                rows = []
                for k in (0, 2):
                    cells = []
                    for im in imgs[k:k + 2]:
                        data = cropped_image(im["data"], 4, 3)
                        cells.append(RLImage(io.BytesIO(data), width=86 * mm,
                                             height=64.5 * mm))
                    rows.append(cells)
                t = Table(rows, colWidths=[86 * mm, 86 * mm],
                          spaceBefore=1 * mm, spaceAfter=1 * mm)
                t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 2),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
                story.append(t)
        story.append(Spacer(1, 6 * mm))

    from reportlab.platypus import SimpleDocTemplate
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm,
                            rightMargin=18 * mm, topMargin=20 * mm,
                            bottomMargin=18 * mm,
                            title="CUVAS Newsletter - %s" % ed["label"],
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


@app.route("/")
def home():
    ed = get_or_create_current_edition()
    items = news_for_edition(ed["id"])
    return render_template("home.html", ed=ed, items=items,
                           editions=list_editions(), uni=UNI_NAME)


@app.route("/edition/<int:eid>")
def view_edition(eid):
    ed = get_edition(eid)
    if not ed:
        abort(404)
    items = news_for_edition(eid)
    return render_template("edition.html", ed=ed, items=items,
                           editions=list_editions(), uni=UNI_NAME)


@app.route("/edition/<int:eid>/pdf")
def edition_pdf(eid):
    ed = get_edition(eid)
    if not ed:
        abort(404)
    items = news_for_edition(eid)
    data = build_pdf(ed, items)
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
            return redirect(url_for("admin_home"))
        err = "Wrong username or password."
    return render_template("login.html", err=err)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("home"))


@app.route("/admin")
def admin_home():
    if not is_admin():
        return redirect(url_for("admin_login"))
    ed = get_or_create_current_edition()
    items = news_for_edition(ed["id"])
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
    return render_template("admin.html", ed=ed, items=items, logs=log_list,
                           editions=list_editions())


@app.route("/admin/news", methods=["POST"])
def admin_add_news():
    if not is_admin():
        return redirect(url_for("admin_login"))
    ed = get_or_create_current_edition()
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    event_date = (request.form.get("event_date") or "").strip()
    event_day = (request.form.get("event_day") or "").strip()
    if not title or not body or not event_date:
        return redirect(url_for("admin_home"))
    try:
        d = parse_event_date(event_date)
    except Exception:
        return redirect(url_for("admin_home"))
    if not event_day:
        event_day = day_name_for(d)
    now = datetime.now(timezone.utc).isoformat()
    nid = q_exec("INSERT INTO news (edition_id, title, body, event_date, "
                 "event_day, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (ed["id"], title, body, event_date, event_day, now))
    if IS_PG:
        # q_exec lastrowid is None for Postgres; fetch by newest row.
        row = q_one("SELECT id FROM news WHERE edition_id = ? "
                    "ORDER BY id DESC LIMIT 1", (ed["id"],))
        nid = row[0] if IS_PG else row["id"]
    files = request.files.getlist("images")
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
        q_exec("INSERT INTO news_images (news_id, position, filename, "
               "mime, data) VALUES (?, ?, ?, ?, ?)",
               (nid, pos, f.filename, mime, data))
        pos += 1
    bump_version(ed["id"], "News added: %s" % title[:80])
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
                         "event_day", "created_at"], row))
    else:
        item = dict(row)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        event_date = (request.form.get("event_date") or "").strip()
        event_day = (request.form.get("event_day") or "").strip()
        if title and body and event_date:
            try:
                d = parse_event_date(event_date)
                if not event_day:
                    event_day = day_name_for(d)
                q_exec("UPDATE news SET title = ?, body = ?, "
                       "event_date = ?, event_day = ? WHERE id = ?",
                       (title, body, event_date, event_day, nid))
                bump_version(item["edition_id"], "News edited: %s"
                             % title[:80])
            except Exception:
                pass
        return redirect(url_for("admin_home"))
    item["images"] = images_for_news(nid)
    return render_template("edit.html", item=item,
                           ed=get_edition(item["edition_id"]))


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

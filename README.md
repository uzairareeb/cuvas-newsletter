# CUVAS Newsletter

Professional monthly newsletter system for Cholistan University of
Veterinary and Animal Sciences, Bahawalpur (CUVAS).

Built from the user's supplied reference template (TUF September 2026,
22-page A4 newsletter, reference only). The layout, branding and content
here are CUVAS's own: official CUVAS logo on top, CUVAS indigo
(#2e3192) + gold branding, and the university name in full on the cover.

## How it works

- **One document per month, not one per news.** News is added through a
  simple form and appended below the previous news in the same monthly
  edition, numbered sequentially in addition order: 1, 2, 3, ...
- **Add News form:** title, body text, event date (day is auto-derived
  from the date, e.g. Monday, and can be corrected), and 1-4 images
  (2-4 recommended). The system places the images for you: the first
  image is the main image, the rest go into a gallery grid, auto-cropped
  to the template slots. No manual layout work.
- **New version every time:** every add / edit / delete bumps the
  edition version. **Download PDF** always gives the latest version,
  named e.g. `CUVAS-Newsletter-October-2026-v3.pdf`.
- **Monthly archive:** at month end the whole month's newsletter is
  saved separately in the system as its own archived edition
  (e.g. October 2026). The new month then starts fresh, numbering from
  1 again. Archived months stay saved and downloadable any time.
  Rollover is automatic when the calendar month changes (Asia/Karachi),
  and the admin can also close a month early with the Close Month button.
- **Volume on top:** the PDF cover and every page header show
  `Volume X • Issue Y • Month Year`. The first published edition is
  Volume 1, Issue 1; each new month increments the Issue, and after
  12 issues the Volume increments (Volume 2, Issue 1, and so on).

## Pages

- `/` — public: current edition (numbered news + PDF download) and
  the monthly archive list.
- `/edition/<id>` — public view of one edition (current or archived).
- `/edition/<id>/pdf` — PDF download for that edition.
- `/admin` — admin dashboard (login required): add / edit / delete
  news, close the month, download the PDF, see the version.
- `/admin/login` — admin sign-in.

## Admin login

- Username: `admin`
- Password: the `ADMIN_PASSWORD` environment variable, or `Admin@123`
  if it is not set. **Change it in production** by setting
  `ADMIN_PASSWORD` in Vercel (Project Settings → Environment Variables)
  and redeploying. The default is a demo convenience, same pattern as
  the MY CUVAS demo logins.

## Running locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5056
```

Local data uses SQLite (`cuvas_newsletter.db` in the project folder).

## Deploying on Vercel

- Framework Preset: **Flask**. `vercel.json` is `{}` on purpose, the
  same as MY CUVAS — Vercel detects `app.py` at the repo root.
- **Persistence:** on Vercel, set up a Postgres database via
  Vercel Storage → Neon integration, exactly as MY CUVAS does. Vercel
  then sets `DATABASE_URL` automatically and the app uses Postgres
  (images stored as BYTEA, news as rows). Without a database, the app
  still runs, but serverless storage is ephemeral and added news may
  not survive across instances — connect Neon before relying on it
  for real monthly editions. No separate Neon login is needed; use
  the Vercel route, not neon.tech.
- **No literal `%` in SQL text.** LIKE patterns (none are used here)
  must be bound as parameters — a stray `%` crashes Postgres at
  import/startup while SQLite tolerates it (lesson from MY CUVAS,
  2026-10-25).

## Files

- `app.py` — Flask app, DB (SQLite locally / Postgres via
  `DATABASE_URL`), admin, PDF generation (ReportLab + Pillow).
- `cuvas_logo.py` — official CUVAS logo as base64 text (binary files
  cannot be pushed through the GitHub API path, same pattern as
  MY CUVAS `brand_logo.py`). Logo bytes are used exactly as supplied,
  never modified.
- `templates/` — Jinja templates, `static/style.css` — hand-written CSS.

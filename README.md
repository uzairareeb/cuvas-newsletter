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
- **PDF layout (v2, 2026-10-26):** page 1 is a cover page (logo,
  university name, NEWSLETTER, Volume/Issue/Month, photo collage),
  page 2 is a styled Table of Contents (numbered titles + dates),
  then each news gets a dark indigo title banner (number in gold,
  white title) and its images are interleaved through the text —
  first image large at the start, one photo at the side mid-text,
  the rest spread through the middle, last photo at the end —
  instead of all photos grouped together.
- **Cover images:** admin can upload up to 4 cover images per
  edition (Admin → Cover page images). If none are set, the cover
  collage falls back to the first images of the news items.
- **Web UI (v3, 2026-10-26):** split-screen login (indigo gradient
  welcome panel + white sign-in card, CUVAS logo, Show password,
  functional Remember me for 30 days; deliberately NO sign-up or
  forgot-password links, as no such flows exist), cards with
  fade-up/hover animations, styled confirmation popups for Delete
  and Close Month (no browser confirm dialogs), and news/cover
  photos shown properly on the public, edition and admin pages
  via `/image/news/<id>` and `/image/cover/<id>` (click a photo to
  open it full-size), instead of filename text only.
- **Theme + layouts (v4, 2026-10-26):** indigo stays, gold is replaced
  by academic green `#15803d` (dates in maroon, orange only as a
  small highlight) on web and in the PDF. **4 image layouts rotate
  per news** in both the PDF and the web pages: 1 Classic
  (interleaved, side photo, last at end), 2 Gallery (text first,
  then a grid), 3 Side story (first paragraph beside the first
  photo, rest as a strip), 4 Magazine (hero, half text, strip,
  rest, last large) — news 5 reuses layout 1, news 6 layout 2, and
  so on. Admin now has a MY CUVAS-style left sidebar (logo band,
  Administrator profile with avatar + Sign out, grouped links) and
  every admin block is its own highlighted section card (Add news,
  Cover, News list, Month, Version history, All editions); public
  news numbers, version and archive are likewise separate
  highlighted pills/sections.
- **Save robustness:** web pages and the version counter never load
  photo blobs (filenames/counts only); only PDF generation loads
  them. A failed Postgres query rolls back instead of poisoning
  the shared connection, and a failed save shows a clear message
  asking the admin to check the list first (to avoid a duplicate
  after a slow cold-start response).
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


## V5 — CUVAS Newsletter Studio (2026-10-26)
- **New identity:** "CUVAS Newsletter Studio", deep academic blue
  `#18335A` + `#315D93` + restrained purple `#7255A7`, white and
  light neutral grey, Inter for the interface, editorial serif
  headings in the publication. The official CUVAS logo is unchanged
  (no AI-generated replacement).
- **Single-page dashboard:** header with logo, edition pill,
  notifications (latest version-log actions, no invented unread
  count) and Administrator profile; summary cards (total news,
  added this week, edition status, archived editions); monthly
  publication progress (news + cover + versioned); actions Add
  News/Event (drawer), Preview Newsletter, Export Newsletter (PDF),
  Edition Archive; recent-news table with thumbnail, title,
  category, event date, date added, serial, status and record ID;
  search, category filter, month selector and sorting.
- **News entry (drawer):** title, category (8 options), date with
  auto day, short summary, full content, venue, department,
  participants, source/credit, 2–4 photos with preview, reorder,
  captions and a designated cover image, upload progress, Save
  Draft vs Add to Current Edition. Drafts stay out of the public
  edition, preview and PDF until published.
- **Newsletter ("CUVAS Chronicle"):** masthead, cover story
  (first published item, teaser on the cover, full story inside
  its section), Contents, then sections in order — University
  Highlights, Academic Activities, Research, Achievements, Events,
  Announcements, Student Activities, Other News — A4 portrait,
  multi-page, running header/footer and page numbers.
- **Editions:** one cumulative edition per month (duplicate
  creation guarded), serial numbers for published items only,
  record IDs kept separate, stages Draft / Ready for Review /
  Published / Archived, month-end archiving preserves content,
  photos and PDFs.
- **Additive-only data changes:** new nullable columns (category,
  summary, venue, department, participants, source_credit,
  is_draft, image caption/is_cover, edition stage) are added by
  migration; existing rows and tables are never dropped, and old
  news defaults to University News with its first photo as cover.

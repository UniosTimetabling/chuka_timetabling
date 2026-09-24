# Operations Matrix — desktop app update

## What this contains
Only the two apps that changed: `desktop_sync/` (Django) and `desktop_app/` (Electron).
Drop them into your project, overwriting the existing folders, then:

```bash
# 1. Backend
python manage.py migrate desktop_sync

# 2. Desktop app
cd desktop_app
npm install
npm run build
npm start        # or your usual dist:win / dist:linux
```

## What it does
Every item from `/timetable/dashboard/`'s Operations Matrix now exists in the desktop
app, grouped into the same 3 sections you see on the web page (Main Campus / Extended
Campuses / Supplemental). Open it from the sidebar → **Operations Matrix**.

- **4 items** (Class Timetable, Exam Timetable, Timetable Analysis & Reports, and the
  editors behind them) use the app's existing native, offline-capable panels — unchanged.
- **~35 items** (Lab Timetable, both Auto Schedulers, Publish flows, PDF/CSV exports,
  Dual Campus, Branch Campus, ODEL, Resits, Venues, Scheduler Config, Feedback,
  Challenge Log, Notifications, etc.) open the **real, live server page** in an embedded
  tab inside the app, **already signed in as the current user** — same UI, same
  functionality, because it's the same page the web dashboard uses. These need the
  server to be reachable (they are not offline).

## How the sign-in works
The desktop app already authenticates with its own token API, separate from the site's
normal cookie-based login. To open an embedded page already signed in, without ever
storing your password or a session cookie in the desktop app:

1. Desktop app asks the server (using its existing token) for a one-time code.
2. It opens `/desktop-bridge/?code=...&next=<page>` in the embedded tab.
3. The server checks the code (single-use, expires in 45 seconds), logs that browser
   tab into a normal Django session for the same user, and redirects to the page.

A fresh code is requested every time an embedded tab opens, so nothing long-lived or
reusable is ever exposed. Embedded tabs are also locked to your own server's origin —
they can't navigate anywhere else; external links open in the OS browser instead.

## Known approximations
A few dashboard entries are buttons on another page rather than pages of their own:
- **Publish Regular / Publish Exam (Official)** → opens `/timetable/dashboard/` itself
  (that's where those publish buttons live).
- **Publish Class / Publish Exam** under Branch Campus and ODEL → open the corresponding
  Auto Scheduler page (`/auto/`, `/odel/auto/`), where those publish actions live.
- **Publish Resit Timetable** → opens `/resits/manual/`, where that action lives.

If any of these actually have their own dedicated page in your build that I didn't spot
in `urls.py`, tell me the URL and I'll point that one item at it directly — it's a
one-line change in `desktop_app/src/renderer/operationsMatrixData.ts`.

## Verified before handoff
- `npm run build` (renderer + main) — succeeds, no errors
- `npx tsc --noEmit` on both the renderer and main tsconfigs — clean
- Django files — syntax-checked; migration follows the existing style in
  `desktop_sync/migrations/`

**Not yet verified:** an actual end-to-end run against your live server (I don't have
access to it). Please run `python manage.py migrate`, start the server, and click
through a handful of Operations Matrix items — especially the ones under "Known
approximations" above — before rolling this out to your team.

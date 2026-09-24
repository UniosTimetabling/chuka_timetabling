# Chuka University Timetable App

A React Native (Expo) mobile app that gives Chuka University students and
lecturers a personalized, always-current timetable on their phone — synced
from the university's timetabling system, cached for offline use, and
backed by local reminder notifications.

## What it does

**Login.** On first launch, a person picks their role and logs in with
just what they'd already know: students enter their registration number,
lecturers enter their name. There's no password to set up — the app asks
the backend "who is this?" and gets back a timetable to show. Because the
Chuka system schedules by cohort rather than by individual student, a
student's login doesn't return a personal name — it returns their cohort
("BSc Computer Science — Year 2"), and everyone in that cohort shares the
same base timetable. Lecturers do have individual records, so they see
their own name.

**Timetable view.** The main screen is a matrix: venues down the side,
timeslots across the top, and the course/lecturer shown wherever the two
intersect for that day. A tab switches between the recurring weekly class
timetable and a separate date-based exam timetable once one is published.

**Adding personal courses.** A student or lecturer can search the course
catalog by code (e.g. "COMS101") and add sections to their own timetable —
useful for electives or extra sections not already on their default
schedule. Search matching is lenient about spacing, case, and section
letters. Added courses that don't have a venue/time slot assigned yet
still show up in "my courses" but won't appear on the grid until the
university schedules them a slot.

**Offline-first sync.** The full timetable, exam timetable, and events are
cached on-device with AsyncStorage, so the app is fully usable with no
signal. Whenever the phone is online, the app pings a lightweight
"version" endpoint — a content hash of the current timetable — and only
re-downloads the full data if that hash actually changed. This check runs
on login, every 15 minutes while the app is open, and immediately whenever
the connection comes back online after being offline. Because the
university's server is only reachable on the campus network, "can't sync"
almost always just means "not on campus / no data" — the app says this
directly instead of showing a generic error.

**Events & memos.** A separate feed for announcements (e.g. "CAT 1
postponed"), which can carry attachments — images, PDFs, Word docs —
opened via the phone's normal share sheet / file viewer.

**Sharing a timetable, two ways:**
- *Inside the app:* one person shows a QR code, another scans it inside
  the app and gets a live, read-only pull of that person's timetable
  straight from the server — not a stale copy baked into the QR code
  itself. They're offered a save so it stays available offline, and if
  they'd already saved that person's timetable before, they can choose to
  replace it or merge the new data into what's already saved. Sharing only
  works between two people of the same role (student <-> student, lecturer
  <-> lecturer). Scanning a QR code that isn't from this app is detected,
  and the scanner explains that the app can't read foreign QR codes — the
  export-to-image/PDF route below is how to receive a timetable from
  outside the app.
- *Outside the app:* the class and exam timetable can be rendered as a PNG
  image or a PDF and handed to the phone's normal share sheet — for
  WhatsApp, email, Bluetooth, or anywhere else that just needs a plain
  file, no scanning required.

**Notifications**, scheduled locally on the device (no server push
needed):
- A reminder a configurable number of minutes before each class.
- A morning digest listing the day's full class list, plus a rotating
  motivational message.
- Notifications are automatically rebuilt every time a sync brings in a
  changed timetable, so they never go stale or double up.

**Feedback.** A simple in-app form (reachable from the Events screen) lets
a user send feedback or a bug report, with optional attachments, straight
to the university team.

## How the pieces fit together

```
App.js                        Root component — wraps everything in auth +
                               toast providers, mounts the navigator.
src/
  config/config.js            BASE_URL + every backend endpoint, in one
                               place. This is the only file that needs to
                               change to point the app at a different
                               server (dev, staging, production).
  api/api.js                  Thin fetch wrapper — every network call the
                               app makes goes through here.
  context/AuthContext.js      Holds the logged-in user, timetable, exam
                               timetable, and events in memory; restores
                               them from local storage on cold start; owns
                               the sync loop (interval + connectivity
                               triggers) described above.
  services/
    storage.js                AsyncStorage persistence (what's cached and
                               under what keys).
    netStatus.js               Connectivity detection.
    syncManager.js             The actual "check version -> fetch if stale
                               -> save -> reschedule notifications" logic.
    notificationManager.js     Schedules/cancels local notifications.
  navigation/AppNavigator.js  Stack navigation; shows the logged-out flow
                               (Welcome -> RoleSelect -> Login) or the
                               logged-in flow (Timetable, Events, AddCourse,
                               QR share/scan, Saved Timetables, Settings,
                               Feedback) depending on auth state.
  screens/                    One file per screen (see navigator above for
                               the full list).
  components/                 Reusable UI: the timetable grid, day/week
                               views, event cards, sync status bar, toast
                               host, and the export-to-image/PDF view.
  utils/                      Date/timeslot helpers, the QR-import
                               replace/merge logic, and the PDF export
                               builder.
```

The app talks to a Django backend (a `mobile_api` app inside the main
Chuka timetabling system) over a documented REST contract — every
endpoint, request/response shape, and edge case (e.g. multiple lecturers
matching one name) is written out in full at the top of
`src/config/config.js`. That file is deliberately the single source of
truth for the backend contract, so the rest of the codebase never
hardcodes a URL.

## Installing it

### For everyday use (students/lecturers, no dev tools needed)

Once a build has been published, installing is just installing a normal
app:
- **From the Play Store** (once published there): search for the app, or
  open the store listing link, and tap Install like any other app.
- **From a direct APK link** (before/without a Play Store listing): open
  the link on the phone, or scan the QR code provided for it. Android will
  ask once to allow installs from that source — approve it, then install
  normally.

### For development (building/running it yourself)

Requires [Node.js](https://nodejs.org) 18+ and either `npm` or `pnpm`.

```bash
npm install          # or: pnpm install
npx expo install     # aligns native module versions with the Expo SDK
npx expo start
```

Scan the QR code Expo prints with the **Expo Go** app (Android/iOS) to run
it on a physical phone, or press `a`/`i` in the terminal to launch an
Android/iOS simulator. Camera-based QR scanning and full notification
scheduling need a physical device or a custom dev client
(`npx expo run:android` / `npx expo run:ios`) — Expo Go alone only
partially supports these.

Before it can log anyone in or show real data, `BASE_URL` in
`src/config/config.js` needs to point at a running instance of the backend
(the `mobile_api` endpoints documented in that same file).

### Packaging a production build (installable APK / Play Store bundle)

See `build.sh` and `PRODUCTION_DEPLOYMENT.md` in this repo — they automate
producing a direct-install APK, a Play Store `.aab`, and submitting
updates to the Play Console.

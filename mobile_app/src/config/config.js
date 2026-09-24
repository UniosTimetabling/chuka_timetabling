// ---------------------------------------------------------------------------
// APP-WIDE CONFIG
// ---------------------------------------------------------------------------
// Replace BASE_URL with the real Chuka University timetable domain once
// it is provisioned (e.g. https://timetable.chuka.ac.ke).
// Everything else in the app is written against the API_CONTRACT documented
// below, so once the backend implements these endpoints, only BASE_URL
// needs to change.
// ---------------------------------------------------------------------------

export const BASE_URL = 'http://127.0.0.1:8001'; // TODO: set real domain

export const ENDPOINTS = {
  studentLogin: `${BASE_URL}/api/mobile/auth/student/`, // POST { regNo }
  lecturerLogin: `${BASE_URL}/api/mobile/auth/lecturer/`, // POST { name }
  timetableVersion: `${BASE_URL}/api/mobile/timetable/version/`, // GET ?userId=&role=
  timetable: `${BASE_URL}/api/mobile/timetable/`, // GET ?userId=&role=
  examTimetableVersion: `${BASE_URL}/api/mobile/exam-timetable/version/`, // GET ?userId=&role=
  examTimetable: `${BASE_URL}/api/mobile/exam-timetable/`, // GET ?userId=&role=
  events: `${BASE_URL}/api/mobile/events/`, // GET ?userId=&role=
  sharedTimetable: `${BASE_URL}/api/mobile/timetable/shared/`, // GET ?token=&viewerRole=
  coursesSearch: `${BASE_URL}/api/mobile/courses/search/`, // GET ?q=&userId=&role=[&regNo=]
  coursesAdd: `${BASE_URL}/api/mobile/courses/add/`, // POST {userId, role, regNo?, allocationIds}
  coursesRemove: `${BASE_URL}/api/mobile/courses/remove/`, // POST {userId, role, regNo?, allocationId}
  coursesMine: `${BASE_URL}/api/mobile/courses/mine/`, // GET ?userId=&role=[&regNo=]
  feedback: `${BASE_URL}/api/mobile/feedback/`, // POST multipart/form-data
  appLink: `${BASE_URL}/api/mobile/app-link/`, // GET — the staff-editable "share the app" link
  install: `${BASE_URL}/api/mobile/install/`, // POST { deviceId, platform, appVersion } — once per device
};

// Bump this whenever you ship a new build. Reported alongside install
// analytics (see services/installTracker.js) so the Mobile Analytics
// dashboard page can show version adoption. Not read automatically from
// app.json/app.config — keeps this file the single source of truth
// without adding an expo-constants dependency just for this.
export const APP_VERSION = '1.0.0';

// How often (ms) to check the server for a newer timetable version while
// the app is in the foreground and online. Kept short so changes made on
// the backend show up while the app is open, without the student needing
// to close and reopen it. Combined with the AppState listener in
// AuthContext (which forces an immediate check the moment the app comes
// back to the foreground), this is what makes updates feel "live".
export const SYNC_INTERVAL_MS = 30 * 1000; // 30 seconds

// Minutes before a class starts that the reminder notification should fire.
export const CLASS_REMINDER_MINUTES = 30;

// Local time (24h) the daily "today's classes" digest notification fires.
export const MORNING_DIGEST_HOUR = 6;
export const MORNING_DIGEST_MINUTE = 30;

// Friendly, actionable copy shown (via toast) whenever a request can't reach
// BASE_URL at all — the domain is only reachable from the university network,
// so this is the #1 real-world cause of a failed fetch (student is off
// campus, or campus Wi-Fi / their mobile data happens to be off).
export const NETWORK_ERROR_MESSAGE =
  "Can't sync right now — you need to be within the university to reach the timetable server. Also double-check your Wi-Fi or mobile data is turned on.";

export const TIMEOUT_ERROR_MESSAGE =
  "The university server is taking too long to respond. Make sure you're within the university and connected, then try again.";

// Feedback attachments — mirrors the backend's own limits
// (mobile_api/views_feedback.py) so the app can reject an oversized/extra
// file locally with a friendly message instead of waiting on a 400.
export const FEEDBACK_MAX_ATTACHMENTS = 5;
export const FEEDBACK_MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024; // 15MB/file

export const STORAGE_KEYS = {
  AUTH: '@chuka/auth',
  TIMETABLE: '@chuka/timetable',
  EXAM_TIMETABLE: '@chuka/examTimetable',
  EVENTS: '@chuka/events',
  LAST_SYNC: '@chuka/lastSync',
  // Map of ownerId -> { owner, ownerId, days, savedAt } for timetables
  // imported from other same-app users via QR scan (see QRScanScreen /
  // SavedTimetablesScreen). Separate from the user's own TIMETABLE key.
  SHARED_TIMETABLES: '@chuka/sharedTimetables',

  // A random ID this device generates once and keeps forever (until the
  // app is uninstalled, which clears AsyncStorage) — see utils/deviceId.js.
  DEVICE_ID: '@chuka/deviceId',
  // Set true once this device has successfully told the backend it's
  // installed — see services/installTracker.js. Kept separate from
  // DEVICE_ID so a request that fails (e.g. offline on first launch) is
  // retried on the next launch instead of silently never reporting.
  INSTALL_REPORTED: '@chuka/installReported',

  // Set true once this device has swiped through the illustrated intro
  // (see screens/OnboardingScreen.js) — shown once ever, before Welcome.
  ONBOARDING_SEEN: '@chuka/onboardingSeen',
  // Set true once this device has dismissed the coach mark pointing at
  // the header menu button (see components/MenuCoachMark.js) — shown
  // once ever, the first time TimetableScreen is reached after login.
  MENU_TOUR_SEEN: '@chuka/menuTourSeen',

  // Last "share the app" link the server told us about, so the invite
  // screen still shows the current link when offline (see hooks/useAppLink.js).
  APP_LINK: '@chuka/appLink',
};

// Identifies the QR payload QRShareScreen encodes as coming from THIS app
// (as opposed to some other, unrelated QR code a camera might pick up).
// Bump if the payload shape ever changes incompatibly.
export const QR_PAYLOAD_KIND = 'chuka-timetable-share';

// FALLBACK ONLY. The real "Share the App" link is set by staff on the web
// dashboard (/mobile/analytics/ → Edit Link) and fetched live from
// ENDPOINTS.appLink by hooks/useAppLink.js — so it can point anywhere (Play
// Store, a direct APK, a Drive link…) and change without a new app release.
// This constant is only used until the first successful fetch on a device
// that has never been online, or if the server has no answer and nothing is
// cached yet.
export const APP_SHARE_URL = 'https://play.google.com/store/apps/details?id=ke.ac.chuka.timetable';

export const buildAppShareMessage = (url) =>
  `Get the Chuka Timetable app to check your classes, exams and events on the go: ${url}`;

/*
===============================================================================
API CONTRACT — implemented by the mobile_api Django app (see backend repo)
===============================================================================
This matches mobile_api/views_auth.py, views_timetable.py, views_events.py,
and views_shared.py exactly — update this block if those change.

IMPORTANT: the Chuka timetabling system doesn't track individual students —
a registration number resolves to a (program, year of study) COHORT via
export_import.student_reg_lookup, and every student in that cohort shares
one timetable (same as the existing PDF-by-reg-number download feature).
So a student's "name" in the login response is a cohort label like
"BSc Computer Science — Year 2", not a personal name. Lecturers DO have
individual records, so lecturer_login returns a real name.

1) POST /api/mobile/auth/student/
   body:  { "regNo": "EB1/66791/23" }
   200:   {
             "user": { "id": "stu:3:12:2", "name": "BSc Computer Science — Year 2",
                        "regNo": "EB1/66791/23", "role": "student" },
             "timetableVersion": "a1b2c3d4e5f60718"
           }
   400:   { "error": "That doesn't look like a registration number..." }
   404:   { "error": "Unknown program code 'EB1'." }

2) POST /api/mobile/auth/lecturer/
   body:  { "name": "Dr. Mwangi" }
   200:   {
             "user": { "id": "lec:45", "name": "Dr. J. Mwangi", "role": "lecturer" },
             "timetableVersion": "a1b2c3d4e5f60718"
           }
   404:   { "error": "No lecturer found matching 'Dr. Mwangi'." }
   300:   { "error": "Multiple lecturers match...", "matches": [{ "id", "name", "department" }] }
   // The app doesn't yet render a disambiguation screen for 300 — see
   // README "next steps". Encourage full/unique names for now.

3) GET /api/mobile/timetable/version/?userId=stu:3:12:2&role=student
   200:   { "version": "a1b2c3d4e5f60718", "lastUpdated": "2026-09-02T10:00:00Z" }
   // `version` is a content hash of the underlying rows — treat it as the
   // source of truth for "did anything change", NOT lastUpdated (the
   // backend has no per-row change timestamp, so lastUpdated is just the
   // time of this check, not the time of the actual change).

4) GET /api/mobile/timetable/?userId=stu:3:12:2&role=student
   200:   {
             "owner": { "name": "BSc Computer Science — Year 2", "role": "student" },
             "version": "a1b2c3d4e5f60718",
             "lastUpdated": "2026-09-02T10:00:00Z",
             "days": [
               {
                 "day": "Monday",
                 "entries": [
                   { "timeSlot": "08:00-09:00", "venue": "LT1", "course": "CS101 - Intro to CS", "lecturer": "Dr. Mwangi" }
                 ]
               }
             ]
           }

4b) GET /api/mobile/exam-timetable/version/?userId=stu:3:12:2&role=student
   200:   { "version": "f00dcafe12345678", "lastUpdated": "2026-09-02T10:00:00Z" }

4c) GET /api/mobile/exam-timetable/?userId=stu:3:12:2&role=student
   200:   {
             "owner": { "name": "BSc Computer Science — Year 2", "role": "student" },
             "version": "f00dcafe12345678",
             "lastUpdated": "2026-09-02T10:00:00Z",
             "dates": [
               {
                 "date": "2026-11-16",
                 "day": "Monday",
                 "entries": [
                   { "timeSlot": "08:00-09:00", "venue": "LT1", "course": "CS101 - Intro to CS", "lecturer": "Dr. Mwangi" }
                 ]
               }
             ]
           }
   // Same shape/versioning contract as the regular timetable (3/4 above),
   // just grouped by calendar "date" (exam dates aren't a recurring weekly
   // grid) with the weekday label carried alongside each date group.

5) GET /api/mobile/events/?userId=stu:3:12:2&role=student
   200:   [
             {
               "id": "7",
               "title": "CAT 1 Postponed",
               "description": "CS101 CAT 1 moved to next Friday.",
               "type": "memo",
               "date": "2026-09-05T00:00:00Z",
               "attachments": [
                 { "name": "memo.pdf", "url": "https://.../media/mobile_announcements/7/memo.pdf", "mimeType": "application/pdf" }
               ]
             }
           ]

6) GET /api/mobile/timetable/shared/?token=<qr-json>&viewerRole=<student|lecturer>
   // token is the JSON payload QRShareScreen.js encoded, url-encoded.
   // POLICY: same-role sharing only.
   200:   same shape as (4), read-only, no login required.
   403:   { "error": "You can only view timetables from your own role (student/lecturer)." }

7) GET /api/mobile/courses/search/?q=coms101,PHYS 342-a&userId=stu:3:12:2&role=student[&regNo=EB1/66791/23]
   // `q` accepts one or several course codes, comma-separated, and is
   // matched leniently (spacing/case/separators/section-letter suffixes
   // all normalize the same way) — see mobile_api/course_search.py.
   // A bare base code ("COMS 101") returns every section under it, so a
   // lecturer co-teaching one of several sections can search the base
   // code and pick theirs. userId/role/regNo are optional here — only
   // used to flag `isAdded` on results already on the person's list.
   200:   {
             "results": [
               { "id": 501, "courseCode": "COMS 101-D", "courseName": "Intro to Programming",
                 "lecturer": "Dr. Mwangi", "lecturerId": 45, "department": "Computer Science",
                 "program": "BSc Computer Science", "year": 1, "students": 32,
                 "isCombinedGroup": false, "matchedTerm": "coms101", "isAdded": false }
             ],
             "notFound": ["PHYS 342-a"]
           }
   400:   { "error": "Provide at least one course code in 'q' (comma-separate several)." }

8) POST /api/mobile/courses/add/   body: { "userId": "stu:3:12:2", "role": "student",
                                            "regNo": "EB1/66791/23", "allocationIds": [501, 502] }
   // regNo required for role=student, not needed for role=lecturer.
   200:   { "added": [501], "alreadyAdded": [502], "invalid": [] }
   400:   { "error": "Please include your registration number (regNo) to manage your added courses." }

9) POST /api/mobile/courses/remove/  body: { "userId": "stu:3:12:2", "role": "student",
                                              "regNo": "EB1/66791/23", "allocationId": 501 }
   200:   { "removed": true }

10) GET /api/mobile/courses/mine/?userId=stu:3:12:2&role=student[&regNo=EB1/66791/23]
   200:   {
             "courses": [
               { "allocationId": 501, "courseCode": "COMS 101-D", "courseName": "Intro to Programming",
                 "lecturer": "Dr. Mwangi", "addedAt": "2026-09-02T10:00:00Z", "scheduled": true }
             ]
           }
   // `scheduled` is false when the course has been added here but doesn't
   // yet have a venue/time slot on the timetable — search (7) doesn't
   // filter that out, so it's addable before it's placed. A false entry
   // here explains why it won't show up in (4)'s merged "days" yet (see
   // NOTE below) — it isn't a bug, there's just nothing to place on the
   // grid until the schedule assigns it one.

NOTE: personal additions (7-10) only show up inside the main timetable (4)
once `regNo` is also passed on THAT request for a student — see
mobile_api/views_timetable.py. Lecturers don't need it there either;
their userId is already individual. Each merged-in entry in (4)'s "days"
is tagged `"personal": true` so the app can badge it as self-added. An
added course only ever appears there once it's `scheduled` (see (10)) —
until then it's real and saved, just not on the grid yet.

11) POST /api/mobile/feedback/   (multipart/form-data)
   fields: full_name, email, message (>= 10 chars), admission_number?,
           userId?, role?, attachments? (repeat the field per file — up
           to 5 files, 15MB each; see FEEDBACK_MAX_ATTACHMENTS above)
   200:   { "ok": true, "message": "Thank you! ...", "id": 42 }
   400:   { "ok": false, "error": "Message is too short. ..." }
   // userId/role are best-effort — an invalid or missing pair never blocks
   // submission, it just means the feedback isn't tagged with a verified
   // student/lecturer identity on the staff side. full_name/email/message
   // are what actually matter and are typed in by the person.

12) POST /api/mobile/install/   body: { "deviceId": "<uuid-ish string>",
                                         "platform": "android|ios|web|other",
                                         "appVersion": "1.0.0" }
   // Called once per device (see services/installTracker.js), right after
   // first launch post-install. Public, no auth. Safe to call again later
   // (e.g. next launch before the "already reported" flag is set) —
   // idempotent on deviceId, never creates a second row.
   200:   { "ok": true, "isNewInstall": true }
   400:   { "error": "deviceId is required." }
===============================================================================
*/

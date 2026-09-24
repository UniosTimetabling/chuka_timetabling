# Production Deployment Guide

`build.sh` handles the build pipeline. A few things only need doing once,
by hand, and can't be scripted (Google requires human sign-off on these).

## 0. Before your first build at all

In `src/config/config.js`, change:
```js
export const BASE_URL = 'http://127.0.0.1:8001';
```
to your real, publicly reachable domain, e.g. `https://timetable.chuka.ac.ke`
(must be HTTPS — Android blocks plain HTTP by default in production builds).
`./build.sh aab` and `./build.sh submit` will refuse to run until this is
changed; `./build.sh apk` will still work for local testing.

## 1. One-time accounts

- **Expo/EAS account** (free): https://expo.dev/signup, then `eas login`.
- **Google Play Developer account** ($25 one-time): https://play.google.com/console/signup

## 2. Getting an installable APK onto a phone (no Play Store needed)

```bash
./build.sh apk
```
EAS builds it in the cloud and prints a download link + QR code. Scan the QR
code with the phone's camera, or send the link — Android will ask to allow
installs from that source once, then install normally. Good for testing,
demos, or distributing to staff before you're on the Play Store.

## 3. First-ever Play Store upload (manual, one time)

Google requires the *first* upload of an app to go through the Play Console
UI — after that, `eas submit` can automate every future update.

1. Build the bundle: `./build.sh aab`
2. In Play Console: **Create app** → fill in name/category/free-or-paid.
3. Complete the mandatory sections before Google will let you publish:
   - Store listing (screenshots, description, icon — 512×512, feature
     graphic 1024×500)
   - Privacy policy URL (required even for a free internal app)
   - Content rating questionnaire
   - Data safety form (what data the app collects — registration numbers,
     device identifiers for notifications, etc.)
   - App access (if login is required, provide test credentials for review)
4. Go to **Testing → Internal testing** (fastest way to get a real release
   out) → **Create release** → upload the `.aab` from step 1 → add testers
   by email → roll out.
5. Once you're happy, promote the same release to **Production** from the
   Play Console, or set up **Closed/Open testing** first if you want a wider
   beta.

## 4. Automating every update after that

1. In Play Console → **Setup → API access** → create a service account,
   grant it **Release manager** permission, download its JSON key.
2. Save it as `google-play-service-account.json` in the project root
   (already gitignored — never commit this file).
3. From then on, shipping an update is:
   ```bash
   # bump "version" in app.json, e.g. 1.0.0 -> 1.0.1
   ./build.sh aab
   ./build.sh submit
   ```
   `eas.json` has `autoIncrement: true`, so the internal Android
   `versionCode` / iOS `buildNumber` go up automatically — you only manage
   the human-readable `version` string.
4. `eas submit` uploads as a **draft** on the **internal** track (set in
   `eas.json`) so nothing goes live without you reviewing it in Play
   Console first. Change `track`/`releaseStatus` in `eas.json` once you
   trust the pipeline (e.g. `"track": "production", "releaseStatus": "completed"`
   for fully automated releases).

## 5. Faster JS-only updates (optional, no store review)

For changes that are pure JS/asset changes (not native module additions),
you can skip the store review entirely using EAS Update:
```bash
npx eas update --branch production --message "Fix timetable sync bug"
```
Users get the update the next time they open the app — no APK reinstall,
no Play Store review. Only use this for JS-level fixes; anything touching
native permissions/modules still needs a full `aab` build + store review.

## 6. iOS (only if you ever need it)

`./build.sh ios` builds an `.ipa` via EAS. You'll additionally need an
Apple Developer account ($99/yr) and `eas submit --platform ios` to push to
TestFlight/App Store — same shape as Android, one extra manual first-time
setup in App Store Connect.

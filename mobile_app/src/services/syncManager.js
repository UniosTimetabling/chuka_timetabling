import { api } from '../api/api';
import { storage } from './storage';
import { isOnline } from './netStatus';
import { rescheduleAllNotifications, notifyDataChanged } from './notificationManager';

// Events don't carry their own version hash (see config.js API_CONTRACT —
// only the timetable/exam-timetable endpoints do), so "did events change"
// is worked out here by comparing id sets between what was cached and
// what the server just returned.
//
// `oldEvents === null` means this device has never synced events before
// (fresh install / just logged in) — in that case everything is "new" in
// the storage sense, but none of it should trigger a notification, or
// the very first sync would fire one notification per existing event.
function diffEventIds(oldEvents, newEvents) {
  if (oldEvents === null || oldEvents === undefined) {
    return { changed: (newEvents || []).length > 0, added: [] };
  }
  const oldIds = new Set(oldEvents.map((e) => String(e.id)));
  const added = (newEvents || []).filter((e) => !oldIds.has(String(e.id)));
  const changed = oldIds.size !== (newEvents || []).length || added.length > 0;
  return { changed, added };
}

// Central place that answers: "is the domain reachable, and if so, is our
// locally cached timetable stale?" If stale, it re-fetches timetable +
// events and updates local storage + notifications.
//
// Always re-checks events too (they have no version hash of their own),
// so a newly-posted announcement/memo is caught even on a run where the
// timetable itself hasn't changed.
//
// Returns a status object the UI can use to show sync state:
// { synced: boolean, updated: boolean, reason?: string }
export async function syncNow({ userId, role, regNo }) {
  const online = await isOnline();
  if (!online) {
    return { synced: false, updated: false, reason: 'offline' };
  }

  try {
    const localTimetable = await storage.getTimetable();
    const localExamTimetable = await storage.getExamTimetable();
    const localEvents = await storage.getEvents();
    const [serverVersionInfo, serverExamVersionInfo, freshEvents] = await Promise.all([
      // regNo lets a student's personal course additions (mobile_api
      // PersonalCourseEntry, added via the "Add a course" screen) merge
      // into the version hash/timetable below — see api.js/config.js.
      api.getVersion(userId, role, regNo),
      api.getExamVersion(userId, role),
      api.getEvents(userId, role),
    ]);

    // Nothing cached at all yet (fresh install, or first sync right after
    // login) — this run is populating local storage for the first time,
    // not reacting to a real change, so it shouldn't trigger a "something
    // changed" push notification below.
    const isFirstSyncEver = !localTimetable && !localExamTimetable && localEvents === null;

    const isStale =
      !localTimetable || localTimetable.version !== serverVersionInfo.version;
    const isExamStale =
      !localExamTimetable || localExamTimetable.version !== serverExamVersionInfo.version;
    const { changed: eventsChanged, added: newEvents } = diffEventIds(localEvents, freshEvents);

    if (!isStale && !isExamStale && !eventsChanged) {
      await storage.saveLastSync(new Date().toISOString());
      return { synced: true, updated: false };
    }

    const [freshTimetable, freshExamTimetable] = await Promise.all([
      isStale ? api.getTimetable(userId, role, regNo) : Promise.resolve(localTimetable),
      isExamStale ? api.getExamTimetable(userId, role) : Promise.resolve(localExamTimetable),
    ]);

    await storage.saveTimetable(freshTimetable);
    await storage.saveExamTimetable(freshExamTimetable);
    await storage.saveEvents(freshEvents);
    await storage.saveLastSync(new Date().toISOString());
    await rescheduleAllNotifications(freshTimetable);

    // Fire an immediate, one-off "something changed" notification — as
    // opposed to rescheduleAllNotifications above, which only manages the
    // recurring class-reminder/morning-digest alarms. This is what lets
    // the person know new data arrived without having to have the app
    // open and looking at the screen. Skipped on the very first sync
    // (see isFirstSyncEver above) so logging in doesn't itself look like
    // a "change".
    if (!isFirstSyncEver) {
      await notifyDataChanged({ timetableChanged: isStale, examChanged: isExamStale, newEvents });
    }

    return { synced: true, updated: true, timetableChanged: isStale, examChanged: isExamStale, newEvents };
  } catch (e) {
    const reason = e.isNetworkError ? 'network' : e.isTimeout ? 'timeout' : 'error';
    return { synced: false, updated: false, reason, message: e.message };
  }
}

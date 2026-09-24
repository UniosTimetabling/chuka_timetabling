import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';
import {
  CLASS_REMINDER_MINUTES,
  MORNING_DIGEST_HOUR,
  MORNING_DIGEST_MINUTE,
} from '../config/config';
import { DAY_ORDER, parseTimeSlot } from '../utils/dateHelpers';
import { getRandomMotivation } from '../utils/motivationalQuotes';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export async function requestNotificationPermissions() {
  const { status: existing } = await Notifications.getPermissionsAsync();
  let finalStatus = existing;
  if (existing !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('class-reminders', {
      name: 'Class Reminders',
      importance: Notifications.AndroidImportance.HIGH,
    });
    await Notifications.setNotificationChannelAsync('morning-digest', {
      name: 'Morning Digest',
      importance: Notifications.AndroidImportance.DEFAULT,
    });
    await Notifications.setNotificationChannelAsync('live-updates', {
      name: 'Timetable & Event Updates',
      importance: Notifications.AndroidImportance.HIGH,
    });
  }
  return finalStatus === 'granted';
}

// Fires a one-off, right-now notification whenever a sync (see
// syncManager.js) actually pulled down something new. Unlike
// rescheduleAllNotifications below (which only manages the recurring
// class-reminder/morning-digest alarms), this is the "something on your
// timetable/events just changed" push — trigger: null means expo-notifications
// shows it immediately instead of scheduling it for later.
export async function notifyDataChanged({ timetableChanged, examChanged, newEvents }) {
  const messages = [];

  if (timetableChanged) messages.push('Your class timetable has been updated.');
  if (examChanged) messages.push('Your exam timetable has been updated.');

  if (newEvents && newEvents.length === 1) {
    messages.push(`New ${newEvents[0].type || 'announcement'}: ${newEvents[0].title}`);
  } else if (newEvents && newEvents.length > 1) {
    messages.push(`${newEvents.length} new announcements posted.`);
  }

  if (messages.length === 0) return;

  await Notifications.scheduleNotificationAsync({
    content: {
      title: messages.length === 1 ? 'Timetable update' : 'Timetable & event updates',
      body: messages.join(' '),
      sound: true,
      data: { kind: 'live-update' },
    },
    // `trigger: null` fires instantly on iOS/Android but expo-notifications
    // has no way to attach an Android channel to a null trigger, so instead
    // fire it 1 second out (still effectively "now" to the user) with the
    // channel set — same pattern the scheduled reminders above use.
    trigger: Platform.OS === 'android' ? { seconds: 1, channelId: 'live-updates' } : null,
  });
}

// Cancels every notification this app previously scheduled, then reschedules
// from scratch based on the freshly-synced timetable. Called after every
// successful sync so notifications always reflect the latest timetable.
export async function rescheduleAllNotifications(timetable) {
  await Notifications.cancelAllScheduledNotificationsAsync();
  if (!timetable || !timetable.days) return;

  await scheduleClassReminders(timetable);
  await scheduleMorningDigests(timetable);
}

// expo-notifications weekday trigger: Sunday=1 ... Saturday=7
function toExpoWeekday(dayName) {
  const map = {
    Sunday: 1,
    Monday: 2,
    Tuesday: 3,
    Wednesday: 4,
    Thursday: 5,
    Friday: 6,
    Saturday: 7,
  };
  return map[dayName];
}

// Subtracts minutes from an {hour, minute} pair, returning the (possibly
// previous-day) resulting hour/minute plus how many days it rolled back.
function subtractMinutes(hour, minute, minutesToSubtract) {
  let total = hour * 60 + minute - minutesToSubtract;
  let dayShift = 0;
  while (total < 0) {
    total += 24 * 60;
    dayShift -= 1;
  }
  return { hour: Math.floor(total / 60), minute: total % 60, dayShift };
}

function shiftWeekday(expoWeekday, dayShift) {
  // expoWeekday is 1..7 (Sun..Sat)
  let idx = expoWeekday - 1 + dayShift;
  idx = ((idx % 7) + 7) % 7;
  return idx + 1;
}

async function scheduleClassReminders(timetable) {
  for (const day of timetable.days) {
    if (!DAY_ORDER.includes(day.day)) continue;
    const baseWeekday = toExpoWeekday(day.day);

    for (const entry of day.entries) {
      const { startHour, startMinute } = parseTimeSlot(entry.timeSlot);
      const { hour, minute, dayShift } = subtractMinutes(
        startHour,
        startMinute,
        CLASS_REMINDER_MINUTES
      );
      const weekday = shiftWeekday(baseWeekday, dayShift);

      await Notifications.scheduleNotificationAsync({
        content: {
          title: `Class in ${CLASS_REMINDER_MINUTES} minutes`,
          body: `${entry.course} at ${entry.venue} (${entry.timeSlot})`,
          sound: true,
        },
        trigger: {
          weekday,
          hour,
          minute,
          repeats: true,
          channelId: 'class-reminders',
        },
      });
    }
  }
}

async function scheduleMorningDigests(timetable) {
  for (const day of timetable.days) {
    if (!DAY_ORDER.includes(day.day) || day.entries.length === 0) continue;

    const sortedEntries = [...day.entries].sort((a, b) => {
      const pa = parseTimeSlot(a.timeSlot);
      const pb = parseTimeSlot(b.timeSlot);
      return pa.startHour * 60 + pa.startMinute - (pb.startHour * 60 + pb.startMinute);
    });
    const list = sortedEntries
      .map((e) => `${e.timeSlot} ${e.course} @ ${e.venue}`)
      .join('\n');

    await Notifications.scheduleNotificationAsync({
      content: {
        title: `Good morning! Today (${day.day}) you have ${day.entries.length} class(es)`,
        body: `${list}\n\n${getRandomMotivation()}`,
        sound: true,
      },
      trigger: {
        weekday: toExpoWeekday(day.day),
        hour: MORNING_DIGEST_HOUR,
        minute: MORNING_DIGEST_MINUTE,
        repeats: true,
        channelId: 'morning-digest',
      },
    });
  }
}

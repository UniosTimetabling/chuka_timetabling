export const DAY_ORDER = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];

export function todayName() {
  return DAY_ORDER[(new Date().getDay() + 6) % 7]; // JS: Sun=0 -> map to our order
}

// "08:00-09:00" -> { startHour: 8, startMinute: 0, endHour: 9, endMinute: 0 }
export function parseTimeSlot(timeSlot) {
  const [start, end] = timeSlot.split('-').map((s) => s.trim());
  const [sh, sm] = start.split(':').map(Number);
  const [eh, em] = (end || start).split(':').map(Number);
  return { startHour: sh, startMinute: sm || 0, endHour: eh, endMinute: em || 0 };
}

// Returns a Date object for the next occurrence of a given day+time from now.
// dayName must be one of DAY_ORDER.
export function nextOccurrence(dayName, hour, minute) {
  const now = new Date();
  const targetDow = DAY_ORDER.indexOf(dayName); // 0=Mon..6=Sun
  const currentDow = (now.getDay() + 6) % 7;
  let daysAhead = targetDow - currentDow;
  const candidate = new Date(now);
  candidate.setHours(hour, minute, 0, 0);
  if (daysAhead < 0 || (daysAhead === 0 && candidate <= now)) {
    daysAhead += 7;
  }
  candidate.setDate(now.getDate() + daysAhead);
  candidate.setHours(hour, minute, 0, 0);
  return candidate;
}

// Exam timetable entries are grouped by calendar date (see
// mobile_api/timetable_builder.py exam_serialize_timetable), each date
// carrying its weekday label alongside it — turn that into a short
// "Mon 15 Sep" row heading for the shared WeekTimetable grid, which
// otherwise expects a "day" label per section.
export function formatExamDateLabel(isoDate, dayName) {
  const d = new Date(`${isoDate}T00:00:00`);
  const dayShort = (dayName || '').slice(0, 3);
  const month = d.toLocaleString('en-US', { month: 'short' });
  return `${dayShort} ${d.getDate()} ${month}`.trim();
}

export function sortTimeSlots(slots) {
  return [...slots].sort((a, b) => {
    const pa = parseTimeSlot(a);
    const pb = parseTimeSlot(b);
    return pa.startHour * 60 + pa.startMinute - (pb.startHour * 60 + pb.startMinute);
  });
}

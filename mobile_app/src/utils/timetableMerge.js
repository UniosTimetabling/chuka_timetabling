// Combines two "days" arrays (the shape returned by GET /timetable and
// /timetable/shared: [{ day, entries: [{ timeSlot, venue, course, lecturer }] }])
// into one, used when a user scans a same-app QR for someone whose
// timetable they already saved before and chooses to MERGE instead of
// REPLACE.
//
// A slot is considered "the same class" if day + timeSlot + venue all
// match, so importing the same person's timetable twice never duplicates
// rows — it just refreshes them. Genuinely different entries (e.g. two
// different people's classes sharing a saved slot) are kept side by side.
export function mergeDays(existingDays = [], incomingDays = []) {
  const dayMap = new Map();

  for (const { day, entries } of existingDays) {
    dayMap.set(day, new Map((entries || []).map((e) => [entryKey(e), e])));
  }

  for (const { day, entries } of incomingDays) {
    if (!dayMap.has(day)) dayMap.set(day, new Map());
    const slotMap = dayMap.get(day);
    for (const entry of entries || []) {
      slotMap.set(entryKey(entry), entry); // incoming wins on exact-slot conflicts
    }
  }

  return Array.from(dayMap.entries()).map(([day, slotMap]) => ({
    day,
    entries: Array.from(slotMap.values()),
  }));
}

function entryKey(entry) {
  return `${entry.timeSlot}__${entry.venue}`;
}

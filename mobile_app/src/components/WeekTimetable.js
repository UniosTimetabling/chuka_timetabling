import React, { useMemo } from 'react';
import { View, Text, ScrollView, StyleSheet, useWindowDimensions } from 'react-native';
import { sortTimeSlots } from '../utils/dateHelpers';
import { COLORS, RADIUS } from '../theme/colors';

// Minimum comfortable width for a slot column and the room column — below
// this, course names genuinely need to wrap onto more lines, so the table
// switches to horizontal scrolling instead of squeezing further.
const MIN_CELL_WIDTH = 150;
const VENUE_COL_WIDTH = 90;
const HEADER_HEIGHT = 34;
const DAY_ROW_HEIGHT = 28;
const TABLE_MARGIN = 12;

// Renders the full week as ONE table: timeslots appear once across the
// top, and every day's venues are listed underneath as their own row
// group, all sharing the same timeslot columns so everything lines up.
// Column width is computed from the available screen width so the table
// fills the whole screen (rather than leaving unused space on wide/web
// viewports) and course names get enough room not to be cut off; it only
// falls back to a fixed minimum + horizontal scroll on narrow screens.
export default function WeekTimetable({ days, entriesByDay, emptyMessage }) {
  const { width: windowWidth } = useWindowDimensions();

  const timeSlots = useMemo(() => {
    const slotSet = new Set();
    days.forEach((day) => {
      (entriesByDay[day] || []).forEach((entry) => slotSet.add(entry.timeSlot));
    });
    return sortTimeSlots(Array.from(slotSet));
  }, [days, entriesByDay]);

  const availableWidth = windowWidth - TABLE_MARGIN * 2;
  const cellWidth =
    timeSlots.length > 0
      ? Math.max(MIN_CELL_WIDTH, (availableWidth - VENUE_COL_WIDTH) / timeSlots.length)
      : MIN_CELL_WIDTH;
  const tableWidth = Math.max(availableWidth, VENUE_COL_WIDTH + cellWidth * timeSlots.length);

  if (timeSlots.length === 0) {
    return (
      <View style={styles.emptyBox}>
        <Text style={styles.emptyEmoji}>—</Text>
        <Text style={styles.emptyText}>{emptyMessage || 'No classes scheduled this week.'}</Text>
      </View>
    );
  }

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator style={{ marginHorizontal: TABLE_MARGIN }}>
      <View style={[styles.tableCard, { width: tableWidth }]}>
        {/* Single header row shared by every day below it */}
        <View style={styles.row}>
          <View style={[styles.headerCell, styles.cornerCell]}>
            <Text style={styles.headerText}>ROOM</Text>
          </View>
          {timeSlots.map((slot) => (
            <View key={slot} style={[styles.headerCell, { width: cellWidth }]}>
              <Text style={styles.headerText}>{slot}</Text>
            </View>
          ))}
        </View>

        {days.map((day) => (
          <DaySection
            key={day}
            day={day}
            entries={entriesByDay[day] || []}
            timeSlots={timeSlots}
            tableWidth={tableWidth}
            cellWidth={cellWidth}
          />
        ))}
      </View>
    </ScrollView>
  );
}

function DaySection({ day, entries, timeSlots, tableWidth, cellWidth }) {
  const { venues, cellMap } = useMemo(() => {
    const venueSet = new Set();
    const map = {};
    entries.forEach((entry) => {
      venueSet.add(entry.venue);
      map[`${entry.venue}__${entry.timeSlot}`] = entry;
    });
    return { venues: Array.from(venueSet).sort(), cellMap: map };
  }, [entries]);

  return (
    <View>
      <View style={[styles.dayBar, { width: tableWidth }]}>
        <Text style={styles.dayBarText}>{day.toUpperCase()}</Text>
      </View>

      {venues.length === 0 ? (
        <View style={[styles.noClassRow, { width: tableWidth }]}>
          <Text style={styles.noClassText}>No classes scheduled</Text>
        </View>
      ) : (
        venues.map((venue) => (
          <View key={venue} style={styles.row}>
            <View style={[styles.venueCell, { width: VENUE_COL_WIDTH }]}>
              <Text style={styles.venueText}>{venue}</Text>
            </View>
            {timeSlots.map((slot) => {
              const entry = cellMap[`${venue}__${slot}`];
              return (
                <View key={slot} style={[styles.cell, { width: cellWidth }]}>
                  {entry ? (
                    <View>
                      {/* No numberOfLines here — course names now get a
                          cell wide enough (see cellWidth above) to wrap
                          fully onto 2-3 lines instead of being cut off
                          with an ellipsis. */}
                      <Text style={styles.courseText}>{entry.course}</Text>
                      {entry.lecturer ? (
                        <Text style={styles.lecturerText} numberOfLines={1}>
                          {entry.lecturer}
                        </Text>
                      ) : null}
                      {entry.personal ? (
                        <Text style={styles.personalBadge}>Added by you</Text>
                      ) : null}
                    </View>
                  ) : null}
                </View>
              );
            })}
          </View>
        ))
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  tableCard: {
    borderWidth: 1,
    borderColor: COLORS.gray300,
    borderRadius: RADIUS.sm,
    overflow: 'hidden',
    marginBottom: 16,
    backgroundColor: COLORS.white,
  },
  row: { flexDirection: 'row' },
  headerCell: {
    height: HEADER_HEIGHT,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.blue,
    borderRightWidth: 1,
    borderColor: 'rgba(255,255,255,0.25)',
  },
  cornerCell: { width: VENUE_COL_WIDTH },
  headerText: { color: COLORS.white, fontWeight: '700', fontSize: 10, letterSpacing: 0.2 },
  dayBar: {
    height: DAY_ROW_HEIGHT,
    justifyContent: 'center',
    paddingHorizontal: 10,
    backgroundColor: COLORS.gray100,
    borderTopWidth: 1,
    borderColor: COLORS.gray300,
  },
  dayBarText: { fontSize: 11, fontWeight: '800', color: COLORS.blue, letterSpacing: 1 },
  venueCell: {
    minHeight: 56,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 4,
    paddingVertical: 8,
    backgroundColor: COLORS.white,
    borderRightWidth: 1,
    borderTopWidth: 1,
    borderColor: COLORS.gray300,
  },
  venueText: { fontWeight: '700', fontSize: 12, color: COLORS.gray800, textAlign: 'center' },
  cell: {
    minHeight: 56,
    justifyContent: 'center',
    paddingHorizontal: 8,
    paddingVertical: 8,
    backgroundColor: COLORS.white,
    borderRightWidth: 1,
    borderTopWidth: 1,
    borderColor: COLORS.gray300,
  },
  courseText: { fontSize: 12, fontWeight: '700', color: COLORS.gray800, flexWrap: 'wrap' },
  lecturerText: { fontSize: 10, color: COLORS.gray500, marginTop: 2 },
  personalBadge: { fontSize: 9, color: COLORS.blue, fontWeight: '700', marginTop: 2 },
  noClassRow: {
    height: 40,
    justifyContent: 'center',
    paddingHorizontal: 10,
    borderTopWidth: 1,
    borderColor: COLORS.gray300,
  },
  noClassText: { color: COLORS.gray500, fontSize: 11, fontStyle: 'italic' },
  emptyBox: { padding: 40, alignItems: 'center' },
  emptyEmoji: { fontSize: 28, color: COLORS.gray300, marginBottom: 6 },
  emptyText: { color: COLORS.gray500, fontSize: 13 },
});

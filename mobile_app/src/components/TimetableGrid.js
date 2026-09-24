import React, { useMemo } from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { sortTimeSlots } from '../utils/dateHelpers';
import { COLORS, RADIUS } from '../theme/colors';

const CELL_WIDTH = 128;
const VENUE_COL_WIDTH = 84;
const ROW_HEIGHT = 52;

// Renders one day's schedule as a plain bordered grid, close to the
// printed university timetable: venues down the left column, timeslots
// across the top row, course shown where a venue's row and a timeslot's
// column intersect. Deliberately low on color/shading — a thin grid of
// lines with a single bold header, like the source PDF.
export default function TimetableGrid({ dayEntries, day }) {
  const { venues, timeSlots, cellMap } = useMemo(() => buildMatrix(dayEntries), [dayEntries]);

  if (venues.length === 0) {
    return (
      <View style={styles.emptyBox}>
        <Text style={styles.emptyEmoji}>—</Text>
        <Text style={styles.emptyText}>No classes scheduled for this day.</Text>
      </View>
    );
  }

  return (
    <View>
      {day ? <Text style={styles.dayHeading}>{day.toUpperCase()}</Text> : null}
      <ScrollView horizontal showsHorizontalScrollIndicator style={{ marginHorizontal: 12 }}>
        <View style={styles.tableCard}>
          {/* Header row: blank corner + timeslot headers */}
          <View style={styles.row}>
            <View style={[styles.headerCell, styles.cornerCell]}>
              <Text style={styles.headerText}>ROOM</Text>
            </View>
            {timeSlots.map((slot) => (
              <View key={slot} style={[styles.headerCell, { width: CELL_WIDTH }]}>
                <Text style={styles.headerText}>{slot}</Text>
              </View>
            ))}
          </View>

          {/* One row per venue */}
          {venues.map((venue) => (
            <View key={venue} style={styles.row}>
              <View style={[styles.venueCell, { width: VENUE_COL_WIDTH }]}>
                <Text style={styles.venueText}>{venue}</Text>
              </View>
              {timeSlots.map((slot) => {
                const entry = cellMap[`${venue}__${slot}`];
                return (
                  <View key={slot} style={[styles.cell, { width: CELL_WIDTH }]}>
                    {entry ? (
                      <View>
                        <Text style={styles.courseText} numberOfLines={2}>
                          {entry.course}
                        </Text>
                        {entry.lecturer ? (
                          <Text style={styles.lecturerText} numberOfLines={1}>
                            {entry.lecturer}
                          </Text>
                        ) : null}
                      </View>
                    ) : null}
                  </View>
                );
              })}
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

function buildMatrix(dayEntries) {
  if (!dayEntries || dayEntries.length === 0) {
    return { venues: [], timeSlots: [], cellMap: {} };
  }
  const venueSet = new Set();
  const slotSet = new Set();
  const cellMap = {};

  dayEntries.forEach((entry) => {
    venueSet.add(entry.venue);
    slotSet.add(entry.timeSlot);
    cellMap[`${entry.venue}__${entry.timeSlot}`] = entry;
  });

  return {
    venues: Array.from(venueSet).sort(),
    timeSlots: sortTimeSlots(Array.from(slotSet)),
    cellMap,
  };
}

const styles = StyleSheet.create({
  dayHeading: {
    fontSize: 12,
    fontWeight: '800',
    color: COLORS.blue,
    letterSpacing: 1,
    marginHorizontal: 16,
    marginBottom: 6,
  },
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
    height: 38,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.blue,
    borderRightWidth: 1,
    borderColor: 'rgba(255,255,255,0.25)',
  },
  cornerCell: { width: VENUE_COL_WIDTH },
  headerText: { color: COLORS.white, fontWeight: '700', fontSize: 10, letterSpacing: 0.2 },
  venueCell: {
    height: ROW_HEIGHT,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 4,
    backgroundColor: COLORS.white,
    borderRightWidth: 1,
    borderTopWidth: 1,
    borderColor: COLORS.gray300,
  },
  venueText: { fontWeight: '700', fontSize: 11, color: COLORS.gray800, textAlign: 'center' },
  cell: {
    height: ROW_HEIGHT,
    justifyContent: 'center',
    paddingHorizontal: 6,
    backgroundColor: COLORS.white,
    borderRightWidth: 1,
    borderTopWidth: 1,
    borderColor: COLORS.gray300,
  },
  courseText: { fontSize: 11, fontWeight: '700', color: COLORS.gray800 },
  lecturerText: { fontSize: 9, color: COLORS.gray500, marginTop: 1 },
  emptyBox: { padding: 40, alignItems: 'center' },
  emptyEmoji: { fontSize: 28, color: COLORS.gray300, marginBottom: 6 },
  emptyText: { color: COLORS.gray500, fontSize: 13 },
});

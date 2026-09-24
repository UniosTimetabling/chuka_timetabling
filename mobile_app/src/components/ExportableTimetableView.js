import React from 'react';
import { View, Text, StyleSheet, Image } from 'react-native';
import { DAY_ORDER, sortTimeSlots, formatExamDateLabel } from '../utils/dateHelpers';
import { COLORS } from '../theme/colors';

const WIDTH = 360;

// A plain, non-scrolling, non-interactive rendering of a timetable, sized
// to a fixed width. It exists purely so QRShareScreen can capture it with
// react-native-view-shot and share it as a single PNG — something any app
// (WhatsApp, email, Gallery...) can open, unlike the interactive
// WeekTimetable/TimetableGrid which scroll and don't fit a snapshot.
//
// Renders the recurring class timetable, and — when present — the
// date-based exam timetable underneath as its own labelled section, so an
// exported image always covers both schedules like the PDF export does.
export default function ExportableTimetableView({ timetable, examTimetable }) {
  const mainSections = (timetable?.days || [])
    .slice()
    .sort((a, b) => DAY_ORDER.indexOf(a.day) - DAY_ORDER.indexOf(b.day))
    .filter((d) => (d.entries || []).length > 0)
    .map((d) => ({ label: d.day, entries: d.entries }));

  const examSections = (examTimetable?.dates || [])
    .filter((d) => (d.entries || []).length > 0)
    .map((d) => ({ label: formatExamDateLabel(d.date, d.day), entries: d.entries }));

  const ownerName = timetable?.owner?.name || examTimetable?.owner?.name || 'Timetable';

  return (
    <View style={styles.page} collapsable={false}>
      <View style={styles.header}>
        <Image
          source={require('../../assets/chuka-logo-circle.png')}
          style={styles.logo}
          resizeMode="contain"
        />
        <View style={{ marginLeft: 10, flexShrink: 1 }}>
          <Text style={styles.name} numberOfLines={2}>{ownerName}</Text>
          <Text style={styles.sub}>Chuka Timetable</Text>
        </View>
      </View>

      <Text style={styles.sectionLabel}>Class Timetable</Text>
      {mainSections.length === 0 ? (
        <Text style={styles.empty}>No classes scheduled.</Text>
      ) : (
        mainSections.map((s) => <DayBlock key={s.label} label={s.label} entries={s.entries} />)
      )}

      {examSections.length > 0 && (
        <>
          <Text style={[styles.sectionLabel, { marginTop: 16 }]}>Exam Timetable</Text>
          {examSections.map((s) => <DayBlock key={s.label} label={s.label} entries={s.entries} />)}
        </>
      )}

      <Text style={styles.footer}>Exported {new Date().toLocaleDateString()}</Text>
    </View>
  );
}

function DayBlock({ label, entries }) {
  const slots = sortTimeSlots(entries.map((e) => e.timeSlot));
  return (
    <View style={styles.dayBlock}>
      <Text style={styles.dayTitle}>{label}</Text>
      {slots.map((slot) => {
        const entry = entries.find((e) => e.timeSlot === slot);
        if (!entry) return null;
        return (
          <View key={slot} style={styles.row}>
            <Text style={styles.slotText}>{slot}</Text>
            <View style={{ flex: 1 }}>
              <Text style={styles.courseText} numberOfLines={2}>{entry.course}</Text>
              <Text style={styles.metaText} numberOfLines={1}>
                {[entry.venue, entry.lecturer].filter(Boolean).join(' · ')}
              </Text>
            </View>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  page: { width: WIDTH, backgroundColor: COLORS.white, padding: 16 },
  header: { flexDirection: 'row', alignItems: 'center', marginBottom: 12 },
  logo: { width: 36, height: 36, borderRadius: 18 },
  name: { fontSize: 15, fontWeight: '800', color: COLORS.black },
  sub: { fontSize: 11, color: COLORS.gray500 },
  sectionLabel: {
    fontSize: 10,
    fontWeight: '800',
    color: COLORS.gray500,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  empty: { fontSize: 12, color: COLORS.gray500, paddingVertical: 12, textAlign: 'center' },
  dayBlock: { marginBottom: 10 },
  dayTitle: {
    fontSize: 12,
    fontWeight: '800',
    color: COLORS.white,
    backgroundColor: COLORS.blue,
    paddingVertical: 4,
    paddingHorizontal: 8,
    borderRadius: 4,
    overflow: 'hidden',
    marginBottom: 4,
  },
  row: {
    flexDirection: 'row',
    paddingVertical: 5,
    paddingHorizontal: 4,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.gray100,
  },
  slotText: { width: 78, fontSize: 10, fontWeight: '700', color: COLORS.blue },
  courseText: { fontSize: 11, fontWeight: '700', color: COLORS.gray800 },
  metaText: { fontSize: 9, color: COLORS.gray500, marginTop: 1 },
  footer: { fontSize: 8, color: COLORS.gray300, textAlign: 'center', marginTop: 6 },
});

import React, { useState, useMemo, useEffect } from 'react';
import { View, Text, Image, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '../context/AuthContext';
import { DAY_ORDER, formatExamDateLabel } from '../utils/dateHelpers';
import SyncStatusBar from '../components/SyncStatusBar';
import WeekTimetable from '../components/WeekTimetable';
import BlockedScheduleNotice from '../components/BlockedScheduleNotice';
import ScheduleTypeTabs from '../components/ScheduleTypeTabs';
import AiPoweredHero from '../components/AiPoweredHero';
import MenuSheet from '../components/MenuSheet';
import MenuCoachMark from '../components/MenuCoachMark';
import { COLORS, RADIUS } from '../theme/colors';

// The screen a person lands on right after logging in. Everything that
// isn't the schedule itself or the promo hero carousel now lives behind
// the menu button in the header (see MenuSheet) instead of a Quick Links
// grid on a separate Home screen.
export default function TimetableScreen({ navigation }) {
  const { user, timetable, examTimetable, syncStatus, refreshNow, logout, menuTourSeen, completeMenuTour } = useAuth();
  const [refreshing, setRefreshing] = useState(false);
  const [menuVisible, setMenuVisible] = useState(false);
  // Which schedule is on screen: 'main' (the recurring weekly timetable)
  // or 'exam' (the published, date-based exam timetable). Local UI state
  // only — both datasets are already synced/cached together (see
  // syncManager.js), so switching tabs is instant and works offline.
  const [scheduleType, setScheduleType] = useState('main');
  const [coachMarkVisible, setCoachMarkVisible] = useState(false);

  // First time this screen is reached after login on this device, point
  // out the menu button once everything's had a moment to render — see
  // components/MenuCoachMark and AuthContext.completeMenuTour.
  useEffect(() => {
    if (menuTourSeen) return;
    const timer = setTimeout(() => setCoachMarkVisible(true), 600);
    return () => clearTimeout(timer);
  }, [menuTourSeen]);

  function dismissCoachMark() {
    setCoachMarkVisible(false);
    completeMenuTour();
  }

  const availableDays = useMemo(() => {
    if (!timetable?.days) return DAY_ORDER;
    const present = timetable.days.map((d) => d.day);
    return DAY_ORDER.filter((d) => present.includes(d));
  }, [timetable]);

  const days = availableDays.length ? availableDays : DAY_ORDER;

  // All days' entries, keyed by day name — the whole week now renders as
  // one continuous table (see WeekTimetable) with a single shared
  // timeslot header, so it all fits on one vertical scroll.
  const entriesByDay = useMemo(() => {
    const map = {};
    days.forEach((day) => {
      map[day] = timetable?.days?.find((d) => d.day === day)?.entries || [];
    });
    return map;
  }, [timetable, days]);

  // Exam timetable is date-based (see mobile_api exam_serialize_timetable),
  // not a recurring weekday grid, so it doesn't have a fixed DAY_ORDER —
  // it's just whatever dates have a published exam, earliest first. Each
  // date is turned into a "Mon 15 Sep"-style label so it can reuse
  // WeekTimetable's day-section rendering unchanged.
  const examDates = examTimetable?.dates || [];
  const examDayLabels = useMemo(
    () => examDates.map((d) => formatExamDateLabel(d.date, d.day)),
    [examDates]
  );
  const examEntriesByDay = useMemo(() => {
    const map = {};
    examDates.forEach((d) => {
      map[formatExamDateLabel(d.date, d.day)] = d.entries;
    });
    return map;
  }, [examDates]);

  async function handleRefresh() {
    setRefreshing(true);
    await refreshNow(true); // manual: always toast if it fails to reach the university network
    setRefreshing(false);
  }

  function handleMenuSelect(routeName) {
    setMenuVisible(false);
    navigation.navigate(routeName);
  }

  function handleLogout() {
    setMenuVisible(false);
    logout();
  }

  return (
    <SafeAreaView style={styles.root} edges={['top']}>
      <View style={styles.header}>
        <View style={styles.headerRow}>
          <View style={styles.headerLeft}>
            <Image
              source={require('../../assets/chuka-logo-circle.png')}
              style={styles.headerLogo}
              resizeMode="contain"
            />
            <View style={{ marginLeft: 8, flexShrink: 1 }}>
              <Text style={styles.headerName} numberOfLines={1}>{user?.name}</Text>
              <Text style={styles.headerTag}>
                {user?.role === 'student' ? user?.regNo : 'Lecturer'}
              </Text>
            </View>
          </View>
          <TouchableOpacity
            onPress={() => {
              if (coachMarkVisible) dismissCoachMark();
              setMenuVisible(true);
            }}
            style={styles.menuBtn}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          >
            <View style={styles.menuBar} />
            <View style={styles.menuBar} />
            <View style={styles.menuBar} />
          </TouchableOpacity>
        </View>
      </View>

      <ScrollView style={styles.pager} contentContainerStyle={{ paddingBottom: 24 }}>
        <AiPoweredHero />

        <SyncStatusBar syncStatus={syncStatus} onRefresh={handleRefresh} refreshing={refreshing} />

        <ScheduleTypeTabs active={scheduleType} onSelect={setScheduleType} />

        {/* Each schedule — the recurring weekly timetable, or the date-based
            exam timetable — lives in one table with a single shared timeslot
            header; the tabs above decide which is on screen, no day tabs
            needed within either since nothing is hidden behind them. */}
        {/* Each side (main/exam) carries its own `visibility` flag straight
            from the API (see mobile_api.timetable_builder._visibility_payload).
            When an admin has blocked a schedule from Django admin, its grid
            is replaced with the message/link they set there — the schedule
            type tabs above still work as normal so a person can check the
            other schedule. */}
        {scheduleType === 'main' ? (
          timetable?.visibility?.blocked ? (
            <BlockedScheduleNotice
              message={timetable.visibility.message}
              linkLabel={timetable.visibility.linkLabel}
              linkUrl={timetable.visibility.linkUrl}
            />
          ) : (
            <WeekTimetable days={days} entriesByDay={entriesByDay} />
          )
        ) : examTimetable?.visibility?.blocked ? (
          <BlockedScheduleNotice
            message={examTimetable.visibility.message}
            linkLabel={examTimetable.visibility.linkLabel}
            linkUrl={examTimetable.visibility.linkUrl}
          />
        ) : (
          <WeekTimetable
            days={examDayLabels}
            entriesByDay={examEntriesByDay}
            emptyMessage="No exam timetable published yet."
          />
        )}
      </ScrollView>

      <MenuSheet
        visible={menuVisible}
        onClose={() => setMenuVisible(false)}
        onSelect={handleMenuSelect}
        onLogout={handleLogout}
      />

      <MenuCoachMark visible={coachMarkVisible} onDismiss={dismissCoachMark} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: COLORS.gray50,
    ...(typeof document !== 'undefined' ? { height: '100vh', overflow: 'hidden' } : {}),
  },
  header: {
    backgroundColor: COLORS.white,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.gray100,
    paddingTop: 8,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingBottom: 10,
    minHeight: 38,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', flexShrink: 1, minWidth: 0 },
  headerLogo: { width: 30, height: 30, borderRadius: 15, backgroundColor: COLORS.gray50 },
  headerName: { fontSize: 14, fontWeight: '800', color: COLORS.black, maxWidth: 180 },
  headerTag: { fontSize: 11, color: COLORS.gray500 },
  menuBtn: { padding: 8, justifyContent: 'center', alignItems: 'center', gap: 4 },
  menuBar: { width: 20, height: 2.5, borderRadius: 2, backgroundColor: COLORS.black },
  pager: { flex: 1 },
});

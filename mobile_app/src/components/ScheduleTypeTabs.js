import React from 'react';
import { View, TouchableOpacity, Text, StyleSheet } from 'react-native';
import { COLORS, RADIUS, SOFT_SHADOW } from '../theme/colors';

// Two-way segmented control shown above the schedule so a student/lecturer
// can flip between their regular weekly timetable and the (date-based)
// exam timetable, without leaving the screen. Deliberately a plain
// TouchableOpacity pair (matching DayTabs' pill styling) rather than a
// native SegmentedControl, so it renders identically on iOS/Android/web.
const TABS = [
  { key: 'main', label: 'Timetable' },
  { key: 'exam', label: 'Exams' },
];

export default function ScheduleTypeTabs({ active, onSelect }) {
  return (
    <View style={styles.wrap}>
      {TABS.map((tab) => {
        const isActive = tab.key === active;
        return (
          <TouchableOpacity
            key={tab.key}
            style={[styles.tab, isActive && styles.activeTab]}
            onPress={() => onSelect(tab.key)}
            activeOpacity={0.85}
            accessibilityRole="tab"
            accessibilityState={{ selected: isActive }}
          >
            <Text style={[styles.tabText, isActive && styles.activeTabText]}>{tab.label}</Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    backgroundColor: COLORS.gray100,
    borderRadius: RADIUS.pill,
    padding: 4,
    marginHorizontal: 16,
    marginTop: 4,
    marginBottom: 8,
  },
  tab: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: RADIUS.pill,
    alignItems: 'center',
    justifyContent: 'center',
  },
  activeTab: { backgroundColor: COLORS.white, ...SOFT_SHADOW },
  tabText: { color: COLORS.gray500, fontWeight: '700', fontSize: 13 },
  activeTabText: { color: COLORS.blue },
});

import React from 'react';
import { ScrollView, TouchableOpacity, Text, StyleSheet } from 'react-native';
import { COLORS, RADIUS, SOFT_SHADOW } from '../theme/colors';

export default function DayTabs({ days, activeDay, onSelect }) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.container} contentContainerStyle={{ paddingHorizontal: 12 }}>
      {days.map((day) => {
        const isActive = day === activeDay;
        return (
          <TouchableOpacity
            key={day}
            style={[styles.tab, isActive && styles.activeTab]}
            onPress={() => onSelect(day)}
            activeOpacity={0.85}
          >
            <Text style={[styles.tabText, isActive && styles.activeTabText]}>{day}</Text>
          </TouchableOpacity>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 0, paddingVertical: 10 },
  tab: {
    paddingHorizontal: 18,
    paddingVertical: 9,
    borderRadius: RADIUS.pill,
    backgroundColor: COLORS.gray100,
    marginRight: 8,
  },
  activeTab: { backgroundColor: COLORS.blue, ...SOFT_SHADOW },
  tabText: { color: COLORS.gray500, fontWeight: '700', fontSize: 13 },
  activeTabText: { color: COLORS.white },
});

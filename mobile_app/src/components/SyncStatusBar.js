import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator } from 'react-native';
import { COLORS, RADIUS } from '../theme/colors';

export default function SyncStatusBar({ syncStatus, lastSync, onRefresh, refreshing }) {
  let label = 'Up to date';
  let dotColor = '#2E9E5B';

  if (refreshing) {
    label = 'Checking the university server…';
    dotColor = COLORS.aqua;
  } else if (!syncStatus?.synced) {
    if (syncStatus?.reason === 'network' || syncStatus?.reason === 'timeout') {
      label = 'Not within the university — showing last saved timetable';
    } else {
      label = 'Offline — showing last saved timetable';
    }
    dotColor = COLORS.red;
  } else if (syncStatus?.updated) {
    label = 'Timetable updated just now';
    dotColor = COLORS.blue;
  } else if (lastSync) {
    label = `Synced • last checked ${new Date(lastSync).toLocaleTimeString()}`;
  }

  return (
    <View style={styles.bar}>
      <View style={styles.left}>
        {refreshing ? (
          <ActivityIndicator size="small" color={dotColor} style={{ marginRight: 8 }} />
        ) : (
          <View style={[styles.dot, { backgroundColor: dotColor }]} />
        )}
        <Text style={styles.text} numberOfLines={1}>{label}</Text>
      </View>
      <TouchableOpacity onPress={onRefresh} style={styles.refreshPill}>
        <Text style={styles.refresh}>Refresh</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  left: { flexDirection: 'row', alignItems: 'center', flexShrink: 1, marginRight: 8 },
  dot: { width: 8, height: 8, borderRadius: 4, marginRight: 8 },
  text: { fontSize: 12, fontWeight: '600', color: COLORS.gray500, flexShrink: 1 },
  refreshPill: {
    backgroundColor: COLORS.gray100,
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: RADIUS.pill,
  },
  refresh: { fontSize: 11, fontWeight: '800', color: COLORS.blue },
});

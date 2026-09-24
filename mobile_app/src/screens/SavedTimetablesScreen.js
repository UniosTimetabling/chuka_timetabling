import React, { useCallback, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, FlatList, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import DayTabs from '../components/DayTabs';
import TimetableGrid from '../components/TimetableGrid';
import { storage } from '../services/storage';
import { DAY_ORDER, todayName } from '../utils/dateHelpers';
import { COLORS, RADIUS, SOFT_SHADOW } from '../theme/colors';

// Timetables saved locally from QRScanScreen — people whose timetable this
// device scanned and chose to keep for offline viewing. Independent of the
// signed-in user's own timetable (see TIMETABLE storage key / AuthContext).
export default function SavedTimetablesScreen({ navigation }) {
  const [entries, setEntries] = useState([]);
  const [selected, setSelected] = useState(null); // ownerId, or null for list view
  const [activeDay, setActiveDay] = useState(() => {
    const t = todayName();
    return DAY_ORDER.includes(t) ? t : 'Monday';
  });

  const load = useCallback(async () => {
    const all = await storage.getAllSharedTimetables();
    setEntries(Object.values(all).sort((a, b) => (b.savedAt || '').localeCompare(a.savedAt || '')));
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  function confirmDelete(entry) {
    Alert.alert(
      'Remove saved timetable?',
      `This removes ${entry.owner?.name || 'this'}'s timetable from this device. You can scan their QR again any time to re-save it.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: async () => {
            await storage.deleteSharedTimetable(entry.ownerId);
            if (selected === entry.ownerId) setSelected(null);
            load();
          },
        },
      ]
    );
  }

  const activeEntry = entries.find((e) => e.ownerId === selected);

  if (activeEntry) {
    const dayEntries = activeEntry.days?.find((d) => d.day === activeDay)?.entries || [];
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <TouchableOpacity style={styles.backBtn} onPress={() => setSelected(null)}>
          <Text style={styles.backText}>‹ Saved timetables</Text>
        </TouchableOpacity>
        <Text style={styles.title}>{activeEntry.owner?.name}'s Timetable</Text>
        <Text style={styles.subtitle}>
          Saved {activeEntry.savedAt ? new Date(activeEntry.savedAt).toLocaleDateString() : ''}
        </Text>
        <DayTabs
          days={activeEntry.days?.map((d) => d.day) || DAY_ORDER}
          activeDay={activeDay}
          onSelect={setActiveDay}
        />
        <TimetableGrid dayEntries={dayEntries} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>Saved Timetables</Text>
      <Text style={styles.subtitle}>Timetables you've scanned and saved from other people on this app.</Text>

      {entries.length === 0 ? (
        <View style={styles.emptyBox}>
          <Text style={styles.emptyText}>Nothing saved yet. Scan a QR code from Timetable › Scan to add one.</Text>
        </View>
      ) : (
        <FlatList
          data={entries}
          keyExtractor={(item) => item.ownerId}
          contentContainerStyle={{ padding: 16 }}
          renderItem={({ item }) => (
            <TouchableOpacity style={styles.card} onPress={() => setSelected(item.ownerId)} activeOpacity={0.85}>
              <View style={{ flex: 1 }}>
                <Text style={styles.cardName}>{item.owner?.name}</Text>
                <Text style={styles.cardMeta}>
                  Saved {item.savedAt ? new Date(item.savedAt).toLocaleDateString() : ''}
                </Text>
              </View>
              <TouchableOpacity style={styles.deleteBtn} onPress={() => confirmDelete(item)}>
                <Text style={styles.deleteText}>Remove</Text>
              </TouchableOpacity>
            </TouchableOpacity>
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.white },
  backBtn: { paddingHorizontal: 16, paddingTop: 8 },
  backText: { color: COLORS.blue, fontSize: 14, fontWeight: '700' },
  title: { fontSize: 19, fontWeight: '800', color: COLORS.black, paddingHorizontal: 16, marginTop: 12 },
  subtitle: { fontSize: 12, color: COLORS.gray500, paddingHorizontal: 16, marginTop: 4, marginBottom: 8 },
  emptyBox: { padding: 40, alignItems: 'center' },
  emptyText: { color: COLORS.gray500, fontSize: 13, textAlign: 'center' },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.gray50,
    borderRadius: RADIUS.md,
    padding: 14,
    marginBottom: 10,
    ...SOFT_SHADOW,
  },
  cardName: { fontSize: 14, fontWeight: '800', color: COLORS.black },
  cardMeta: { fontSize: 11, color: COLORS.gray500, marginTop: 2 },
  deleteBtn: { paddingHorizontal: 10, paddingVertical: 6 },
  deleteText: { color: COLORS.red, fontSize: 12, fontWeight: '700' },
});

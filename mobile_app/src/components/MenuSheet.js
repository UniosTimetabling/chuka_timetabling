import React from 'react';
import { Modal, View, Text, TouchableOpacity, StyleSheet, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { COLORS, RADIUS, CARD_SHADOW } from '../theme/colors';

// Everything that used to live in HomeScreen's Quick Links grid, now
// reached from a menu button in TimetableScreen's header instead — the
// timetable itself (plus the hero carousel) is the whole landing page,
// so this is the one place all the secondary destinations live.
const ITEMS = [
  { key: 'Events', label: 'Events & Memos', subtitle: 'Notices from the university' },
  { key: 'AddCourse', label: 'Add a Course', subtitle: 'Add a unit to your timetable' },
  { key: 'QRShare', label: 'Share', subtitle: 'QR code, image, PDF, or invite to the app' },
  { key: 'QRScan', label: 'Scan QR', subtitle: "View someone else's" },
  { key: 'SavedTimetables', label: 'Saved Timetables', subtitle: 'Ones you scanned and saved' },
  { key: 'Feedback', label: 'Send Feedback', subtitle: 'Report an issue' },
  { key: 'Settings', label: 'Settings', subtitle: 'Account & saved timetables' },
];

export default function MenuSheet({ visible, onClose, onSelect, onLogout }) {
  return (
    <Modal visible={visible} animationType="fade" transparent onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <SafeAreaView edges={['top']} style={{ width: '100%', alignItems: 'flex-end' }}>
          <Pressable style={styles.sheet} onPress={() => {}}>
            {ITEMS.map((item) => (
              <TouchableOpacity
                key={item.key}
                style={styles.row}
                activeOpacity={0.7}
                onPress={() => onSelect(item.key)}
              >
                <Text style={styles.rowLabel}>{item.label}</Text>
                <Text style={styles.rowSubtitle}>{item.subtitle}</Text>
              </TouchableOpacity>
            ))}
            <View style={styles.divider} />
            <TouchableOpacity style={styles.row} activeOpacity={0.7} onPress={onLogout}>
              <Text style={[styles.rowLabel, styles.logoutLabel]}>Log out</Text>
            </TouchableOpacity>
          </Pressable>
        </SafeAreaView>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(10,26,31,0.35)' },
  sheet: {
    marginTop: 6,
    marginRight: 16,
    width: 240,
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.md,
    paddingVertical: 6,
    ...CARD_SHADOW,
  },
  row: { paddingHorizontal: 16, paddingVertical: 11 },
  rowLabel: { fontSize: 14, fontWeight: '700', color: COLORS.black },
  rowSubtitle: { fontSize: 11, color: COLORS.gray500, marginTop: 1 },
  divider: { height: 1, backgroundColor: COLORS.gray100, marginVertical: 4 },
  logoutLabel: { color: COLORS.red },
});

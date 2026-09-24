import React from 'react';
import { Modal, View, Text, TouchableOpacity, StyleSheet, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { COLORS, RADIUS, CARD_SHADOW } from '../theme/colors';

// A one-time callout pointing at the ☰ menu button in TimetableScreen's
// header — that's where Events, Add a Course, Share, Scan, Saved
// Timetables, Feedback and Settings all live now (see components/MenuSheet).
// Shown once ever, right after a person's first login on this device
// (gated by storage.getMenuTourSeen() — see AuthContext.completeMenuTour).
export default function MenuCoachMark({ visible, onDismiss }) {
  return (
    <Modal visible={visible} animationType="fade" transparent onRequestClose={onDismiss}>
      <Pressable style={styles.backdrop} onPress={onDismiss}>
        <SafeAreaView edges={['top']} style={{ width: '100%', alignItems: 'flex-end' }}>
          <View style={styles.pointer} />
          <Pressable style={styles.card} onPress={() => {}}>
            <Text style={styles.title}>Everything else lives here</Text>
            <Text style={styles.body}>
              Tap the menu button to find Events &amp; Memos, Add a Course, Share your timetable, Scan someone
              else's, and Settings.
            </Text>
            <TouchableOpacity style={styles.button} onPress={onDismiss} activeOpacity={0.85}>
              <Text style={styles.buttonText}>Got it</Text>
            </TouchableOpacity>
          </Pressable>
        </SafeAreaView>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(10,26,31,0.55)' },
  pointer: {
    marginTop: 40,
    marginRight: 26,
    width: 16,
    height: 16,
    backgroundColor: COLORS.white,
    transform: [{ rotate: '45deg' }],
  },
  card: {
    marginTop: -8,
    marginRight: 16,
    width: 250,
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.md,
    padding: 18,
    ...CARD_SHADOW,
  },
  title: { fontSize: 15, fontWeight: '800', color: COLORS.black, marginBottom: 6 },
  body: { fontSize: 13, color: COLORS.gray500, lineHeight: 18, marginBottom: 14 },
  button: {
    backgroundColor: COLORS.blue,
    borderRadius: RADIUS.sm,
    paddingVertical: 10,
    alignItems: 'center',
  },
  buttonText: { color: COLORS.white, fontWeight: '800', fontSize: 13 },
});

import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ActivityIndicator, TouchableOpacity, Alert } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { SafeAreaView } from 'react-native-safe-area-context';
import { api } from '../api/api';
import TimetableGrid from '../components/TimetableGrid';
import DayTabs from '../components/DayTabs';
import { DAY_ORDER, todayName } from '../utils/dateHelpers';
import { mergeDays } from '../utils/timetableMerge';
import { useAuth } from '../context/AuthContext';
import { toastBus } from '../services/toastBus';
import { storage } from '../services/storage';
import { QR_PAYLOAD_KIND } from '../config/config';
import { COLORS } from '../theme/colors';

export default function QRScanScreen({ navigation }) {
  const { user } = useAuth(); // viewer's own role — sharing is same-role only
  const [permission, requestPermission] = useCameraPermissions();
  const [scanned, setScanned] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sharedTimetable, setSharedTimetable] = useState(null);
  const [saveState, setSaveState] = useState(null); // null | 'saved' | 'viewingOnly'
  const [activeDay, setActiveDay] = useState(() => {
    const t = todayName();
    return DAY_ORDER.includes(t) ? t : 'Monday';
  });

  useEffect(() => {
    if (!permission) return;
    if (!permission.granted) requestPermission();
  }, [permission]);

  async function handleScan({ data }) {
    if (scanned || loading) return;
    setScanned(true);
    setLoading(true);
    setError('');
    try {
      const parsed = JSON.parse(data);

      // Not our QR shape at all — most likely a random code, or one this
      // person exported from a DIFFERENT app. We can't magically decode
      // someone else's format, so point them at the image/PDF route
      // instead, which needs no scanning at all.
      if (parsed.kind !== QR_PAYLOAD_KIND || !parsed.role || !parsed.id) {
        setError(
          "This isn't a Chuka Timetable QR code. If it's from another app, ask them to send their timetable as an image or PDF instead — from Share > Other apps you can do the same."
        );
        setLoading(false);
        return;
      }

      // Same-role sharing only: students see students, lecturers see
      // lecturers. Checked here for instant feedback; the backend enforces
      // it too (403) since client checks alone aren't trustworthy.
      if (parsed.role !== user?.role) {
        setError(
          user?.role === 'student'
            ? 'That QR is from a lecturer. Students can only view other students\u2019 timetables.'
            : 'That QR is from a student. Lecturers can only view other lecturers\u2019 timetables.'
        );
        setLoading(false);
        return;
      }

      // The QR encodes an identity payload; we exchange it for a fresh,
      // read-only timetable straight from the domain so it's never stale.
      const token = encodeURIComponent(JSON.stringify(parsed));
      const result = await api.getSharedTimetable(token, user.role);
      setSharedTimetable(result);
      await resolveSaveDecision(parsed.id, result);
    } catch (e) {
      if (e.isNetworkError || e.isTimeout) {
        // Connectivity issue, not a bad QR code — toast it and let them
        // rescan once they're back within the university.
        toastBus.show(e.message);
        setScanned(false);
        setError('');
      } else {
        setError(e.status === 403 ? e.message : 'Could not read that QR code. Try again.');
      }
    } finally {
      setLoading(false);
    }
  }

  // Decides what happens to the freshly scanned timetable in local storage:
  // save it for offline access, and if one for this same person was already
  // saved before, ask whether to replace it outright or merge the two
  // (existing entries kept, new ones added/updated) rather than silently
  // picking one.
  async function resolveSaveDecision(ownerId, freshTimetable) {
    const existing = await storage.getSharedTimetable(ownerId);

    if (!existing) {
      Alert.alert(
        'Save this timetable?',
        `Keep ${freshTimetable.owner?.name || 'this'}'s timetable saved for offline viewing?`,
        [
          { text: 'Just view', style: 'cancel', onPress: () => setSaveState('viewingOnly') },
          {
            text: 'Save',
            onPress: async () => {
              await storage.saveSharedTimetable(ownerId, {
                owner: freshTimetable.owner,
                ownerId,
                days: freshTimetable.days || [],
                savedAt: new Date().toISOString(),
              });
              setSaveState('saved');
            },
          },
        ]
      );
      return;
    }

    Alert.alert(
      'Timetable already saved',
      `You already have a saved timetable for ${existing.owner?.name || 'this person'}. Replace it with the new one, or merge them together?`,
      [
        { text: 'Cancel', style: 'cancel', onPress: () => setSaveState('viewingOnly') },
        {
          text: 'Merge',
          onPress: async () => {
            const merged = mergeDays(existing.days, freshTimetable.days || []);
            await storage.saveSharedTimetable(ownerId, {
              owner: freshTimetable.owner,
              ownerId,
              days: merged,
              savedAt: new Date().toISOString(),
            });
            setSaveState('saved');
          },
        },
        {
          text: 'Replace',
          style: 'destructive',
          onPress: async () => {
            await storage.saveSharedTimetable(ownerId, {
              owner: freshTimetable.owner,
              ownerId,
              days: freshTimetable.days || [],
              savedAt: new Date().toISOString(),
            });
            setSaveState('saved');
          },
        },
      ]
    );
  }

  function resetScanner() {
    setScanned(false);
    setError('');
    setSharedTimetable(null);
    setSaveState(null);
  }

  if (sharedTimetable) {
    const dayEntries = sharedTimetable.days?.find((d) => d.day === activeDay)?.entries || [];
    return (
      <SafeAreaView style={styles.resultContainer} edges={['top']}>
        <View style={styles.resultTopRow}>
          <TouchableOpacity style={styles.backBtnLight} onPress={() => navigation.goBack()}>
            <Text style={styles.backTextDark}>‹ Back</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.backBtnLight} onPress={resetScanner}>
            <Text style={styles.backTextDark}>Scan another</Text>
          </TouchableOpacity>
        </View>
        <Text style={styles.resultTitle}>{sharedTimetable.owner?.name}'s Timetable</Text>
        <Text style={styles.resultSubtitle}>
          Read-only • synced from the domain
          {saveState === 'saved' ? ' • saved for offline viewing' : ''}
        </Text>
        <DayTabs
          days={sharedTimetable.days?.map((d) => d.day) || DAY_ORDER}
          activeDay={activeDay}
          onSelect={setActiveDay}
        />
        <TimetableGrid dayEntries={dayEntries} />
      </SafeAreaView>
    );
  }

  if (!permission) return <View style={styles.center}><ActivityIndicator /></View>;

  if (!permission.granted) {
    return (
      <SafeAreaView style={styles.center} edges={['top']}>
        <TouchableOpacity style={styles.backBtnLight} onPress={() => navigation.goBack()}>
          <Text style={styles.backTextDark}>‹ Back</Text>
        </TouchableOpacity>
        <Text style={styles.permText}>Camera permission is needed to scan QR codes.</Text>
        <TouchableOpacity style={styles.grantBtn} onPress={requestPermission}>
          <Text style={styles.grantBtnText}>Grant Permission</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  return (
    <View style={styles.container}>
      <CameraView
        style={StyleSheet.absoluteFillObject}
        barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
        onBarcodeScanned={scanned ? undefined : handleScan}
      />
      <SafeAreaView style={styles.topOverlay} edges={['top']}>
        <TouchableOpacity style={styles.backBtnDark} onPress={() => navigation.goBack()}>
          <Text style={styles.backTextLight}>‹ Back</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.backBtnDark} onPress={() => navigation.navigate('SavedTimetables')}>
          <Text style={styles.backTextLight}>Saved ›</Text>
        </TouchableOpacity>
      </SafeAreaView>
      <View style={styles.overlay}>
        {loading && <ActivityIndicator color={COLORS.white} size="large" />}
        {!!error && (
          <>
            <Text style={styles.errorText}>{error}</Text>
            <TouchableOpacity style={styles.grantBtn} onPress={resetScanner}>
              <Text style={styles.grantBtnText}>Try Again</Text>
            </TouchableOpacity>
          </>
        )}
        {!loading && !error && <Text style={styles.hint}>Point your camera at a shared timetable QR code</Text>}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.black },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, backgroundColor: COLORS.white },
  topOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  backBtnDark: { paddingHorizontal: 20, paddingTop: 8 },
  backBtnLight: { paddingHorizontal: 4, paddingVertical: 8, marginBottom: 8 },
  backTextLight: { color: COLORS.white, fontSize: 15, fontWeight: '700' },
  backTextDark: { color: COLORS.blue, fontSize: 15, fontWeight: '700' },
  overlay: {
    position: 'absolute',
    bottom: 40,
    left: 0,
    right: 0,
    alignItems: 'center',
    paddingHorizontal: 24,
  },
  hint: { color: COLORS.white, fontSize: 13, textAlign: 'center', backgroundColor: 'rgba(0,0,0,0.6)', padding: 10, borderRadius: 4 },
  errorText: { color: COLORS.white, backgroundColor: 'rgba(198,40,40,0.9)', padding: 10, borderRadius: 4, marginBottom: 10, textAlign: 'center' },
  permText: { textAlign: 'center', color: COLORS.gray800, marginBottom: 16 },
  grantBtn: { backgroundColor: COLORS.blue, paddingHorizontal: 20, paddingVertical: 10, borderRadius: 4 },
  grantBtnText: { color: COLORS.white, fontWeight: '700' },
  resultContainer: { flex: 1, backgroundColor: COLORS.white, paddingTop: 4, paddingHorizontal: 4 },
  resultTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  resultTitle: { fontSize: 18, fontWeight: '800', color: COLORS.black, textAlign: 'center' },
  resultSubtitle: { fontSize: 12, color: COLORS.gray500, textAlign: 'center', marginBottom: 4 },
});

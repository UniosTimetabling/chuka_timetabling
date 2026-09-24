import React, { useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  ScrollView,
  Share,
} from 'react-native';
import ViewShot from 'react-native-view-shot';
import QRCode from 'react-native-qrcode-svg';
import * as Print from 'expo-print';
import * as Sharing from 'expo-sharing';
import { SafeAreaView } from 'react-native-safe-area-context';
import GradientBackground from '../components/GradientBackground';
import ExportableTimetableView from '../components/ExportableTimetableView';
import { useAuth } from '../context/AuthContext';
import { toastBus } from '../services/toastBus';
import { buildTimetableHtml } from '../utils/exportTimetable';
import { QR_PAYLOAD_KIND, buildAppShareMessage } from '../config/config';
import { useAppLink } from '../hooks/useAppLink';
import { COLORS, CARD_SHADOW, RADIUS } from '../theme/colors';

// Two independent things live on this screen:
//
// 1. "Show my QR" — encodes {kind, role, id} so another signed-in user of
//    THIS app can scan it with QRScanScreen and pull a live, read-only copy
//    of this person's timetable straight from the domain (see QRScanScreen
//    for the scanning/merge side of this flow).
// 2. "Invite someone to the app" — a QR code + native share sheet pointing
//    at the app's install link, for people who don't have the app yet.
//
// Image/PDF export stays as the universal fallback that works with anyone,
// app or no app.
export default function QRShareScreen({ navigation }) {
  const { user, timetable, examTimetable } = useAuth();
  // Staff-editable link from the web dashboard (cached + fallback built in).
  const { url: appShareUrl } = useAppLink();
  const [exporting, setExporting] = useState(null); // null | 'image' | 'pdf'
  const [showMyQr, setShowMyQr] = useState(false);
  const [showAppQr, setShowAppQr] = useState(false);
  const shotRef = useRef(null);

  const myQrValue = user?.id
    ? JSON.stringify({ kind: QR_PAYLOAD_KIND, role: user.role, id: user.id })
    : null;

  async function shareAsImage() {
    if (exporting) return;
    setExporting('image');
    try {
      const uri = await shotRef.current.capture();
      await ensureShareable(uri, 'Share timetable image');
    } catch (e) {
      toastBus.show("Couldn't create the image. Please try again.");
    } finally {
      setExporting(null);
    }
  }

  async function shareAsPdf() {
    if (exporting) return;
    setExporting('pdf');
    try {
      const html = buildTimetableHtml(timetable, examTimetable);
      const { uri } = await Print.printToFileAsync({ html, base64: false });
      await ensureShareable(uri, 'Share timetable PDF');
    } catch (e) {
      toastBus.show("Couldn't create the PDF. Please try again.");
    } finally {
      setExporting(null);
    }
  }

  async function ensureShareable(uri, dialogTitle) {
    const canShare = await Sharing.isAvailableAsync();
    if (!canShare) {
      toastBus.show('Sharing is not available on this device.');
      return;
    }
    await Sharing.shareAsync(uri, { dialogTitle });
  }

  async function shareAppLink() {
    try {
      await Share.share({
        message: buildAppShareMessage(appShareUrl),
        url: appShareUrl, // used on iOS; Android falls back to the message text
      });
    } catch (e) {
      toastBus.show("Couldn't open the share sheet. Please try again.");
    }
  }

  return (
    <GradientBackground>
      <SafeAreaView style={{ flex: 1 }} edges={['top']}>
        <TouchableOpacity style={styles.backBtn} onPress={() => navigation.goBack()}>
          <Text style={styles.backText}>‹ Back</Text>
        </TouchableOpacity>

        <ScrollView
          contentContainerStyle={styles.container}
          showsVerticalScrollIndicator={false}
        >
          {/* ---- Section 1: share MY timetable ---- */}
          <Text style={styles.title}>
            {user?.role === 'student' ? "Share your program's timetable" : 'Share your timetable'}
          </Text>

          {!!myQrValue && (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Show a QR code</Text>
              <Text style={styles.cardSubtitle}>
                Anyone with the Chuka Timetable app can scan this to view — and
                optionally save — a live copy of{' '}
                {user?.role === 'student' ? "your program's timetable" : 'your timetable'}.
              </Text>

              {showMyQr ? (
                <View style={styles.qrWrap}>
                  <QRCode value={myQrValue} size={200} backgroundColor={COLORS.white} color={COLORS.black} />
                  <TouchableOpacity style={styles.linkBtn} onPress={() => setShowMyQr(false)}>
                    <Text style={styles.linkBtnText}>Hide QR code</Text>
                  </TouchableOpacity>
                </View>
              ) : (
                <TouchableOpacity style={styles.exportBtn} onPress={() => setShowMyQr(true)}>
                  <Text style={styles.exportBtnText}>Show My QR Code</Text>
                </TouchableOpacity>
              )}
            </View>
          )}

          <Text style={styles.subtitle}>
            Or send it as an image or a PDF — it opens anywhere, no app required.
          </Text>

          <View style={styles.exportButtons}>
            <TouchableOpacity style={styles.exportBtn} onPress={shareAsImage} disabled={!!exporting}>
              {exporting === 'image' ? (
                <ActivityIndicator color={COLORS.white} />
              ) : (
                <Text style={styles.exportBtnText}>Share as Image</Text>
              )}
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.exportBtn, styles.exportBtnOutline]}
              onPress={shareAsPdf}
              disabled={!!exporting}
            >
              {exporting === 'pdf' ? (
                <ActivityIndicator color={COLORS.blue} />
              ) : (
                <Text style={[styles.exportBtnText, styles.exportBtnTextOutline]}>Share as PDF</Text>
              )}
            </TouchableOpacity>
          </View>

          {/* ---- Section 2: invite someone to the app itself ---- */}
          <View style={styles.divider} />

          <Text style={styles.title}>Invite someone to the app</Text>
          <Text style={styles.subtitle}>
            Don't have the Chuka Timetable app yet? Share the app itself so they
            can install it and scan timetables directly.
          </Text>

          <View style={styles.card}>
            {showAppQr ? (
              <View style={styles.qrWrap}>
                <QRCode value={appShareUrl} size={200} backgroundColor={COLORS.white} color={COLORS.black} />
                <TouchableOpacity style={styles.linkBtn} onPress={() => setShowAppQr(false)}>
                  <Text style={styles.linkBtnText}>Hide QR code</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <View style={styles.exportButtons}>
                <TouchableOpacity style={styles.exportBtn} onPress={() => setShowAppQr(true)}>
                  <Text style={styles.exportBtnText}>Show App QR Code</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.exportBtn, styles.exportBtnOutline]}
                  onPress={shareAppLink}
                >
                  <Text style={[styles.exportBtnText, styles.exportBtnTextOutline]}>
                    Share App Link
                  </Text>
                </TouchableOpacity>
              </View>
            )}
          </View>

          {/* Rendered off-screen purely so ViewShot has something to
              capture; never shown to the user. */}
          <View style={styles.offscreen} pointerEvents="none">
            <ViewShot ref={shotRef} options={{ format: 'png', quality: 1 }}>
              <ExportableTimetableView timetable={timetable} examTimetable={examTimetable} />
            </ViewShot>
          </View>
        </ScrollView>
      </SafeAreaView>
    </GradientBackground>
  );
}

const styles = StyleSheet.create({
  backBtn: {
    position: 'absolute',
    top: 8,
    left: 20,
    zIndex: 10,
    elevation: 10,
    paddingVertical: 8,
    paddingHorizontal: 8,
  },
  backText: { color: COLORS.white, fontSize: 15, fontWeight: '700' },
  container: { flexGrow: 1, alignItems: 'center', padding: 24, paddingTop: 56 },
  title: { fontSize: 21, fontWeight: '800', color: COLORS.white, marginBottom: 10, textAlign: 'center' },
  subtitle: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.9)',
    textAlign: 'center',
    marginBottom: 20,
    paddingHorizontal: 8,
  },
  divider: {
    width: '100%',
    height: 1,
    backgroundColor: 'rgba(255,255,255,0.25)',
    marginVertical: 28,
  },
  card: {
    width: '100%',
    backgroundColor: 'rgba(255,255,255,0.08)',
    borderRadius: RADIUS.md,
    padding: 18,
    marginBottom: 20,
    alignItems: 'center',
  },
  cardTitle: { fontSize: 15, fontWeight: '800', color: COLORS.white, marginBottom: 6, textAlign: 'center' },
  cardSubtitle: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.85)',
    textAlign: 'center',
    marginBottom: 14,
    paddingHorizontal: 4,
  },
  qrWrap: { alignItems: 'center', padding: 16, backgroundColor: COLORS.white, borderRadius: RADIUS.md, ...CARD_SHADOW },
  linkBtn: { marginTop: 14 },
  linkBtnText: { color: COLORS.blue, fontWeight: '700', fontSize: 13 },
  exportButtons: { width: '100%', gap: 12 },
  exportBtn: {
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.md,
    paddingVertical: 14,
    paddingHorizontal: 16,
    alignItems: 'center',
    justifyContent: 'center',
    ...CARD_SHADOW,
  },
  exportBtnOutline: { backgroundColor: 'transparent', borderWidth: 2, borderColor: COLORS.white },
  exportBtnText: { fontSize: 14, fontWeight: '800', color: COLORS.blue },
  exportBtnTextOutline: { color: COLORS.white },
  offscreen: { position: 'absolute', top: 0, left: -9999 },
});

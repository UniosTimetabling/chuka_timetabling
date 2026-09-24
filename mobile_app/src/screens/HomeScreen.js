import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Image, ScrollView } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import GradientBackground from '../components/GradientBackground';
import { useAuth } from '../context/AuthContext';
import MotivationBanner from '../components/MotivationBanner';
import AiPoweredHero from '../components/AiPoweredHero';
import SyncStatusBar from '../components/SyncStatusBar';
import { COLORS, RADIUS, SOFT_SHADOW } from '../theme/colors';

// The screen a person lands on right after logging in: the hero banner,
// a "greeting of the day", the promo carousel, sync status, and quick
// links to everywhere else in the app — including the dedicated
// Timetable page (TimetableScreen), which now shows the schedule only.
export default function HomeScreen({ navigation }) {
  const { user, syncStatus, refreshNow, logout } = useAuth();
  const [refreshing, setRefreshing] = useState(false);
  const insets = useSafeAreaInsets();

  async function handleRefresh() {
    setRefreshing(true);
    await refreshNow(true); // manual: always toast if it fails to reach the university network
    setRefreshing(false);
  }

  return (
    <View style={styles.root}>
      <GradientBackground style={styles.hero}>
        <View style={{ paddingTop: Math.max(insets.top, 12) }}>
          <View style={styles.heroRow}>
            <View style={styles.heroLeft}>
              <Image
                source={require('../../assets/chuka-logo-circle.png')}
                style={styles.heroLogo}
                resizeMode="contain"
              />
              <View style={{ marginLeft: 8, flexShrink: 1 }}>
                <Text style={styles.heroName} numberOfLines={1}>{user?.name}</Text>
                <Text style={styles.heroTag}>
                  {user?.role === 'student' ? user?.regNo : 'Lecturer'}
                </Text>
              </View>
            </View>
            <TouchableOpacity onPress={logout} style={styles.logoutBtn} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
              <Text style={styles.logoutText}>Log out</Text>
            </TouchableOpacity>
          </View>
        </View>
      </GradientBackground>

      <ScrollView style={styles.body} contentContainerStyle={{ paddingBottom: 32 }}>
        <View style={styles.bannerOverlap}>
          <MotivationBanner userName={user?.name} role={user?.role} />
        </View>

        {/* Primary CTA — everything else on this page is secondary to
            actually seeing your schedule. */}
        <TouchableOpacity
          style={styles.timetableCard}
          onPress={() => navigation.navigate('Timetable')}
          activeOpacity={0.85}
        >
          <View style={{ flex: 1 }}>
            <Text style={styles.timetableCardTitle}>View Timetable</Text>
            <Text style={styles.timetableCardSubtitle}>
              Tap to see your full weekly schedule, plus exam dates
            </Text>
          </View>
          <View style={styles.timetableCardArrowWrap}>
            <Text style={styles.timetableCardArrow}>›</Text>
          </View>
        </TouchableOpacity>

        <AiPoweredHero />

        <View style={styles.syncWrap}>
          <SyncStatusBar syncStatus={syncStatus} onRefresh={handleRefresh} refreshing={refreshing} />
        </View>

        <Text style={styles.sectionLabel}>Quick Links</Text>
        <View style={styles.grid}>
          <QuickTile title="Events & Memos" subtitle="Notices from the university" onPress={() => navigation.navigate('Events')} />
          <QuickTile title="Add a Course" subtitle="Add a unit to your timetable" onPress={() => navigation.navigate('AddCourse')} />
          <QuickTile
            title="Share Timetable"
            subtitle={user?.role === 'student' ? "Share your program's timetable" : 'Share your timetable'}
            onPress={() => navigation.navigate('QRShare')}
          />
          <QuickTile title="Scan QR" subtitle="View someone else's" onPress={() => navigation.navigate('QRScan')} />
          <QuickTile title="Send Feedback" subtitle="Report an issue" onPress={() => navigation.navigate('Feedback')} />
          <QuickTile title="Settings" subtitle="Account & saved timetables" onPress={() => navigation.navigate('Settings')} />
        </View>
      </ScrollView>
    </View>
  );
}

function QuickTile({ title, subtitle, onPress }) {
  return (
    <TouchableOpacity style={styles.tile} onPress={onPress} activeOpacity={0.85}>
      <Text style={styles.tileTitle}>{title}</Text>
      <Text style={styles.tileSubtitle} numberOfLines={2}>{subtitle}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: COLORS.gray50,
    ...(typeof document !== 'undefined' ? { height: '100vh', overflow: 'hidden' } : {}),
  },
  hero: {
    flex: 0,
    paddingBottom: 20,
    borderBottomLeftRadius: RADIUS.md,
    borderBottomRightRadius: RADIUS.md,
  },
  heroRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 4,
    minHeight: 38,
  },
  heroLeft: { flexDirection: 'row', alignItems: 'center', flexShrink: 1, minWidth: 0 },
  heroLogo: { width: 30, height: 30, borderRadius: 15, backgroundColor: COLORS.white },
  heroName: { fontSize: 13, fontWeight: '800', color: COLORS.white, maxWidth: 160 },
  heroTag: { fontSize: 10, color: 'rgba(255,255,255,0.85)' },
  logoutBtn: {
    flexShrink: 0,
    minWidth: 64,
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.22)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: RADIUS.pill,
    marginLeft: 8,
  },
  logoutText: { fontSize: 11, fontWeight: '700', color: COLORS.white },
  body: { flex: 1 },
  bannerOverlap: { marginTop: -12, marginBottom: 4 },
  timetableCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.blue,
    borderRadius: RADIUS.md,
    marginHorizontal: 16,
    marginTop: 12,
    paddingHorizontal: 16,
    paddingVertical: 16,
    ...SOFT_SHADOW,
  },
  timetableCardTitle: { color: COLORS.white, fontWeight: '800', fontSize: 16 },
  timetableCardSubtitle: { color: 'rgba(255,255,255,0.85)', fontSize: 12, marginTop: 3 },
  timetableCardArrowWrap: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: 'rgba(255,255,255,0.22)',
    alignItems: 'center',
    justifyContent: 'center',
    marginLeft: 10,
  },
  timetableCardArrow: { color: COLORS.white, fontSize: 20, fontWeight: '700', marginLeft: 2 },
  syncWrap: { marginTop: 4 },
  sectionLabel: {
    marginTop: 8,
    marginBottom: 8,
    marginHorizontal: 16,
    fontSize: 12,
    fontWeight: '800',
    color: COLORS.gray500,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    paddingHorizontal: 12,
  },
  tile: {
    width: '46%',
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.sm,
    padding: 12,
    margin: '2%',
    ...SOFT_SHADOW,
  },
  tileTitle: { fontSize: 13, fontWeight: '700', color: COLORS.black },
  tileSubtitle: { fontSize: 11, color: COLORS.gray500, marginTop: 4, lineHeight: 15 },
});

import React, { useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Dimensions } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import GradientBackground from '../components/GradientBackground';
import {
  TimetableIllustration,
  LiveUpdatesIllustration,
  MenuIllustration,
} from '../components/onboarding/OnboardingIllustrations';
import { useAuth } from '../context/AuthContext';
import { COLORS, RADIUS, CARD_SHADOW } from '../theme/colors';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

const SLIDES = [
  {
    key: 'timetable',
    Illustration: TimetableIllustration,
    title: 'Your timetable, always with you',
    body: "Classes and exam dates for your program, laid out by day — right where you land after logging in.",
  },
  {
    key: 'live',
    Illustration: LiveUpdatesIllustration,
    title: 'Updates as they happen',
    body: "The app checks for changes while it's open and resyncs the moment you come back to it — plus a notification whenever something changes.",
  },
  {
    key: 'menu',
    Illustration: MenuIllustration,
    title: 'Everything else, one tap away',
    body: 'Events, adding a course, sharing or scanning a timetable, and settings all live behind the ☰ menu button in the top-right corner.',
  },
];

// Shown once ever, the first time the app is opened (gated by
// storage.getOnboardingSeen() — see AuthContext.completeOnboarding).
// After the last slide (or Skip), it hands off to WelcomeScreen as usual.
export default function OnboardingScreen({ navigation }) {
  const { completeOnboarding } = useAuth();
  const [index, setIndex] = useState(0);
  const scrollRef = useRef(null);

  async function finish() {
    await completeOnboarding();
    navigation.replace('Welcome');
  }

  function goNext() {
    if (index >= SLIDES.length - 1) {
      finish();
      return;
    }
    const nextIndex = index + 1;
    scrollRef.current?.scrollTo({ x: nextIndex * SCREEN_WIDTH, animated: true });
    setIndex(nextIndex);
  }

  function handleScrollEnd(e) {
    const nextIndex = Math.round(e.nativeEvent.contentOffset.x / SCREEN_WIDTH);
    setIndex(nextIndex);
  }

  const isLast = index === SLIDES.length - 1;

  return (
    <GradientBackground>
      <SafeAreaView style={{ flex: 1 }}>
        <View style={styles.topRow}>
          <TouchableOpacity onPress={finish} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Text style={styles.skipText}>Skip</Text>
          </TouchableOpacity>
        </View>

        <ScrollView
          ref={scrollRef}
          horizontal
          pagingEnabled
          showsHorizontalScrollIndicator={false}
          onMomentumScrollEnd={handleScrollEnd}
          style={{ flex: 1 }}
        >
          {SLIDES.map((slide) => (
            <View key={slide.key} style={[styles.slide, { width: SCREEN_WIDTH }]}>
              <View style={styles.illustrationWrap}>
                <slide.Illustration />
              </View>
              <Text style={styles.title}>{slide.title}</Text>
              <Text style={styles.body}>{slide.body}</Text>
            </View>
          ))}
        </ScrollView>

        <View style={styles.bottom}>
          <View style={styles.dots}>
            {SLIDES.map((slide, i) => (
              <View key={slide.key} style={[styles.dot, i === index && styles.dotActive]} />
            ))}
          </View>

          <TouchableOpacity activeOpacity={0.9} onPress={goNext} style={styles.button}>
            <Text style={styles.buttonText}>{isLast ? 'Get Started' : 'Next'}</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    </GradientBackground>
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: 'row', justifyContent: 'flex-end', paddingHorizontal: 20, paddingTop: 4 },
  skipText: { color: 'rgba(255,255,255,0.9)', fontSize: 14, fontWeight: '700' },
  slide: { alignItems: 'center', paddingHorizontal: 32, paddingTop: 24 },
  illustrationWrap: { marginBottom: 28, ...CARD_SHADOW },
  title: { fontSize: 21, fontWeight: '800', color: COLORS.white, textAlign: 'center', marginBottom: 10 },
  body: { fontSize: 14, color: 'rgba(255,255,255,0.92)', textAlign: 'center', lineHeight: 20 },
  bottom: { paddingHorizontal: 28, paddingBottom: 28, paddingTop: 12 },
  dots: { flexDirection: 'row', justifyContent: 'center', gap: 8, marginBottom: 20 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: 'rgba(255,255,255,0.4)' },
  dotActive: { backgroundColor: COLORS.white, width: 22 },
  button: {
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.pill,
    paddingVertical: 16,
    alignItems: 'center',
    ...CARD_SHADOW,
  },
  buttonText: { color: COLORS.blue, fontWeight: '800', fontSize: 16, letterSpacing: 0.3 },
});

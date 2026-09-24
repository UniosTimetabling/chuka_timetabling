import React, { useEffect, useRef } from 'react';
import { View, Text, Image, Animated, Easing, StyleSheet } from 'react-native';
import GradientBackground from './GradientBackground';
import { COLORS } from '../theme/colors';

// The app's branded loading state: a gently pulsing crest with three
// bouncing dots underneath, on the signature aqua gradient — used
// wherever the app previously showed a bare spinner (initial boot,
// restoring a session, first sync).
export default function AppLoadingScreen({ label = 'Loading your timetable…' }) {
  const pulse = useRef(new Animated.Value(0)).current;
  const dot1 = useRef(new Animated.Value(0)).current;
  const dot2 = useRef(new Animated.Value(0)).current;
  const dot3 = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 1100, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 1100, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      ])
    ).start();

    const bounce = (val, delay) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(delay),
          Animated.timing(val, { toValue: 1, duration: 320, useNativeDriver: true }),
          Animated.timing(val, { toValue: 0, duration: 320, useNativeDriver: true }),
          Animated.delay(600 - delay),
        ])
      ).start();

    bounce(dot1, 0);
    bounce(dot2, 130);
    bounce(dot3, 260);
  }, []);

  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.08] });
  const dotStyle = (val) => ({
    transform: [{ translateY: val.interpolate({ inputRange: [0, 1], outputRange: [0, -8] }) }],
    opacity: val.interpolate({ inputRange: [0, 1], outputRange: [0.5, 1] }),
  });

  return (
    <GradientBackground>
      <View style={styles.center}>
        <View style={styles.badge}>
          <Animated.Image
            source={require('../../assets/chuka-logo-circle.png')}
            style={[styles.logo, { transform: [{ scale }] }]}
            resizeMode="contain"
          />
        </View>
        <Text style={styles.title}>Chuka University</Text>
        <Text style={styles.subtitle}>{label}</Text>
        <View style={styles.dotsRow}>
          <Animated.View style={[styles.dot, dotStyle(dot1)]} />
          <Animated.View style={[styles.dot, dotStyle(dot2)]} />
          <Animated.View style={[styles.dot, dotStyle(dot3)]} />
        </View>
      </View>
    </GradientBackground>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24 },
  badge: {
    width: 128,
    height: 128,
    borderRadius: 64,
    backgroundColor: COLORS.white,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 20,
    shadowColor: '#04323D',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.25,
    shadowRadius: 16,
    elevation: 8,
  },
  logo: { width: 96, height: 96 },
  title: { color: COLORS.white, fontSize: 20, fontWeight: '800', letterSpacing: 0.3 },
  subtitle: { color: 'rgba(255,255,255,0.9)', fontSize: 13, marginTop: 6, marginBottom: 20 },
  dotsRow: { flexDirection: 'row' },
  dot: {
    width: 9,
    height: 9,
    borderRadius: 5,
    backgroundColor: COLORS.white,
    marginHorizontal: 5,
  },
});

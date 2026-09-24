import React, { useEffect, useRef } from 'react';
import { View, Text, Image, TouchableOpacity, Animated, Easing, StyleSheet } from 'react-native';
import GradientBackground from '../components/GradientBackground';
import { COLORS, CARD_SHADOW, RADIUS } from '../theme/colors';

export default function WelcomeScreen({ navigation }) {
  const fade = useRef(new Animated.Value(0)).current;
  const rise = useRef(new Animated.Value(24)).current;
  const logoScale = useRef(new Animated.Value(0.7)).current;
  const btnScale = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    Animated.sequence([
      Animated.spring(logoScale, { toValue: 1, friction: 5, tension: 60, useNativeDriver: true }),
      Animated.parallel([
        Animated.timing(fade, { toValue: 1, duration: 450, easing: Easing.out(Easing.ease), useNativeDriver: true }),
        Animated.timing(rise, { toValue: 0, duration: 450, easing: Easing.out(Easing.ease), useNativeDriver: true }),
      ]),
    ]).start();
  }, []);

  function pressIn() {
    Animated.spring(btnScale, { toValue: 0.96, useNativeDriver: true }).start();
  }
  function pressOut() {
    Animated.spring(btnScale, { toValue: 1, friction: 4, useNativeDriver: true }).start();
  }

  return (
    <GradientBackground>
      <View style={styles.container}>
        <View style={{ flex: 1 }} />

        <Animated.View style={[styles.badge, { transform: [{ scale: logoScale }] }]}>
          <Image
            source={require('../../assets/chuka-logo-circle.png')}
            style={styles.logo}
            resizeMode="contain"
          />
        </Animated.View>

        <Animated.View style={{ opacity: fade, transform: [{ translateY: rise }], alignItems: 'center' }}>
          <Text style={styles.welcome}>Welcome to</Text>
          <Text style={styles.title}>Chuka University</Text>
          <Text style={styles.subtitle}>Your timetable, always in your pocket.</Text>
        </Animated.View>

        <View style={{ flex: 1 }} />

        <Animated.View style={{ opacity: fade, transform: [{ translateY: rise }], width: '100%' }}>
          <TouchableOpacity
            activeOpacity={0.9}
            onPressIn={pressIn}
            onPressOut={pressOut}
            onPress={() => navigation.navigate('RoleSelect')}
          >
            <Animated.View style={[styles.button, { transform: [{ scale: btnScale }] }]}>
              <Text style={styles.buttonText}>Get Started</Text>
            </Animated.View>
          </TouchableOpacity>
          <Text style={styles.footer}>Powered by Extt</Text>
        </Animated.View>
      </View>
    </GradientBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', paddingHorizontal: 28, paddingVertical: 48 },
  badge: {
    width: 160,
    height: 160,
    borderRadius: 80,
    backgroundColor: COLORS.white,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 24,
    ...CARD_SHADOW,
  },
  logo: { width: 120, height: 120 },
  welcome: { color: 'rgba(255,255,255,0.9)', fontSize: 16, fontWeight: '500' },
  title: { color: COLORS.white, fontSize: 30, fontWeight: '800', marginTop: 2, textAlign: 'center' },
  subtitle: { color: 'rgba(255,255,255,0.92)', fontSize: 14, marginTop: 10, textAlign: 'center' },
  button: {
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.pill,
    paddingVertical: 16,
    alignItems: 'center',
    ...CARD_SHADOW,
  },
  buttonText: { color: COLORS.blue, fontWeight: '800', fontSize: 16, letterSpacing: 0.3 },
  footer: { color: 'rgba(255,255,255,0.75)', fontSize: 11, textAlign: 'center', marginTop: 14 },
});

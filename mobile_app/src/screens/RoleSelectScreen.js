import React, { useRef } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Animated, Image } from 'react-native';
import GradientBackground from '../components/GradientBackground';
import { COLORS, CARD_SHADOW, RADIUS } from '../theme/colors';

function RoleCard({ title, subtitle, onPress }) {
  const scale = useRef(new Animated.Value(1)).current;
  const pressIn = () => Animated.spring(scale, { toValue: 0.97, useNativeDriver: true }).start();
  const pressOut = () => Animated.spring(scale, { toValue: 1, friction: 4, useNativeDriver: true }).start();

  return (
    <TouchableOpacity activeOpacity={0.9} onPress={onPress} onPressIn={pressIn} onPressOut={pressOut}>
      <Animated.View style={[styles.card, { transform: [{ scale }] }]}>
        <Text style={styles.cardTitle}>{title}</Text>
        <Text style={styles.cardSubtitle}>{subtitle}</Text>
      </Animated.View>
    </TouchableOpacity>
  );
}

export default function RoleSelectScreen({ navigation }) {
  return (
    <GradientBackground>
      <View style={styles.container}>
        <View style={styles.badge}>
          <Image
            source={require('../../assets/chuka-logo-circle.png')}
            style={styles.logo}
            resizeMode="contain"
          />
        </View>
        <Text style={styles.title}>Chuka University</Text>
        <Text style={styles.subtitle}>Who's picking up their timetable?</Text>

        <View style={{ height: 28 }} />

        <RoleCard
          title="I'm a Student"
          subtitle="Enter your registration number"
          onPress={() => navigation.navigate('Login', { role: 'student' })}
        />
        <View style={{ height: 14 }} />
        <RoleCard
          title="I'm a Lecturer"
          subtitle="Enter your full name"
          onPress={() => navigation.navigate('Login', { role: 'lecturer' })}
        />

        <Text style={styles.scanHint}>
          Log in first to scan a friend's shared timetable — sharing is
          student-to-student and lecturer-to-lecturer only.
        </Text>
      </View>
    </GradientBackground>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 24, justifyContent: 'center' },
  badge: {
    width: 92,
    height: 92,
    borderRadius: 46,
    backgroundColor: COLORS.white,
    alignSelf: 'center',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
    ...CARD_SHADOW,
  },
  logo: { width: 68, height: 68 },
  title: { color: COLORS.white, fontSize: 24, fontWeight: '800', textAlign: 'center' },
  subtitle: { color: 'rgba(255,255,255,0.92)', fontSize: 13, textAlign: 'center', marginTop: 4 },
  card: {
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.lg,
    padding: 22,
    alignItems: 'center',
    ...CARD_SHADOW,
  },
  cardTitle: { fontSize: 17, fontWeight: '800', color: COLORS.gray800 },
  cardSubtitle: { fontSize: 12, color: COLORS.gray500, marginTop: 3 },
  scanHint: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 11,
    textAlign: 'center',
    marginTop: 24,
    paddingHorizontal: 12,
  },
});

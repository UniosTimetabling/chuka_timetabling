import React from 'react';
import { StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { GRADIENT_AQUA } from '../theme/colors';

// Wraps a screen in the app's signature aqua → deep teal gradient.
// Use for the welcome screen, auth flow, and screen headers — not for
// every screen, so the gradient stays a meaningful accent rather than
// wallpaper.
export default function GradientBackground({ children, style }) {
  return (
    <LinearGradient
      colors={GRADIENT_AQUA}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[styles.fill, style]}
    >
      {children}
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
});

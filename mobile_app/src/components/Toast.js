import React, { useEffect, useRef, useState } from 'react';
import { Animated, Text, StyleSheet, Easing } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { toastBus } from '../services/toastBus';
import { COLORS, RADIUS, CARD_SHADOW } from '../theme/colors';

const VISIBLE_MS = 4200;

// Mount this once near the root of the app (see App.js). Any code, anywhere,
// can call toastBus.show(message) to surface a small dismissable banner —
// this is where errors (e.g. "can't reach the university network") should
// go instead of a blocking Alert or a buried inline <Text>.
export default function ToastHost() {
  const [toast, setToast] = useState(null);
  const queueRef = useRef([]);
  const opacity = useRef(new Animated.Value(0)).current;
  const translateY = useRef(new Animated.Value(16)).current;
  const hideTimer = useRef(null);
  const toastRef = useRef(null);
  toastRef.current = toast;

  useEffect(() => {
    const unsubscribe = toastBus.subscribe((next) => {
      queueRef.current.push(next);
      if (!toastRef.current) showNext();
    });
    return () => {
      unsubscribe();
      clearTimeout(hideTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function showNext() {
    const next = queueRef.current.shift();
    if (!next) return;
    setToast(next);
    opacity.setValue(0);
    translateY.setValue(16);
    Animated.parallel([
      Animated.timing(opacity, { toValue: 1, duration: 220, useNativeDriver: true }),
      Animated.timing(translateY, {
        toValue: 0,
        duration: 220,
        easing: Easing.out(Easing.ease),
        useNativeDriver: true,
      }),
    ]).start();
    clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(dismiss, VISIBLE_MS);
  }

  function dismiss() {
    Animated.parallel([
      Animated.timing(opacity, { toValue: 0, duration: 180, useNativeDriver: true }),
      Animated.timing(translateY, { toValue: 16, duration: 180, useNativeDriver: true }),
    ]).start(() => {
      setToast(null);
      showNext();
    });
  }

  if (!toast) return null;

  const bg = toast.type === 'success' ? COLORS.blue : COLORS.red;

  return (
    <SafeAreaView style={styles.wrap} edges={['bottom']} pointerEvents="box-none">
      <Animated.View
        style={[styles.toast, { backgroundColor: bg, opacity, transform: [{ translateY }] }]}
      >
        <Text style={styles.text}>{toast.message}</Text>
      </Animated.View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: 'center',
    paddingHorizontal: 16,
  },
  toast: {
    maxWidth: 520,
    width: '100%',
    borderRadius: RADIUS.md,
    paddingVertical: 12,
    paddingHorizontal: 16,
    marginBottom: 10,
    ...CARD_SHADOW,
  },
  text: { color: COLORS.white, fontSize: 13, fontWeight: '600', lineHeight: 18 },
});

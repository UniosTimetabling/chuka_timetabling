import React, { useMemo } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { getRandomMotivation } from '../utils/motivationalQuotes';
import { COLORS, CARD_SHADOW, RADIUS } from '../theme/colors';

export default function MotivationBanner({ userName, role }) {
  const quote = useMemo(() => getRandomMotivation(), []);

  // Students are identified by cohort ("BSc Computer Science — Year 2"),
  // not a personal name — there's no individual student record in the
  // university's system (see backend mobile_api/scope.py) — so we greet
  // generically for students and by first name for lecturers, who do
  // have real individual records.
  const greeting =
    role === 'lecturer' && userName ? `Hi ${userName.split(' ').pop()}` : 'Hi there';

  return (
    <View style={styles.banner}>
      <Text style={styles.greeting}>{greeting}</Text>
      <Text style={styles.quote}>{quote}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.md,
    paddingVertical: 10,
    paddingHorizontal: 14,
    marginHorizontal: 16,
    ...CARD_SHADOW,
  },
  greeting: { color: COLORS.gray800, fontWeight: '800', fontSize: 13, marginBottom: 2 },
  quote: { color: COLORS.gray500, fontSize: 11, lineHeight: 15 },
});

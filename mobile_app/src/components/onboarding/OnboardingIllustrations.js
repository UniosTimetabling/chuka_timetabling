import React from 'react';
import Svg, { Rect, Circle, Line, Path } from 'react-native-svg';
import { COLORS } from '../../theme/colors';

// Small, flat, single-color-accent SVG illustrations for the onboarding
// carousel (see screens/OnboardingScreen.js). Built from plain shapes —
// no image assets — so they stay crisp at any size and match the app's
// existing "white card on the aqua gradient" look used everywhere else
// (see GradientBackground, WelcomeScreen).

const SIZE = 168;

function Card({ children }) {
  return (
    <Svg width={SIZE} height={SIZE} viewBox="0 0 168 168">
      <Circle cx="84" cy="84" r="84" fill="rgba(255,255,255,0.14)" />
      <Circle cx="84" cy="84" r="66" fill={COLORS.white} />
      {children}
    </Svg>
  );
}

// Slide 1 — the timetable grid itself
export function TimetableIllustration() {
  return (
    <Card>
      <Rect x="46" y="50" width="76" height="68" rx="6" fill="none" stroke={COLORS.blue} strokeWidth="3" />
      <Line x1="46" y1="68" x2="122" y2="68" stroke={COLORS.blue} strokeWidth="3" />
      <Line x1="70" y1="50" x2="70" y2="118" stroke={COLORS.aqua} strokeWidth="2.5" />
      <Line x1="96" y1="50" x2="96" y2="118" stroke={COLORS.aqua} strokeWidth="2.5" />
      <Rect x="72" y="72" width="22" height="16" rx="3" fill={COLORS.aqua} />
      <Rect x="98" y="90" width="22" height="16" rx="3" fill={COLORS.blue} />
    </Card>
  );
}

// Slide 2 — live updates / notifications
export function LiveUpdatesIllustration() {
  return (
    <Card>
      <Path
        d="M84 48c-14 0-22 10-22 24v14l-8 12h60l-8-12V72c0-14-8-24-22-24z"
        fill="none"
        stroke={COLORS.blue}
        strokeWidth="3"
        strokeLinejoin="round"
      />
      <Path d="M76 102a8 8 0 0 0 16 0" fill="none" stroke={COLORS.blue} strokeWidth="3" strokeLinecap="round" />
      <Circle cx="104" cy="52" r="9" fill={COLORS.aqua} />
    </Card>
  );
}

// Slide 3 — the menu, where every action lives
export function MenuIllustration() {
  return (
    <Card>
      <Line x1="54" y1="66" x2="114" y2="66" stroke={COLORS.blue} strokeWidth="4" strokeLinecap="round" />
      <Line x1="54" y1="84" x2="114" y2="84" stroke={COLORS.blue} strokeWidth="4" strokeLinecap="round" />
      <Line x1="54" y1="102" x2="114" y2="102" stroke={COLORS.blue} strokeWidth="4" strokeLinecap="round" />
      <Circle cx="120" cy="66" r="5" fill={COLORS.aqua} />
    </Card>
  );
}

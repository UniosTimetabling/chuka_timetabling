// Central palette for the whole app.
// Brand mark: Chuka University crest (assets/chuka-logo.png / chuka-logo-circle.png).
// Primary surface: an aqua → deep teal linear gradient (see GRADIENT_AQUA),
// used behind the welcome, auth, and header surfaces for a bit of depth.
// White cards float on top of the gradient with real elevation (shadows) —
// this app intentionally uses soft shadows/elevation for a "3D card" feel.
export const COLORS = {
  black: '#0A1A1F',
  white: '#FFFFFF',
  blue: '#0C6B8C', // primary accent — echoes the deep end of the gradient
  aqua: '#22B8C7', // secondary accent — echoes the light end of the gradient
  gray50: '#F3FAFB', // page background on flat (non-gradient) screens
  gray100: '#E7F3F5', // subtle row/venue backgrounds
  gray300: '#D6E7EA', // borders
  gray500: '#6E8A90', // secondary/muted text
  gray800: '#1C2E33', // body text
  red: '#C62828', // errors only — kept for genuine failure feedback
};

// Linear gradient stops used behind the welcome/auth flow and screen headers.
// Light aqua (top-left) fading into deep teal-blue (bottom-right).
export const GRADIENT_AQUA = ['#8FF0E6', '#22B8C7', '#0C6B8C'];

// Reusable "floating card" shadow — this is the app's one depth effect,
// used deliberately (form cards, the motivation banner, filled timetable
// cells) rather than scattered everywhere.
export const CARD_SHADOW = {
  shadowColor: '#04323D',
  shadowOffset: { width: 0, height: 6 },
  shadowOpacity: 0.14,
  shadowRadius: 12,
  elevation: 6,
};

export const SOFT_SHADOW = {
  shadowColor: '#04323D',
  shadowOffset: { width: 0, height: 2 },
  shadowOpacity: 0.08,
  shadowRadius: 6,
  elevation: 3,
};

export const RADIUS = {
  sm: 8,
  md: 14,
  lg: 22,
  pill: 999,
};


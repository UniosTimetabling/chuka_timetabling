export const MOTIVATIONAL_QUOTES = [
  'Small steps every day add up to big results. Have a great day at Chuka!',
  "You've prepared for this. Walk into today's classes with confidence.",
  'Discipline today, degree tomorrow. Make today count.',
  'Every lecture you attend is an investment in your future self.',
  'Focus on progress, not perfection. One class at a time.',
  'Your future self will thank you for showing up today.',
  'Great things take time — keep showing up, Comrade.',
  "Today is a fresh page. Write something you'll be proud of.",
  'Consistency beats intensity. Keep going.',
  'You are capable of more than you know. Go get it today!',
];

export function getRandomMotivation() {
  const i = Math.floor(Math.random() * MOTIVATIONAL_QUOTES.length);
  return MOTIVATIONAL_QUOTES[i];
}

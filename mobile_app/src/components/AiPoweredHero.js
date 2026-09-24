import React, { useRef, useState, useEffect, useCallback } from 'react';
import { View, Text, Image, StyleSheet, FlatList, Dimensions } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { COLORS, RADIUS, SOFT_SHADOW } from '../theme/colors';

// Small promo strip: "Powered by EXTT Chuka University" badge + a
// lightweight, self-advancing image carousel. No extra deps beyond
// expo-linear-gradient (already in the project) — everything else is
// FlatList's own paging, so it behaves the same on native and web.
//
// Each slide carries its own headline/subtitle so the photos read as
// an actual hero banner instead of bare images — the badge above
// already covers the "Powered by" attribution, so the overlay on the
// image itself is real copy, not a repeat of that badge.
const SLIDES = [
  {
    id: '1',
    image: require('../../assets/hero-ai-1.jpg'),
    title: 'Your timetable, always up to date',
    subtitle: 'Synced straight from the university system.',
  },
  {
    id: '2',
    image: require('../../assets/hero-ai-2.jpg'),
    title: 'Never miss a class or exam',
    subtitle: 'Get notified before every session.',
  },
];

const { width: SCREEN_WIDTH } = Dimensions.get('window');

// Pick a random starting slide each time the hero mounts, so it isn't
// always the same image up front.
function randomStartIndex() {
  return Math.floor(Math.random() * SLIDES.length);
}

export default function AiPoweredHero() {
  const listRef = useRef(null);
  const [index, setIndex] = useState(randomStartIndex);
  const [cardWidth, setCardWidth] = useState(SCREEN_WIDTH - 32);

  // Once the list has a real width, jump to the randomly chosen slide
  // (scrollToIndex needs getItemLayout, which needs a non-zero cardWidth).
  useEffect(() => {
    if (cardWidth > 0 && index > 0) {
      listRef.current?.scrollToIndex({ index, animated: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardWidth > 0]);

  useEffect(() => {
    // Long enough to actually read the headline + subtitle on each slide
    // before it moves on, not just glimpse it mid-transition.
    const timer = setInterval(() => {
      setIndex((prev) => {
        const next = (prev + 1) % SLIDES.length;
        listRef.current?.scrollToIndex({ index: next, animated: true });
        return next;
      });
    }, 7000);
    return () => clearInterval(timer);
  }, [cardWidth]);

  const onScrollEnd = useCallback(
    (e) => {
      const newIndex = Math.round(e.nativeEvent.contentOffset.x / cardWidth);
      setIndex(newIndex);
    },
    [cardWidth]
  );

  return (
    <View
      style={styles.wrap}
      onLayout={(e) => setCardWidth(e.nativeEvent.layout.width)}
    >
      <View style={styles.badgeRow}>
        <View style={styles.badge}>
          <Image
            source={require('../../assets/chuka-logo-circle.png')}
            style={styles.badgeIcon}
            resizeMode="contain"
          />
          <Text style={styles.badgeText}>Powered by EXTT Chuka University</Text>
        </View>
      </View>

      {cardWidth > 0 && (
        <FlatList
          ref={listRef}
          data={SLIDES}
          keyExtractor={(item) => item.id}
          horizontal
          pagingEnabled
          showsHorizontalScrollIndicator={false}
          onMomentumScrollEnd={onScrollEnd}
          getItemLayout={(_, i) => ({ length: cardWidth, offset: cardWidth * i, index: i })}
          renderItem={({ item }) => (
            <View style={[styles.slide, { width: cardWidth }]}>
              <Image source={item.image} style={styles.slideImage} resizeMode="cover" />
              {/* Taller, darker fade than a plain vignette — the copy below
                  needs a reliable dark region behind it regardless of how
                  bright the photo underneath happens to be. */}
              <LinearGradient
                colors={['transparent', 'rgba(6,18,22,0.35)', 'rgba(6,18,22,0.88)']}
                locations={[0, 0.45, 1]}
                style={styles.slideGradient}
              />
              <View style={styles.slideOverlay}>
                <Text style={styles.slideTitle} numberOfLines={2}>{item.title}</Text>
                {!!item.subtitle && (
                  <Text style={styles.slideSubtitle} numberOfLines={1}>{item.subtitle}</Text>
                )}
              </View>
            </View>
          )}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    marginHorizontal: 16,
    marginTop: 10,
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.md,
    paddingTop: 10,
    paddingBottom: 10,
    overflow: 'hidden',
    ...SOFT_SHADOW,
  },
  badgeRow: {
    paddingHorizontal: 12,
    marginBottom: 8,
  },
  badge: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.gray100,
    borderRadius: RADIUS.pill,
    paddingLeft: 4,
    paddingRight: 10,
    paddingVertical: 3,
  },
  badgeIcon: {
    width: 16,
    height: 16,
    borderRadius: 8,
    marginRight: 6,
    backgroundColor: COLORS.white,
  },
  badgeText: { color: COLORS.blue, fontWeight: '800', fontSize: 11 },
  slide: {
    height: 140,
    paddingHorizontal: 12,
  },
  slideImage: {
    width: '100%',
    height: '100%',
    borderRadius: RADIUS.sm,
  },
  slideGradient: {
    position: 'absolute',
    left: 12,
    right: 12,
    bottom: 0,
    height: '75%',
    borderBottomLeftRadius: RADIUS.sm,
    borderBottomRightRadius: RADIUS.sm,
  },
  slideOverlay: {
    position: 'absolute',
    left: 20,
    right: 20,
    bottom: 12,
  },
  slideTitle: {
    color: COLORS.white,
    fontSize: 16,
    fontWeight: '800',
    lineHeight: 20,
    textShadowColor: 'rgba(0,0,0,0.35)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 3,
  },
  slideSubtitle: {
    marginTop: 3,
    color: 'rgba(255,255,255,0.9)',
    fontSize: 12,
    fontWeight: '500',
    textShadowColor: 'rgba(0,0,0,0.35)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 3,
  },
});

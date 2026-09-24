import React from 'react';
import { View, FlatList, Text, StyleSheet, Linking } from 'react-native';
import * as Sharing from 'expo-sharing';
import { useAuth } from '../context/AuthContext';
import EventCard from '../components/EventCard';
import { COLORS } from '../theme/colors';

export default function EventsScreen({ navigation }) {
  const { events } = useAuth();

  async function handleOpenAttachment(attachment) {
    // Attachments (memos/pictures/PDFs/Word docs) are hosted on the domain
    // and referenced by URL. We open them in the system viewer / share sheet
    // so any file type just works without bundling extra viewers.
    try {
      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(attachment.url);
      } else {
        await Linking.openURL(attachment.url);
      }
    } catch (e) {
      await Linking.openURL(attachment.url);
    }
  }

  return (
    <View style={styles.container}>
      <FlatList
        data={events}
        keyExtractor={(item) => item.id}
        contentContainerStyle={{ paddingTop: 12, paddingBottom: 24 }}
        ListEmptyComponent={
          <Text style={styles.empty}>No events or memos right now. Check back later.</Text>
        }
        renderItem={({ item }) => (
          <EventCard
            event={item}
            onOpen={(e) => navigation.navigate('EventDetail', { event: e })}
            onOpenAttachment={handleOpenAttachment}
          />
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.white },
  empty: { textAlign: 'center', color: COLORS.gray500, marginTop: 40, paddingHorizontal: 24 },
});

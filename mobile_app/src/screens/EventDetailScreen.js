import React from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Linking, Image } from 'react-native';
import * as Sharing from 'expo-sharing';
import FileTypeIcon from '../components/FileTypeIcon';
import { detectFileKind, FILE_KIND_LABEL } from '../utils/fileType';
import { COLORS } from '../theme/colors';

export default function EventDetailScreen({ route }) {
  const { event } = route.params;

  async function openAttachment(att) {
    try {
      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(att.url);
      } else {
        await Linking.openURL(att.url);
      }
    } catch {
      await Linking.openURL(att.url);
    }
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 16 }}>
      <Text style={styles.badge}>{event.type === 'memo' ? 'MEMO' : 'EVENT'}</Text>
      <Text style={styles.title}>{event.title}</Text>
      <Text style={styles.date}>{new Date(event.date).toDateString()}</Text>
      <Text style={styles.description}>{event.description}</Text>

      {event.attachments?.map((att) => {
        const kind = detectFileKind({ url: att.url, name: att.name, mimeType: att.mimeType });
        return (
          <TouchableOpacity key={att.url} style={styles.attachmentCard} onPress={() => openAttachment(att)}>
            {kind === 'image' && <Image source={{ uri: att.url }} style={styles.thumb} resizeMode="cover" />}
            <View style={styles.attachmentRow}>
              <FileTypeIcon kind={kind} size={28} />
              <View style={styles.attachmentInfo}>
                <Text style={styles.attachmentName} numberOfLines={2}>{att.name}</Text>
                <Text style={styles.attachmentAction}>{FILE_KIND_LABEL[kind]} · Open / Share</Text>
              </View>
            </View>
          </TouchableOpacity>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.white },
  badge: {
    alignSelf: 'flex-start',
    fontSize: 10,
    fontWeight: '700',
    color: COLORS.blue,
    borderWidth: 1,
    borderColor: COLORS.blue,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
    marginBottom: 8,
  },
  title: { fontSize: 20, fontWeight: '800', color: COLORS.black, marginBottom: 4 },
  date: { fontSize: 12, color: COLORS.gray500, marginBottom: 12 },
  description: { fontSize: 14, color: COLORS.gray800, lineHeight: 20, marginBottom: 20 },
  attachmentCard: {
    borderWidth: 1,
    borderColor: COLORS.gray300,
    borderRadius: 4,
    padding: 12,
    marginBottom: 10,
  },
  attachmentRow: { flexDirection: 'row', alignItems: 'center' },
  attachmentInfo: { flex: 1, marginLeft: 10 },
  thumb: { width: '100%', height: 160, borderRadius: 4, marginBottom: 8 },
  attachmentName: { fontSize: 13, fontWeight: '600', color: COLORS.black },
  attachmentAction: { fontSize: 12, color: COLORS.blue, marginTop: 4, fontWeight: '600' },
});

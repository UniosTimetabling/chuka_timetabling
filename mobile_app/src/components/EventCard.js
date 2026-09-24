import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import FileTypeIcon from './FileTypeIcon';
import { detectFileKind } from '../utils/fileType';
import { COLORS } from '../theme/colors';

export default function EventCard({ event, onOpen, onOpenAttachment }) {
  return (
    <TouchableOpacity style={styles.card} onPress={() => onOpen(event)} activeOpacity={0.8}>
      <View style={styles.headerRow}>
        <Text style={styles.badge}>{event.type === 'memo' ? 'MEMO' : 'EVENT'}</Text>
        <Text style={styles.date}>{new Date(event.date).toDateString()}</Text>
      </View>
      <Text style={styles.title}>{event.title}</Text>
      <Text style={styles.description} numberOfLines={2}>
        {event.description}
      </Text>
      {event.attachments?.length > 0 && (
        <View style={styles.attachmentsRow}>
          {event.attachments.map((att) => (
            <TouchableOpacity
              key={att.url}
              style={styles.attachmentChip}
              onPress={() => onOpenAttachment(att)}
            >
              <FileTypeIcon
                kind={detectFileKind({ url: att.url, name: att.name, mimeType: att.mimeType })}
                size={16}
              />
              <Text style={styles.attachmentText} numberOfLines={1}>
                {att.name}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: COLORS.white,
    borderRadius: 4,
    padding: 12,
    marginHorizontal: 12,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: COLORS.gray300,
  },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  badge: {
    fontSize: 10,
    fontWeight: '700',
    color: COLORS.blue,
    borderWidth: 1,
    borderColor: COLORS.blue,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 4,
  },
  date: { fontSize: 11, color: COLORS.gray500 },
  title: { fontSize: 15, fontWeight: '700', color: COLORS.black, marginBottom: 2 },
  description: { fontSize: 12, color: COLORS.gray500 },
  attachmentsRow: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 8 },
  attachmentChip: {
    flexDirection: 'row',
    alignItems: 'center',
    maxWidth: '100%',
    borderWidth: 1,
    borderColor: COLORS.gray300,
    borderRadius: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    marginRight: 6,
    marginBottom: 6,
  },
  attachmentText: { fontSize: 11, color: COLORS.gray800, marginLeft: 6, flexShrink: 1 },
});

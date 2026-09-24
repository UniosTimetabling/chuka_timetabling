import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Linking, Alert } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import FileTypeIcon from './FileTypeIcon';
import { detectFileKind, FILE_KIND_LABEL } from '../utils/fileType';
import { COLORS, RADIUS, CARD_SHADOW } from '../theme/colors';

// Shown instead of WeekTimetable when an admin has switched a schedule
// (main timetable or exam timetable) off from Django admin — see
// mobile_api.models.ScheduleVisibility and timetable_builder.py, which
// fold {blocked, message, linkLabel, linkUrl} into the same JSON payload
// TimetableScreen already reads timetable/examTimetable from, so no
// separate fetch or loading state is needed here.
//
// `linkUrl` is opened with Linking.openURL — for a normal https link the
// device browser handles it, and for a direct PDF URL that's exactly
// what makes it download/open in the OS's own PDF viewer, no extra
// library needed.
//
// The button icon follows the END of linkUrl: .pdf -> PDF icon,
// .doc/.docx -> Word icon, anything without a recognised file extension ->
// link icon. So a link that points straight at a PDF shows the PDF icon.
export default function BlockedScheduleNotice({ message, linkLabel, linkUrl }) {
  const kind = detectFileKind({ url: linkUrl, fallback: 'link' });

  async function handleOpenLink() {
    try {
      const canOpen = await Linking.canOpenURL(linkUrl);
      if (!canOpen) throw new Error('unsupported');
      await Linking.openURL(linkUrl);
    } catch {
      Alert.alert('Could not open link', 'Please check your connection and try again.');
    }
  }

  return (
    <View style={styles.box}>
      <View style={styles.iconCircle}>
        <MaterialCommunityIcons name="calendar-clock" size={30} color={COLORS.blue} />
      </View>
      <Text style={styles.message}>
        {message || 'This schedule is currently unavailable.'}
      </Text>
      {!!linkLabel && !!linkUrl && (
        <TouchableOpacity
          style={styles.linkBtn}
          onPress={handleOpenLink}
          activeOpacity={0.85}
          accessibilityRole="button"
          accessibilityLabel={`${linkLabel} (${FILE_KIND_LABEL[kind]})`}
        >
          <FileTypeIcon kind={kind} size={20} color={COLORS.white} />
          <Text style={styles.linkBtnText}>{linkLabel}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    marginHorizontal: 16,
    marginTop: 24,
    padding: 28,
    borderRadius: RADIUS.lg,
    backgroundColor: COLORS.white,
    alignItems: 'center',
    ...CARD_SHADOW,
  },
  iconCircle: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: COLORS.gray100,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  message: {
    color: COLORS.gray800,
    fontSize: 14,
    lineHeight: 21,
    textAlign: 'center',
    marginBottom: 4,
  },
  linkBtn: {
    marginTop: 16,
    paddingVertical: 12,
    paddingHorizontal: 20,
    borderRadius: RADIUS.pill,
    backgroundColor: COLORS.blue,
    flexDirection: 'row',
    alignItems: 'center',
  },
  linkBtnText: { color: COLORS.white, fontWeight: '700', fontSize: 13, marginLeft: 8 },
});

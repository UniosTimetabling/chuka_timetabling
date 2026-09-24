import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import * as DocumentPicker from 'expo-document-picker';
import { useAuth } from '../context/AuthContext';
import { api } from '../api/api';
import { toastBus } from '../services/toastBus';
import FileTypeIcon from '../components/FileTypeIcon';
import { detectFileKind } from '../utils/fileType';
import { COLORS, RADIUS, SOFT_SHADOW } from '../theme/colors';
import { FEEDBACK_MAX_ATTACHMENTS, FEEDBACK_MAX_ATTACHMENT_BYTES } from '../config/config';

// Lets a student or lecturer report an issue (a timetable clash, a wrong
// venue, an app bug — anything) straight to the timetabling office, with
// optional attachments (a screenshot, a scanned form, a photo of a notice
// board). Posts to POST /api/mobile/feedback/ — see mobile_api/
// views_feedback.py on the backend and the API_CONTRACT note in config.js.
export default function FeedbackScreen({ navigation }) {
  const { user } = useAuth();

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [admissionNumber, setAdmissionNumber] = useState(user?.role === 'student' ? user?.regNo || '' : '');
  const [message, setMessage] = useState('');
  const [attachments, setAttachments] = useState([]); // [{ uri, name, size, mimeType }]
  const [submitting, setSubmitting] = useState(false);

  function formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function handlePickAttachment() {
    if (attachments.length >= FEEDBACK_MAX_ATTACHMENTS) {
      toastBus.show(`You can attach up to ${FEEDBACK_MAX_ATTACHMENTS} files.`);
      return;
    }
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['image/*', 'application/pdf', 'application/msword',
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
        multiple: true,
        copyToCacheDirectory: true,
      });
      if (result.canceled) return;

      const picked = result.assets || [];
      const room = FEEDBACK_MAX_ATTACHMENTS - attachments.length;
      const accepted = [];
      let rejectedForSize = false;

      for (const file of picked.slice(0, room)) {
        if (file.size && file.size > FEEDBACK_MAX_ATTACHMENT_BYTES) {
          rejectedForSize = true;
          continue;
        }
        accepted.push(file);
      }

      if (picked.length > room) {
        toastBus.show(`Only added ${room} more — ${FEEDBACK_MAX_ATTACHMENTS} files max.`);
      }
      if (rejectedForSize) {
        toastBus.show('One or more files were skipped — 15MB max per file.');
      }

      setAttachments((prev) => [...prev, ...accepted]);
    } catch (e) {
      toastBus.show("Couldn't open the file picker — try again.");
    }
  }

  function removeAttachment(uri) {
    setAttachments((prev) => prev.filter((f) => f.uri !== uri));
  }

  async function handleSubmit() {
    if (submitting) return;
    if (!fullName.trim() || !email.trim() || !message.trim()) {
      toastBus.show('Please fill in your name, email, and message.');
      return;
    }
    if (message.trim().length < 10) {
      toastBus.show('Message is too short — please add a bit more detail.');
      return;
    }

    setSubmitting(true);
    try {
      const formData = new FormData();
      formData.append('full_name', fullName.trim());
      formData.append('email', email.trim());
      formData.append('message', message.trim());
      if (admissionNumber.trim()) formData.append('admission_number', admissionNumber.trim());
      if (user?.id) formData.append('userId', user.id);
      if (user?.role) formData.append('role', user.role);

      attachments.forEach((file, i) => {
        formData.append('attachments', {
          uri: file.uri,
          name: file.name || `attachment-${i}`,
          type: file.mimeType || 'application/octet-stream',
        });
      });

      await api.submitFeedback(formData);
      toastBus.show('Thanks — your feedback has been sent.', 'success');
      navigation.goBack();
    } catch (e) {
      toastBus.show(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Text style={styles.hint}>
          Spotted a timetable clash, a wrong venue, or something else worth flagging? Let the
          timetabling office know below — you can attach a screenshot or document too.
        </Text>

        <Text style={styles.label}>Full Name</Text>
        <TextInput
          style={styles.input}
          value={fullName}
          onChangeText={setFullName}
          placeholder="Your name"
          placeholderTextColor={COLORS.gray500}
        />

        <Text style={styles.label}>Email</Text>
        <TextInput
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          placeholder="you@example.com"
          placeholderTextColor={COLORS.gray500}
          autoCapitalize="none"
          keyboardType="email-address"
        />

        <Text style={styles.label}>Admission Number (optional)</Text>
        <TextInput
          style={styles.input}
          value={admissionNumber}
          onChangeText={setAdmissionNumber}
          placeholder="e.g. EB1/66791/23"
          placeholderTextColor={COLORS.gray500}
          autoCapitalize="characters"
        />

        <Text style={styles.label}>Message</Text>
        <TextInput
          style={[styles.input, styles.textArea]}
          value={message}
          onChangeText={setMessage}
          placeholder="Describe the issue..."
          placeholderTextColor={COLORS.gray500}
          multiline
          textAlignVertical="top"
        />

        <Text style={styles.label}>Attachments (optional)</Text>
        {attachments.map((file) => (
          <View key={file.uri} style={styles.attachmentRow}>
            <FileTypeIcon
              kind={detectFileKind({ name: file.name, url: file.uri, mimeType: file.mimeType })}
              size={24}
              style={{ marginRight: 10 }}
            />
            <View style={{ flex: 1 }}>
              <Text style={styles.attachmentName} numberOfLines={1}>{file.name}</Text>
              {file.size ? <Text style={styles.attachmentSize}>{formatSize(file.size)}</Text> : null}
            </View>
            <TouchableOpacity onPress={() => removeAttachment(file.uri)}>
              <Text style={styles.removeText}>Remove</Text>
            </TouchableOpacity>
          </View>
        ))}
        {attachments.length < FEEDBACK_MAX_ATTACHMENTS ? (
          <TouchableOpacity style={styles.attachBtn} onPress={handlePickAttachment}>
            <Text style={styles.attachBtnText}>+ Add Photo / Document</Text>
          </TouchableOpacity>
        ) : null}

        <TouchableOpacity
          style={[styles.submitBtn, submitting && styles.submitBtnDisabled]}
          onPress={handleSubmit}
          disabled={submitting}
        >
          {submitting ? (
            <ActivityIndicator color={COLORS.white} size="small" />
          ) : (
            <Text style={styles.submitBtnText}>Send Feedback</Text>
          )}
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: COLORS.gray50 },
  scroll: { padding: 16, paddingBottom: 40 },
  hint: { color: COLORS.gray500, fontSize: 12, lineHeight: 17, marginBottom: 16 },
  label: {
    fontSize: 12,
    fontWeight: '700',
    color: COLORS.gray500,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
    marginBottom: 6,
    marginTop: 14,
  },
  input: {
    backgroundColor: COLORS.white,
    borderWidth: 1,
    borderColor: COLORS.gray300,
    borderRadius: RADIUS.sm,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: COLORS.gray800,
  },
  textArea: { minHeight: 110 },
  attachmentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.white,
    borderWidth: 1,
    borderColor: COLORS.gray300,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginTop: 8,
  },
  attachmentName: { fontSize: 13, fontWeight: '600', color: COLORS.black },
  attachmentSize: { fontSize: 11, color: COLORS.gray500, marginTop: 2 },
  removeText: { color: COLORS.red, fontSize: 12, fontWeight: '700', marginLeft: 10 },
  attachBtn: {
    marginTop: 10,
    borderWidth: 1,
    borderColor: COLORS.blue,
    borderStyle: 'dashed',
    borderRadius: RADIUS.sm,
    paddingVertical: 12,
    alignItems: 'center',
  },
  attachBtnText: { color: COLORS.blue, fontWeight: '700', fontSize: 13 },
  submitBtn: {
    backgroundColor: COLORS.blue,
    borderRadius: RADIUS.sm,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 26,
    ...SOFT_SHADOW,
  },
  submitBtnDisabled: { opacity: 0.6 },
  submitBtnText: { color: COLORS.white, fontWeight: '700', fontSize: 14 },
});

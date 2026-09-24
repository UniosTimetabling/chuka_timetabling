import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { useAuth } from '../context/AuthContext';
import { COLORS } from '../theme/colors';

export default function SettingsScreen({ navigation }) {
  const { user, logout } = useAuth();

  return (
    <View style={styles.container}>
      <Text style={styles.label}>Logged in as</Text>
      <Text style={styles.value}>{user?.name}</Text>
      <Text style={styles.subvalue}>
        {user?.role === 'student' ? `Reg No: ${user?.regNo}` : 'Lecturer'}
      </Text>

      <TouchableOpacity
        style={styles.savedBtn}
        onPress={() => navigation.navigate('SavedTimetables')}
      >
        <Text style={styles.savedText}>Saved Timetables</Text>
      </TouchableOpacity>

      <TouchableOpacity
        style={styles.savedBtn}
        onPress={() => navigation.navigate('Feedback')}
      >
        <Text style={styles.savedText}>Send Feedback</Text>
      </TouchableOpacity>

      <TouchableOpacity style={styles.logoutBtn} onPress={logout}>
        <Text style={styles.logoutText}>Log Out</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.white, padding: 24 },
  label: { fontSize: 12, color: COLORS.gray500, marginTop: 12 },
  value: { fontSize: 18, fontWeight: '700', color: COLORS.black },
  subvalue: { fontSize: 13, color: COLORS.gray500, marginBottom: 24 },
  savedBtn: {
    backgroundColor: COLORS.gray100,
    borderRadius: 4,
    paddingVertical: 14,
    alignItems: 'center',
    marginBottom: 12,
  },
  savedText: { color: COLORS.blue, fontWeight: '700' },
  logoutBtn: {
    backgroundColor: COLORS.red,
    borderRadius: 4,
    paddingVertical: 14,
    alignItems: 'center',
  },
  logoutText: { color: COLORS.white, fontWeight: '700' },
});

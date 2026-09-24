import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  FlatList,
  Image,
  ScrollView,
} from 'react-native';
import GradientBackground from '../components/GradientBackground';
import { useAuth } from '../context/AuthContext';
import { toastBus } from '../services/toastBus';
import { COLORS, CARD_SHADOW, RADIUS } from '../theme/colors';

export default function LoginScreen({ route }) {
  const { role } = route.params; // 'student' | 'lecturer'
  const { loginStudent, loginLecturer } = useAuth();
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [matches, setMatches] = useState(null); // set when backend returns 300 (ambiguous name)
  const [departmentChoices, setDepartmentChoices] = useState(null); // set when backend returns 300 (unrecognised program code, step 1)
  const [programChoices, setProgramChoices] = useState(null); // set when backend returns 300 (department picked, step 2)

  const isStudent = role === 'student';

  async function attemptLogin(inputValue, programId, departmentId) {
    setError('');
    setMatches(null);
    // Picking a department narrows down to that department's programs on
    // the next response — don't clear programChoices in that case, or the
    // list flashes empty before the narrowed one arrives. Every other path
    // (fresh submit, or a program finally being chosen) should clear both.
    if (!departmentId) {
      setDepartmentChoices(null);
      setProgramChoices(null);
    }
    setLoading(true);
    try {
      if (isStudent) {
        // On a retry (departmentId and/or programId set) this is the same
        // regNo the student already typed — we're not asking them to type
        // it again, just to pick their department then their program. The
        // year of study came back already resolved in the first response,
        // so the backend re-derives it from this same reg number rather
        // than needing it passed in again.
        await loginStudent(inputValue, programId, departmentId);
      } else {
        await loginLecturer(inputValue);
      }
      // Navigation to Timetable happens automatically once `user` is set
      // in AuthContext — see AppNavigator's conditional stack.
    } catch (e) {
      if (e.status === 300 && e.body?.needsDepartmentSelection && e.body?.departments?.length) {
        setDepartmentChoices(e.body.departments);
        setError("We couldn't match the program code in that registration number — pick your department below.");
      } else if (e.status === 300 && e.body?.needsProgramSelection && e.body?.programs?.length) {
        setDepartmentChoices(null);
        setProgramChoices(e.body.programs);
        setError('Now pick your program below.');
      } else if (e.status === 300 && e.body?.matches?.length) {
        setMatches(e.body.matches);
        setError('That name matches more than one lecturer — pick yours below.');
      } else if (e.isNetworkError || e.isTimeout) {
        // Can't reach the university server at all — surface this as a
        // toast rather than inline text, since it's about connectivity,
        // not something wrong with what the student typed.
        toastBus.show(e.message);
      } else {
        setError(e.message || 'Something went wrong. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit() {
    if (!value.trim()) {
      setError(isStudent ? 'Please enter your registration number.' : 'Please enter your name.');
      return;
    }
    attemptLogin(value);
  }

  return (
    <GradientBackground>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.badge}>
            <Image
              source={require('../../assets/chuka-logo-circle.png')}
              style={styles.logo}
              resizeMode="contain"
            />
          </View>

          <View style={styles.card}>
            <Text style={styles.title}>{isStudent ? 'Student Login' : 'Lecturer Login'}</Text>
            <Text style={styles.helper}>
              {isStudent
                ? "Enter your registration number to fetch your program's timetable."
                : 'Enter your full name to fetch the units you teach.'}
            </Text>

            <TextInput
              style={styles.input}
              placeholder={isStudent ? 'e.g. EB1/66791/23' : 'e.g. Dr. Jane Mwangi'}
              placeholderTextColor={COLORS.gray500}
              autoCapitalize={isStudent ? 'characters' : 'words'}
              value={value}
              onChangeText={setValue}
              editable={!loading}
            />

            {!!error && <Text style={styles.error}>{error}</Text>}

            {matches ? (
              <FlatList
                data={matches}
                keyExtractor={(m) => m.id}
                style={{ marginTop: 4, marginBottom: 12 }}
                renderItem={({ item }) => (
                  <TouchableOpacity
                    style={styles.matchRow}
                    onPress={() => attemptLogin(item.name)}
                    disabled={loading}
                  >
                    <Text style={styles.matchName}>{item.name}</Text>
                    {!!item.department && <Text style={styles.matchDept}>{item.department}</Text>}
                  </TouchableOpacity>
                )}
              />
            ) : null}

            {departmentChoices ? (
              <FlatList
                data={departmentChoices}
                keyExtractor={(d) => String(d.id)}
                style={{ marginTop: 4, marginBottom: 12 }}
                renderItem={({ item }) => (
                  <TouchableOpacity
                    style={styles.matchRow}
                    onPress={() => attemptLogin(value, null, item.id)}
                    disabled={loading}
                  >
                    <Text style={styles.matchName}>{item.name}</Text>
                  </TouchableOpacity>
                )}
              />
            ) : null}

            {programChoices ? (
              <FlatList
                data={programChoices}
                keyExtractor={(p) => String(p.id)}
                style={{ marginTop: 4, marginBottom: 12 }}
                renderItem={({ item }) => (
                  <TouchableOpacity
                    style={styles.matchRow}
                    onPress={() => attemptLogin(value, item.id)}
                    disabled={loading}
                  >
                    <Text style={styles.matchName}>{item.name}</Text>
                  </TouchableOpacity>
                )}
              />
            ) : null}

            <TouchableOpacity style={styles.button} onPress={handleSubmit} disabled={loading}>
              {loading ? (
                <ActivityIndicator color={COLORS.white} />
              ) : (
                <Text style={styles.buttonText}>Fetch My Timetable</Text>
              )}
            </TouchableOpacity>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </GradientBackground>
  );
}

const styles = StyleSheet.create({
  scrollContent: { flexGrow: 1, justifyContent: 'center', padding: 24, paddingTop: 56 },
  badge: {
    width: 84,
    height: 84,
    borderRadius: 42,
    backgroundColor: COLORS.white,
    alignSelf: 'center',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: -42,
    zIndex: 2,
    ...CARD_SHADOW,
  },
  logo: { width: 60, height: 60 },
  card: {
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.lg,
    padding: 24,
    paddingTop: 56,
    ...CARD_SHADOW,
  },
  title: { fontSize: 21, fontWeight: '800', color: COLORS.gray800, marginBottom: 6, textAlign: 'center' },
  helper: { fontSize: 13, color: COLORS.gray500, marginBottom: 22, textAlign: 'center' },
  input: {
    borderWidth: 1.5,
    borderColor: COLORS.gray300,
    borderRadius: RADIUS.md,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 15,
    marginBottom: 8,
    color: COLORS.gray800,
    backgroundColor: COLORS.gray50,
  },
  error: { color: COLORS.red, fontSize: 12, marginBottom: 8 },
  matchRow: {
    borderWidth: 1,
    borderColor: COLORS.gray300,
    borderRadius: RADIUS.sm,
    paddingVertical: 10,
    paddingHorizontal: 12,
    marginBottom: 6,
  },
  matchName: { fontSize: 14, fontWeight: '600', color: COLORS.gray800 },
  matchDept: { fontSize: 11, color: COLORS.gray500, marginTop: 2 },
  button: {
    backgroundColor: COLORS.blue,
    borderRadius: RADIUS.pill,
    paddingVertical: 16,
    alignItems: 'center',
    marginTop: 10,
    ...CARD_SHADOW,
  },
  buttonText: { color: COLORS.white, fontWeight: '800', fontSize: 15, letterSpacing: 0.2 },
});

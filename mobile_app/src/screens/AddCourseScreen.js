import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useAuth } from '../context/AuthContext';
import { api } from '../api/api';
import { toastBus } from '../services/toastBus';
import { COLORS, RADIUS, SOFT_SHADOW } from '../theme/colors';

// Search a course code (or several, comma-separated) and add it to your own
// mobile timetable on top of your normal program/year curriculum (student)
// or CourseAllocation.lecturer assignments (lecturer). Covers cross-cutting
// electives a student picks up outside their program, and sections a
// lecturer co-teaches/guest-lectures that aren't recorded against them —
// search the base code ("COMS 101") to see every section and pick theirs.
// See mobile_api/course_search.py + personal_entries.py on the backend.
export default function AddCourseScreen() {
  const { user, refreshNow } = useAuth();
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [results, setResults] = useState([]);
  const [notFound, setNotFound] = useState([]);
  const [pendingIds, setPendingIds] = useState(new Set());
  const [mine, setMine] = useState([]);
  const [mineLoading, setMineLoading] = useState(true);

  const loadMine = useCallback(async () => {
    try {
      const res = await api.getMyCourses(user.id, user.role, user.regNo);
      setMine(res.courses || []);
    } catch (e) {
      // Non-fatal — the "My added courses" list just stays as-is; the
      // search flow below still works even if this particular fetch fails.
    } finally {
      setMineLoading(false);
    }
  }, [user]);

  useEffect(() => {
    loadMine();
  }, [loadMine]);

  function setPending(id, isPending) {
    setPendingIds((prev) => {
      const next = new Set(prev);
      if (isPending) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function handleSearch() {
    if (!query.trim() || searching) return;
    setSearching(true);
    setHasSearched(true);
    try {
      const res = await api.searchCourses(query, user.id, user.role, user.regNo);
      setResults(res.results || []);
      setNotFound(res.notFound || []);
    } catch (e) {
      setResults([]);
      setNotFound([]);
      toastBus.show(e.message);
    } finally {
      setSearching(false);
    }
  }

  async function handleAdd(item) {
    setPending(item.id, true);
    try {
      const res = await api.addCourses(user.id, user.role, user.regNo, [item.id]);
      if (res.added?.includes(item.id) || res.alreadyAdded?.includes(item.id)) {
        setResults((prev) => prev.map((r) => (r.id === item.id ? { ...r, isAdded: true } : r)));
        toastBus.show(`Added ${item.courseCode} to your timetable.`, 'success');
        await loadMine();
        refreshNow(true);
      } else if (res.invalid?.includes(item.id)) {
        toastBus.show("That section can't be added on its own — search its base course code instead.");
      }
    } catch (e) {
      toastBus.show(e.message);
    } finally {
      setPending(item.id, false);
    }
  }

  async function handleRemove(allocationId) {
    setPending(allocationId, true);
    try {
      await api.removeCourse(user.id, user.role, user.regNo, allocationId);
      setMine((prev) => prev.filter((c) => c.allocationId !== allocationId));
      setResults((prev) =>
        prev.map((r) => (r.id === allocationId ? { ...r, isAdded: false } : r))
      );
      refreshNow(true);
    } catch (e) {
      toastBus.show(e.message);
    } finally {
      setPending(allocationId, false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <FlatList
        style={styles.list}
        contentContainerStyle={{ paddingBottom: 32 }}
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={
          <View>
            <View style={styles.searchBox}>
              <TextInput
                style={styles.input}
                value={query}
                onChangeText={setQuery}
                placeholder="e.g. COMS 101, PHYS 342-A"
                placeholderTextColor={COLORS.gray500}
                autoCapitalize="characters"
                returnKeyType="search"
                onSubmitEditing={handleSearch}
              />
              <TouchableOpacity
                style={[styles.searchBtn, (!query.trim() || searching) && styles.searchBtnDisabled]}
                onPress={handleSearch}
                disabled={!query.trim() || searching}
              >
                {searching ? (
                  <ActivityIndicator color={COLORS.white} size="small" />
                ) : (
                  <Text style={styles.searchBtnText}>Search</Text>
                )}
              </TouchableOpacity>
            </View>
            <Text style={styles.hint}>
              Search one or several codes, comma-separated. Type just the base code (like
              "COMS 101") to see every section — handy if you co-teach or need to pick out your
              own group from a list.
            </Text>

            {notFound.length > 0 ? (
              <View style={styles.notFoundBox}>
                <Text style={styles.notFoundText}>
                  No match for: {notFound.join(', ')}
                </Text>
              </View>
            ) : null}

            {hasSearched && !searching ? (
              <Text style={styles.sectionLabel}>
                {results.length ? `${results.length} result${results.length === 1 ? '' : 's'}` : 'No results'}
              </Text>
            ) : null}
          </View>
        }
        data={results}
        keyExtractor={(item) => String(item.id)}
        renderItem={({ item }) => (
          <ResultRow
            item={item}
            pending={pendingIds.has(item.id)}
            onAdd={() => handleAdd(item)}
          />
        )}
        ListFooterComponent={
          <View style={styles.mineSection}>
            <Text style={styles.sectionLabel}>My added courses</Text>
            {mineLoading ? (
              <ActivityIndicator color={COLORS.blue} style={{ marginTop: 12 }} />
            ) : mine.length === 0 ? (
              <Text style={styles.emptyMine}>
                Nothing added yet — search above and tap "Add" on a course.
              </Text>
            ) : (
              mine.map((c) => (
                <View key={c.allocationId} style={styles.mineRow}>
                  <View style={{ flex: 1 }}>
                    <View style={styles.mineTitleRow}>
                      <Text style={styles.mineCourse}>{c.courseCode}</Text>
                      {c.scheduled === false ? (
                        <View style={styles.unscheduledBadge}>
                          <Text style={styles.unscheduledBadgeText}>NOT YET ON TIMETABLE</Text>
                        </View>
                      ) : null}
                    </View>
                    <Text style={styles.mineName} numberOfLines={1}>{c.courseName}</Text>
                    <Text style={styles.mineLecturer}>{c.lecturer}</Text>
                    {c.scheduled === false ? (
                      <Text style={styles.unscheduledHint}>
                        Added, but it doesn't have a venue/time slot yet — it'll appear on your
                        main timetable as soon as it's scheduled.
                      </Text>
                    ) : null}
                  </View>
                  <TouchableOpacity
                    style={styles.removeBtn}
                    onPress={() => handleRemove(c.allocationId)}
                    disabled={pendingIds.has(c.allocationId)}
                  >
                    {pendingIds.has(c.allocationId) ? (
                      <ActivityIndicator color={COLORS.red} size="small" />
                    ) : (
                      <Text style={styles.removeBtnText}>Remove</Text>
                    )}
                  </TouchableOpacity>
                </View>
              ))
            )}
          </View>
        }
      />
    </KeyboardAvoidingView>
  );
}

function ResultRow({ item, pending, onAdd }) {
  return (
    <View style={styles.resultRow}>
      <View style={{ flex: 1 }}>
        <View style={styles.resultTitleRow}>
          <Text style={styles.resultCode}>{item.courseCode}</Text>
          {item.isCombinedGroup ? (
            <View style={styles.groupBadge}>
              <Text style={styles.groupBadgeText}>GROUP</Text>
            </View>
          ) : null}
        </View>
        <Text style={styles.resultName} numberOfLines={2}>{item.courseName}</Text>
        <Text style={styles.resultMeta} numberOfLines={1}>
          {[item.lecturer, item.program, item.year ? `Year ${item.year}` : null]
            .filter(Boolean)
            .join(' • ')}
        </Text>
      </View>
      <TouchableOpacity
        style={[styles.addBtn, item.isAdded && styles.addBtnDone]}
        onPress={onAdd}
        disabled={item.isAdded || pending}
      >
        {pending ? (
          <ActivityIndicator color={COLORS.white} size="small" />
        ) : (
          <Text style={styles.addBtnText}>{item.isAdded ? 'Added' : 'Add'}</Text>
        )}
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: COLORS.gray50 },
  list: { flex: 1, paddingHorizontal: 16 },
  searchBox: { flexDirection: 'row', marginTop: 16 },
  input: {
    flex: 1,
    backgroundColor: COLORS.white,
    borderWidth: 1,
    borderColor: COLORS.gray300,
    borderRadius: RADIUS.sm,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: COLORS.gray800,
    marginRight: 8,
  },
  searchBtn: {
    backgroundColor: COLORS.blue,
    borderRadius: RADIUS.sm,
    paddingHorizontal: 16,
    justifyContent: 'center',
    alignItems: 'center',
    minWidth: 76,
  },
  searchBtnDisabled: { opacity: 0.5 },
  searchBtnText: { color: COLORS.white, fontWeight: '700', fontSize: 13 },
  hint: { color: COLORS.gray500, fontSize: 11, marginTop: 8, lineHeight: 16 },
  notFoundBox: {
    marginTop: 10,
    backgroundColor: '#FDECEC',
    borderRadius: RADIUS.sm,
    padding: 10,
  },
  notFoundText: { color: COLORS.red, fontSize: 12, fontWeight: '600' },
  sectionLabel: {
    marginTop: 18,
    marginBottom: 8,
    fontSize: 12,
    fontWeight: '800',
    color: COLORS.gray500,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  resultRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.sm,
    padding: 12,
    marginBottom: 8,
    ...SOFT_SHADOW,
  },
  resultTitleRow: { flexDirection: 'row', alignItems: 'center' },
  resultCode: { fontWeight: '800', fontSize: 13, color: COLORS.black },
  groupBadge: {
    marginLeft: 6,
    backgroundColor: COLORS.gray100,
    borderRadius: RADIUS.pill,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  groupBadgeText: { fontSize: 9, fontWeight: '800', color: COLORS.blue },
  resultName: { fontSize: 12, color: COLORS.gray800, marginTop: 2 },
  resultMeta: { fontSize: 11, color: COLORS.gray500, marginTop: 2 },
  addBtn: {
    backgroundColor: COLORS.blue,
    borderRadius: RADIUS.pill,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginLeft: 10,
    minWidth: 64,
    alignItems: 'center',
  },
  addBtnDone: { backgroundColor: COLORS.gray300 },
  addBtnText: { color: COLORS.white, fontWeight: '700', fontSize: 12 },
  mineSection: { marginTop: 8 },
  emptyMine: { color: COLORS.gray500, fontSize: 12, marginTop: 4 },
  mineRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.white,
    borderRadius: RADIUS.sm,
    padding: 12,
    marginBottom: 8,
    ...SOFT_SHADOW,
  },
  mineTitleRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap' },
  mineCourse: { fontWeight: '800', fontSize: 13, color: COLORS.black },
  unscheduledBadge: {
    marginLeft: 6,
    backgroundColor: '#FDECEC',
    borderRadius: RADIUS.pill,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  unscheduledBadgeText: { fontSize: 8, fontWeight: '800', color: COLORS.red, letterSpacing: 0.3 },
  unscheduledHint: { fontSize: 10, color: COLORS.gray500, marginTop: 4, lineHeight: 14 },
  mineName: { fontSize: 12, color: COLORS.gray800, marginTop: 2 },
  mineLecturer: { fontSize: 11, color: COLORS.gray500, marginTop: 2 },
  removeBtn: {
    marginLeft: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: RADIUS.pill,
    borderWidth: 1,
    borderColor: COLORS.red,
    minWidth: 76,
    alignItems: 'center',
  },
  removeBtnText: { color: COLORS.red, fontWeight: '700', fontSize: 12 },
});

import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { api } from '../api/api';
import { storage } from '../services/storage';
import { syncNow } from '../services/syncManager';
import { onConnectivityChange } from '../services/netStatus';
import { requestNotificationPermissions } from '../services/notificationManager';
import { toastBus } from '../services/toastBus';
import { SYNC_INTERVAL_MS } from '../config/config';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // { id, name, role, regNo? }
  const [timetable, setTimetable] = useState(null);
  const [examTimetable, setExamTimetable] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncStatus, setSyncStatus] = useState({ synced: false, updated: false });
  // First-run guidance flags — both default to `true` (nothing to show)
  // until the cold-start effect below reads their real, possibly-false
  // value from AsyncStorage, so a slow read never causes either screen to
  // flash on for a returning user.
  const [onboardingSeen, setOnboardingSeen] = useState(true);
  const [menuTourSeen, setMenuTourSeen] = useState(true);
  const intervalRef = useRef(null);
  // Tracks the last failure reason so background (non-manual) syncs only
  // toast once when they first start failing, instead of re-toasting every
  // SYNC_INTERVAL_MS while a student is simply off campus.
  const lastReasonRef = useRef(null);

  // Restore session on cold start
  useEffect(() => {
    (async () => {
      const savedAuth = await storage.getAuth();
      const savedTimetable = await storage.getTimetable();
      const savedExamTimetable = await storage.getExamTimetable();
      const savedEvents = await storage.getEvents();
      const savedOnboardingSeen = await storage.getOnboardingSeen();
      const savedMenuTourSeen = await storage.getMenuTourSeen();
      if (savedAuth) setUser(savedAuth);
      if (savedTimetable) setTimetable(savedTimetable);
      if (savedExamTimetable) setExamTimetable(savedExamTimetable);
      if (savedEvents) setEvents(savedEvents);
      setOnboardingSeen(!!savedOnboardingSeen);
      setMenuTourSeen(!!savedMenuTourSeen);
      await requestNotificationPermissions();
      setLoading(false);
    })();
  }, []);

  // Whenever we have a logged-in user, sync now, then keep syncing:
  // - on a fixed short interval while foregrounded (SYNC_INTERVAL_MS)
  // - immediately whenever connectivity flips from offline -> online
  // - immediately whenever the app comes back to the foreground (see the
  //   AppState effect below) — covers the "I switched apps for a minute
  //   and came back" case without waiting for the next interval tick
  //
  // Note: JS timers (setInterval) only run while the app is in the
  // foreground — that's a platform limitation, not something this app can
  // work around without a native push service (Expo push notifications +
  // a backend trigger). So this makes updates feel live *while the app is
  // open*, which is what was actually static before (it only ever synced
  // once, on cold start).
  useEffect(() => {
    if (!user) return undefined;

    runSync();
    intervalRef.current = setInterval(runSync, SYNC_INTERVAL_MS);
    const unsubscribe = onConnectivityChange((online) => {
      if (online) runSync();
    });

    return () => {
      clearInterval(intervalRef.current);
      unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  // Re-sync the moment the app is brought back to the foreground (opened
  // from the app switcher, unlocked screen, etc.) rather than waiting for
  // the next SYNC_INTERVAL_MS tick — this is the main fix for "I have to
  // close and reopen the app for changes to show".
  useEffect(() => {
    if (!user) return undefined;

    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        runSync();
      }
    });

    return () => subscription.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  async function runSync(manual = false) {
    if (!user) return;
    const result = await syncNow({ userId: user.id, role: user.role, regNo: user.regNo });
    setSyncStatus(result);

    if (result.updated) {
      const [freshTimetable, freshExamTimetable, freshEvents] = await Promise.all([
        storage.getTimetable(),
        storage.getExamTimetable(),
        storage.getEvents(),
      ]);
      setTimetable(freshTimetable);
      setExamTimetable(freshExamTimetable);
      setEvents(freshEvents);
    }

    // Data already loaded from local storage on cold start, and every
    // successful sync above saves fresh data straight back to storage — so
    // whatever we last fetched keeps working while offline. Here we just
    // decide whether this particular failure deserves a toast: always for
    // a manual (user-tapped) refresh, and for background syncs only the
    // first time they start failing (not offline, which already has its
    // own steady indicator in SyncStatusBar).
    if (!result.synced && (result.reason === 'network' || result.reason === 'timeout')) {
      if (manual || lastReasonRef.current !== result.reason) {
        toastBus.show(result.message);
      }
    }
    lastReasonRef.current = result.synced ? null : result.reason;
  }

  async function loginStudent(regNo, programId, departmentId) {
    const { user: loggedInUser } = await api.loginStudent(regNo.trim(), programId, departmentId);
    await completeLogin(loggedInUser);
    return loggedInUser;
  }

  async function loginLecturer(name) {
    const { user: loggedInUser } = await api.loginLecturer(name.trim());
    await completeLogin(loggedInUser);
    return loggedInUser;
  }

  // Shared by both login paths. AppNavigator switches to TimetableScreen
  // the instant `user` becomes truthy — so if we set `user` right after
  // login and let the first course/exam-timetable fetch happen in the
  // background (as before), the screen mounts while `timetable` is still
  // null and shows "no course curriculum for that program" until that
  // fetch finishes a moment later. It was self-correcting, but only once
  // the background sync landed — which made it look like only a reload
  // (which reads the by-then-cached data straight from storage) could
  // fix it. Doing the first sync here, before `setUser`, means the
  // screen only ever mounts once real data is already sitting in state.
  async function completeLogin(loggedInUser) {
    await storage.saveAuth(loggedInUser);
    const result = await syncNow({
      userId: loggedInUser.id,
      role: loggedInUser.role,
      regNo: loggedInUser.regNo,
    });
    const [freshTimetable, freshExamTimetable, freshEvents] = await Promise.all([
      storage.getTimetable(),
      storage.getExamTimetable(),
      storage.getEvents(),
    ]);
    setTimetable(freshTimetable);
    setExamTimetable(freshExamTimetable);
    setEvents(freshEvents);
    setSyncStatus(result);
    lastReasonRef.current = result.synced ? null : result.reason;
    setUser(loggedInUser);
  }

  async function completeOnboarding() {
    await storage.saveOnboardingSeen();
    setOnboardingSeen(true);
  }

  async function completeMenuTour() {
    await storage.saveMenuTourSeen();
    setMenuTourSeen(true);
  }

  async function logout() {
    await storage.clearAuth();
    setUser(null);
    setTimetable(null);
    setExamTimetable(null);
    setEvents([]);
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        timetable,
        examTimetable,
        events,
        loading,
        syncStatus,
        onboardingSeen,
        menuTourSeen,
        completeOnboarding,
        completeMenuTour,
        loginStudent,
        loginLecturer,
        logout,
        refreshNow: runSync,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

import AsyncStorage from '@react-native-async-storage/async-storage';
import { STORAGE_KEYS } from '../config/config';

async function setItem(key, value) {
  await AsyncStorage.setItem(key, JSON.stringify(value));
}

async function getItem(key) {
  const raw = await AsyncStorage.getItem(key);
  return raw ? JSON.parse(raw) : null;
}

export const storage = {
  saveAuth: (auth) => setItem(STORAGE_KEYS.AUTH, auth),
  getAuth: () => getItem(STORAGE_KEYS.AUTH),
  clearAuth: () => AsyncStorage.removeItem(STORAGE_KEYS.AUTH),

  saveTimetable: (timetable) => setItem(STORAGE_KEYS.TIMETABLE, timetable),
  getTimetable: () => getItem(STORAGE_KEYS.TIMETABLE),

  saveExamTimetable: (examTimetable) => setItem(STORAGE_KEYS.EXAM_TIMETABLE, examTimetable),
  getExamTimetable: () => getItem(STORAGE_KEYS.EXAM_TIMETABLE),

  saveEvents: (events) => setItem(STORAGE_KEYS.EVENTS, events),
  getEvents: () => getItem(STORAGE_KEYS.EVENTS),

  saveLastSync: (isoString) => setItem(STORAGE_KEYS.LAST_SYNC, isoString),
  getLastSync: () => getItem(STORAGE_KEYS.LAST_SYNC),

  // --- Timetables imported from other same-app users (QR scan) ---------
  // Stored as a map keyed by ownerId so each scanned person gets one slot,
  // which is what makes "replace or merge if one already exists" possible.
  async getAllSharedTimetables() {
    return (await getItem(STORAGE_KEYS.SHARED_TIMETABLES)) || {};
  },
  async getSharedTimetable(ownerId) {
    const all = await getItem(STORAGE_KEYS.SHARED_TIMETABLES);
    return (all && all[ownerId]) || null;
  },
  async saveSharedTimetable(ownerId, entry) {
    const all = (await getItem(STORAGE_KEYS.SHARED_TIMETABLES)) || {};
    all[ownerId] = entry;
    await setItem(STORAGE_KEYS.SHARED_TIMETABLES, all);
    return entry;
  },
  async deleteSharedTimetable(ownerId) {
    const all = (await getItem(STORAGE_KEYS.SHARED_TIMETABLES)) || {};
    delete all[ownerId];
    await setItem(STORAGE_KEYS.SHARED_TIMETABLES, all);
  },

  // --- Install tracking (see utils/deviceId.js, services/installTracker.js) ---
  saveDeviceId: (id) => setItem(STORAGE_KEYS.DEVICE_ID, id),
  getDeviceId: () => getItem(STORAGE_KEYS.DEVICE_ID),
  saveInstallReported: (v) => setItem(STORAGE_KEYS.INSTALL_REPORTED, v),
  getInstallReported: () => getItem(STORAGE_KEYS.INSTALL_REPORTED),

  // --- Staff-editable "share the app" link (see hooks/useAppLink.js) ---
  saveAppLink: (link) => setItem(STORAGE_KEYS.APP_LINK, link),
  getAppLink: () => getItem(STORAGE_KEYS.APP_LINK),

  // --- First-run guidance (see screens/OnboardingScreen.js, components/MenuCoachMark.js) ---
  saveOnboardingSeen: () => setItem(STORAGE_KEYS.ONBOARDING_SEEN, true),
  getOnboardingSeen: () => getItem(STORAGE_KEYS.ONBOARDING_SEEN),
  saveMenuTourSeen: () => setItem(STORAGE_KEYS.MENU_TOUR_SEEN, true),
  getMenuTourSeen: () => getItem(STORAGE_KEYS.MENU_TOUR_SEEN),
};

/**
 * src/services/installTracker.js
 * ================================
 * Reports this device to the backend once — right after a fresh install,
 * before any login — so the Timetabling Dashboard's Mobile Analytics page
 * can show how many distinct devices actually have the app installed. This
 * system has no per-student account to count logins against instead (a
 * registration number resolves to a shared cohort timetable — see
 * mobile_api/scope.py) so a login count alone would badly undercount.
 *
 * Called from App.js on every launch, but is a no-op after the first
 * successful report — see storage.getInstallReported/saveInstallReported.
 * A failed attempt (e.g. no connectivity on first launch) is simply
 * retried on the next launch, since nothing else depends on it succeeding
 * immediately.
 */
import { Platform } from 'react-native';
import { ENDPOINTS, APP_VERSION } from '../config/config';
import { storage } from './storage';
import { getDeviceId } from '../utils/deviceId';

function mapPlatform() {
  if (Platform.OS === 'android') return 'android';
  if (Platform.OS === 'ios') return 'ios';
  if (Platform.OS === 'web') return 'web';
  return 'other';
}

export async function reportInstallIfNeeded() {
  try {
    const alreadyReported = await storage.getInstallReported();
    if (alreadyReported) return;

    const deviceId = await getDeviceId();
    const response = await fetch(ENDPOINTS.install, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        deviceId,
        platform: mapPlatform(),
        appVersion: APP_VERSION,
      }),
    });

    if (response.ok) {
      await storage.saveInstallReported(true);
    }
  } catch (e) {
    // Silent by design — install tracking should never interrupt or be
    // visible to the person using the app. It'll just try again next launch.
  }
}

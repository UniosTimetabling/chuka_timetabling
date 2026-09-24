/**
 * src/utils/deviceId.js
 * ======================
 * Gives this device a persistent, locally-generated ID so the backend can
 * tell distinct installs apart for the Mobile Analytics dashboard page
 * (see services/installTracker.js). Generated once and saved to
 * AsyncStorage — reinstalling the app (which clears AsyncStorage) means a
 * fresh ID next time, which is the intended behaviour: a reinstall should
 * count as an install again.
 *
 * Not cryptographically secure and not meant to be — it only needs to be
 * unique enough to distinguish devices for install counting, not to
 * authenticate anyone or identify a person.
 */
import { storage } from '../services/storage';

function generateId() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/** Returns this device's persistent ID, generating and saving one on first call. */
export async function getDeviceId() {
  const existing = await storage.getDeviceId();
  if (existing) return existing;

  const id = generateId();
  await storage.saveDeviceId(id);
  return id;
}

/**
 * src/services/fileCache.js
 * ==========================
 * Downloads documents (PDF, Word, Excel, PowerPoint, images, ...) once, keeps
 * them on the device, and opens them from there.
 *
 * Why: when staff push a memo/PDF, every student's phone used to fetch it
 * straight from the university server each time it was opened — a lot of
 * simultaneous requests for the same file, and nothing at all when the phone
 * is off the campus network. Now:
 *
 *   1. After each sync the app quietly downloads any attachment it doesn't
 *      have yet (prefetchFiles) — ONE file at a time, after a small random
 *      delay, so thousands of phones never hit the server in the same second.
 *   2. Tapping a file opens the saved copy (openFile) — no request at all.
 *      If it hasn't been saved yet and the phone is online, it is downloaded
 *      right then (deduped with any background download of the same file).
 *   3. Files whose memo has been removed are deleted (pruneFiles).
 *
 * Files live in FileSystem.documentDirectory/cached_files/ (the OS never
 * purges this folder, unlike cacheDirectory). The index in AsyncStorage
 * stores the file *name* only, not the absolute path — on iOS the app
 * container path changes between updates, so a stored absolute path breaks.
 *
 * Web has no file system: everything here is a no-op there and openFile
 * falls back to opening the URL, exactly as before.
 */
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { Alert, Linking, Platform } from 'react-native';
import { storage } from './storage';
import { isOnline } from './netStatus';
import { detectFileKind } from '../utils/fileType';
import {
  FILE_CACHE_MAX_FILE_BYTES,
  FILE_CACHE_MAX_TOTAL_BYTES,
  FILE_PREFETCH_JITTER_MS,
} from '../config/config';

const SUPPORTED = Platform.OS !== 'web' && !!FileSystem.documentDirectory;
const DIR = SUPPORTED ? `${FileSystem.documentDirectory}cached_files/` : null;

// ---------------------------------------------------------------------------
// Keys, names, small helpers
// ---------------------------------------------------------------------------

// A file is identified by its attachment id when the API gives one (stable
// even if the server's host/URL changes), otherwise by a hash of its URL.
export function keyFor(item) {
  if (item.id != null && item.id !== '') return `a${item.id}`;
  const url = item.url || '';
  let h = 5381;
  for (let i = 0; i < url.length; i += 1) {
    h = ((h * 33) ^ url.charCodeAt(i)) >>> 0;
  }
  return `u${h.toString(36)}`;
}

function hasExtension(s) {
  return /\.[A-Za-z0-9]{1,5}$/.test(s || '');
}

function lastUrlSegment(url) {
  try {
    const path = (url || '').split('#')[0].split('?')[0].replace(/\/+$/, '');
    return decodeURIComponent(path.split('/').pop() || '');
  } catch {
    return '';
  }
}

// Keep a real extension in the saved file name — the share sheet / viewer
// apps use it to decide how to open the file.
function fileNameFor(item) {
  const fromUrl = lastUrlSegment(item.url);
  const raw = hasExtension(item.name)
    ? item.name
    : hasExtension(fromUrl)
      ? fromUrl
      : item.name || fromUrl || 'file';
  return raw.replace(/[^\w.\-]+/g, '_').slice(-80);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// Index (AsyncStorage) — { [key]: { file, name, mimeType, size, savedAt } }
// Writes go through one queue so a background download and a prune can't
// overwrite each other's changes.
// ---------------------------------------------------------------------------

let indexQueue = Promise.resolve();

export async function readFileCacheIndex() {
  return (await storage.getFileCache()) || {};
}

function updateIndex(mutator) {
  const run = indexQueue.then(async () => {
    const index = await readFileCacheIndex();
    const result = await mutator(index);
    await storage.saveFileCache(index);
    return result;
  });
  indexQueue = run.catch(() => {});
  return run;
}

const listeners = new Set();
export function subscribeFileCache(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function notify() {
  listeners.forEach((fn) => {
    try {
      fn();
    } catch {
      // a broken listener must never break downloads
    }
  });
}

async function ensureDir() {
  const info = await FileSystem.getInfoAsync(DIR);
  if (!info.exists) {
    await FileSystem.makeDirectoryAsync(DIR, { intermediates: true });
  }
}

// ---------------------------------------------------------------------------
// What should be saved on the device?
// ---------------------------------------------------------------------------

// events: the /events/ list. schedules: the timetable / exam-timetable
// payloads — when a schedule is blocked and its admin link points straight
// at a document (e.g. a timetable PDF), that is saved too.
export function collectFilesToCache(events, schedules = []) {
  const files = [];
  const seen = new Set();
  const add = (file) => {
    if (!file.url || !/^https?:/i.test(file.url)) return;
    const key = keyFor(file);
    if (seen.has(key)) return;
    seen.add(key);
    files.push(file);
  };

  (events || []).forEach((ev) =>
    (ev.attachments || []).forEach((att) =>
      add({ id: att.id, url: att.url, name: att.name, mimeType: att.mimeType, size: att.size })
    )
  );

  schedules.forEach((s) => {
    const v = s && s.visibility;
    if (!v || !v.blocked || !v.linkUrl) return;
    // A plain web page isn't a document — only save links that are files.
    if (detectFileKind({ url: v.linkUrl, fallback: 'link' }) === 'link') return;
    add({ url: v.linkUrl, name: v.linkLabel });
  });

  return files;
}

// ---------------------------------------------------------------------------
// Download one file (shared by background prefetch and tap-to-open)
// ---------------------------------------------------------------------------

const inFlight = new Map(); // key -> Promise<fileName | null>

async function localUriIfPresent(entry) {
  if (!SUPPORTED || !entry) return null;
  const uri = DIR + entry.file;
  const info = await FileSystem.getInfoAsync(uri);
  return info.exists ? uri : null;
}

export async function getCachedUri(item) {
  if (!SUPPORTED) return null;
  const index = await readFileCacheIndex();
  return localUriIfPresent(index[keyFor(item)]);
}

function downloadOne(item) {
  const key = keyFor(item);
  if (inFlight.has(key)) return inFlight.get(key);

  const promise = (async () => {
    const fileName = `${key}_${fileNameFor(item)}`;
    const dest = DIR + fileName;
    const tmp = `${dest}.part`; // never a half-written file under the real name
    try {
      await ensureDir();
      const res = await FileSystem.downloadAsync(item.url, tmp);
      if (res.status !== 200) throw new Error(`HTTP ${res.status}`);
      const info = await FileSystem.getInfoAsync(tmp);
      if (!info.exists || !info.size) throw new Error('empty download');

      await FileSystem.deleteAsync(dest, { idempotent: true });
      await FileSystem.moveAsync({ from: tmp, to: dest });

      await updateIndex((index) => {
        index[key] = {
          file: fileName,
          name: item.name || fileName,
          mimeType: item.mimeType || '',
          size: info.size,
          savedAt: new Date().toISOString(),
        };
      });
      notify();
      return dest;
    } catch {
      await FileSystem.deleteAsync(tmp, { idempotent: true }).catch(() => {});
      return null; // not saved -> retried by the next sync
    } finally {
      inFlight.delete(key);
    }
  })();

  inFlight.set(key, promise);
  return promise;
}

// ---------------------------------------------------------------------------
// Background prefetch — sequential, jittered, capped
// ---------------------------------------------------------------------------

let prefetching = null;
let queuedFiles = null;

async function runBatch(files) {
  const index = await readFileCacheIndex();
  let total = Object.values(index).reduce((sum, e) => sum + (e.size || 0), 0);

  const todo = [];
  for (const item of files) {
    if (item.size && item.size > FILE_CACHE_MAX_FILE_BYTES) continue; // too big to grab unasked
    if (await localUriIfPresent(index[keyFor(item)])) continue; // already saved
    todo.push(item);
  }
  if (todo.length === 0) return;

  // Every phone runs its sync on its own schedule, but a fresh memo makes a
  // lot of them notice at about the same time. A random wait spreads the
  // first request out so the server sees a trickle, not a spike.
  await sleep(Math.random() * FILE_PREFETCH_JITTER_MS);

  for (const item of todo) {
    if (!(await isOnline())) return; // resume on the next sync
    if (total >= FILE_CACHE_MAX_TOTAL_BYTES) return;
    const uri = await downloadOne(item); // one at a time on purpose
    if (uri) total += item.size || 0;
  }
}

// Safe to call after every sync. If a run is already going, the latest list
// is remembered and processed when it finishes (no parallel runs).
export function prefetchFiles(files) {
  if (!SUPPORTED) return Promise.resolve();
  if (prefetching) {
    queuedFiles = files;
    return prefetching;
  }
  prefetching = (async () => {
    let batch = files;
    while (batch) {
      queuedFiles = null;
      try {
        await runBatch(batch);
      } catch {
        // background work must never surface an error
      }
      batch = queuedFiles;
    }
  })().finally(() => {
    prefetching = null;
  });
  return prefetching;
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

// Delete saved files that are no longer in `files` (memo removed/unpublished,
// audience changed), plus stray leftovers from interrupted downloads.
export async function pruneFiles(files) {
  if (!SUPPORTED) return;
  try {
    const keep = new Set(files.map(keyFor));
    const removed = await updateIndex((index) => {
      const gone = [];
      Object.keys(index).forEach((key) => {
        if (!keep.has(key)) {
          gone.push(index[key].file);
          delete index[key];
        }
      });
      return gone;
    });
    for (const file of removed) {
      await FileSystem.deleteAsync(DIR + file, { idempotent: true });
    }

    // Sweep orphans — but never while a download is writing its .part file.
    if (inFlight.size === 0) {
      const info = await FileSystem.getInfoAsync(DIR);
      if (info.exists) {
        const index = await readFileCacheIndex();
        const known = new Set(Object.values(index).map((e) => e.file));
        const onDisk = await FileSystem.readDirectoryAsync(DIR);
        for (const name of onDisk) {
          if (!known.has(name)) {
            await FileSystem.deleteAsync(DIR + name, { idempotent: true });
          }
        }
      }
    }
    if (removed.length) notify();
  } catch {
    // cleanup is best-effort
  }
}

// Called on logout: memos are targeted per program/year, and phones get
// shared, so don't leave one person's documents for the next.
export async function clearFileCache() {
  if (!SUPPORTED) return;
  try {
    await FileSystem.deleteAsync(DIR, { idempotent: true });
    await updateIndex((index) => {
      Object.keys(index).forEach((k) => delete index[k]);
    });
    notify();
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Open
// ---------------------------------------------------------------------------

// Opens the saved copy; downloads it first if it isn't saved yet. Only when
// there's no copy AND no connection does the person see a message.
export async function openFile(item) {
  let uri = await getCachedUri(item);

  if (!uri && SUPPORTED && (await isOnline())) {
    uri = await downloadOne(item);
  }

  if (uri) {
    try {
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, {
          mimeType: item.mimeType || undefined,
          dialogTitle: item.name || undefined,
        });
        return;
      }
    } catch {
      // fall through to the URL fallback below
    }
  }

  if (item.url && (!SUPPORTED || (await isOnline()))) {
    try {
      await Linking.openURL(item.url);
      return;
    } catch {
      // fall through
    }
  }

  Alert.alert(
    'Not available offline',
    'This file has not been saved on your phone yet. Connect to the university network once and it will be saved for offline use.'
  );
}

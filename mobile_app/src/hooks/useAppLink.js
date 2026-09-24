/**
 * src/hooks/useAppLink.js
 * ========================
 * The "share the app" link, kept live from the server.
 *
 * Staff edit it on the web dashboard (/mobile/analytics/); this hook reads
 * it from GET /api/mobile/app-link/ so a change there reaches every device
 * without a new app release. Resolution order for what to show right now:
 *   1. the value just fetched from the server
 *   2. the last value we cached on this device (works offline)
 *   3. the hardcoded APP_SHARE_URL fallback in config.js
 * The cached/fallback value is shown instantly; the fetch then swaps it in.
 */
import { useEffect, useState } from 'react';
import { api } from '../api/api';
import { storage } from '../services/storage';
import { APP_SHARE_URL } from '../config/config';

export function useAppLink() {
  const [url, setUrl] = useState(APP_SHARE_URL);
  const [source, setSource] = useState('default');

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const cached = await storage.getAppLink();
        if (!cancelled && cached?.url) {
          setUrl(cached.url);
          setSource(cached.source || 'default');
        }
      } catch (e) {
        /* cache is best-effort */
      }

      try {
        const fresh = await api.getAppLink();
        if (cancelled || !fresh?.url) return;
        setUrl(fresh.url);
        setSource(fresh.source || 'default');
        storage.saveAppLink({ url: fresh.url, source: fresh.source }).catch(() => {});
      } catch (e) {
        // Offline / off-campus: keep showing the cached or fallback link.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return { url, source };
}

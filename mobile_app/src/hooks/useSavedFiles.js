import { useCallback, useEffect, useState } from 'react';
import { keyFor, readFileCacheIndex, subscribeFileCache } from '../services/fileCache';

// Returns isSaved(file) -> boolean, re-rendering the screen whenever a
// background download finishes, so a "Saved" badge appears on its own.
export default function useSavedFiles() {
  const [index, setIndex] = useState({});

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const latest = await readFileCacheIndex();
      if (alive) setIndex(latest);
    };
    load();
    const unsubscribe = subscribeFileCache(load);
    return () => {
      alive = false;
      unsubscribe();
    };
  }, []);

  return useCallback((file) => Boolean(index[keyFor(file)]), [index]);
}

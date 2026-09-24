// Minimal pub-sub so both React components AND plain async code (e.g.
// AuthContext's background sync loop, which fires outside any button press)
// can trigger a toast without needing to thread a context down to it.
let listeners = [];

function subscribe(listener) {
  listeners.push(listener);
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

function show(message, type = 'error') {
  if (!message) return;
  listeners.forEach((listener) =>
    listener({ id: `${Date.now()}-${Math.random()}`, message, type })
  );
}

export const toastBus = { subscribe, show };

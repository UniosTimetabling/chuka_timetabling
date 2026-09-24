import NetInfo from '@react-native-community/netinfo';

// Returns true only when the device has an active connection AND that
// connection is verified as actually reaching the internet (isInternetReachable).
// This is what gates the "sync happens when the domain is reachable and
// internet is on" requirement.
export async function isOnline() {
  const state = await NetInfo.fetch();
  return Boolean(state.isConnected && state.isInternetReachable !== false);
}

// Subscribe to connectivity changes. Returns an unsubscribe function.
export function onConnectivityChange(callback) {
  return NetInfo.addEventListener((state) => {
    const online = Boolean(state.isConnected && state.isInternetReachable !== false);
    callback(online);
  });
}

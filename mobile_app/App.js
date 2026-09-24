import React, { useEffect } from 'react';
import { Platform } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { AuthProvider } from './src/context/AuthContext';
import AppNavigator from './src/navigation/AppNavigator';
import ToastHost from './src/components/Toast';
import { reportInstallIfNeeded } from './src/services/installTracker';

// Web only: lock the real <html>/<body>/#root to the viewport.
//
// TimetableScreen pins its own header by giving its root View
// `height: 100vh; overflow: hidden` and only lets the inner table
// ScrollView scroll. That works for that View itself, but it can't
// stop the *document* from scrolling — and on the web the document
// was still free to. Two everyday things nudge it into a scrolled
// state: mobile browsers' address bar makes `100vh` taller than the
// actually-visible viewport, and the hero carousel's photos finish
// loading a moment after first paint and can briefly grow the page.
// Either one is enough for the browser to let the page scroll a few
// dozen pixels — which is exactly enough to slide the header (logo,
// name, and the Log out button) up out of view while "Hi there" and
// the rest of the fixed content look untouched.
// Forcing the real document element to `overflow: hidden` removes
// the possibility entirely: the page itself can never scroll, so the
// header can't be carried off screen no matter what causes a stray
// layout growth. All intended scrolling already happens inside the
// app's own ScrollViews, so this doesn't take anything away.
function useLockDocumentScroll() {
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') return;
    const style = document.createElement('style');
    style.setAttribute('data-app-scroll-lock', 'true');
    style.innerHTML = `
      html, body, #root {
        height: 100%;
        margin: 0;
        padding: 0;
        overflow: hidden;
        overscroll-behavior: none;
      }
    `;
    document.head.appendChild(style);
    return () => style.remove();
  }, []);
}

export default function App() {
  useLockDocumentScroll();

  // Reports this device to the backend once, on the very first launch
  // after install, for the Mobile Analytics dashboard page — see
  // src/services/installTracker.js.
  useEffect(() => {
    reportInstallIfNeeded();
  }, []);

  return (
    <SafeAreaProvider>
      <AuthProvider>
        <StatusBar style="dark" />
        <AppNavigator />
        <ToastHost />
      </AuthProvider>
    </SafeAreaProvider>
  );
}

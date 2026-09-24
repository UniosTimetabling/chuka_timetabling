import { app, BrowserWindow, shell } from 'electron';
import { autoUpdater } from 'electron-updater';
import fs from 'fs';
import path from 'path';
import { registerIpcHandlers, startBackgroundSync } from './ipcHandlers';
import { openDb } from './db';

let win: BrowserWindow | null = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'), // dist/main/preload.js
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
      // No webviewTag: every page in this app is a real native Electron panel backed by the
      // local SQLite store, not a browser tab pointed at the live server. See operationsMatrixData.ts.
    }
  });

  // The renderer only ever shows our own UI: never let it navigate away or open new windows in-app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith('file://') && !(process.env.VITE_DEV_SERVER_URL && url.startsWith(process.env.VITE_DEV_SERVER_URL))) e.preventDefault();
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    win.loadFile(path.join(__dirname, '../renderer/index.html')); // dist/renderer/index.html
  }
}

app.whenReady().then(() => {
  // Open (and, if needed, migrate) the offline SQLite store BEFORE the window loads. Migrations only
  // run when the on-disk schema_version is behind CURRENT_SCHEMA_VERSION — a plain UI/code update
  // (new renderer bundle) never touches this file, so shipping a new release never risks offline data.
  openDb();
  registerIpcHandlers();
  createWindow();
  startBackgroundSync();

  // App-shell auto-update is a separate concern from data sync. It only runs in an installed build that
  // was packaged with an update feed (electron-builder writes app-update.yml when `publish` is configured).
  const feed = path.join(process.resourcesPath || '', 'app-update.yml');
  if (app.isPackaged && fs.existsSync(feed)) {
    autoUpdater.autoDownload = false;
    autoUpdater.on('error', () => undefined); // offline / update server unreachable — non-fatal
    autoUpdater.checkForUpdatesAndNotify().catch(() => undefined);
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
const electron_updater_1 = require("electron-updater");
const fs_1 = __importDefault(require("fs"));
const path_1 = __importDefault(require("path"));
const ipcHandlers_1 = require("./ipcHandlers");
const db_1 = require("./db");
let win = null;
function createWindow() {
    win = new electron_1.BrowserWindow({
        width: 1400,
        height: 900,
        webPreferences: {
            preload: path_1.default.join(__dirname, 'preload.js'), // dist/main/preload.js
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true
            // No webviewTag: every page in this app is a real native Electron panel backed by the
            // local SQLite store, not a browser tab pointed at the live server. See operationsMatrixData.ts.
        }
    });
    // The renderer only ever shows our own UI: never let it navigate away or open new windows in-app.
    win.webContents.setWindowOpenHandler(({ url }) => {
        if (/^https?:\/\//i.test(url))
            electron_1.shell.openExternal(url);
        return { action: 'deny' };
    });
    win.webContents.on('will-navigate', (e, url) => {
        if (!url.startsWith('file://') && !(process.env.VITE_DEV_SERVER_URL && url.startsWith(process.env.VITE_DEV_SERVER_URL)))
            e.preventDefault();
    });
    if (process.env.VITE_DEV_SERVER_URL) {
        win.loadURL(process.env.VITE_DEV_SERVER_URL);
    }
    else {
        win.loadFile(path_1.default.join(__dirname, '../renderer/index.html')); // dist/renderer/index.html
    }
}
electron_1.app.whenReady().then(() => {
    // Open (and, if needed, migrate) the offline SQLite store BEFORE the window loads. Migrations only
    // run when the on-disk schema_version is behind CURRENT_SCHEMA_VERSION — a plain UI/code update
    // (new renderer bundle) never touches this file, so shipping a new release never risks offline data.
    (0, db_1.openDb)();
    (0, ipcHandlers_1.registerIpcHandlers)();
    createWindow();
    (0, ipcHandlers_1.startBackgroundSync)();
    // App-shell auto-update is a separate concern from data sync. It only runs in an installed build that
    // was packaged with an update feed (electron-builder writes app-update.yml when `publish` is configured).
    const feed = path_1.default.join(process.resourcesPath || '', 'app-update.yml');
    if (electron_1.app.isPackaged && fs_1.default.existsSync(feed)) {
        electron_updater_1.autoUpdater.autoDownload = false;
        electron_updater_1.autoUpdater.on('error', () => undefined); // offline / update server unreachable — non-fatal
        electron_updater_1.autoUpdater.checkForUpdatesAndNotify().catch(() => undefined);
    }
    electron_1.app.on('activate', () => {
        if (electron_1.BrowserWindow.getAllWindows().length === 0)
            createWindow();
    });
});
electron_1.app.on('window-all-closed', () => {
    if (process.platform !== 'darwin')
        electron_1.app.quit();
});

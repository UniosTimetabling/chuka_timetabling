"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
electron_1.contextBridge.exposeInMainWorld('chukaApi', {
    login: (baseUrl, username, password) => electron_1.ipcRenderer.invoke('auth:login', { baseUrl, username, password }),
    logout: () => electron_1.ipcRenderer.invoke('auth:logout'),
    getSession: () => electron_1.ipcRenderer.invoke('auth:getSession'),
    listEntries: (kind) => electron_1.ipcRenderer.invoke('entries:list', kind),
    listAllEntries: () => electron_1.ipcRenderer.invoke('entries:listAll'),
    applyLocalEdit: (change) => electron_1.ipcRenderer.invoke('entries:edit', change),
    listConflicts: () => electron_1.ipcRenderer.invoke('conflicts:list'),
    resolveConflict: (entryId, resolution, merged) => electron_1.ipcRenderer.invoke('conflicts:resolve', { entryId, resolution, merged }),
    configureServer: (baseUrl, token) => electron_1.ipcRenderer.invoke('sync:configure', { baseUrl, token }),
    syncNow: (kind) => electron_1.ipcRenderer.invoke('sync:run', kind),
    getLabExamReference: () => electron_1.ipcRenderer.invoke('reference:labExam'),
    getCachedLabExamReference: () => electron_1.ipcRenderer.invoke('reference:labExamCached'),
    runLabExamScheduler: (freshData = true) => electron_1.ipcRenderer.invoke('scheduler:runLabExam', freshData),
    listPublishedDocuments: () => electron_1.ipcRenderer.invoke('documents:list'),
    listCachedDocumentsOffline: () => electron_1.ipcRenderer.invoke('documents:listCachedOffline'),
    downloadDocument: (doc) => electron_1.ipcRenderer.invoke('documents:download', doc),
    openCachedDocument: (localPath) => electron_1.ipcRenderer.invoke('documents:openCached', localPath),
    removeCachedDocument: (id) => electron_1.ipcRenderer.invoke('documents:removeCached', id),
    syncInfo: () => electron_1.ipcRenderer.invoke('sync:info'),
    exportPdf: (html, suggestedFileName) => electron_1.ipcRenderer.invoke('pdf:export', { html, suggestedFileName }),
    /** Returns an unsubscribe function (call it in the effect cleanup so listeners don't pile up). */
    onSyncStatus: (cb) => {
        const listener = (_e, status) => cb(status);
        electron_1.ipcRenderer.on('sync:status', listener);
        return () => electron_1.ipcRenderer.removeListener('sync:status', listener);
    }
});

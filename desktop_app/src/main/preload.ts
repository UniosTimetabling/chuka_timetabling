import { contextBridge, ipcRenderer } from 'electron';
import { EntryKind, LabExamReferenceData, PendingChange, TimetableEntry, UserSession, PublishedPdfDoc, CachedPdfDoc } from '../shared/types';

contextBridge.exposeInMainWorld('chukaApi', {
  login: (baseUrl: string, username: string, password: string): Promise<UserSession> =>
    ipcRenderer.invoke('auth:login', { baseUrl, username, password }),
  logout: (): Promise<void> => ipcRenderer.invoke('auth:logout'),
  getSession: (): Promise<UserSession | null> => ipcRenderer.invoke('auth:getSession'),

  listEntries: (kind: EntryKind): Promise<TimetableEntry[]> => ipcRenderer.invoke('entries:list', kind),
  listAllEntries: (): Promise<TimetableEntry[]> => ipcRenderer.invoke('entries:listAll'),
  applyLocalEdit: (change: PendingChange): Promise<void> => ipcRenderer.invoke('entries:edit', change),
  listConflicts: () => ipcRenderer.invoke('conflicts:list'),
  resolveConflict: (entryId: string, resolution: 'keep_local' | 'keep_server' | 'merged', merged?: Partial<TimetableEntry>) =>
    ipcRenderer.invoke('conflicts:resolve', { entryId, resolution, merged }),
  configureServer: (baseUrl: string, token: string) => ipcRenderer.invoke('sync:configure', { baseUrl, token }),
  syncNow: (kind?: EntryKind) => ipcRenderer.invoke('sync:run', kind),
  getLabExamReference: (): Promise<LabExamReferenceData | null> => ipcRenderer.invoke('reference:labExam'),
  getCachedLabExamReference: (): Promise<LabExamReferenceData | null> => ipcRenderer.invoke('reference:labExamCached'),
  runLabExamScheduler: (freshData = true) => ipcRenderer.invoke('scheduler:runLabExam', freshData),

  listPublishedDocuments: (): Promise<CachedPdfDoc[]> => ipcRenderer.invoke('documents:list'),
  listCachedDocumentsOffline: (): Promise<CachedPdfDoc[]> => ipcRenderer.invoke('documents:listCachedOffline'),
  downloadDocument: (doc: PublishedPdfDoc): Promise<string> => ipcRenderer.invoke('documents:download', doc),
  openCachedDocument: (localPath: string) => ipcRenderer.invoke('documents:openCached', localPath),
  removeCachedDocument: (id: number) => ipcRenderer.invoke('documents:removeCached', id),
  syncInfo: (): Promise<{ pending: number; conflicts: number; status: string }> => ipcRenderer.invoke('sync:info'),
  exportPdf: (html: string, suggestedFileName: string): Promise<string | null> =>
    ipcRenderer.invoke('pdf:export', { html, suggestedFileName }),
  /** Returns an unsubscribe function (call it in the effect cleanup so listeners don't pile up). */
  onSyncStatus: (cb: (status: string) => void): (() => void) => {
    const listener = (_e: unknown, status: string) => cb(status);
    ipcRenderer.on('sync:status', listener);
    return () => ipcRenderer.removeListener('sync:status', listener);
  }
});

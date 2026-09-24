import { EntryKind, LabExamReferenceData, PendingChange, TimetableEntry, ConflictRecord, SyncSummary, UserSession, PublishedPdfDoc, CachedPdfDoc } from '@shared/types';

export interface ChukaApi {
  login(baseUrl: string, username: string, password: string): Promise<UserSession>;
  logout(): Promise<void>;
  getSession(): Promise<UserSession | null>;

  listEntries(kind: EntryKind): Promise<TimetableEntry[]>;
  listAllEntries(): Promise<TimetableEntry[]>;
  applyLocalEdit(change: PendingChange): Promise<void>;
  listConflicts(): Promise<ConflictRecord[]>;
  resolveConflict(
    entryId: string,
    resolution: 'keep_local' | 'keep_server' | 'merged',
    merged?: Partial<TimetableEntry>
  ): Promise<void>;
  configureServer(baseUrl: string, token: string): Promise<void>;
  /** Pushes queued edits, then refreshes every kind. `kind` is ignored (kept for compatibility). */
  syncNow(kind?: EntryKind): Promise<SyncSummary>;
  /** Pulls fresh lab/exam autoscheduler input data from the server and returns it. */
  getLabExamReference(): Promise<LabExamReferenceData | null>;
  /** Returns the last-pulled lab/exam autoscheduler input data without hitting the network. */
  getCachedLabExamReference(): Promise<LabExamReferenceData | null>;
  /**
   * Runs the lab/exam autoscheduler ENTIRELY OFFLINE (spawns a local Python process — see
   * pythonRunner.ts) and queues the result as ordinary local edits. `freshData=false` skips
   * the network pull and uses whatever reference data was cached from the last sync.
   */
  runLabExamScheduler(freshData?: boolean): Promise<{ status: 'success' | 'warning' | 'error'; message: string; removed: number; created: number }>;

  /** Latest published Regular/Exam timetable PDFs, merged with this device's local cache state. Needs a connection. */
  listPublishedDocuments(): Promise<CachedPdfDoc[]>;
  /** Same list, from the local cache only — works fully offline. */
  listCachedDocumentsOffline(): Promise<CachedPdfDoc[]>;
  /** Downloads one document's PDF and caches it on disk. Returns the local file path. */
  downloadDocument(doc: PublishedPdfDoc): Promise<string>;
  /** Opens a cached PDF with the OS's default viewer. */
  openCachedDocument(localPath: string): Promise<void>;
  /** Deletes a document from the local cache (file + metadata). */
  removeCachedDocument(id: number): Promise<void>;
  syncInfo(): Promise<{ pending: number; conflicts: number; status: string }>;
  exportPdf(html: string, suggestedFileName: string): Promise<string | null>;
  onSyncStatus(cb: (status: string) => void): () => void;
}

declare global {
  interface Window {
    chukaApi: ChukaApi;
  }
}

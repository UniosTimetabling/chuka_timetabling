import { create } from 'zustand';
import { v4 as uuid } from 'uuid';
import { EntryKind, TimetableEntry } from '@shared/types';

interface ClipboardState {
  mode: 'copy' | 'cut';
  entryIds: string[];
}

/** Where an entry is moved/pasted to. The entry keeps its own venue; exam kinds also carry the calendar date. */
export interface SlotTarget {
  day: string;
  start_time: string;
  end_time: string;
  date?: string | null;
}

interface TimetableStore {
  kind: EntryKind;
  entries: Record<string, TimetableEntry>;
  selectedIds: string[];
  clipboard: ClipboardState | null;
  syncStatus: string;
  conflictCount: number;
  pendingCount: number;
  /** Transient one-line message shown above the grid (e.g. why a shortcut did nothing). */
  notice: string | null;

  setNotice: (n: string | null) => void;
  loadEntries: (kind: EntryKind) => Promise<void>;
  select: (id: string, additive?: boolean) => void;
  clearSelection: () => void;

  /** Drag-and-drop: move one entry to a new day/time slot (it keeps its venue). */
  moveEntry: (id: string, target: SlotTarget) => Promise<void>;

  copySelection: () => void;
  cutSelection: () => void;
  pasteInto: (target: SlotTarget) => Promise<void>;
  deleteSelection: () => Promise<void>;

  setSyncStatus: (s: string) => void;
  refreshConflictCount: () => Promise<void>;
  refreshPendingCount: () => Promise<void>;
}

export const useTimetableStore = create<TimetableStore>((set, get) => ({
  kind: 'regular',
  entries: {},
  selectedIds: [],
  clipboard: null,
  syncStatus: 'idle',
  conflictCount: 0,
  pendingCount: 0,
  notice: null,

  setNotice: (notice) => set({ notice }),

  loadEntries: async (kind) => {
    const list = await window.chukaApi.listEntries(kind);
    const map: Record<string, TimetableEntry> = {};
    for (const e of list) map[e.id] = e;
    set((s) => ({ kind, entries: map, selectedIds: s.kind === kind ? s.selectedIds.filter((id) => map[id]) : [], notice: null }));
  },

  select: (id, additive) => {
    set((s) => ({
      selectedIds: additive
        ? s.selectedIds.includes(id)
          ? s.selectedIds.filter((x) => x !== id)
          : [...s.selectedIds, id]
        : [id]
    }));
  },

  clearSelection: () => set({ selectedIds: [] }),

  moveEntry: async (id, target) => {
    const entry = get().entries[id];
    if (!entry) return;
    if (entry.sync_state === 'conflict') {
      set({ notice: 'Resolve the conflict on that entry (Conflicts button) before moving it again.' });
      return;
    }
    // The entry keeps its own venue: a drop target only implies a day/time, never a venue.
    const payload = { ...target, venue_id: entry.venue_id };
    const updated: TimetableEntry = { ...entry, ...payload, sync_state: 'dirty' };
    set((s) => ({ entries: { ...s.entries, [id]: updated }, notice: null }));
    await window.chukaApi.applyLocalEdit({
      local_op_id: uuid(),
      entry_id: id,
      kind: entry.kind,
      op: 'move',
      payload,
      base_version: entry.base_version,
      created_at: new Date().toISOString()
    });
    get().refreshPendingCount();
  },

  copySelection: () => {
    const { kind, selectedIds } = get();
    if (kind === 'lab' || kind === 'lab_exam') {
      // The web app keeps exactly one session per lab allocation, so a copy could only ever be rejected.
      set({ notice: 'Lab sessions can be moved (Ctrl+X) but not duplicated — each lab has one session.' });
      return;
    }
    set({ clipboard: { mode: 'copy', entryIds: [...selectedIds] }, notice: null });
  },
  cutSelection: () => set((s) => ({ clipboard: { mode: 'cut', entryIds: [...s.selectedIds] }, notice: null })),

  // Paste covers both Word-style behaviors:
  //  - copy+paste duplicates the entry (a new local row, course/venue kept,
  //    dropped onto the target slot).
  //  - cut+paste is a move: same as drag-and-drop, then clears the clipboard.
  pasteInto: async (target) => {
    const clip = get().clipboard;
    if (!clip || clip.entryIds.length === 0) return;

    if (clip.mode === 'cut') {
      for (const id of clip.entryIds) {
        await get().moveEntry(id, target);
      }
      set({ clipboard: null });
      return;
    }

    for (const id of clip.entryIds) {
      const src = get().entries[id];
      if (!src) continue;
      const newId = uuid();
      const clone: TimetableEntry = {
        ...src,
        id: newId,
        server_id: null,
        ...target,
        version: 0,
        base_version: 0,
        sync_state: 'dirty'
      };
      set((s) => ({ entries: { ...s.entries, [newId]: clone } }));
      await window.chukaApi.applyLocalEdit({
        local_op_id: uuid(),
        entry_id: newId,
        kind: clone.kind,
        op: 'create',
        payload: clone,
        base_version: 0,
        created_at: new Date().toISOString()
      });
    }
    get().refreshPendingCount();
  },

  deleteSelection: async () => {
    const { selectedIds, entries } = get();
    for (const id of selectedIds) {
      const e = entries[id];
      if (!e || e.sync_state === 'conflict') continue;
      await window.chukaApi.applyLocalEdit({
        local_op_id: uuid(),
        entry_id: id,
        kind: e.kind,
        op: 'delete',
        payload: {},
        base_version: e.base_version,
        created_at: new Date().toISOString()
      });
    }
    set((s) => {
      const next = { ...s.entries };
      for (const id of selectedIds) if (next[id]?.sync_state !== 'conflict') delete next[id];
      return { entries: next, selectedIds: [] };
    });
    get().refreshPendingCount();
  },

  setSyncStatus: (syncStatus) => set({ syncStatus }),
  refreshConflictCount: async () => {
    const list = await window.chukaApi.listConflicts();
    set({ conflictCount: list.length });
  },
  refreshPendingCount: async () => {
    const { pending, status } = await window.chukaApi.syncInfo();
    // Adopt the main process's status only if nothing more specific has been shown yet.
    set((s) => ({ pendingCount: pending, syncStatus: s.syncStatus === 'idle' ? status : s.syncStatus }));
  }
}));

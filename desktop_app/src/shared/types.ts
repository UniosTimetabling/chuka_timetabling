/**
 * Shared contract between the Electron renderer, the local SQLite cache,
 * and the Django `desktop_sync` API (see server_integration/).
 *
 * Field names deliberately mirror the existing Django models so the
 * desktop app is a genuine offline mirror, not a parallel data shape:
 *   - timetable.models.Timetable        -> EntryKind "regular"  (venue: room_management.Venue)
 *   - timetable.models.ExamTimetable    -> EntryKind "exam"     (venue: room_management.Venue)
 *   - timetable.models.LabTimetable     -> EntryKind "lab"      (venue: room_management.LabVenue)
 *   - timetable.models.LabExamTimetable -> EntryKind "lab_exam" (venue: room_management.LabVenue)
 * The "lab" kinds use a distinct venue table server-side (LabVenue, not
 * Venue) and a distinct allocation table (LabAllocation, not
 * CourseAllocation) — see desktop_sync/views_sync.py's ALLOCATION_FIELD /
 * VENUE_FIELD maps for how the two are reconciled into one client shape.
 */

export type EntryKind = 'regular' | 'exam' | 'lab' | 'lab_exam';
/** Which resource family a venue belongs to — mirrors room_management.Venue vs room_management.LabVenue being separate tables server-side. */
export type VenueKind = 'hall' | 'lab';

export type SyncState = 'clean' | 'dirty' | 'pending' | 'conflict';

export interface TimetableEntry {
  /** Local primary key. For rows pulled from the server this equals server_id. */
  id: string;
  /** Server-side numeric PK, once known. Null for rows created offline and not yet pushed. */
  server_id: number | null;
  kind: EntryKind;

  course_allocation_id: number;
  course_code: string;
  course_name: string;
  lecturer_name: string;
  program_name: string;

  venue_id: number;
  venue_name: string;
  venue_kind: VenueKind;

  day: string;              // "Monday" ... matches Django CharField(day)
  date: string | null;      // exam only, ISO date, matches ExamTimetable.date
  start_time: string;       // "HH:MM"
  end_time: string;         // "HH:MM"

  /**
   * Optimistic-concurrency token: a hash of the row's scheduling content,
   * computed by the server on every read (so it changes no matter what wrote
   * the row — web panel, autoscheduler, another desktop).
   */
  version: number;
  updated_at: string | null; // ISO timestamp, when known
  updated_by: string | null;

  /** Local-only bookkeeping, never sent to the server. */
  sync_state: SyncState;
  /** version this row was last known-equal to the server at (the "base" for 3-way diff) */
  base_version: number;
  deleted: boolean;
}

/** One offline change, queued until the app can reach the server. */
export interface PendingChange {
  local_op_id: string;         // uuid, idempotency key
  entry_id: string;            // TimetableEntry.id
  kind: EntryKind;
  op: 'create' | 'update' | 'move' | 'delete';
  /** Full entry snapshot at the time of the edit (post-edit state). */
  payload: Partial<TimetableEntry>;
  base_version: number;        // version the edit was made against
  created_at: string;
}

export interface ConflictRecord {
  entry_id: string;
  kind: EntryKind;
  local: TimetableEntry;       // what the user has, offline
  server: TimetableEntry;      // what the server has now
  base_version: number;        // the version both derived from
  /**
   * Set when the server REJECTED the change (clash with another class, group
   * lock, invalid venue...) instead of a plain version mismatch. Shown to the user.
   */
  reason: string | null;
  /** Field-level diff, computed client-side for display */
  diverging_fields?: string[];
}

export interface PushResultItem {
  local_op_id: string;
  entry_id: string;
  status: 'applied' | 'conflict' | 'error';
  /**
   * 'applied': the row as it now exists (absent when the row is gone — a delete, or a replay of a create that was deleted since).
   * 'conflict' / 'error': the server's current row when one exists (absent = deleted on the server).
   */
  server_entry?: TimetableEntry | null;
  message?: string;
}

export interface PullResponse {
  kind: EntryKind;
  server_version: number;          // changes iff anything in this kind changes
  /** FULL current dataset for the kind; the client diffs it against its cache to find server-side deletions. */
  entries: TimetableEntry[];
  deleted_ids: number[];           // always [] — kept for wire compatibility
}

export interface SyncSummary {
  applied: number;                 // local changes accepted by the server
  conflicts: number;               // changes that need a decision (version conflicts + server rejections)
  pulled: number;                  // rows in the server snapshot(s)
  pending: number;                 // changes still queued afterwards
}

/** Logged-in desktop session, persisted locally so the app can re-open without asking for a password again. */
export interface UserSession {
  baseUrl: string;
  token: string;
  username: string;
  displayName: string;
  isStaff: boolean;
  isSuperuser: boolean;
  roles: string[];
}

export interface PublishedPdfDoc {
  id: number;
  title: string;
  document_type: 'REGULAR' | 'EXAM';
  academic_year: string;
  semester: string;
  version: number;
  file_size: number;
  uploaded_at: string | null;
}

/** A PublishedPdfDoc plus this device's local cache state, for the desktop UI. */
export interface CachedPdfDoc extends PublishedPdfDoc {
  cached: boolean;
  local_path: string | null;
  cached_at: string | null;
}

export interface AppUpdateInfo {
  /** Electron app-shell (UI/code) version — separate channel from data. */
  shellVersion: string;
  /** Bump this only when the local SQLite schema needs a migration. */
  dataSchemaVersion: number;
}

/**
 * Read-only reference data the lab/exam autoscheduler needs as INPUT to build a timetable
 * from scratch — mirrors desktop_sync/views_reference.py's response shape exactly. Unlike
 * TimetableEntry, none of this is edited on the desktop, so there's no version/conflict
 * machinery: every pull just replaces the local copy wholesale.
 */
export interface LabVenueRef {
  id: number;
  code: string;
  capacity: number | null;
}

export interface LecturerRef {
  id: number;
  name: string;
  designation: string;
}

export interface ProgramRef {
  id: number;
  name: string;
  department_name: string;
}

export interface LabAllocationRef {
  id: number;
  course_code: string;
  course_name: string;
  additional_course_codes: string[];
  program_id: number | null;
  lecturer_id: number | null;
  venue_ids: number[];
  number_of_students: number;
  is_workshop_course: boolean;
  allocation_set_id: number | null;
}

export interface ExamSchedulerConfigRef {
  start_date: string | null;
  start_time: string | null;
  end_time: string | null;
  slot_size: number;
  excluded_days: string[];
  max_exam_days: number;
  spacing_ratio: number;
}

export interface ExistingExamBusy {
  date: string | null;
  start_time: string | null;
  end_time: string | null;
  lecturer_id: number | null;
  program_id: number | null;
}

export interface LabExamReferenceData {
  generated_at: string;
  config: ExamSchedulerConfigRef;
  lab_allocations: LabAllocationRef[];
  lab_venues: LabVenueRef[];
  lecturers: LecturerRef[];
  programs: ProgramRef[];
  existing_exam_busy: ExistingExamBusy[];
}

"use strict";
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
Object.defineProperty(exports, "__esModule", { value: true });

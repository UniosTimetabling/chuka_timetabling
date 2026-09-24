import { v4 as uuid } from 'uuid';
import * as db from './db';
import * as sync from './syncEngine';
import { applyEdit } from './editOps';
import { runLabExamScheduler } from './pythonRunner';
import type { LabExamReferenceData, TimetableEntry } from '../shared/types';

export interface LocalSchedulerSummary {
  status: 'success' | 'warning' | 'error';
  message: string;
  removed: number;
  created: number;
}

/**
 * Runs the lab/exam autoscheduler ENTIRELY OFFLINE against the local reference-data mirror
 * (see pythonRunner.ts), then queues the result as ordinary local edits: every existing
 * lab_exam entry is queued for delete, and every row the algorithm placed is queued as a
 * create — mirroring the server view's own "wipe and regenerate" behavior, but through the
 * app's normal offline-edit queue so the result pushes on the next sync like any other change.
 *
 * `freshData`: when true (the default), pulls the latest reference data from the server first
 * (requires being online). Pass false to run against whatever was last cached — the whole
 * point of "runs locally" — but be aware the result may be stale if allocations/venues/lecturers
 * changed on the server since the last pull.
 */
export async function runLabExamSchedulerLocally(freshData = true): Promise<LocalSchedulerSummary> {
  let data: LabExamReferenceData | null;
  if (freshData) {
    await sync.pullLabExamReference();
    data = db.getLabExamReference();
  } else {
    data = db.getLabExamReference();
  }
  if (!data) {
    return { status: 'error', message: 'No lab/exam reference data available yet — connect once to pull it, then this can run offline.', removed: 0, created: 0 };
  }

  const result = await runLabExamScheduler(data);
  if (result.status === 'error') {
    return { status: 'error', message: result.message, removed: 0, created: 0 };
  }

  const venueById = new Map(data.lab_venues.map((v) => [v.id, v]));
  const lecturerById = new Map(data.lecturers.map((l) => [l.id, l]));
  const programById = new Map(data.programs.map((p) => [p.id, p]));
  const allocById = new Map(data.lab_allocations.map((a) => [a.id, a]));

  const existing = db.listEntriesRaw('lab_exam');
  const now = new Date().toISOString();

  db.runInTransaction(() => {
    for (const e of existing) {
      applyEdit({ local_op_id: uuid(), entry_id: e.id, kind: 'lab_exam', op: 'delete', payload: {}, base_version: e.base_version, created_at: now });
    }

    for (const row of result.schedule) {
      const alloc = allocById.get(row.lab_allocation_id);
      const venue = venueById.get(row.lab_venue_id);
      const lecturer = alloc?.lecturer_id != null ? lecturerById.get(alloc.lecturer_id) : undefined;
      const program = alloc?.program_id != null ? programById.get(alloc.program_id) : undefined;

      const payload: TimetableEntry = {
        id: uuid(),
        server_id: null,
        kind: 'lab_exam',
        course_allocation_id: row.lab_allocation_id,
        course_code: alloc?.course_code || '',
        course_name: alloc?.course_name || '',
        lecturer_name: lecturer?.name || '',
        program_name: program?.name || '',
        venue_id: row.lab_venue_id,
        venue_name: venue?.code || '',
        venue_kind: 'lab',
        day: row.day,
        date: row.date,
        start_time: row.start_time,
        end_time: row.end_time,
        version: 0,
        updated_at: now,
        updated_by: null,
        sync_state: 'dirty',
        base_version: 0,
        deleted: false
      };
      applyEdit({ local_op_id: uuid(), entry_id: payload.id, kind: 'lab_exam', op: 'create', payload, base_version: 0, created_at: now });
    }
  });

  return { status: result.status, message: result.message, removed: existing.length, created: result.schedule.length };
}

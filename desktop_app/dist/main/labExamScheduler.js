"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.runLabExamSchedulerLocally = runLabExamSchedulerLocally;
const uuid_1 = require("uuid");
const db = __importStar(require("./db"));
const sync = __importStar(require("./syncEngine"));
const editOps_1 = require("./editOps");
const pythonRunner_1 = require("./pythonRunner");
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
async function runLabExamSchedulerLocally(freshData = true) {
    let data;
    if (freshData) {
        await sync.pullLabExamReference();
        data = db.getLabExamReference();
    }
    else {
        data = db.getLabExamReference();
    }
    if (!data) {
        return { status: 'error', message: 'No lab/exam reference data available yet — connect once to pull it, then this can run offline.', removed: 0, created: 0 };
    }
    const result = await (0, pythonRunner_1.runLabExamScheduler)(data);
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
            (0, editOps_1.applyEdit)({ local_op_id: (0, uuid_1.v4)(), entry_id: e.id, kind: 'lab_exam', op: 'delete', payload: {}, base_version: e.base_version, created_at: now });
        }
        for (const row of result.schedule) {
            const alloc = allocById.get(row.lab_allocation_id);
            const venue = venueById.get(row.lab_venue_id);
            const lecturer = alloc?.lecturer_id != null ? lecturerById.get(alloc.lecturer_id) : undefined;
            const program = alloc?.program_id != null ? programById.get(alloc.program_id) : undefined;
            const payload = {
                id: (0, uuid_1.v4)(),
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
            (0, editOps_1.applyEdit)({ local_op_id: (0, uuid_1.v4)(), entry_id: payload.id, kind: 'lab_exam', op: 'create', payload, base_version: 0, created_at: now });
        }
    });
    return { status: result.status, message: result.message, removed: existing.length, created: result.schedule.length };
}

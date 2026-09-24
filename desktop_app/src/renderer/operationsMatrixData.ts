/**
 * Mirrors /timetable/dashboard/'s "Operations Matrix" 1:1 — same three numbered sections, same
 * sub-panel groupings, same item labels/descriptions (see dashboards/templates/dashboard/
 * timetabling_dashboard.html for the source of truth this was built from).
 *
 * Every item is a NATIVE Electron panel, backed by the local SQLite store and (where the item
 * involves an algorithm — auto-schedulers, PDF/CSV builders, publish flows) local logic, not a
 * browser tab pointed at the live server. There is no "embed" mode; the desktop app never loads
 * a server-rendered page inside itself.
 *
 * `target.panel` is set once a real panel exists (wired up in DashboardShell.tsx).
 * `target.status: 'queued'` items don't have one yet — `sourceRoute` records which Django view/
 * template that panel needs to be built from, so building it is "port this exact behavior
 * locally", not guesswork.
 */
import type { PanelKey } from './components/DashboardShell';

export type NavTarget =
  | { type: 'native'; status: 'built'; panel: PanelKey }
  | { type: 'native'; status: 'queued'; sourceRoute: string };

const built = (panel: PanelKey): NavTarget => ({ type: 'native', status: 'built', panel });
const queued = (sourceRoute: string): NavTarget => ({ type: 'native', status: 'queued', sourceRoute });

export interface MatrixItem {
  key: string;
  label: string;
  description: string;
  target: NavTarget;
}

export interface MatrixColumn {
  title: string;
  items: MatrixItem[];
}

export interface MatrixSection {
  key: string;
  title: string;
  columns: MatrixColumn[];
}

export const OPERATIONS_MATRIX: MatrixSection[] = [
  {
    key: 'main-campus',
    title: '1. Main Campus Internal Schedules & Deliverables',
    columns: [
      {
        title: 'Regular Class & Laboratory Panels',
        items: [
          { key: 'class_timetable', label: 'Class Timetable', description: 'Main campus lecture schedules workspace panel', target: built('main') },
          { key: 'lab_timetable', label: 'Lab Timetable', description: 'Laboratory technical practical room scheduler (Lab Sessions tab on Main Timetable)', target: built('main') },
          { key: 'timetable_autoscheduler', label: 'Timetable Auto Scheduler', description: 'Batch class schedule automation engine run', target: queued('/autoscheduler/home/') }
        ]
      },
      {
        title: 'Examination Timetables',
        items: [
          { key: 'exam_timetable', label: 'Exam Timetable', description: 'Main campus written evaluation schedule panel', target: built('exam') },
          { key: 'lab_exam_timetable', label: 'Lab Exam Timetable', description: 'Practical science lab testing venue sessions (Lab Exams tab on Exam Timetable)', target: built('exam') },
          { key: 'exam_autoscheduler', label: 'Exam Auto Scheduler', description: 'Automated structural algorithm for final exam blocks', target: queued('/exam-autoscheduler/') }
        ]
      },
      {
        title: 'Live Publication Feeds',
        items: [
          { key: 'publish_regular_official', label: 'Publish Regular (Official)', description: 'Compile and release standard timetables online', target: queued('/timetable/dashboard/') },
          { key: 'publish_exam_official', label: 'Publish Exam (Official)', description: 'Compile and release main examination rows online', target: queued('/timetable/dashboard/') },
          { key: 'published_regular', label: 'Published Regular Timetable', description: 'View & download the latest published regular timetable — cached for offline use', target: built('published_timetables') },
          { key: 'published_exam', label: 'Published Exam Timetable', description: 'View & download the latest published exam timetable — cached for offline use', target: built('published_timetables') }
        ]
      },
      {
        title: 'Main Campus Official Exports',
        items: [
          { key: 'regular_pdf', label: 'Regular Timetable (PDF)', description: 'Build and export official comprehensive class PDF', target: queued('/download/regular/timetable/') },
          { key: 'exam_pdf', label: 'Exam Timetable (PDF)', description: 'Build and export official comprehensive exam PDF', target: queued('/download/exam/timetable/') },
          { key: 'regular_csv', label: 'Regular Timetable CSV', description: 'Download clean data spreadsheet of main classes', target: queued('/autoscheduler/export/main/csv/') },
          { key: 'exam_csv', label: 'Exam CSV', description: 'Download clean data spreadsheet of main exams', target: queued('/export/timetable/') }
        ]
      }
    ]
  },
  {
    key: 'extended-campuses',
    title: '2. Extended Campuses & Cross-Site Unified Systems',
    columns: [
      {
        title: 'Dual Campus Coordination Workflows',
        items: [
          { key: 'dual_regular_scheduling', label: 'Regular Timetable Scheduling', description: 'Map cross-referenced lectures across concurrent sites', target: queued('/dual-scheduler/') },
          { key: 'dual_exam_scheduling', label: 'Exam Auto Scheduling', description: 'Algorithmic synchronized examination scheduling pipeline', target: queued('/exam/dual-campus/') },
          { key: 'global_regular_pdf', label: 'Global Regular Timetable (PDF)', description: 'Generate master multi-site combined lecture document', target: queued('/export/global/regular-timetable/') },
          { key: 'global_exam_pdf', label: 'Global Exam Timetable (PDF)', description: 'Generate master multi-site combined exam document', target: queued('/export/global/exam-timetable/') },
          { key: 'all_campuses_regular', label: 'All Campuses — Regular Timetable', description: 'Download verified universal regular timeline sheet', target: queued('/published/timetables/?section=campus_class') },
          { key: 'all_campuses_exam', label: 'All Campuses — Exam Timetable', description: 'Download verified universal examination timeline sheet', target: queued('/published/timetables/?section=campus_exam') }
        ]
      },
      {
        title: 'Branch Campus Timetables',
        items: [
          { key: 'campus_manual_scheduling', label: 'Campus Manual Scheduling', description: 'Manually input session matrices for branch hubs', target: queued('/manual/') },
          { key: 'campus_auto_scheduling', label: 'Auto Scheduling', description: 'Execute automatic branch processing tracks', target: queued('/auto/') },
          { key: 'campus_publish_class', label: 'Publish Class', description: 'Make active satellite lecture data available', target: queued('/auto/') },
          { key: 'campus_publish_exam', label: 'Publish Exam', description: 'Make active satellite evaluation rows available', target: queued('/auto/') },
          { key: 'campus_generate_class_pdf', label: 'Generate Class PDF', description: 'Export compile framework of satellite branches lectures', target: queued('/campuses/pdf/class/generate/') },
          { key: 'campus_generate_exam_pdf', label: 'Generate Exam PDF', description: 'Export compile framework of satellite branches exams', target: queued('/campuses/pdf/exam/generate/') },
          { key: 'campus_latest_class', label: 'Latest Class Timetable', description: 'Download official live satellite branches class document', target: queued('/campuses/download/class/latest/') },
          { key: 'campus_latest_exam', label: 'Latest Exam Timetable', description: 'Download official live satellite branches exam document', target: queued('/campuses/download/exam/latest/') }
        ]
      },
      {
        title: 'ODEL (Distance Learning) Division',
        items: [
          { key: 'odel_manual_scheduling', label: 'ODEL Manual Scheduling', description: 'Manually map open, distance, e-learning sessions', target: queued('/odel/manual/') },
          { key: 'odel_auto_scheduling', label: 'ODEL Auto Scheduling', description: 'Automated distribution track for virtual courses', target: queued('/odel/auto/') },
          { key: 'odel_publish_class', label: 'Publish Class', description: 'Lock and broadcast remote lecture structures', target: queued('/odel/auto/') },
          { key: 'odel_publish_exam', label: 'Publish Exam', description: 'Lock and broadcast remote assessment structures', target: queued('/odel/auto/') },
          { key: 'odel_latest_class', label: 'Latest Class Timetable', description: 'Acquire compiled tracking file for live digital lectures', target: queued('/odel/download/class/latest/') },
          { key: 'odel_latest_exam', label: 'Latest Exam Timetable', description: 'Acquire compiled tracking file for live digital exams', target: queued('/odel/download/exam/latest/') }
        ]
      }
    ]
  },
  {
    key: 'supplemental',
    title: '3. Supplemental Evaluation Tracks, Resource Assets & Configurations',
    columns: [
      {
        title: 'Resit Special Timetabling',
        items: [
          { key: 'resit_manual', label: 'Manual Timetabling', description: 'Explicit override allocations for supplemental tests', target: queued('/resits/manual/') },
          { key: 'resit_auto', label: 'Auto Scheduler', description: 'Algorithmic configuration tracker for student resits', target: queued('/resits/auto/') },
          { key: 'resit_my_allocations', label: 'My Resit Allocations', description: 'Review departmental specific metrics allocations logs', target: queued('/resits/cod/') },
          { key: 'resit_generate_allocation', label: 'Generate Resit Allocation', description: 'Build/refresh the resit allocation PDF for every department with resit data', target: queued('/allocations/resit/generate/') },
          { key: 'resit_publish', label: 'Publish Resit Timetable', description: 'Commit completed resit structures to live indexes', target: queued('/resits/manual/') },
          { key: 'resit_latest', label: 'Latest Resit Timetable', description: 'Direct pull access file for current supplemental rosters', target: queued('/resits/pdf/download/') }
        ]
      },
      {
        title: 'Resource Management & Parameters',
        items: [
          { key: 'venues_constraints', label: 'Venues & Constraints', description: 'Venues, lecturer blocks/preferences & autoscheduler constraint toggles', target: queued('/venues/') },
          { key: 'lab_venues', label: 'Lab Venues', description: 'Manage technical practical resource constraints', target: queued('/lab-venues/') },
          { key: 'scheduler_config', label: 'Scheduler Config', description: 'Modify foundational parameters and engine boundaries', target: queued('/autoscheduler/config/') },
          { key: 'allocation_reports', label: 'Allocation Reports', description: 'Main/ODeL/Campus/Resit allocation PDFs — every department, auto-refreshed', target: queued('/allocations/timetable-dashboard/') }
        ]
      },
      {
        title: 'System Performance Feedback & Diagnostics',
        items: [
          { key: 'view_feedback', label: 'View Feedback', description: 'Review user reported anomalies and structural notes', target: queued('/feedback_panel/') },
          {
            key: 'system_challenge_log',
            label: 'System Challenge Log',
            description: "Log whether a challenge was a system fault, its cause, resolution, prevention & who was involved — export/re-import as Excel or PDF",
            target: queued('/system-challenges/')
          },
          { key: 'send_notification', label: 'Send Notification', description: 'Push a message or document to all, students, or lecturers on the mobile app', target: queued('/notifications/mobile/') },
          { key: 'download_feedback_pdf', label: 'Download Feedback (PDF)', description: 'Compile and extract log system comments list', target: queued('/feedback/export/pdf/') },
          {
            key: 'timetable_analysis_reports',
            label: 'Timetable Analysis & Reports',
            description: 'Scheduled/unscheduled counts, PDF exports by faculty/department/program, and course schedule lookup',
            target: built('analysis')
          }
        ]
      }
    ]
  }
];

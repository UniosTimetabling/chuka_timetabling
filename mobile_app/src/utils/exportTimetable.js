import { DAY_ORDER, sortTimeSlots, formatExamDateLabel } from './dateHelpers';

// Builds a self-contained HTML document for a timetable, used by
// QRShareScreen to render a PDF (via expo-print) for sharing outside the
// app — into WhatsApp, email, another timetable app, or just to save as a
// file — where a person-to-person in-app QR handshake isn't possible.
// `examTimetable` is optional (same shape as the main timetable but with
// `dates` instead of `days` — see config.js API_CONTRACT #4c); when
// present it's appended as its own section so an export always reflects
// both schedules, not just the recurring weekly one.
export function buildTimetableHtml(timetable, examTimetable) {
  const ownerName = escapeHtml(
    timetable?.owner?.name || examTimetable?.owner?.name || 'Timetable'
  );

  const days = (timetable?.days || [])
    .slice()
    .sort((a, b) => DAY_ORDER.indexOf(a.day) - DAY_ORDER.indexOf(b.day));
  const mainSections = days.map((d) => sectionHtml(d.day, d.entries));

  const examDates = examTimetable?.dates || [];
  const examSections = examDates.map((d) => sectionHtml(formatExamDateLabel(d.date, d.day), d.entries));

  const mainHtml = mainSections.filter(Boolean).join('');
  const examHtml = examSections.filter(Boolean).join('');

  return `
    <html>
      <head>
        <meta charset="utf-8" />
        <style>
          body { font-family: -apple-system, Helvetica, Arial, sans-serif; color: #1a1a1a; padding: 24px; }
          h1 { font-size: 20px; margin-bottom: 2px; }
          .subtitle { font-size: 11px; color: #666; margin-bottom: 20px; }
          h2 { font-size: 14px; color: #0C6B8C; border-bottom: 2px solid #22B8C7; padding-bottom: 4px; margin-top: 22px; }
          h3.section { font-size: 12px; color: #6E8A90; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 28px; }
          table { width: 100%; border-collapse: collapse; margin-top: 8px; }
          th { background: #0C6B8C; color: white; text-align: left; padding: 6px 8px; font-size: 11px; }
          td { padding: 6px 8px; font-size: 11px; border-bottom: 1px solid #eee; }
          td.slot { font-weight: 700; white-space: nowrap; }
        </style>
      </head>
      <body>
        <h1>${ownerName}</h1>
        <div class="subtitle">Chuka Timetable &middot; exported ${new Date().toLocaleString()}</div>

        <h3 class="section">Class Timetable</h3>
        ${mainHtml || '<p>No classes scheduled.</p>'}

        ${examHtml ? `<h3 class="section">Exam Timetable</h3>${examHtml}` : ''}
      </body>
    </html>`;
}

function sectionHtml(label, entries) {
  const rows = sortTimeSlots((entries || []).map((e) => e.timeSlot))
    .map((slot) => (entries || []).find((e) => e.timeSlot === slot))
    .filter(Boolean)
    .map(
      (e) => `
        <tr>
          <td class="slot">${escapeHtml(e.timeSlot)}</td>
          <td>${escapeHtml(e.course || '')}</td>
          <td>${escapeHtml(e.venue || '')}</td>
          <td>${escapeHtml(e.lecturer || '')}</td>
        </tr>`
    )
    .join('');

  if (!rows) return '';

  return `
    <h2>${escapeHtml(label)}</h2>
    <table>
      <thead>
        <tr><th>Time</th><th>Course</th><th>Venue</th><th>Lecturer</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

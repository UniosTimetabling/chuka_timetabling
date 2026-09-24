import React, { useMemo, useState } from 'react';
import { TimetableEntry } from '@shared/types';
import { useTimetableStore, SlotTarget } from '../state/store';

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const NAMES_SUNDAY_FIRST = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

interface SlotDef {
  start_time: string;
  end_time: string;
}

interface Column {
  key: string;            // day name, or ISO date for exam kinds
  label: string;
  day: string;
  date: string | null;
}

interface Props {
  /** Slot rows to show when there is no data yet. Once entries exist, the rows come from the data itself. */
  slots: SlotDef[];
  /** Exam kinds: columns are calendar dates (an exam period spans weeks) instead of weekdays. */
  showDateColumn?: boolean;
}

const weekdayOf = (iso: string) => NAMES_SUNDAY_FIRST[new Date(`${iso}T00:00:00Z`).getUTCDay()];
const slotId = (s: SlotDef) => `${s.start_time}__${s.end_time}`;

export default function TimetableGrid({ slots: defaultSlots, showDateColumn }: Props) {
  const entries = useTimetableStore((s) => s.entries);
  const selectedIds = useTimetableStore((s) => s.selectedIds);
  const select = useTimetableStore((s) => s.select);
  const clearSelection = useTimetableStore((s) => s.clearSelection);
  const moveEntry = useTimetableStore((s) => s.moveEntry);
  const copySelection = useTimetableStore((s) => s.copySelection);
  const cutSelection = useTimetableStore((s) => s.cutSelection);
  const pasteInto = useTimetableStore((s) => s.pasteInto);
  const deleteSelection = useTimetableStore((s) => s.deleteSelection);
  const clipboard = useTimetableStore((s) => s.clipboard);
  const notice = useTimetableStore((s) => s.notice);

  const [query, setQuery] = useState('');
  const [venue, setVenue] = useState('');

  const all = useMemo(() => Object.values(entries), [entries]);

  const venues = useMemo(() => Array.from(new Set(all.map((e) => e.venue_name))).sort((a, b) => a.localeCompare(b)), [all]);

  // Columns and rows are derived from ALL entries (not just the filtered ones) so the layout stays put while filtering.
  const columns: Column[] = useMemo(() => {
    if (showDateColumn) {
      const dates = Array.from(new Set(all.map((e) => e.date).filter((d): d is string => !!d))).sort();
      return dates.map((d) => ({ key: d, label: `${weekdayOf(d).slice(0, 3)} ${d}`, day: weekdayOf(d), date: d }));
    }
    const extra = Array.from(new Set(all.map((e) => e.day).filter((d) => !WEEKDAYS.includes(d))));
    const days = [...WEEKDAYS, ...extra.sort((a, b) => NAMES_SUNDAY_FIRST.indexOf(a) - NAMES_SUNDAY_FIRST.indexOf(b))];
    return days.map((d) => ({ key: d, label: d, day: d, date: null }));
  }, [all, showDateColumn]);

  const slots: SlotDef[] = useMemo(() => {
    const seen = new Map<string, SlotDef>();
    for (const e of all) seen.set(slotId(e), { start_time: e.start_time, end_time: e.end_time });
    const list = seen.size > 0 ? Array.from(seen.values()) : defaultSlots;
    return [...list].sort((a, b) => a.start_time.localeCompare(b.start_time) || a.end_time.localeCompare(b.end_time));
  }, [all, defaultSlots]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all.filter(
      (e) =>
        (!venue || e.venue_name === venue) &&
        (!q || [e.course_code, e.course_name, e.lecturer_name, e.program_name, e.venue_name].some((f) => (f || '').toLowerCase().includes(q)))
    );
  }, [all, query, venue]);

  const cellKey = (colKey: string, s: SlotDef) => `${colKey}__${slotId(s)}`;
  const byCell = useMemo(() => {
    const map = new Map<string, TimetableEntry[]>();
    for (const e of visible) {
      const key = cellKey(showDateColumn ? e.date ?? '' : e.day, e);
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(e);
    }
    return map;
  }, [visible, showDateColumn]);

  // Last-focused cell, used as the paste target for Ctrl+V.
  const lastCellRef = React.useRef<SlotTarget | null>(null);
  const targetFor = (col: Column, s: SlotDef): SlotTarget => ({
    day: col.day,
    start_time: s.start_time,
    end_time: s.end_time,
    ...(showDateColumn ? { date: col.date } : {})
  });

  function handleKeyDown(e: React.KeyboardEvent) {
    if ((e.target as HTMLElement).tagName === 'INPUT' || (e.target as HTMLElement).tagName === 'SELECT') return; // typing in a filter box
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) {
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIds.length) {
        e.preventDefault();
        deleteSelection();
      }
      return;
    }
    const k = e.key.toLowerCase();
    if (k === 'c') {
      e.preventDefault();
      copySelection();
    } else if (k === 'x') {
      e.preventDefault();
      cutSelection();
    } else if (k === 'v') {
      e.preventDefault();
      if (lastCellRef.current) pasteInto(lastCellRef.current);
    }
  }

  function onDragStart(ev: React.DragEvent, entryId: string) {
    ev.dataTransfer.setData('text/chuka-entry-id', entryId);
    ev.dataTransfer.effectAllowed = 'move';
  }

  function onDrop(ev: React.DragEvent, col: Column, s: SlotDef) {
    ev.preventDefault();
    const entryId = ev.dataTransfer.getData('text/chuka-entry-id');
    if (entryId) moveEntry(entryId, targetFor(col, s)); // the entry keeps its own venue
  }

  return (
    <div className="tt-grid-wrap" tabIndex={0} onKeyDown={handleKeyDown} onClick={() => clearSelection()}>
      <div className="tt-filters" onClick={(e) => e.stopPropagation()}>
        <input
          type="search"
          placeholder="Filter by course, lecturer, program or venue…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select value={venue} onChange={(e) => setVenue(e.target.value)}>
          <option value="">All venues</option>
          {venues.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <span className="tt-hint">
          Showing {visible.length} of {all.length}
        </span>
      </div>
      {notice && <div className="tt-notice">{notice}</div>}

      {all.length === 0 && (
        <p className="tt-hint tt-empty">
          Nothing here yet. Use “Sync now” at the bottom to download the timetable from the server.
        </p>
      )}

      <table className="tt-grid">
        <thead>
          <tr>
            <th className="tt-corner">Time</th>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {slots.map((slot) => (
            <tr key={slotId(slot)}>
              <td className="tt-time-col">
                {slot.start_time}–{slot.end_time}
              </td>
              {columns.map((col) => {
                const cellEntries = byCell.get(cellKey(col.key, slot)) || [];
                return (
                  <td
                    key={col.key}
                    className="tt-cell"
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => onDrop(e, col, slot)}
                    onClick={(e) => {
                      e.stopPropagation();
                      lastCellRef.current = targetFor(col, slot);
                      if (cellEntries.length === 0) clearSelection();
                    }}
                  >
                    {cellEntries.map((entry) => (
                      <div
                        key={entry.id}
                        draggable={entry.sync_state !== 'conflict'}
                        onDragStart={(e) => onDragStart(e, entry.id)}
                        onClick={(e) => {
                          e.stopPropagation();
                          select(entry.id, e.ctrlKey || e.metaKey);
                          lastCellRef.current = targetFor(col, slot);
                        }}
                        className={
                          'tt-entry ' +
                          (selectedIds.includes(entry.id) ? 'tt-entry--selected ' : '') +
                          (clipboard?.entryIds.includes(entry.id) && clipboard.mode === 'cut' ? 'tt-entry--cut ' : '') +
                          (entry.sync_state === 'dirty' ? 'tt-entry--dirty ' : '') +
                          (entry.sync_state === 'conflict' ? 'tt-entry--conflict ' : '')
                        }
                        title={`${entry.course_code} — ${entry.course_name}\n${entry.lecturer_name}\n${entry.program_name}\n${entry.venue_name}${
                          entry.date ? '\n' + entry.date : ''
                        }`}
                      >
                        <div className="tt-entry-code">{entry.course_code}</div>
                        <div className="tt-entry-venue">
                          {entry.venue_name}
                          {entry.venue_kind === 'lab' && <span className="tt-badge tt-badge--lab">LAB</span>}
                        </div>
                        {entry.sync_state === 'conflict' && <span className="tt-badge">⚠ needs decision</span>}
                        {entry.sync_state === 'dirty' && <span className="tt-badge tt-badge--dirty">unsynced</span>}
                      </div>
                    ))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

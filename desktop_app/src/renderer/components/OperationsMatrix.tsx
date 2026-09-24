import React, { useState } from 'react';
import { OPERATIONS_MATRIX } from '../operationsMatrixData';
import type { PanelKey } from './DashboardShell';

export default function OperationsMatrix({ onNavigate }: { onNavigate: (panel: PanelKey) => void }) {
  const [activeSection, setActiveSection] = useState(OPERATIONS_MATRIX[0].key);
  const section = OPERATIONS_MATRIX.find((s) => s.key === activeSection) ?? OPERATIONS_MATRIX[0];

  return (
    <div className="tt-panel tt-matrix">
      <div className="tt-panel-header">
        <h2>Timetabling Dashboard — Operations Matrix</h2>
        <p className="tt-hint">
          Every panel from the web dashboard, built as a native, offline-capable panel here. Items marked
          "queued" aren't built yet — they open nothing rather than a copy of the website.
        </p>
      </div>

      <div className="tt-matrix-section-tabs">
        {OPERATIONS_MATRIX.map((s) => (
          <button key={s.key} className={s.key === activeSection ? 'active' : ''} onClick={() => setActiveSection(s.key)}>
            {s.title}
          </button>
        ))}
      </div>

      <div className="tt-matrix-columns">
        {section.columns.map((col) => (
          <div key={col.title} className="tt-matrix-column">
            <h4>{col.title}</h4>
            <div className="tt-matrix-items">
              {col.items.map((item) => {
                const isBuilt = item.target.status === 'built';
                return (
                  <button
                    key={item.key}
                    className={`tt-matrix-item${isBuilt ? '' : ' tt-matrix-item--queued'}`}
                    disabled={!isBuilt}
                    onClick={() => isBuilt && onNavigate((item.target as { panel: PanelKey }).panel)}
                    title={isBuilt ? undefined : 'Not built yet — coming in a later update'}
                  >
                    <span className="tt-matrix-item-label">
                      {item.label}
                      {!isBuilt && <span className="tt-matrix-item-badge tt-matrix-item-badge--queued">queued</span>}
                    </span>
                    <span className="tt-matrix-item-desc">{item.description}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

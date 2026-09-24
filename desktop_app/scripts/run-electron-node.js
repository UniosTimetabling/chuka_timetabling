// Runs a script on Electron's bundled Node (same ABI as the app's better-sqlite3 build) — no window, no display needed.
const { spawnSync } = require('child_process');
const electron = require('electron'); // path to the electron binary
const r = spawnSync(electron, process.argv.slice(2), { stdio: 'inherit', env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' } });
process.exit(r.status ?? 1);

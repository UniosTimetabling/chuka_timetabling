import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';
import { app } from 'electron';
import type { LabExamReferenceData } from '../shared/types';

/**
 * Runs desktop_app/python-runner/lab_exam_scheduler.py — the dependency-free port of
 * timetable/algorithms/run_lab_exam_autoscheduler.py's RULES (see that script's own docstring
 * for the parity story) — as a subprocess, entirely offline: no network call, no server.
 *
 * IMPORTANT — current limitation, not yet resolved: this spawns whatever `python3` (or `python`)
 * is first found on the machine's PATH. It does NOT yet bundle its own Python runtime, so on a
 * machine with no Python installed this will fail with a clear error rather than silently doing
 * nothing. Bundling a self-contained interpreter (e.g. the official Windows embeddable
 * distribution, placed under extraResources) is the next piece of this feature — see
 * python-runner/README.md.
 */

export interface SchedulerResult {
  status: 'success' | 'warning' | 'error';
  message: string;
  schedule: Array<{
    lab_allocation_id: number;
    lab_venue_id: number;
    date: string;
    day: string;
    start_time: string;
    end_time: string;
  }>;
}

function scriptPath(name: string): string {
  // Dev: desktop_app/python-runner/<name>.py, three levels up from dist/main/pythonRunner.js.
  // Packaged: resources/python-runner/<name>.py (see the "extraResources" entry in package.json).
  // `app` is undefined when this runs headless (ELECTRON_RUN_AS_NODE=1, e.g. the sync E2E test)
  // rather than inside a real Electron window — fall straight through to the dev path then.
  const packaged = path.join(process.resourcesPath || '', 'python-runner', name);
  if (app?.isPackaged && fs.existsSync(packaged)) return packaged;
  return path.join(__dirname, '..', '..', 'python-runner', name);
}

function findPython(): string {
  // Prefer a bundled, self-contained interpreter if one has been placed under
  // python-runtime/ (see python-runtime/README.md — not populated yet as of this writing).
  const platformDir = process.platform === 'win32' ? 'win' : 'linux';
  const exeName = process.platform === 'win32' ? 'python.exe' : 'bin/python3';
  const bundled = path.join(process.resourcesPath || '', 'python-runtime', platformDir, exeName);
  if (app?.isPackaged && fs.existsSync(bundled)) return bundled;

  const bundledDev = path.join(__dirname, '..', '..', 'python-runtime', platformDir, exeName);
  if (fs.existsSync(bundledDev)) return bundledDev;

  // No bundled interpreter yet — fall back to whatever's on PATH.
  return process.platform === 'win32' ? 'python' : 'python3';
}

function runScript<T>(name: string, input: unknown, timeoutMs = 30_000): Promise<T> {
  return new Promise((resolve, reject) => {
    const target = scriptPath(name);
    if (!fs.existsSync(target)) {
      reject(new Error(`Scheduler script not found: ${target}`));
      return;
    }

    const child = spawn(findPython(), [target], { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('The local scheduler took too long and was stopped.'));
    }, timeoutMs);

    child.stdout.on('data', (d) => (stdout += d.toString()));
    child.stderr.on('data', (d) => (stderr += d.toString()));
    child.on('error', (err) => {
      clearTimeout(timer);
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') {
        reject(new Error('Python was not found on this machine. Install Python 3, or wait for the bundled-runtime update.'));
      } else {
        reject(err);
      }
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(stderr.trim() || `The local scheduler exited with code ${code}.`));
        return;
      }
      try {
        resolve(JSON.parse(stdout) as T);
      } catch {
        reject(new Error(`The local scheduler returned something that wasn't valid JSON: ${stdout.slice(0, 500)}`));
      }
    });

    child.stdin.write(JSON.stringify(input));
    child.stdin.end();
  });
}

export function runLabExamScheduler(data: LabExamReferenceData): Promise<SchedulerResult> {
  return runScript<SchedulerResult>('lab_exam_scheduler.py', data);
}

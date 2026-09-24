"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.runLabExamScheduler = runLabExamScheduler;
const child_process_1 = require("child_process");
const fs_1 = __importDefault(require("fs"));
const path_1 = __importDefault(require("path"));
const electron_1 = require("electron");
function scriptPath(name) {
    // Dev: desktop_app/python-runner/<name>.py, three levels up from dist/main/pythonRunner.js.
    // Packaged: resources/python-runner/<name>.py (see the "extraResources" entry in package.json).
    // `app` is undefined when this runs headless (ELECTRON_RUN_AS_NODE=1, e.g. the sync E2E test)
    // rather than inside a real Electron window — fall straight through to the dev path then.
    const packaged = path_1.default.join(process.resourcesPath || '', 'python-runner', name);
    if (electron_1.app?.isPackaged && fs_1.default.existsSync(packaged))
        return packaged;
    return path_1.default.join(__dirname, '..', '..', 'python-runner', name);
}
function findPython() {
    // Prefer a bundled, self-contained interpreter if one has been placed under
    // python-runtime/ (see python-runtime/README.md — not populated yet as of this writing).
    const platformDir = process.platform === 'win32' ? 'win' : 'linux';
    const exeName = process.platform === 'win32' ? 'python.exe' : 'bin/python3';
    const bundled = path_1.default.join(process.resourcesPath || '', 'python-runtime', platformDir, exeName);
    if (electron_1.app?.isPackaged && fs_1.default.existsSync(bundled))
        return bundled;
    const bundledDev = path_1.default.join(__dirname, '..', '..', 'python-runtime', platformDir, exeName);
    if (fs_1.default.existsSync(bundledDev))
        return bundledDev;
    // No bundled interpreter yet — fall back to whatever's on PATH.
    return process.platform === 'win32' ? 'python' : 'python3';
}
function runScript(name, input, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
        const target = scriptPath(name);
        if (!fs_1.default.existsSync(target)) {
            reject(new Error(`Scheduler script not found: ${target}`));
            return;
        }
        const child = (0, child_process_1.spawn)(findPython(), [target], { stdio: ['pipe', 'pipe', 'pipe'] });
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
            if (err.code === 'ENOENT') {
                reject(new Error('Python was not found on this machine. Install Python 3, or wait for the bundled-runtime update.'));
            }
            else {
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
                resolve(JSON.parse(stdout));
            }
            catch {
                reject(new Error(`The local scheduler returned something that wasn't valid JSON: ${stdout.slice(0, 500)}`));
            }
        });
        child.stdin.write(JSON.stringify(input));
        child.stdin.end();
    });
}
function runLabExamScheduler(data) {
    return runScript('lab_exam_scheduler.py', data);
}

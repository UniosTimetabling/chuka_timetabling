"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.exportHtmlToPdf = exportHtmlToPdf;
const electron_1 = require("electron");
const fs_1 = __importDefault(require("fs"));
/**
 * Renders an HTML string to PDF entirely inside the desktop app and saves
 * it to disk, via Chromium's own print-to-PDF (Electron exposes it as
 * webContents.printToPDF). No network call, no server-side report
 * endpoint involved — this is why it works with the app fully offline.
 *
 * Returns the saved file path, or null if the user cancelled the save
 * dialog.
 */
async function exportHtmlToPdf(html, suggestedFileName) {
    const { canceled, filePath } = await electron_1.dialog.showSaveDialog({
        title: 'Export PDF',
        defaultPath: suggestedFileName.endsWith('.pdf') ? suggestedFileName : `${suggestedFileName}.pdf`,
        filters: [{ name: 'PDF', extensions: ['pdf'] }]
    });
    if (canceled || !filePath)
        return null;
    const win = new electron_1.BrowserWindow({ show: false, webPreferences: { offscreen: true } });
    try {
        await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
        const pdfBuffer = await win.webContents.printToPDF({
            printBackground: true,
            landscape: false,
            margins: { top: 0, bottom: 0, left: 0, right: 0 },
            pageSize: 'A4'
        });
        fs_1.default.writeFileSync(filePath, pdfBuffer);
        return filePath;
    }
    finally {
        win.destroy();
    }
}

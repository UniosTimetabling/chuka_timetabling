# PDF / Word export choice

Every PDF export can now be downloaded as Word (.docx) — the user chooses.

* Engine: `core/doc_export.py` (middleware `ExportFormatMiddleware` is registered in settings).
* URL switch: add `?export_format=docx` to any GET export URL (`pdf` is the default).
* Browser dialog: `core/templates/core/_export_format_chooser.html`
  - links: `<a href="..." data-export-choice>`  (optional `data-word-url`)
  - JS: `window.exportWithFormat(url)` / `window.chooseExportFormat()`
* ReportLab builders import `SimpleDocTemplate` / `BaseDocTemplate` from `core.doc_export`;
  WeasyPrint builders import `HTML` from `core.doc_export`.
* Builders that STORE documents (publish_timetables_pdfs, program_year_pdf_cache,
  allocation runs) stay real PDFs; Word for those is converted from the stored PDF (pdf2docx).
* Email (analysis page): "Send attachments as: PDF / Word".
* Install: `pip install -r requirements.txt` (adds pdf2docx, lxml).
* Not covered: notification attachments, mobile/desktop apps. Word copies of the feedback
  challenge export cannot be re-imported.

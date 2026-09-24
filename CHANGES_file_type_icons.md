# Mobile app: file-type icons (no emojis)

Icons are chosen from the END of the URL / file name (a link can be a PDF):
.pdf -> PDF icon, .doc/.docx -> Word icon, no known extension -> link icon.
Also handles Excel, PowerPoint, images and zip.

New:     mobile_app/src/utils/fileType.js, src/components/FileTypeIcon.js
Changed: BlockedScheduleNotice.js (emoji removed, type-aware link button),
         EventCard.js, screens/EventDetailScreen.js, screens/FeedbackScreen.js

Uses @expo/vector-icons (bundled with Expo, already in the lockfile).

# Merged: Word/PDF export choice
Merged from university_timetabling_updated.zip (see WORD_EXPORT_README.md):
core/doc_export.py (+ middleware in settings.py), core/templates/core/_export_format_chooser.html,
export/report modules, and the PDF-or-Word chooser on the dashboard and panel templates.
Run `pip install -r requirements.txt` (adds pdf2docx, lxml) and `python manage.py migrate`.

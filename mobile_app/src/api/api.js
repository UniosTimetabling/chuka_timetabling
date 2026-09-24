import { ENDPOINTS, NETWORK_ERROR_MESSAGE, TIMEOUT_ERROR_MESSAGE } from '../config/config';

async function request(url, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(timeout);
    const contentType = res.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await res.json() : null;
    if (!res.ok) {
      const message = (body && body.error) || `Request failed (${res.status})`;
      const err = new Error(message);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  } catch (e) {
    clearTimeout(timeout);
    if (e.name === 'AbortError') {
      const timeoutErr = new Error(TIMEOUT_ERROR_MESSAGE);
      timeoutErr.isTimeout = true;
      throw timeoutErr;
    }
    // Errors we already normalized above (bad HTTP status) carry a `status`
    // field — pass those straight through.
    if (e.status) {
      throw e;
    }
    // Anything else here is the raw fetch/network failure (e.g. "Failed to
    // fetch" on web, "Network request failed" on native) — in practice this
    // almost always means the device can't reach the university network at
    // all, so swap it for copy that actually tells the student what to do.
    const networkErr = new Error(NETWORK_ERROR_MESSAGE);
    networkErr.isNetworkError = true;
    throw networkErr;
  }
}

// Appends &regNo=... only when one is actually supplied — lecturers (and
// any call made before a student's regNo is known) never send this param,
// rather than serializing the literal string "undefined".
function withRegNo(base, regNo) {
  return regNo ? `${base}&regNo=${encodeURIComponent(regNo)}` : base;
}

export const api = {
  loginStudent: (regNo, programId, departmentId) =>
    request(ENDPOINTS.studentLogin, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // programId/departmentId are only set on retries, once the backend
      // has failed to recognise the code in the reg number the first time:
      // departmentId on the second call (student picked a department),
      // programId on the third (student picked their program from the
      // list narrowed down to that department).
      body: JSON.stringify({
        regNo,
        ...(programId ? { programId } : {}),
        ...(departmentId ? { departmentId } : {}),
      }),
    }),

  loginLecturer: (name) =>
    request(ENDPOINTS.lecturerLogin, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),

  // regNo is optional here (student-only) — without it the merged
  // timetable/version simply won't reflect that student's personal
  // course additions (see config.js API_CONTRACT note after entry 10).
  getVersion: (userId, role, regNo) =>
    request(
      withRegNo(`${ENDPOINTS.timetableVersion}?userId=${encodeURIComponent(userId)}&role=${role}`, regNo)
    ),

  getTimetable: (userId, role, regNo) =>
    request(
      withRegNo(`${ENDPOINTS.timetable}?userId=${encodeURIComponent(userId)}&role=${role}`, regNo)
    ),

  getExamVersion: (userId, role) =>
    request(`${ENDPOINTS.examTimetableVersion}?userId=${encodeURIComponent(userId)}&role=${role}`),

  getExamTimetable: (userId, role) =>
    request(`${ENDPOINTS.examTimetable}?userId=${encodeURIComponent(userId)}&role=${role}`),

  getEvents: (userId, role) =>
    request(`${ENDPOINTS.events}?userId=${encodeURIComponent(userId)}&role=${role}`),

  getSharedTimetable: (token, viewerRole) =>
    request(
      `${ENDPOINTS.sharedTimetable}?token=${encodeURIComponent(token)}&viewerRole=${viewerRole}`
    ),

  // Accepts one or several comma-separated course codes exactly as the
  // person typed them ("coms101, PHYS 342-a") — the backend normalizes.
  // userId/role/regNo are optional (only used to flag isAdded on results).
  searchCourses: (q, userId, role, regNo) => {
    let url = `${ENDPOINTS.coursesSearch}?q=${encodeURIComponent(q)}`;
    if (userId && role) url += `&userId=${encodeURIComponent(userId)}&role=${role}`;
    return request(withRegNo(url, regNo));
  },

  addCourses: (userId, role, regNo, allocationIds) =>
    request(ENDPOINTS.coursesAdd, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userId, role, regNo, allocationIds }),
    }),

  removeCourse: (userId, role, regNo, allocationId) =>
    request(ENDPOINTS.coursesRemove, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userId, role, regNo, allocationId }),
    }),

  getMyCourses: (userId, role, regNo) =>
    request(
      withRegNo(`${ENDPOINTS.coursesMine}?userId=${encodeURIComponent(userId)}&role=${role}`, regNo)
    ),

  // Public, no auth. Returns { url, source: 'custom'|'apk'|'default' } —
  // the link staff set on the web dashboard.
  getAppLink: () => request(ENDPOINTS.appLink, {}, 8000),

  // `formData` is built by the caller (FeedbackScreen) — a plain FormData
  // with full_name/email/message/admission_number/userId/role and zero or
  // more 'attachments' file parts. Deliberately no 'Content-Type' header
  // here: fetch sets the multipart boundary itself when given a FormData
  // body, and overriding it manually breaks the upload.
  submitFeedback: (formData) =>
    request(ENDPOINTS.feedback, {
      method: 'POST',
      body: formData,
    }),
};

const state = {
  user: JSON.parse(localStorage.getItem("currentUser") || "null"),
  courses: [],
  lessons: [],
  activeCourse: null,
  activeLesson: null,
  selectedText: "",
  lastAnswer: "",
};

const el = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  applySavedPrefs();
  renderUser();
  loadAll();
});

function bindEvents() {
  el("menuButton").addEventListener("click", () => {
    el("nav").classList.toggle("open");
    el("menuButton").setAttribute("aria-expanded", el("nav").classList.contains("open"));
  });
  el("themeToggle").addEventListener("click", toggleTheme);
  el("textToggle").addEventListener("click", toggleTextSize);
  el("loginTab").addEventListener("click", () => setAuthTab("login"));
  el("registerTab").addEventListener("click", () => setAuthTab("register"));
  el("loginForm").addEventListener("submit", login);
  el("registerForm").addEventListener("submit", register);
  el("courseSearch").addEventListener("input", renderCourses);
  el("enrollButton").addEventListener("click", enrollActiveCourse);
  el("saveHighlight").addEventListener("click", openNoteDialog);
  el("confirmNote").addEventListener("click", saveNote);
  el("checkAnswer").addEventListener("click", checkAnswer);
  el("completeLesson").addEventListener("click", completeLesson);
  el("resetCode").addEventListener("click", resetCode);
  el("runCode").addEventListener("click", runCodePreview);
  el("donationForm").addEventListener("submit", submitDonation);
  el("applyFilters").addEventListener("click", loadDonations);
  el("lessonForm").addEventListener("submit", submitLesson);
  document.addEventListener("click", handleDocumentClick);
}

async function loadAll() {
  await loadCourses();
  await loadDonations();
  await loadDashboard();
  await loadNotes();
  await loadAdmin();
  updateManageVisibility();
}

function handleDocumentClick(event) {
  const courseButton = event.target.closest("[data-course-id]");
  if (courseButton) {
    selectCourse(Number(courseButton.dataset.courseId), true);
    return;
  }

  const lessonButton = event.target.closest("[data-lesson-id]");
  if (lessonButton) {
    selectLesson(Number(lessonButton.dataset.lessonId));
    return;
  }

  const requestButton = event.target.closest("[data-request-donation]");
  if (requestButton) {
    requestDonation(Number(requestButton.dataset.requestDonation));
    return;
  }

  const statusButton = event.target.closest("[data-donation-status]");
  if (statusButton) {
    setDonationStatus(Number(statusButton.dataset.donationId), statusButton.dataset.donationStatus);
    return;
  }

  const deleteButton = event.target.closest("[data-delete-note]");
  if (deleteButton) {
    deleteNote(Number(deleteButton.dataset.deleteNote));
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function setAuthTab(tab) {
  el("loginTab").classList.toggle("active", tab === "login");
  el("registerTab").classList.toggle("active", tab === "register");
  el("loginForm").classList.toggle("hidden", tab !== "login");
  el("registerForm").classList.toggle("hidden", tab !== "register");
}

async function login(event) {
  event.preventDefault();
  try {
    state.user = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget).entries())),
    });
    localStorage.setItem("currentUser", JSON.stringify(state.user));
    el("authStatus").textContent = "Logged in successfully.";
    renderUser();
    await loadAll();
  } catch (error) {
    el("authStatus").textContent = error.message;
  }
}

async function register(event) {
  event.preventDefault();
  try {
    state.user = await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget).entries())),
    });
    localStorage.setItem("currentUser", JSON.stringify(state.user));
    el("authStatus").textContent = "Account created.";
    renderUser();
    await loadAll();
  } catch (error) {
    el("authStatus").textContent = error.message;
  }
}

function renderUser() {
  const loggedIn = Boolean(state.user);
  el("loginForm").classList.toggle("hidden", loggedIn);
  el("registerForm").classList.add("hidden");
  el("loginTab").classList.toggle("hidden", loggedIn);
  el("registerTab").classList.toggle("hidden", loggedIn);
  el("userPanel").classList.toggle("hidden", !loggedIn);
  el("rolePill").textContent = loggedIn ? state.user.role : "Guest";
  if (loggedIn) {
    el("userPanel").innerHTML = `
      <strong>${escapeHtml(state.user.name)}</strong>
      <span>${escapeHtml(state.user.role)} · ${escapeHtml(state.user.location || "No location")}</span>
      <button class="button secondary small" id="logoutButton">Logout</button>
    `;
    el("logoutButton").addEventListener("click", logout);
  }
  updateManageVisibility();
}

function logout() {
  localStorage.removeItem("currentUser");
  state.user = null;
  renderUser();
  loadAll();
}

async function loadDashboard() {
  if (!state.user) {
    el("statGrid").innerHTML = statCards({ courses: state.courses.length, lessons: 0, progress_percent: 0, donations: 0, available_resources: 0, requests: 0, notes: 0 });
    return;
  }
  const data = await api(`/api/dashboard?user_id=${state.user.id}`);
  el("statGrid").innerHTML = statCards(data.stats);
}

function statCards(stats) {
  const items = [
    ["Courses", stats.courses],
    ["Lessons", stats.lessons],
    ["Progress", `${stats.progress_percent || 0}%`],
    ["Quiz avg", `${stats.quiz_average || 0}%`],
    ["Donations", stats.donations],
    ["Available items", stats.available_resources],
    ["Requests", stats.requests],
    ["Notes", stats.notes],
  ];
  return items.map(([label, value]) => `<article class="stat-card"><strong>${value}</strong><span>${label}</span></article>`).join("");
}

async function loadCourses() {
  state.courses = await api("/api/courses");
  renderCourses();
  el("lessonCourseSelect").innerHTML = state.courses.map((course) => `<option value="${course.id}">${escapeHtml(course.title)}</option>`).join("");
  if (!state.activeCourse && state.courses.length) await selectCourse(state.courses[0].id, false);
}

function renderCourses() {
  const term = el("courseSearch").value.toLowerCase();
  const courses = state.courses.filter((course) => `${course.title} ${course.language} ${course.description}`.toLowerCase().includes(term));
  el("courseGrid").innerHTML = courses
    .map((course) => `
      <button class="course-card ${state.activeCourse?.id === course.id ? "selected" : ""}" style="border-top: 6px solid ${course.accent}" data-course-id="${course.id}">
        <span class="course-badge" style="background:${course.accent}">${escapeHtml(course.language)}</span>
        <h3>${escapeHtml(course.title)}</h3>
        <p>${escapeHtml(course.description)}</p>
        <div class="course-meta">
          <span>${escapeHtml(course.level)}</span>
          <span>${escapeHtml(course.duration)}</span>
          <span>${course.lesson_count} lessons</span>
        </div>
      </button>
    `)
    .join("");
}

async function selectCourse(courseId, openClassroom = false) {
  state.activeCourse = state.courses.find((course) => course.id === courseId);
  const userParam = state.user ? `?user_id=${state.user.id}` : "";
  state.lessons = await api(`/api/courses/${courseId}/lessons${userParam}`);
  el("activeCourseTitle").textContent = state.activeCourse.title;
  renderCourses();
  renderLessonList();
  if (state.lessons.length) await selectLesson(state.lessons[0].id);
  if (openClassroom) {
    el("classroom").classList.add("visible");
    el("classroom").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderLessonList() {
  el("lessonList").innerHTML = state.lessons
    .map((lesson) => `
      <button class="lesson-button ${state.activeLesson?.id === lesson.id ? "active" : ""}" data-lesson-id="${lesson.id}">
        <strong>${lesson.position}. ${escapeHtml(lesson.title)}</strong>
        <span>${escapeHtml(lesson.summary)}</span>
        <small>${lesson.completed ? "Completed" : "Not completed"} ${lesson.score !== null ? "· Score " + lesson.score : ""}</small>
      </button>
    `)
    .join("");
}

async function selectLesson(lessonId) {
  state.activeLesson = await api(`/api/lessons/${lessonId}`);
  el("lessonLanguage").textContent = `${state.activeLesson.course_title} - ${state.activeLesson.language}`;
  el("lessonTitle").textContent = state.activeLesson.title;
  resetCode();
  el("challengeText").textContent = `${state.activeLesson.challenge} ${state.activeLesson.question}`;
  el("answerInput").value = "";
  el("answerFeedback").textContent = "";
  el("lessonResources").innerHTML = state.activeLesson.resources.map((resource) => `<span class="pill">${escapeHtml(resource.resource_type)}: ${escapeHtml(resource.title)}</span>`).join("");
  renderLessonList();
  await loadLessonNotes();
}

function resetCode() {
  el("codeEditor").value = state.activeLesson?.starter_code || "// Lesson code appears here";
  el("codeOutput").textContent = state.activeLesson?.expected_output || "Output preview appears here.";
}

function runCodePreview() {
  const code = el("codeEditor").value;
  const lines = code.split("\n").filter((line) => line.includes("print") || line.includes("System.out.println") || line.includes("console.log"));
  el("codeOutput").textContent = lines.length
    ? lines.map((line) => line.replace(/.*\((.*)\).*/, "$1").replaceAll("\"", "").replaceAll("'", "")).join("\n")
    : state.activeLesson?.expected_output || "This preview checks common print statements. Use the challenge answer for scoring.";
}

async function enrollActiveCourse() {
  if (!requireLogin()) return;
  await api("/api/enrollments", {
    method: "POST",
    body: JSON.stringify({ user_id: state.user.id, course_id: state.activeCourse.id }),
  });
  el("answerFeedback").textContent = "Enrollment saved.";
}

async function checkAnswer() {
  if (!state.activeLesson) return;
  state.lastAnswer = el("answerInput").value.trim();
  const expected = state.activeLesson.answer.trim().toLowerCase();
  const actual = state.lastAnswer.toLowerCase();
  el("answerFeedback").textContent = actual === expected
    ? "Correct. Use Complete check to save progress."
    : `Try again. Hint: ${state.activeLesson.hint || "review the lesson text."}`;
}

async function completeLesson() {
  if (!requireLogin() || !state.activeLesson) return;
  const result = await api("/api/progress", {
    method: "POST",
    body: JSON.stringify({ user_id: state.user.id, lesson_id: state.activeLesson.id, answer: el("answerInput").value.trim() }),
  });
  el("answerFeedback").textContent = result.correct ? "Progress saved. Lesson completed." : "Progress saved, but answer is not correct yet.";
  await selectCourse(state.activeCourse.id, false);
  await loadDashboard();
}

async function loadLessonNotes() {
  if (!state.activeLesson) return;
  let content = escapeHtml(state.activeLesson.content);
  if (state.user) {
    const notes = await api(`/api/notes?user_id=${state.user.id}&lesson_id=${state.activeLesson.id}`);
    notes.forEach((note) => {
      const escaped = escapeHtml(note.highlight_text);
      content = content.replace(escaped, `<mark class="saved-highlight" style="background:${note.color}" title="${escapeHtml(note.note_text)}">${escaped}</mark>`);
    });
  }
  el("lessonContent").innerHTML = content;
}

function openNoteDialog() {
  if (!requireLogin() || !state.activeLesson) return;
  state.selectedText = window.getSelection().toString().trim() || sentenceNearSelection();
  if (!state.selectedText) {
    el("answerFeedback").textContent = "Select a sentence from the lesson before saving a note.";
    return;
  }
  el("selectedTextPreview").textContent = state.selectedText;
  el("noteText").value = "";
  el("noteDialog").showModal();
}

function sentenceNearSelection() {
  const text = el("lessonContent").textContent.trim();
  return text.split(/(?<=[.!?])\s+/)[0] || "";
}

async function saveNote(event) {
  event.preventDefault();
  const noteText = el("noteText").value.trim();
  if (!noteText) {
    el("noteText").focus();
    return;
  }
  await api("/api/notes", {
    method: "POST",
    body: JSON.stringify({
      user_id: state.user.id,
      student_name: state.user.name,
      lesson_id: state.activeLesson.id,
      highlight_text: state.selectedText,
      note_text: noteText,
      color: el("highlightColor").value,
    }),
  });
  el("noteDialog").close();
  await loadLessonNotes();
  await loadNotes();
  await loadDashboard();
}

async function loadNotes() {
  if (!state.user) {
    el("notesGrid").innerHTML = `<article class="note-card"><strong>No account selected</strong><p>Login to view saved highlights.</p></article>`;
    return;
  }
  const notes = await api(`/api/notes?user_id=${state.user.id}`);
  el("notesGrid").innerHTML = notes.length
    ? notes.map((note) => `
      <article class="note-card" style="border-top-color:${note.color}">
        <strong>${escapeHtml(note.lesson_title)}</strong>
        <small>${escapeHtml(note.course_title)} - ${new Date(note.created_at).toLocaleString()}</small>
        <blockquote>${escapeHtml(note.highlight_text)}</blockquote>
        <p>${escapeHtml(note.note_text)}</p>
        <button class="button secondary small" data-delete-note="${note.id}">Delete</button>
      </article>
    `).join("")
    : `<article class="note-card"><strong>No notes yet</strong><p>Highlight a lesson sentence and save your first note.</p></article>`;
}

async function deleteNote(noteId) {
  await api(`/api/notes/${noteId}?user_id=${state.user.id}`, { method: "DELETE" });
  await loadNotes();
  await loadLessonNotes();
  await loadDashboard();
}

async function submitDonation(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  if (state.user) payload.donor_user_id = state.user.id;
  try {
    const result = await api("/api/donations", { method: "POST", body: JSON.stringify(payload) });
    el("donationStatus").textContent = result.message;
    event.currentTarget.reset();
    await loadDonations();
    await loadDashboard();
  } catch (error) {
    el("donationStatus").textContent = error.message;
  }
}

async function loadDonations() {
  const params = new URLSearchParams({
    search: el("resourceSearch").value,
    item_type: el("resourceType").value,
    location: el("resourceLocation").value,
  });
  const donations = await api(`/api/donations?${params.toString()}`);
  el("donationList").innerHTML = donations.map((item) => `
    <article class="donation-card">
      <strong>${escapeHtml(item.title)}</strong>
      <span>${escapeHtml(item.item_type)} - Qty ${item.quantity} - ${escapeHtml(item.status)}</span>
      <span>${escapeHtml(item.condition_note || "Ready for students")}</span>
      <small>${escapeHtml(item.donor_name)} ${item.location ? "- " + escapeHtml(item.location) : ""}</small>
      <div class="tool-row">
        <button class="button secondary small" data-request-donation="${item.id}">Request</button>
        ${state.user && ["Admin", "Donor"].includes(state.user.role) ? `
          <button class="button secondary small" data-donation-id="${item.id}" data-donation-status="Assigned">Assign</button>
          <button class="button secondary small" data-donation-id="${item.id}" data-donation-status="Delivered">Deliver</button>
        ` : ""}
      </div>
    </article>
  `).join("");
}

async function requestDonation(donationId) {
  if (!requireLogin()) return;
  await api("/api/resource-requests", {
    method: "POST",
    body: JSON.stringify({ user_id: state.user.id, donation_id: donationId, message: "I need this for my studies." }),
  });
  await loadDashboard();
  await loadAdmin();
}

async function setDonationStatus(donationId, status) {
  await api(`/api/donations/${donationId}`, {
    method: "PATCH",
    body: JSON.stringify({ user_id: state.user.id, status }),
  });
  await loadDonations();
}

async function submitLesson(event) {
  event.preventDefault();
  if (!state.user || !["Admin", "Teacher"].includes(state.user.role)) {
    el("lessonFormStatus").textContent = "Login as teacher or admin to add lessons.";
    return;
  }
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  payload.user_id = state.user.id;
  try {
    const result = await api("/api/lessons", { method: "POST", body: JSON.stringify(payload) });
    el("lessonFormStatus").textContent = result.message;
    event.currentTarget.reset();
    await loadCourses();
  } catch (error) {
    el("lessonFormStatus").textContent = error.message;
  }
}

async function loadAdmin() {
  if (!state.user || state.user.role !== "Admin") {
    el("adminPanel").innerHTML = `<article class="note-card"><strong>Admin report</strong><p>Only admins can see users, resource requests, and progress records here.</p></article>`;
    return;
  }
  const data = await api(`/api/admin?user_id=${state.user.id}`);
  el("adminPanel").innerHTML = `
    <article class="report-card"><h3>Users</h3>${data.users.map((u) => `<p>${escapeHtml(u.name)} - ${escapeHtml(u.role)} - ${escapeHtml(u.email)}</p>`).join("")}</article>
    <article class="report-card"><h3>Resource requests</h3>${data.requests.map((r) => `<p>${escapeHtml(r.student_name)} requested ${escapeHtml(r.donation_title)} - ${escapeHtml(r.status)}</p>`).join("") || "<p>No requests yet.</p>"}</article>
    <article class="report-card"><h3>Recent progress</h3>${data.progress.map((p) => `<p>${escapeHtml(p.name)} - ${escapeHtml(p.lesson_title)} - Score ${p.score}</p>`).join("") || "<p>No progress yet.</p>"}</article>
  `;
}

function updateManageVisibility() {
  const canTeach = state.user && ["Admin", "Teacher"].includes(state.user.role);
  el("lessonForm").classList.toggle("locked", !canTeach);
  const submitButton = el("lessonForm").querySelector("button[type='submit']");
  submitButton.disabled = !canTeach;
  el("lessonFormStatus").textContent = canTeach
    ? "Use Manage to add new lessons. Admins also see reports on the right."
    : "Manage is for teachers/admins. Login as teacher@example.com or admin@example.com to add lessons.";
}

function requireLogin() {
  if (state.user) return true;
  el("authStatus").textContent = "Please login first.";
  location.hash = "#home";
  return false;
}

function applySavedPrefs() {
  if (localStorage.getItem("theme") === "dark") document.body.classList.add("dark");
  if (localStorage.getItem("largeText") === "true") document.body.classList.add("large-text");
}

function toggleTheme() {
  document.body.classList.toggle("dark");
  localStorage.setItem("theme", document.body.classList.contains("dark") ? "dark" : "light");
}

function toggleTextSize() {
  document.body.classList.toggle("large-text");
  localStorage.setItem("largeText", document.body.classList.contains("large-text") ? "true" : "false");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

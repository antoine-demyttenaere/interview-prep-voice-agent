/*
app.js - the logic of the web page: talks to the API, records your voice, plays the interviewer.

How it works:
- Login first: on load, GET /auth/me says whether the browser's login cookie is still valid. If
  not, the login screen shows (POST /auth/login sets the cookie; the browser then sends it with
  every request by itself, and page scripts can't read it). Any 401 later (login expired,
  account disabled) brings the login screen back, and logging in again continues where you
  were. "Create an account" switches to the registration form (POST /auth/register: email,
  password and invite code), which logs you in too; #register in the address opens it. "Log out" calls POST /auth/logout, forgets this browser's session id and draft, and
  reloads the page.
- Every screen comes from a "view" the API returns (status + what the session is waiting for).
  render(view) shows the right panel: brief review, interview, or feedback.
- Setup is a 3-step form moved through from left to right: Next / Back, a swipe, a click on the
  step bar, or Enter (goStep). Step 1 needs a company and a role before moving on.
- Setup: "Your CV" lists your stored CV (GET /documents/cv) with a Delete button. A new CV (file
  or pasted) replaces the stored one when "Replace my stored CV" is ticked: the old ones are
  deleted after the new one is saved. Files go to POST /documents/upload, pasted text to
  POST /documents.
- Recording uses the browser's MediaRecorder: click the button (or press Space) to start, and
  again to stop and send the audio to POST /sessions/{id}/turn (it also stops by itself after
  5 minutes). The reply holds what was heard, the next question, its audio (base64) and how
  long each step took.
- Typed answers (Voice switched off) go to POST /sessions/{id}/resume, like the terminal CLI.
- The transcript is grouped by question; "Based on N documents" folds open to show which of
  your documents the interviewer drew on.
- "End interview and get feedback" calls POST /sessions/{id}/end; "New interview" (top right)
  goes back to the setup form, and the session it leaves stays saved.
- The brief is a carousel: six numbered cards you scroll from left to right (carousel()).
- The feedback shows the score, a carousel (in short, fix first, keep doing) and details you
  click open: each rubric criterion (score bar -> why, and how to improve) and each question
  (score -> comment, a stronger answer, and your own answer from the transcript).
- The interviewer's audio for the first question, and for replays, comes from POST /speak.
- The last session id is remembered in this browser (localStorage) so a reload continues it.
*/

const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  view: null,
  voiceOn: true,
  recorder: null,   // MediaRecorder while recording
  starting: false,  // the microphone is opening
  chunks: [],
  recordStart: 0,
  autoStop: null,
  audio: null,      // the interviewer audio currently playing
  storedCv: [],     // [{id, source, chars, chunks}] from GET /documents/cv
};

// ---------- small helpers ----------

function remember(key, value) {
  try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch (_) {}
}
function recall(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

function busy(text) {
  $("busy").hidden = !text;
  $("busy-text").textContent = text || "";
  document.querySelectorAll("button").forEach((b) => (b.disabled = Boolean(text)));
}

// Builds an element: el("p", {class: "x"}, "text", otherElement, [moreChildren]).
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => (k === "class" ? (node.className = v) : node.setAttribute(k, v)));
  children.flat().forEach((c) => {
    if (c === null || c === undefined || c === false || c === "") return;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  });
  return node;
}

function list(items, ordered = false) {
  return el(ordered ? "ol" : "ul", {}, (items && items.length ? items : ["(none)"]).map((i) => el("li", {}, i)));
}

// 1-2 low (red), 3 mid (amber), 4-5 high (green)
const level = (score) => (score <= 2 ? "low" : score === 3 ? "mid" : "high");

function scoreBar(score) {
  return el("span", { class: `bar ${level(score)}` },
    [1, 2, 3, 4, 5].map((i) => el("span", { class: i <= score ? "on" : "" })));
}

// Calls the API and returns the JSON reply (null for "204 No Content"). Errors become a message.
async function api(method, path, body, isForm = false) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) options.body = body;
    else {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
  }
  const res = await fetch(path, options);
  // 401 = not logged in (any more): show the login screen. A wrong password on the login form
  // is also a 401, but that one is shown as a normal error.
  if (res.status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/auth/register")) {
    showLogin(path === "/auth/me" ? "" : "Your login has ended: please log in again.");
    const err = new Error("Please log in.");
    err.status = 401;
    err.loggedOut = true;
    throw err;
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

// Runs an action with a busy message, and shows any error.
async function run(text, action) {
  showError("");
  busy(text);
  try {
    await action();
  } catch (err) {
    hidePending(); // no "..." left behind when a step fails
    if (err.loggedOut) return; // the login screen is already showing
    showError(err.message);
    // A failed Claude call leaves the session "stopped": reload the view so Retry appears.
    if (err.status === 502 && state.sessionId) {
      try { render(await api("GET", `/sessions/${state.sessionId}`)); } catch (_) {}
    }
  } finally {
    busy("");
  }
}

// ---------- audio out ----------

function play(blob) {
  if (state.audio) state.audio.pause();
  state.audio = new Audio(URL.createObjectURL(blob));
  state.audio.play().catch(() => showError("The browser blocked audio playback: click 'Replay the question'."));
}

function playBase64(audio) {
  const bytes = Uint8Array.from(atob(audio.base64), (c) => c.charCodeAt(0));
  play(new Blob([bytes], { type: audio.mime_type }));
}

async function speakCurrentQuestion() {
  const res = await fetch(`/sessions/${state.sessionId}/speak`, { method: "POST" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Text-to-speech failed");
  }
  play(await res.blob());
}

// ---------- rendering ----------

function showPanel(name) {
  ["login", "setup", "brief", "interview", "feedback"].forEach((p) => ($(`panel-${p}`).hidden = p !== name));
  window.scrollTo({ top: 0 });
}

function render(view) {
  state.view = view;
  state.sessionId = view.session_id;
  remember("sessionId", view.session_id);
  $("session-label").textContent = `Session ${view.session_id.slice(0, 8)}`;
  $("leave").hidden = false;
  document.documentElement.lang = view.language || "en";

  if (view.status === "done") return renderFeedback(view);
  if (view.status === "stopped") return renderStopped(view);
  if (view.pending.type === "review_brief") return renderBrief(view.pending.brief);
  renderInterview(view);
}

// --- carousel: a row of cards you scroll (or swipe) from left to right ---
// cards: [{title, cls, content: [nodes]}]. Arrows move one card; dots show where you are.

function carousel(cards, onChange) {
  const track = el("div", { class: "carousel-track" },
    cards.map((c, i) => el("article", { class: `slide ${c.cls || ""}` },
      el("h3", {}, el("span", { class: "slide-no" }, i + 1), c.title), ...c.content)));
  const dots = el("div", { class: "carousel-dots" }, cards.map(() => el("span")));
  const slides = [...track.children];
  let current = 0;

  // Center card i in the track (its neighbours peek out on each side).
  const goTo = (i) => {
    const s = slides[Math.max(0, Math.min(slides.length - 1, i))];
    track.scrollTo({ left: s.offsetLeft + s.offsetWidth / 2 - track.clientWidth / 2, behavior: "smooth" });
  };
  const step = (dir) => goTo(current + dir);
  const prev = el("button", { type: "button", class: "btn arrow", "aria-label": "Previous card" }, "‹");
  const next = el("button", { type: "button", class: "btn arrow", "aria-label": "Next card" }, "›");
  prev.onclick = () => step(-1);
  next.onclick = () => step(1);
  slides.forEach((s, i) => s.addEventListener("click", () => { if (i !== current) goTo(i); }));

  // The current card is the one whose middle is closest to the middle of the track.
  const update = () => {
    const middle = track.scrollLeft + track.clientWidth / 2;
    current = slides.reduce((best, s, i) =>
      Math.abs(s.offsetLeft + s.offsetWidth / 2 - middle) <
      Math.abs(slides[best].offsetLeft + slides[best].offsetWidth / 2 - middle) ? i : best, 0);
    slides.forEach((s, i) => s.classList.toggle("current", i === current));
    [...dots.children].forEach((d, i) => d.classList.toggle("on", i === current));
    prev.classList.toggle("off", current === 0);
    next.classList.toggle("off", current === slides.length - 1);
    if (onChange) onChange(current, slides.length);
  };
  track.addEventListener("scroll", () => requestAnimationFrame(update));
  requestAnimationFrame(update);
  const node = el("div", { class: "carousel" }, el("div", { class: "carousel-row" }, prev, track, next), dots);
  node.step = step; // lets the keyboard arrows move this carousel
  return node;
}

// Keyboard ← → move the brief's cards (when the brief is on screen and you are not typing).
document.addEventListener("keydown", (e) => {
  if (!["ArrowLeft", "ArrowRight"].includes(e.key) || $("panel-brief").hidden) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  const c = $("brief").querySelector(".carousel");
  if (c) { e.preventDefault(); c.step(e.key === "ArrowLeft" ? -1 : 1); }
});

// --- brief: six cards in reading order ---

// "Approve" stays locked until you have reached the last card, so you go through the whole brief.
function setApprove(ready, left) {
  $("approve").classList.toggle("btn-locked", !ready);
  $("approve").classList.toggle("btn-ready", ready);
  $("approve").setAttribute("aria-disabled", String(!ready));
  $("approve-hint").textContent = ready
    ? "Ready when you are."
    : `Go through the cards first: ${left} more to see.`;
}

function renderBrief(b) {
  showPanel("brief");
  let seenLast = false;
  setApprove(false, 5);
  const people = b.interviewers.map((i) => `${i.name}, ${i.role}${i.notes ? ` (${i.notes})` : ""}`);
  $("brief").replaceChildren(carousel([
    { title: "Focus on", cls: "accent", content: [list(b.focus_areas)] },
    { title: "The company", content: [el("p", {}, b.company_summary)] },
    { title: "The interview", content: [el("p", {}, b.interview_format), people.length ? list(people) : ""] },
    { title: "The role needs", content: [list(b.role_requirements)] },
    { title: "Your gaps", cls: "warn", content: [list(b.cv_gaps)] },
    { title: "Likely questions", content: [list(b.probable_questions, true)] },
  ], (current, total) => {
    if (current === total - 1) seenLast = true;
    setApprove(seenLast, total - 1 - current);
  }));
}

// --- interview: the transcript grouped by question ---

function renderTranscript(turns) {
  const nodes = [];
  let lastQ = -1;
  for (const t of turns) {
    if (t.role === "interviewer" && t.q_index !== lastQ) {
      nodes.push(el("div", { class: "q-divider" }, `Question ${t.q_index + 1}`));
      lastQ = t.q_index;
    }
    const sources = t.sources && t.sources.length
      ? el("details", { class: "sources" },
          el("summary", {}, `Based on ${t.sources.length} document${t.sources.length > 1 ? "s" : ""}`),
          list(t.sources))
      : "";
    nodes.push(el("div", { class: `line ${t.role}` },
      el("span", { class: "who" }, t.role === "interviewer" ? "Interviewer" : "You",
        t.followup && t.role === "interviewer" ? el("span", { class: "tag" }, "follow-up") : ""),
      el("p", {}, t.text),
      sources));
  }
  $("transcript").replaceChildren(...nodes);
  const box = $("transcript").parentElement;
  box.scrollTop = box.scrollHeight; // wide screens: the conversation scrolls inside its panel
  $("transcript").lastElementChild?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// A temporary "..." bubble at the end of the conversation: role "candidate" while you speak,
// "interviewer" while the next question is being prepared. render() replaces it.
function showPending(role) {
  hidePending();
  const bubble = el("div", { class: `line ${role} pending`, id: "pending-bubble" },
    el("span", { class: "who" }, role === "interviewer" ? "Interviewer" : "You"),
    el("span", { class: "dots", "aria-label": role === "interviewer" ? "The interviewer is thinking" : "Recording" },
      el("i"), el("i"), el("i")));
  $("transcript").append(bubble);
  bubble.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function hidePending() { $("pending-bubble")?.remove(); }

// ---------- interview countdown (the duration chosen at setup) ----------
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function updateClock() {
  const v = state.view;
  const running = v && v.duration_min && v.interview_started_at && v.status === "waiting"
    && v.pending && v.pending.type === "answer";
  $("clock").hidden = !running;
  if (!running) return;
  const total = v.duration_min * 60;
  const left = Math.max(0, total - (Date.now() - Date.parse(v.interview_started_at)) / 1000);
  $("clock-text").textContent = fmt(left);
  $("clock-fill").style.width = `${(left / total) * 100}%`;
  $("clock").classList.toggle("low", left <= 5 * 60);
  $("clock-note").hidden = left > 0;
  $("end-interview").classList.toggle("urgent", left === 0);
}
setInterval(updateClock, 1000);

function setProgress(qIndex, total, note) {
  $("progress").textContent = total ? `Question ${Math.min(qIndex + 1, total)} of ${total}` : "Interview";
  $("progress-fill").style.width = total ? `${Math.round((qIndex / total) * 100)}%` : "0";
  $("progress-note").textContent = note || "";
}

function renderInterview(view) {
  showPanel("interview");
  const p = view.pending;
  setProgress(p.q_index, p.total, p.followup ? "Follow-up on this question" : "");
  renderTranscript(view.transcript);
  $("voice-controls").hidden = !state.voiceOn;
  $("text-controls").hidden = state.voiceOn;
  $("end-interview").hidden = false;
  updateClock();
}

function renderStopped(view) {
  showPanel("interview");
  setProgress(view.q_index || 0, view.total_questions, "A step failed");
  renderTranscript(view.transcript);
  $("voice-controls").hidden = true;
  $("text-controls").hidden = true;
  showError("A step failed (see the message above). Press Retry to run it again.");
  const retry = el("button", { class: "btn btn-primary" }, "Retry");
  retry.onclick = () => run("Retrying...", async () => render(await api("POST", `/sessions/${state.sessionId}/retry`)));
  $("transcript").append(retry);
}

// --- feedback: overview on the left, clickable details on the right ---

function exchangeFor(transcript, number) {
  const turns = (transcript || []).filter((t) => t.q_index === number - 1);
  if (!turns.length) return "";
  return el("div", { class: "exchange" },
    turns.map((t) => el("p", {}, el("strong", {}, t.role === "interviewer" ? "Interviewer: " : "You: "), t.text)));
}

function renderFeedback(view) {
  showPanel("feedback");
  const fb = view.final_feedback;
  const names = Object.fromEntries((view.rubric || []).map((c) => [c.id, c.name]));
  const prettify = (id) => names[id] || id.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const answered = new Set((view.transcript || []).filter((t) => t.role === "candidate").map((t) => t.q_index)).size;

  // Top: the score, then the three key messages as cards you swipe through.
  const overview = el("div", { class: "panel overview" },
    el("div", { class: "score-head" },
      el("div", { class: `score-big ${level(fb.overall_score)}` }, el("span", {}, fb.overall_score, el("small", {}, "/ 5"))),
      el("div", {},
        el("h2", {}, "Your feedback"),
        view.ended_early
          ? el("span", { class: "badge" }, `Ended early: ${answered} of ${view.total_questions} questions`)
          : el("span", { class: "muted small" }, `${view.total_questions} questions`))),
    carousel([
      { title: "In short", content: [el("p", {}, fb.summary)] },
      { title: "Fix first", cls: "accent", content: [list(fb.fixes, true)] },
      { title: "Keep doing", cls: "good", content: [list(fb.strengths)] },
    ]),
  );

  // Each criterion opens to: why this score, and how to raise it.
  const rubric = fb.rubric_scores.map((s) =>
    el("details", { class: "item" },
      el("summary", {}, el("span", { class: "crit-name" }, prettify(s.criterion)), scoreBar(s.score),
        el("span", { class: `num ${level(s.score)}` }, s.score)),
      el("div", { class: "body" },
        el("p", {}, el("span", { class: "label" }, "Why this score"), s.note),
        s.improve && s.improve.length
          ? el("div", { class: "improve" }, el("span", { class: "label" }, "How to improve"), list(s.improve))
          : "")));

  const questions = fb.per_question.map((q) =>
    el("details", { class: "item" },
      el("summary", {}, el("span", { class: `num ${level(q.score)}` }, q.score),
        el("span", { class: "q-text" }, q.number ? `Q${q.number}. ${q.question}` : q.question)),
      el("div", { class: "body" },
        el("p", {}, el("span", { class: "label" }, "Comment"), q.comment),
        q.better ? el("p", {}, el("span", { class: "label" }, "A stronger answer"), q.better) : "",
        q.number ? el("span", { class: "label" }, "The exchange") : "",
        q.number ? exchangeFor(view.transcript, q.number) : "")));

  $("feedback").replaceChildren(
    overview,
    el("div", { class: "panel details" },
      el("h2", {}, "Rubric"), el("p", { class: "hint muted small" }, "Click a criterion to see why, and how to improve it."), ...rubric,
      el("h2", {}, "Question by question"), el("p", { class: "hint muted small" }, "Click a question to see the comment and your answer."),
      ...questions),
  );
}

function showTimings(t) {
  if (!t) return ($("timings").textContent = "");
  const s = (ms) => (ms === undefined ? "-" : `${(ms / 1000).toFixed(1)} s`);
  $("timings").textContent =
    `Last turn ${s(t.total)}: listening ${s(t.speech_to_text)}, interviewer ${s(t.interviewer)}, voice ${s(t.text_to_speech)}`;
}

// After a view that waits for an answer, say the question out loud (if voice is on).
async function afterView(view) {
  render(view);
  if (state.voiceOn && view.status === "waiting" && view.pending.type === "answer") {
    await speakCurrentQuestion();
  }
}

// ---------- setup: my CV ----------

async function loadStoredCv() {
  try {
    state.storedCv = await api("GET", "/documents/cv");
  } catch (_) {
    state.storedCv = [];
  }
  const box = $("my-cv");
  if (!state.storedCv.length) {
    box.replaceChildren(el("p", { class: "empty" }, "No CV stored yet: add one on the right."));
  } else {
    box.replaceChildren(...state.storedCv.map((d) => {
      const del = el("button", { type: "button", class: "btn btn-danger btn-small" }, "Delete");
      del.onclick = () => {
        if (!confirm(`Delete the stored CV "${d.source}"?`)) return;
        run("Deleting...", async () => {
          await api("DELETE", `/documents/${d.id}`);
          if (state.editing) state.editing.changed = true;
          await loadStoredCv();
          updateSubmitLabel();
        });
      };
      return el("div", { class: "stored" },
        el("span", {}, el("strong", {}, d.source), el("span", { class: "muted small" }, ` · ${d.chunks} chunks`)), del);
    }));
  }
  updateReplaceRow();
}

function updateReplaceRow() {
  const f = $("setup-form");
  const hasNew = ($("cv-file-input").files.length > 0) || f.elements.cv.value.trim();
  $("replace-cv-row").hidden = !(hasNew && state.storedCv.length);
}

$("cv-file-input").addEventListener("change", updateReplaceRow);
$("setup-form").elements.cv.addEventListener("input", updateReplaceRow);
$("files-input").addEventListener("change", (e) => {
  $("file-chips").replaceChildren(...[...e.target.files].map((file) => el("li", {}, file.name)));
});

// ---------- setup: the 3-step form (left to right) ----------
// The steps sit side by side on #wizard-rail, inside #wizard-track (which clips). Going to a
// step slides the rail with a CSS transform; the other steps are made `inert` (no focus, no
// clicks), so nothing can scroll a half-hidden step into view. A swipe on a touchscreen also
// moves one step.

const wizard = { step: 0, count: 3 };
const WIZARD_HINTS = [
  "Who the interview is with, and what kind.",
  "The job post and what you know about the company: paste texts, add links or files.",
  "Check your CV, then prepare your interview brief (about 30 s).",
];

function updateWizard() {
  [...$("wizard-steps").children].forEach((li, i) => {
    li.classList.toggle("active", i === wizard.step);
    li.classList.toggle("done", i < wizard.step);
  });
  const last = wizard.step === wizard.count - 1;
  $("wizard-back").style.visibility = wizard.step === 0 ? "hidden" : "visible";
  $("wizard-next").hidden = last;
  $("wizard-submit").hidden = !last;
  $("wizard-hint").textContent = WIZARD_HINTS[wizard.step];
}

// The form keeps the height of its tallest step on every step, so the Back / Next bar stays in
// the same place when you move between steps.
function fitTrack() {
  const tallest = Math.max(...[...$("wizard-rail").children].map((s) => s.offsetHeight));
  if (tallest > 0) $("wizard-track").style.height = `${tallest}px`;
}

function goStep(i, smooth = true) {
  wizard.step = Math.max(0, Math.min(wizard.count - 1, i));
  const rail = $("wizard-rail");
  rail.style.transition = smooth ? "" : "none";
  rail.style.transform = `translateX(${-100 * wizard.step}%)`;
  [...rail.children].forEach((s, k) => { s.inert = k !== wizard.step; });
  updateWizard();
  fitTrack();
}

// A step's height also changes when its content does (file chips, the stored CV list...).
const stepResize = new ResizeObserver(fitTrack);
[...$("wizard-rail").children].forEach((s) => stepResize.observe(s));

// Step 1 needs a company and a role before moving on.
function stepOneValid() {
  const f = $("setup-form").elements;
  for (const name of ["company", "role"]) {
    if (!f[name].value.trim()) {
      goStep(0);                 // bring step 1 back into view
      f[name].reportValidity();  // the browser's "Please fill out this field" bubble
      return false;
    }
  }
  return true;
}

function nextStep() {
  if (wizard.step === 0 && !stepOneValid()) return;
  goStep(wizard.step + 1);
}

$("wizard-next").addEventListener("click", nextStep);
$("wizard-back").addEventListener("click", () => goStep(wizard.step - 1));
[...$("wizard-steps").children].forEach((li, i) =>
  li.addEventListener("click", () => { if (i <= wizard.step || stepOneValid()) goStep(i); }));

// A horizontal swipe on a touchscreen moves one step (left = next, right = back).
let swipeStart = null;
$("wizard-track").addEventListener("touchstart", (e) => {
  swipeStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
}, { passive: true });
$("wizard-track").addEventListener("touchend", (e) => {
  if (!swipeStart) return;
  const dx = e.changedTouches[0].clientX - swipeStart.x;
  const dy = e.changedTouches[0].clientY - swipeStart.y;
  swipeStart = null;
  if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy)) return; // a tap or a vertical scroll
  if (dx < 0) nextStep(); else goStep(wizard.step - 1);
});
window.addEventListener("resize", fitTrack);

// Enter in a one-line field = Next (or "Write my brief" on the last step); Enter in a text area adds a line.
$("setup-form").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.target.tagName !== "INPUT" || e.target.id === "resume-id") return;
  e.preventDefault();
  if (wizard.step < wizard.count - 1) nextStep();
  else $("setup-form").requestSubmit($("wizard-submit"));
});
goStep(0, false);

// ---------- setup: write my brief ----------

$("setup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (wizard.step < wizard.count - 1) return nextStep();
  if (!stepOneValid()) return;
  const f = new FormData(e.target);
  const job = f.get("job_post").trim(), notes = f.get("notes").trim(), cv = f.get("cv").trim();
  const jobLink = f.get("job_link").trim();
  // the job post's link is stored as the job post; the company pages as web pages
  const links = [
    ...(jobLink ? [{ url: jobLink, doc_type: "job_post" }] : []),
    ...f.get("links").split(/\s+/).map((s) => s.trim()).filter(Boolean).map((url) => ({ url, doc_type: "web_page" })),
  ];
  const files = f.getAll("files").filter((file) => file.size), cvFile = f.get("cv_file");
  const hasCvFile = cvFile && cvFile.size;
  const replaceCv = f.get("replace_cv") === "on";
  if (state.editing && !editingChanged()) {
    const { sessionId } = state.editing;
    return run("Back to your brief...", async () => {
      const view = await api("GET", `/sessions/${sessionId}`);
      state.editing = null;
      clearDraft();
      render(view);
    });
  }
  const alreadyAdded = state.editing ? $("existing-docs").children.length : 0;
  if (!job && !notes && !links.length && !files.length && !cv && !hasCvFile && !state.storedCv.length && !alreadyAdded) {
    return showError("Add at least one thing to read: the job post, a link, notes, a file or your CV.");
  }

  run("Reading everything and preparing your interview brief (about 30 s)...", async () => {
    const fields = { company: f.get("company"), role: f.get("role"), mode: f.get("mode"), language: f.get("language") };
    const editing = state.editing;
    let interview;
    if (editing) {
      // Back from a brief: update the same interview instead of creating a new one.
      interview = await api("PATCH", `/interviews/${editing.interviewId}`, fields);
    } else {
      interview = await api("POST", "/interviews", fields);
    }
    // Files are read on the server (PDF, Word, .eml), then cut into chunks and embedded.
    const upload = async (file, fields) => {
      const form = new FormData();
      form.append("file", file);
      Object.entries(fields).forEach(([k, v]) => form.append(k, v));
      const doc = await api("POST", "/documents/upload", form, true);
      if (doc.embedding_error) showError(`${file.name} was saved but not indexed: ${doc.embedding_error}`);
    };

    // A new CV, then (if asked) remove the old ones, so only the new one is used.
    const oldCvIds = state.storedCv.map((d) => d.id);
    let newCv = false;
    if (hasCvFile) { await upload(cvFile, { doc_type: "cv" }); newCv = true; }
    if (cv) { await api("POST", "/documents", { doc_type: "cv", source: "pasted CV", text: cv }); newCv = true; }
    if (newCv && replaceCv) for (const id of oldCvIds) await api("DELETE", `/documents/${id}`);

    // Pasted job post and notes. When editing, an unchanged text is kept as it is (no need to
    // index it again); a changed or emptied one replaces the old document.
    for (const [docType, text, source] of [["job_post", job, "pasted job post"], ["notes", notes, "pasted notes"]]) {
      const old = editing && editing.pasted[docType];
      if (old && old.text.trim() === text) continue;
      if (old) await api("DELETE", `/documents/${old.id}`);
      if (text) await api("POST", "/documents", { interview_id: interview.id, doc_type: docType, source, text });
    }
    for (const file of files) await upload(file, { interview_id: interview.id });

    // Links: the server reads each page. A page that cannot be read does not stop the brief;
    // it is listed so you can paste its text instead.
    const failed = [];
    for (const { url, doc_type } of links) {
      try {
        const doc = await api("POST", "/documents/from-url", { interview_id: interview.id, url, doc_type });
        if (doc.embedding_error) failed.push(`${url} was read but not indexed: ${doc.embedding_error}`);
      } catch (err) {
        failed.push(err.message);
      }
    }
    if (links.length) { $("setup-form").elements.links.value = ""; $("setup-form").elements.job_link.value = ""; }

    const questions = Number(f.get("questions")) || null;
    const view = await api("POST", `/interviews/${interview.id}/sessions`,
      { num_questions: questions, duration_min: Number(f.get("duration")) || null });
    state.editing = null;
    clearDraft();
    render(view);
    if (failed.length) showError(`Some links could not be read: ${failed.join(" · ")}`);
  });
});

function resumeById() {
  const id = $("resume-id").value.trim();
  if (!id) return showError("Paste a session id first.");
  run("Loading the session...", async () => render(await api("GET", `/sessions/${id}`)));
}
$("resume").addEventListener("click", resumeById);
$("resume-id").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); resumeById(); } // don't submit the setup form
});

// ---------- brief review ----------

$("approve").addEventListener("click", () => {
  if ($("approve").classList.contains("btn-locked")) {
    // Still locked: move to the next card instead, so the click is not lost.
    $("brief").querySelector(".carousel")?.step(1);
    return;
  }
  run("Planning the questions...", async () =>
    afterView(await api("POST", `/sessions/${state.sessionId}/resume`, { value: { action: "approve" } })));
});

$("revise").addEventListener("click", () => {
  const feedback = $("brief-feedback").value.trim();
  if (!feedback) return showError("Write what should change first.");
  run("Rewriting the brief...", async () => {
    render(await api("POST", `/sessions/${state.sessionId}/resume`, { value: { action: "revise", feedback } }));
    $("brief-feedback").value = "";
  });
});

// ---------- interview: voice ----------

const MAX_RECORDING_MS = 5 * 60 * 1000; // safety net: an answer is sent after 5 minutes

async function startRecording() {
  if (state.recorder || state.starting || $("talk").disabled) return;
  if (state.audio) state.audio.pause(); // stop the interviewer talking over you
  showError("");
  state.starting = true; // the microphone takes a moment to open; ignore clicks meanwhile
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.chunks = [];
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (e) => e.data.size && state.chunks.push(e.data);
    state.recorder.onstop = () => stream.getTracks().forEach((t) => t.stop());
    state.recorder.start();
    state.recordStart = Date.now();
    state.autoStop = setTimeout(stopRecording, MAX_RECORDING_MS);
    $("talk").classList.add("recording");
    $("talk").textContent = "Stop and send";
    showPending("candidate");
    // "1:23 / 5:00" under the button; coral blinking in the last 30 seconds
    const limit = MAX_RECORDING_MS / 1000;
    const tick = () => {
      const s = (Date.now() - state.recordStart) / 1000;
      $("rec-timer").textContent = `${fmt(s)} / ${fmt(limit)}`;
      $("rec-timer").classList.toggle("late", s >= limit - 30);
    };
    tick();
    $("rec-timer").hidden = false;
    state.recTick = setInterval(tick, 250);
  } catch (err) {
    state.recorder = null;
    showError(`Microphone unavailable: ${err.message}. Allow the microphone for localhost, or switch voice off.`);
  } finally {
    state.starting = false;
  }
}

function stopRecording() {
  const recorder = state.recorder;
  if (!recorder) return;
  state.recorder = null;
  clearTimeout(state.autoStop);
  clearInterval(state.recTick);
  $("rec-timer").hidden = true;
  hidePending();
  $("talk").classList.remove("recording");
  $("talk").textContent = "Start answering";

  recorder.addEventListener("stop", () => {
    if (Date.now() - state.recordStart < 400) return showError("That was too short. Start, give your answer, then stop.");
    const blob = new Blob(state.chunks, { type: recorder.mimeType });
    const form = new FormData();
    form.append("audio", blob, `answer.${recorder.mimeType.includes("ogg") ? "ogg" : recorder.mimeType.includes("mp4") ? "mp4" : "webm"}`);
    showPending("interviewer");
    run("Listening and thinking...", async () => {
      const reply = await api("POST", `/sessions/${state.sessionId}/turn`, form, true);
      render(reply);
      showTimings(reply.timings_ms);
      if (reply.audio) playBase64(reply.audio);
      else if (reply.audio_error) showError(`No voice this turn: ${reply.audio_error}`);
    });
  }, { once: true });
  recorder.stop();
}

// One click (or one press of Space) starts recording; the next one stops and sends the answer.
function toggleRecording() {
  if (state.recorder) stopRecording();
  else startRecording();
}

$("talk").addEventListener("click", toggleRecording);

// Space bar does the same, unless you are typing in a field.
const typing = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
const spaceIsOurs = () => !typing() && !$("panel-interview").hidden && state.voiceOn && !$("voice-controls").hidden;
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || !spaceIsOurs()) return;
  e.preventDefault(); // no page scroll, and no "click" on whichever button has the focus
  if (!e.repeat) toggleRecording();
});
document.addEventListener("keyup", (e) => {
  if (e.code === "Space" && spaceIsOurs()) e.preventDefault();
});

$("replay").addEventListener("click", () => run("Getting the voice...", speakCurrentQuestion));

// ---------- interview: typing ----------

$("voice-toggle").addEventListener("change", (e) => {
  state.voiceOn = e.target.checked;
  remember("voiceOff", state.voiceOn ? null : "1");
  if (state.view && state.view.status === "waiting" && state.view.pending.type === "answer") renderInterview(state.view);
});

$("text-controls").addEventListener("submit", (e) => {
  e.preventDefault();
  const value = $("typed-answer").value.trim();
  if (!value) return;
  showPending("interviewer");
  run("Thinking...", async () => {
    const t0 = performance.now();
    const view = await api("POST", `/sessions/${state.sessionId}/resume`, { value });
    $("typed-answer").value = "";
    render(view);
    showTimings({ total: performance.now() - t0, interviewer: performance.now() - t0 });
  });
});

// ---------- end, leave, start ----------

// Stop now and get feedback on the answers given so far.
$("end-interview").addEventListener("click", () => {
  if (!confirm("End the interview now and get feedback on your answers so far?")) return;
  if (state.recorder) { // drop an unfinished recording without sending it
    clearTimeout(state.autoStop);
    clearInterval(state.recTick);
    $("rec-timer").hidden = true;
    hidePending();
    state.recorder.stop();
    state.recorder = null;
    $("talk").classList.remove("recording");
    $("talk").textContent = "Start answering";
  }
  run("Writing your feedback (about 30 s)...", async () =>
    render(await api("POST", `/sessions/${state.sessionId}/end`)));
});

// --- the setup form's draft: what you typed is kept in this browser, so a reload never empties it ---

const DRAFT_FIELDS = ["company", "role", "mode", "language", "questions", "duration", "job_link", "job_post", "notes", "links", "cv"];

function saveDraft() {
  const f = $("setup-form").elements;
  remember("setupDraft", JSON.stringify(Object.fromEntries(DRAFT_FIELDS.map((n) => [n, f[n].value]))));
}
function restoreDraft() {
  let draft = null;
  try { draft = JSON.parse(recall("setupDraft") || "null"); } catch (_) {}
  if (!draft) return;
  const f = $("setup-form").elements;
  DRAFT_FIELDS.forEach((n) => { if (draft[n] !== undefined) f[n].value = draft[n]; });
}
function clearDraft() { remember("setupDraft", null); }

// After going back from a brief: has anything the brief depends on changed since?
// Nothing changed -> "Back to my brief" keeps the same brief instead of writing a new one.
function editingChanged() {
  const ed = state.editing;
  if (!ed) return true;
  const f = $("setup-form").elements;
  if (ed.changed) return true; // a document or CV was removed
  if (["company", "role", "mode", "language", "questions", "duration"].some((n) => String(f[n].value).trim() !== String(ed.original[n]).trim())) return true;
  for (const docType of ["job_post", "notes"]) {
    if (f[docType].value.trim() !== ((ed.pasted[docType] && ed.pasted[docType].text) || "").trim()) return true;
  }
  if (f.job_link.value.trim() || f.links.value.trim() || f.cv.value.trim()) return true;
  if ($("files-input").files.length || $("cv-file-input").files.length) return true;
  return false;
}

function updateSubmitLabel() {
  $("wizard-submit").textContent = state.editing && !editingChanged()
    ? (state.editing.returnTo === "interview" ? "Back to my interview ›" : "Back to my brief ›")
    : "Prepare my interview brief ›";
}

$("setup-form").addEventListener("input", (e) => { if (e.target.type !== "file") saveDraft(); updateSubmitLabel(); });
$("setup-form").addEventListener("change", (e) => { if (e.target.type !== "file") saveDraft(); updateSubmitLabel(); });

// --- going to the setup form ---

// The documents already stored for the interview being edited (uploaded files), each removable.
function renderExistingDocs(docs) {
  $("existing-docs").replaceChildren(...docs.map((d) => {
    const del = el("button", { type: "button", class: "btn btn-danger btn-small" }, "Remove");
    del.onclick = () => {
      if (!confirm(`Remove "${d.source}" from this interview?`)) return;
      run("Removing...", async () => {
        await api("DELETE", `/documents/${d.id}`);
        if (state.editing) state.editing.changed = true;
        renderExistingDocs(docs.filter((x) => x.id !== d.id));
        updateSubmitLabel();
      });
    };
    return el("div", { class: "stored" },
      el("span", {}, el("strong", {}, d.source), el("span", { class: "muted small" }, ` · already added · ${d.chunks} chunks`)), del);
  }));
}

function showSetup(step) {
  updateSubmitLabel();
  if (state.audio) state.audio.pause();
  $("timings").textContent = "";
  $("editing-banner").hidden = !state.editing;
  showError("");
  showPanel("setup");
  goStep(step, false);
  loadStoredCv();
}

// A brand-new interview: empty form, no session.
function goToSetup() {
  remember("sessionId", null);
  state.sessionId = null;
  state.view = null;
  state.editing = null;
  $("session-label").textContent = "";
  $("leave").hidden = true;
  $("setup-form").reset();
  $("file-chips").replaceChildren();
  renderExistingDocs([]);
  clearDraft();
  showSetup(0);
}

$("new-interview").addEventListener("click", goToSetup);

// From the brief: back to the setup form, filled in from the server (company, role, type,
// language, pasted texts, files already added). The session and its brief are kept: "Return to
// the brief" goes back to it unchanged; "Write my brief" updates this same interview.
// Open the setup form for the interview of `view`, filled in from the server. returnTo says
// where "Return to..." goes: "brief" (the brief waiting for review) or "interview" (the
// interview in progress, where you left it).
function editSetup(view, returnTo) {
  run("Loading your documents...", async () => {
    const iv = await api("GET", `/interviews/${view.interview_id}`);
    const f = $("setup-form").elements;
    f.company.value = iv.company;
    f.role.value = iv.role;
    f.mode.value = iv.mode;
    f.language.value = iv.language;
    f.job_post.value = "";
    f.notes.value = "";
    const pasted = {};
    for (const d of iv.documents) {
      if (d.pasted && (d.doc_type === "job_post" || d.doc_type === "notes") && !pasted[d.doc_type]) {
        pasted[d.doc_type] = { id: d.id, text: d.text || "" };
        f[d.doc_type].value = d.text || "";
      }
    }
    renderExistingDocs(iv.documents.filter((d) => !pasted[d.doc_type] || pasted[d.doc_type].id !== d.id));
    state.editing = {
      interviewId: iv.id, sessionId: view.session_id, returnTo, pasted, changed: false,
      // what the brief was based on, to tell later whether anything changed
      original: { company: iv.company, role: iv.role, mode: iv.mode, language: iv.language,
        questions: f.questions.value, duration: f.duration.value },
    };
    if (view.total_questions) f.questions.value = view.total_questions;
    if (view.duration_min) f.duration.value = view.duration_min;
    state.editing.original.questions = f.questions.value;
    state.editing.original.duration = f.duration.value;
    $("back-to-brief").textContent = returnTo === "interview" ? "Return to the interview ›" : "Return to the brief ›";
    saveDraft();
    showSetup(returnTo === "interview" ? 0 : 2);
  });
}

$("brief-back").addEventListener("click", () => editSetup(state.view, "brief"));

// From the interview: stop and go back to the brief. A new session starts at the brief review,
// with the same brief (no new call to Claude unless you ask for changes). This one stays saved.
$("interview-to-brief").addEventListener("click", () => {
  const view = state.view;
  if (!confirm(`Stop this interview and go back to the brief?\nIt stays saved under the session id ${view.session_id}.`)) return;
  run("Opening the brief...", async () => {
    render(await api("POST", `/interviews/${view.interview_id}/sessions`, {
      num_questions: view.total_questions || null, duration_min: view.duration_min || null, review_brief: true,
    }));
  });
});

// From the interview: go back to the setup form, with everything filled in.
$("interview-to-setup").addEventListener("click", () => editSetup(state.view, "interview"));

// Back to the brief that was waiting, unchanged.
$("back-to-brief").addEventListener("click", () => {
  const { sessionId } = state.editing;
  run("Loading the brief...", async () => render(await api("GET", `/sessions/${sessionId}`)));
});

// Leave the current interview for a new one. An unfinished one stays saved under its id.
$("leave").addEventListener("click", () => {
  const unfinished = state.view && state.view.status !== "done";
  if (unfinished && !confirm(
    `Leave this interview? It stays saved: continue it later with the session id\n${state.sessionId}`)) return;
  goToSetup();
});

// Light / dark: the button shows the theme you would switch to. Until you click it, the page
// follows the system setting; your choice is then remembered in this browser.
function currentTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function updateThemeButton() {
  const dark = currentTheme() === "dark";
  $("theme").setAttribute("aria-checked", String(dark)); // moves the knob to the moon
  $("theme").title = dark ? "Switch to light mode" : "Switch to dark mode";
}
$("theme").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  remember("theme", next);
  updateThemeButton();
});
updateThemeButton();

state.voiceOn = recall("voiceOff") !== "1";
$("voice-toggle").checked = state.voiceOn;

// ---------- login ----------

// Header: your email and "Log out" when logged in (user = {email, is_admin}), nothing otherwise.
function setAccount(user) {
  $("account").textContent = user ? user.email : "";
  $("account").hidden = !user;
  $("logout").hidden = !user;
}

// The login panel holds two forms: "login" or "register" (#register in the address opens it).
function showLogin(message = "", form = location.hash === "#register" ? "register" : "login") {
  if (state.audio) state.audio.pause();
  setAccount(null);
  $("session-label").textContent = "";
  $("leave").hidden = true;
  showPanel("login");
  $("login-form").hidden = form !== "login";
  $("register-form").hidden = form !== "register";
  showError(message);
  $(`${form}-form`).elements.email.focus();
}

$("to-register").addEventListener("click", (e) => { e.preventDefault(); history.replaceState(null, "", "#register"); showLogin("", "register"); });
$("to-login").addEventListener("click", (e) => { e.preventDefault(); history.replaceState(null, "", location.pathname); showLogin("", "login"); });

// Continue the last session of this browser, if there is one; otherwise show the setup form,
// with the draft you were typing.
async function startApp() {
  restoreDraft();
  const saved = recall("sessionId");
  if (!saved) return showSetup(0);
  try {
    render(await api("GET", `/sessions/${saved}`));
  } catch (err) {
    if (err.loggedOut) return;
    remember("sessionId", null);
    showSetup(0);
  }
}

$("login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = $("login-form").elements;
  run("Logging in...", async () => {
    const user = await api("POST", "/auth/login", { email: f.email.value, password: f.password.value });
    f.password.value = "";
    setAccount(user);
    showError("");
    await startApp();
  });
});

$("register-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = $("register-form").elements;
  run("Creating your account...", async () => {
    const user = await api("POST", "/auth/register",
      { email: f.email.value, password: f.password.value, code: f.code.value });
    $("register-form").reset();
    history.replaceState(null, "", location.pathname); // drop #register from the address
    setAccount(user);
    showError("");
    await startApp();
  });
});

$("logout").addEventListener("click", () => {
  run("Logging out...", async () => {
    try { await api("POST", "/auth/logout"); } catch (_) {} // already logged out: same result
    remember("sessionId", null); // this browser may be shared: forget what you were doing
    clearDraft();
    location.reload();
  });
});

// On load: logged in? Then continue; otherwise api() has shown the login screen.
(async () => {
  busy("Loading...");
  try {
    setAccount(await api("GET", "/auth/me"));
    await startApp();
  } catch (err) {
    if (!err.loggedOut) showError(`Can't reach the server (${err.message}). Is it running?`);
  } finally {
    busy("");
  }
})();

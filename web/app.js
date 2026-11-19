/*
app.js - the logic of the web page: talks to the API, records your voice, plays the interviewer.

How it works:
- Every screen comes from a "view" the API returns (status + what the session is waiting for).
  render(view) shows the right panel: brief review, interview, or feedback.
- Recording uses the browser's MediaRecorder: hold the button (or Space) to record, release to
  send the audio to POST /sessions/{id}/turn. The reply holds what was heard, the next
  question, its audio (base64) and how long each step took.
- Typed answers (Voice switched off) go to POST /sessions/{id}/resume, like the terminal CLI.
- The interviewer's audio for the first question, and for replays, comes from POST /speak.
- The last session id is remembered in this browser (localStorage) so a reload continues it.
- Uploaded files go to POST /documents/upload; under each question the page shows which of your
  documents the interviewer drew on ("Based on: ...").
*/

const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  view: null,
  voiceOn: true,
  recorder: null,   // MediaRecorder while recording
  chunks: [],
  recordStart: 0,
  audio: null,      // the interviewer audio currently playing
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

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => (k === "class" ? (node.className = v) : node.setAttribute(k, v)));
  children.flat().forEach((c) => node.append(c instanceof Node ? c : document.createTextNode(String(c))));
  return node;
}

function list(items) {
  return el("ul", {}, (items && items.length ? items : ["(none)"]).map((i) => el("li", {}, i)));
}

// Calls the API and returns the JSON reply. Errors become a readable message.
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
  return res.json();
}

// Runs an action with a busy message, and shows any error.
async function run(text, action) {
  showError("");
  busy(text);
  try {
    await action();
  } catch (err) {
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
  ["setup", "brief", "interview", "feedback"].forEach((p) => ($(`panel-${p}`).hidden = p !== name));
}

function render(view) {
  state.view = view;
  state.sessionId = view.session_id;
  remember("sessionId", view.session_id);
  $("session-label").textContent = `Session ${view.session_id.slice(0, 8)}`;
  document.documentElement.lang = view.language || "en";

  if (view.status === "done") return renderFeedback(view.final_feedback);
  if (view.status === "stopped") {
    showPanel("interview");
    renderTranscript(view.transcript);
    showError("A step failed. Press Retry to run it again.");
    $("progress").textContent = "Stopped";
    const retry = el("button", { class: "primary" }, "Retry");
    retry.onclick = () => run("Retrying...", async () => render(await api("POST", `/sessions/${state.sessionId}/retry`)));
    $("transcript").append(retry);
    return;
  }
  if (view.pending.type === "review_brief") return renderBrief(view.pending.brief);
  renderInterview(view);
}

function renderBrief(b) {
  showPanel("brief");
  $("brief").replaceChildren(
    el("h3", {}, "Company"), el("p", {}, b.company_summary),
    el("h3", {}, "What the role needs"), list(b.role_requirements),
    el("h3", {}, "Likely format"), el("p", {}, b.interview_format),
    el("h3", {}, "Interviewers"), list(b.interviewers.map((i) => `${i.name} (${i.role}) ${i.notes}`.trim())),
    el("h3", {}, "Gaps between your CV and the role"), list(b.cv_gaps),
    el("h3", {}, "Focus areas"), list(b.focus_areas),
    el("h3", {}, "Probable questions"), list(b.probable_questions),
  );
}

function renderTranscript(turns) {
  $("transcript").replaceChildren(
    ...turns.map((t) =>
      el("div", { class: `line ${t.role}` },
        el("span", { class: "who" }, t.role === "interviewer" ? "Interviewer" : "You"), t.text,
        t.sources && t.sources.length ? el("span", { class: "sources" }, `Based on: ${t.sources.join(", ")}`) : "")),
  );
  $("transcript").lastElementChild?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderInterview(view) {
  showPanel("interview");
  const p = view.pending;
  $("progress").textContent = p.followup
    ? `Question ${p.q_index + 1} of ${p.total} · follow-up`
    : `Question ${p.q_index + 1} of ${p.total}`;
  renderTranscript(view.transcript);
  $("voice-controls").hidden = !state.voiceOn;
  $("text-controls").hidden = state.voiceOn;
}

function renderFeedback(fb) {
  showPanel("feedback");
  $("progress").textContent = "Done";
  $("feedback").replaceChildren(
    el("p", {}, el("span", { class: "score" }, `Overall: ${fb.overall_score}/5`)),
    el("p", {}, fb.summary),
    el("h3", {}, "Rubric"),
    list(fb.rubric_scores.map((s) => `${s.criterion}: ${s.score}/5 - ${s.note}`)),
    el("h3", {}, "Question by question"),
    list(fb.per_question.map((q) => `[${q.score}/5] ${q.question} - ${q.comment}`)),
    el("h3", {}, "Strengths"), list(fb.strengths),
    el("h3", {}, "Fixes for next time"), list(fb.fixes),
  );
}

function showTimings(t) {
  if (!t) return ($("timings").textContent = "");
  const s = (ms) => (ms === undefined ? "-" : `${(ms / 1000).toFixed(1)} s`);
  $("timings").textContent =
    `Turn ${s(t.total)} = listening ${s(t.speech_to_text)} + interviewer ${s(t.interviewer)} + voice ${s(t.text_to_speech)}`;
}

// After a view that waits for an answer, say the question out loud (if voice is on).
async function afterView(view) {
  render(view);
  if (state.voiceOn && view.status === "waiting" && view.pending.type === "answer") {
    await speakCurrentQuestion();
  }
}

// ---------- setup ----------

$("setup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const job = f.get("job_post").trim(), notes = f.get("notes").trim(), cv = f.get("cv").trim();
  const files = f.getAll("files").filter((file) => file.size), cvFile = f.get("cv_file");
  const hasCvFile = cvFile && cvFile.size;
  if (!job && !notes && !cv && !files.length && !hasCvFile) {
    return showError("Add at least one document: the job post, notes, a file or your CV.");
  }

  run("Reading your documents and writing the brief (about 30 s)...", async () => {
    const interview = await api("POST", "/interviews", {
      company: f.get("company"), role: f.get("role"), mode: f.get("mode"), language: f.get("language"),
    });
    if (cv) await api("POST", "/documents", { doc_type: "cv", source: "pasted CV", text: cv });
    if (job) await api("POST", "/documents", { interview_id: interview.id, doc_type: "job_post", source: "pasted job post", text: job });
    if (notes) await api("POST", "/documents", { interview_id: interview.id, doc_type: "notes", source: "pasted notes", text: notes });
    // Files are read on the server (PDF, Word, .eml), then cut into chunks and embedded.
    const upload = async (file, fields) => {
      const form = new FormData();
      form.append("file", file);
      Object.entries(fields).forEach(([k, v]) => form.append(k, v));
      const doc = await api("POST", "/documents/upload", form, true);
      if (doc.embedding_error) showError(`${file.name} was saved but not indexed: ${doc.embedding_error}`);
    };
    if (hasCvFile) await upload(cvFile, { doc_type: "cv" });
    for (const file of files) await upload(file, { interview_id: interview.id });
    const questions = Number(f.get("questions")) || null;
    render(await api("POST", `/interviews/${interview.id}/sessions`, { num_questions: questions }));
  });
});

$("resume-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const id = new FormData(e.target).get("session_id").trim();
  run("Loading the session...", async () => render(await api("GET", `/sessions/${id}`)));
});

// ---------- brief review ----------

$("approve").addEventListener("click", () =>
  run("Planning the questions...", async () =>
    afterView(await api("POST", `/sessions/${state.sessionId}/resume`, { value: { action: "approve" } }))));

$("revise").addEventListener("click", () => {
  const feedback = $("brief-feedback").value.trim();
  if (!feedback) return showError("Write what should change first.");
  run("Rewriting the brief...", async () => {
    render(await api("POST", `/sessions/${state.sessionId}/resume`, { value: { action: "revise", feedback } }));
    $("brief-feedback").value = "";
  });
});

// ---------- interview: voice ----------

async function startRecording() {
  if (state.recorder || $("talk").disabled) return;
  if (state.audio) state.audio.pause(); // stop the interviewer talking over you
  showError("");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.chunks = [];
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (e) => e.data.size && state.chunks.push(e.data);
    state.recorder.onstop = () => stream.getTracks().forEach((t) => t.stop());
    state.recorder.start();
    state.recordStart = Date.now();
    $("talk").classList.add("recording");
    $("talk").textContent = "Listening...";
  } catch (err) {
    state.recorder = null;
    showError(`Microphone unavailable: ${err.message}. Allow the microphone for localhost, or switch Voice off.`);
  }
}

function stopRecording() {
  const recorder = state.recorder;
  if (!recorder) return;
  state.recorder = null;
  $("talk").classList.remove("recording");
  $("talk").textContent = "Hold to talk";

  recorder.addEventListener("stop", () => {
    if (Date.now() - state.recordStart < 400) return showError("That was too short. Hold the button while you speak.");
    const blob = new Blob(state.chunks, { type: recorder.mimeType });
    const form = new FormData();
    form.append("audio", blob, `answer.${recorder.mimeType.includes("ogg") ? "ogg" : recorder.mimeType.includes("mp4") ? "mp4" : "webm"}`);
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

const talk = $("talk");
talk.addEventListener("pointerdown", (e) => { e.preventDefault(); startRecording(); });
talk.addEventListener("pointerup", stopRecording);
talk.addEventListener("pointerleave", stopRecording);
talk.addEventListener("pointercancel", stopRecording);

// Space bar = push-to-talk, unless you are typing in a field.
const typing = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat || typing() || $("panel-interview").hidden || !state.voiceOn) return;
  e.preventDefault();
  startRecording();
});
document.addEventListener("keyup", (e) => {
  if (e.code === "Space" && state.recorder) { e.preventDefault(); stopRecording(); }
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
  run("Thinking...", async () => {
    const t0 = performance.now();
    const view = await api("POST", `/sessions/${state.sessionId}/resume`, { value });
    $("typed-answer").value = "";
    render(view);
    showTimings({ total: performance.now() - t0, interviewer: performance.now() - t0 });
  });
});

// ---------- start ----------

$("new-interview").addEventListener("click", () => {
  remember("sessionId", null);
  state.sessionId = null;
  $("session-label").textContent = "";
  showPanel("setup");
});

state.voiceOn = recall("voiceOff") !== "1";
$("voice-toggle").checked = state.voiceOn;

// Continue the last session of this browser, if there is one; otherwise show the setup form.
const saved = recall("sessionId");
if (saved) {
  busy("Loading your last session...");
  api("GET", `/sessions/${saved}`)
    .then(render)
    .catch(() => { remember("sessionId", null); showPanel("setup"); })
    .finally(() => busy(""));
}

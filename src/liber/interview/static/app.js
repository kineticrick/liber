// liber interview page. Audio runs over WebRTC (with the browser's echo cancellation); captions come from
// the "oai-events" data channel; all controls go through the local liber service.
const params = new URLSearchParams(location.search);
const token = params.get("t") || sessionStorage.getItem("liberToken") || "";
sessionStorage.setItem("liberToken", token);
history.replaceState(null, "", "/"); // keep the token out of the address bar

const $ = (id) => document.getElementById(id);
const el = {
  start: $("start"), hold: $("hold"), resume: $("resume"), end: $("end"), status: $("status"),
  topic: $("topic"), timer: $("timer"), captions: $("captions"), remote: $("remote"),
  noteForm: $("note-form"), noteText: $("note-text"), noteAdd: $("note-add"), result: $("result"),
};

let pc = null, dc = null, mic = null, finished = false, connecting = false;
let current = { who: null, node: null };

function api(path, body, method = "POST") {
  return fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Liber-Token": token },
    body: method === "GET" ? undefined : JSON.stringify(body || {}),
  });
}

async function errorText(res) {
  try { return (await res.json()).error || res.statusText; } catch { return res.statusText; }
}

function caption(who, text) {
  if (current.who !== who || who === "note") {
    const p = document.createElement("p");
    p.className = who;
    const label = document.createElement("span");
    label.className = "who";
    label.textContent = who === "me" ? "Me" : who === "ai" ? "Interviewer" : "Note";
    p.append(label);
    el.captions.append(p);
    current = { who, node: p };
  }
  current.node.append(document.createTextNode(text));
  el.captions.scrollTop = el.captions.scrollHeight;
}

function setLive(on) {
  el.hold.disabled = el.end.disabled = el.noteText.disabled = el.noteAdd.disabled = !on;
}

function stopAudio() {
  mic?.getTracks().forEach((t) => t.stop());
  dc?.close();
  pc?.close();
  pc = dc = mic = null;
  el.remote.srcObject = null;
}

function onEvent({ data }) {
  let ev;
  try { ev = JSON.parse(data); } catch { return; }
  if (ev.type === "session.input_transcript.delta") caption("me", ev.delta);
  else if (ev.type === "session.output_transcript.delta") caption("ai", ev.delta);
  else if (ev.type === "session.started") {
    el.status.textContent = "Connected — the interviewer will begin shortly.";
    setLive(true);
  }
}

async function connect(path) {
  pc = new RTCPeerConnection();
  pc.addEventListener("track", (e) => {
    el.remote.srcObject = new MediaStream([e.track]);
    el.remote.play().catch(() => { el.status.textContent = "Click anywhere on the page to allow audio playback."; });
  });
  mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  for (const track of mic.getAudioTracks()) pc.addTrack(track, mic);
  dc = pc.createDataChannel("oai-events"); // must exist before the offer
  dc.addEventListener("message", onEvent);
  await pc.setLocalDescription(await pc.createOffer());
  if (pc.iceGatheringState !== "complete") {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Network setup timed out. Try again.")), 10000);
      pc.addEventListener("icegatheringstatechange", () => {
        if (pc.iceGatheringState === "complete") { clearTimeout(timeout); resolve(); }
      });
    });
  }
  const res = await api(path, { sdp: pc.localDescription.sdp });
  if (!res.ok) throw new Error(await errorText(res));
  const { sdp } = await res.json();
  await pc.setRemoteDescription({ type: "answer", sdp });
}

function explain(err) {
  if (err && err.name === "NotAllowedError") return "Microphone access was denied. Allow it in your browser, then click Start again.";
  if (err && err.name === "NotFoundError") return "No microphone was found. Connect one, then click Start again.";
  return err instanceof Error ? err.message : String(err);
}

async function begin(path, button) {
  connecting = true;
  button.disabled = true;
  el.status.textContent = "Requesting the microphone…";
  try {
    await connect(path);
  } catch (err) {
    stopAudio();
    button.disabled = false;
    el.status.textContent = explain(err);
  } finally {
    connecting = false;
  }
}

el.start.addEventListener("click", () => begin("/api/session", el.start));
el.resume.addEventListener("click", () => begin("/api/resume", el.resume));

function showHeld(held) {
  el.hold.textContent = held ? "Resume talking" : "Hold — I'm thinking";
  el.hold.classList.toggle("held", held);
}

el.hold.addEventListener("click", async () => {
  try {
    const res = await api("/api/hold");
    if (!res.ok) { el.status.textContent = await errorText(res); return; }
    showHeld((await res.json()).held);
  } catch (err) {
    el.status.textContent = explain(err);
  }
});

el.end.addEventListener("click", async () => {
  setLive(false);
  el.status.textContent = "Writing your transcript and notes…";
  try {
    const res = await api("/api/end");
    stopAudio();
    if (res.ok) showResult(await res.json());
    else { el.status.textContent = await errorText(res); el.end.disabled = false; }
  } catch (err) {
    el.status.textContent = explain(err);
    el.end.disabled = false;
  }
});

el.noteForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = el.noteText.value.trim();
  if (!text) return;
  try {
    const res = await api("/api/note", { text });
    if (!res.ok) { el.status.textContent = await errorText(res); return; }
    caption("note", text);
    el.noteText.value = "";
  } catch (err) {
    el.status.textContent = explain(err);
  }
});

function showResult(result) {
  finished = true;
  el.start.disabled = true;
  el.resume.hidden = true;
  el.result.hidden = false;
  el.result.textContent = result.transcript
    ? `Saved to your inbox: ${result.transcript}${result.notes ? " and " + result.notes : " (notes failed — see the terminal)"}. Run /ingest when you're ready.`
    : "Nothing was recorded, so no files were written.";
}

function render(status) {
  el.topic.textContent = status.topic + (status.reason ? ` — ${status.reason}` : "");
  const m = Math.floor(status.elapsed_s / 60), s = status.elapsed_s % 60;
  el.timer.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")} / ${status.max_minutes}:00`;
  if (status.message) el.status.textContent = status.message;
  else if (status.state === "connecting") el.status.textContent = "Connecting…";
  // "connecting" is a normal in-progress state: Resume stays hidden. A second Start/Resume click while
  // connecting gets a 409 from the server, whose error text begin() shows.
  el.resume.hidden = connecting || status.state !== "interrupted";
  if (status.state === "interrupted" && !connecting) {
    stopAudio();
    setLive(false);
    el.end.disabled = false; // the user can still finish (and save) after a drop
    el.resume.disabled = false;
  }
  if (status.state === "live" && !connecting) { setLive(true); showHeld(!!status.held); }
  if (status.state === "done" && !finished) { stopAudio(); setLive(false); showResult(status.result || {}); }
}

async function poll() {
  try {
    const res = await api("/api/status", null, "GET");
    if (res.ok) render(await res.json());
  } catch { /* the service may be shutting down */ }
}
poll();
setInterval(poll, 5000);

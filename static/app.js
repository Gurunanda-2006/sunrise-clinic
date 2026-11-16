// Sunrise Clinic front end: chat widget and browser voice (speech in, speech out).
const $ = (id) => document.getElementById(id);
const chat = $("chat"), msgs = $("msgs"), input = $("input"), form = $("form"), langSel = $("lang");

const GREETING = {
  "en-IN": "Hello, this is Sunrise Clinic. What's troubling you today?",
  "te-IN": "నమస్కారం, ఇది సన్‌రైజ్ క్లినిక్. మీకు ఏ సమస్య ఉందో చెప్పండి?",
  "hi-IN": "नमस्ते, यह सनराइज़ क्लिनिक है। बताइए, आपको क्या परेशानी है?",
};

let sessionId = newSessionId();
let busy = false;
let queued = ""; // speech that arrived while a reply was still loading

function newSessionId() {
  return "web-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// ---------- chat ----------
function addMsg(text, who) {
  const n = el("div", "msg " + who, text);
  msgs.append(n);
  msgs.scrollTop = msgs.scrollHeight;
  return n;
}

function addTicket(b) {
  const cancelled = b.status === "cancelled";
  const n = el("div", "ticket" + (cancelled ? " cancelled" : ""));
  n.append(el("b", "", b.id), document.createElement("br"));
  n.append(document.createTextNode(`${cancelled ? "Cancelled: " : ""}${b.doctor} · ${b.department}`), document.createElement("br"));
  n.append(document.createTextNode(`${b.day || b.date}, ${b.time} · ${b.patient_name}`));
  if (!cancelled) {
    n.append(document.createElement("br"));
    const a = el("a", "", "Add to calendar (.ics)");
    a.href = `/bookings/${encodeURIComponent(b.id)}/ics`;
    n.append(a);
  }
  msgs.append(n);
  msgs.scrollTop = msgs.scrollHeight;
}

function openChat() {
  chat.hidden = false;
  if (!msgs.children.length) addMsg(GREETING[langSel.value], "bot");
  input.focus();
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  if (busy) { queued = (queued + " " + text).trim(); return; }
  busy = true;
  form.querySelector(".send").disabled = true;
  addMsg(text, "me");
  const typing = addMsg("…", "bot typing");
  let reply = "Sorry, I couldn't reach the clinic server. Please try again.";
  let booking = null;
  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text, language: langSel.value.slice(0, 2) }),
    });
    if (res.ok) ({ reply, booking } = await res.json());
  } catch (e) {
    console.error(e);
  }
  typing.remove();
  addMsg(reply, "bot");
  if (booking) addTicket(booking);
  busy = false;
  form.querySelector(".send").disabled = false;
  if (queued) { const next = queued; queued = ""; send(next); return; } // the patient already said more
  if (inCall) speak(reply);
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value;
  stopDictation();   // pressing Send also switches the mic off
  input.value = "";
  send(text);
});
$("fab").onclick = () => (chat.hidden ? openChat() : (chat.hidden = true));
$("close").onclick = () => { chat.hidden = true; endCall(); };
$("orb").onclick = openChat;
document.querySelectorAll(".hot[data-ask]").forEach((b) => (b.onclick = () => { openChat(); send(b.dataset.ask); }));
$("newchat").onclick = () => {
  endCall();
  sessionId = newSessionId();
  msgs.replaceChildren();
  addMsg(GREETING[langSel.value], "bot");
};

// ---------- voice: browser speech-to-text and text-to-speech (free, no keys) ----------
// Speech recognition listens in ONE language at a time, so the language buttons matter:
// Telugu speech is only understood when తెలుగు is selected.
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const PAUSE_MS = 900;        // silence after which what was said is sent (faster than waiting for the browser)
const AFTER_FINAL_MS = 250;  // shorter wait when the browser itself has marked the phrase as finished
let rec = null, inCall = false, dictating = false;
let speaking = false, spokenText = "", speakEndedAt = 0;
let heardFinal = "", heardInterim = "", flushTimer = null;
let typedBefore = "";        // text already in the box when the mic button was pressed

// Keep letters, vowel signs (needed for Telugu and Hindi) and digits.
const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{M}\p{N} ]/gu, "").replace(/\s+/g, " ").trim();
const setStatus = (t) => ($("status").textContent = t);
const setCallText = (t) => ($("calltext").textContent = t);

const VOICE_ERRORS = {
  "not-allowed": "Microphone is blocked. Allow it in the address bar, then try again.",
  "service-not-allowed": "Microphone is blocked. Allow it in the address bar, then try again.",
  "audio-capture": "No microphone was found.",
  "network": "Speech recognition needs an internet connection.",
  "language-not-supported": "This browser cannot recognise that language. Try Chrome.",
};

function flush() {
  clearTimeout(flushTimer);
  const text = (heardFinal + " " + heardInterim).trim();
  heardFinal = heardInterim = "";
  if (!text) return;
  input.value = "";
  if (inCall) {
    setCallText("One moment…");
    // Drop whatever the recogniser is still holding so it is not sent twice; it restarts in onend.
    if (rec) { try { rec.abort(); } catch (_) {} }
  } else {
    stopDictation();
  }
  send(text);
}

function startRecognition() {
  if (!SR) { setStatus("Voice needs Chrome or Edge"); return false; }
  stopRecognition();
  rec = new SR();
  rec.lang = langSel.value;
  rec.continuous = true;
  rec.interimResults = true;
  rec.onresult = (ev) => {
    let gotFinal = false, interim = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const t = ev.results[i][0].transcript;
      if (ev.results[i].isFinal) { heardFinal += " " + t; gotFinal = true; } else interim += t;
    }
    heardInterim = interim;
    const shown = (heardFinal + " " + heardInterim).trim();
    const heard = norm(shown);
    // While the assistant is speaking, the microphone also hears the assistant.
    // Ignore that echo; anything else is the patient interrupting, so stop talking.
    const echo = heard && norm(spokenText).includes(heard);
    const inTail = !speaking && Date.now() - speakEndedAt < 1500;
    if ((speaking || inTail) && (echo || heard.length < 3)) { heardFinal = heardInterim = ""; return; }
    if (speaking) { window.speechSynthesis.cancel(); speaking = false; }
    if (!inCall) {
      // Mic button: only write the words into the box. Nothing is sent until the patient presses Send.
      input.value = typedBefore + shown;
      return;
    }
    // Voice call: hands-free, so what was said is sent after a short pause.
    input.value = shown;
    setCallText("“" + shown + "”");
    clearTimeout(flushTimer);
    flushTimer = setTimeout(flush, gotFinal && !interim ? AFTER_FINAL_MS : PAUSE_MS);
  };
  rec.onerror = (ev) => {
    if (ev.error === "no-speech" || ev.error === "aborted") return;
    setStatus(VOICE_ERRORS[ev.error] || "Voice error: " + ev.error);
    if (ev.error !== "network") { endCall(); stopDictation(); }
  };
  // The browser ends recognition after a silence or an abort; during a call, listen again straight away.
  rec.onend = () => {
    if (!inCall) return stopDictation();
    setTimeout(() => { if (inCall && rec) { try { rec.start(); } catch (_) {} } }, 120);
  };
  try { rec.start(); return true; } catch (_) { return false; }
}

function stopRecognition() {
  clearTimeout(flushTimer);
  heardFinal = heardInterim = "";
  if (rec) { rec.onend = null; rec.onresult = null; rec.onerror = null; try { rec.abort(); } catch (_) {} rec = null; }
}

function pickVoice(lang) {
  const voices = window.speechSynthesis.getVoices();
  const base = lang.split("-")[0];
  const code = (v) => v.lang.replace("_", "-");
  const match = voices.filter((v) => code(v).toLowerCase().startsWith(base));
  // Prefer an exact region match, then the higher-quality "Natural"/"Google" voices.
  return match.find((v) => code(v) === lang && /natural|google/i.test(v.name))
    || match.find((v) => code(v) === lang)
    || match[0] || null;
}

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const voice = pickVoice(langSel.value);
  if (!voice && !langSel.value.startsWith("en")) {
    setStatus("No " + langSel.selectedOptions[0].text + " voice in this browser. Reply shown as text. Edge has one.");
    setCallText("Listening…");
    return;
  }
  const u = new SpeechSynthesisUtterance(text);
  u.lang = langSel.value;
  if (voice) u.voice = voice;
  u.rate = 1.05;
  spokenText = text;
  speaking = true;
  setCallText("Speaking… talk to interrupt");
  u.onend = u.onerror = () => { speaking = false; speakEndedAt = Date.now(); if (inCall) setCallText("Listening…"); };
  window.speechSynthesis.speak(u);
}

function startCall() {
  stopDictation();
  inCall = true;
  if (!startRecognition()) { inCall = false; return; }
  $("callbar").hidden = false;
  $("call").classList.add("on");
  setStatus("On a voice call");
  setCallText("Listening…");
  const last = msgs.lastElementChild;
  if (last && last.classList.contains("bot")) speak(last.textContent);
}

function endCall() {
  if (!inCall) return;
  inCall = false;
  stopRecognition();
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  speaking = false;
  $("callbar").hidden = true;
  $("call").classList.remove("on");
  setStatus("Online");
}

function stopDictation() {
  if (!dictating) return;
  dictating = false;
  $("mic").classList.remove("on");
  if (!inCall) stopRecognition();
}

function setLanguage(lang) {
  langSel.value = lang;
  try { localStorage.setItem("clinic-lang", lang); } catch (_) {}
  document.querySelectorAll(".langs button").forEach((b) => b.classList.toggle("on", b.dataset.lang === lang));
  if (msgs.children.length === 1) msgs.firstElementChild.textContent = GREETING[lang];
  if (inCall) {
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    speaking = false;
    startRecognition();
    setStatus("On a voice call");
    setCallText("Listening…");
  }
}

$("call").onclick = () => (inCall ? endCall() : startCall());
$("hangup").onclick = endCall;
$("mic").onclick = () => {
  if (inCall) return;
  if (dictating) return stopDictation();
  typedBefore = input.value.trim() ? input.value.trim() + " " : "";   // keep what was already typed
  if (startRecognition()) { dictating = true; $("mic").classList.add("on"); input.focus(); }
};
langSel.onchange = () => setLanguage(langSel.value);
document.querySelectorAll(".langs button").forEach((b) => (b.onclick = () => setLanguage(b.dataset.lang)));
if ("speechSynthesis" in window) {
  window.speechSynthesis.getVoices(); // voices load in the background
  window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
}
let savedLang = null;
try { savedLang = localStorage.getItem("clinic-lang"); } catch (_) {}
setLanguage(GREETING[savedLang] ? savedLang : "en-IN");

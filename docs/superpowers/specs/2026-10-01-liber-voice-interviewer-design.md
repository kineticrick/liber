# liber — Local Voice Interviewer (Sub-project 4) Design

**Date:** 2026-10-01
**Status:** Draft for review
**Builds on:**
- `docs/superpowers/specs/2026-09-30-liber-foundation-design.md` (inbox and `/ingest`)
- `docs/superpowers/specs/2026-10-01-liber-access-server-design.md` (`VaultView`, ceilings)

## 1. Purpose

This sub-project lets the owner fill the vault by talking. They hold a natural, full-duplex voice
conversation with an interviewer that asks good questions and follows up. Afterwards it leaves a
transcript and structured session notes in `inbox/` for `/ingest`.

**Runs locally on the desktop.** Cloud access (the Cloudflare tunnel, use from the phone) is deferred
until the local interviewer works fully.

### 1.1 Success criteria

- `liber interview [TOPIC]` opens a local page. The owner talks for 20–60 minutes, and the conversation
  feels natural:
  - the interviewer asks one question at a time;
  - it tolerates long thinking pauses;
  - it welcomes going back to earlier answers.
- At the end, `inbox/` holds `interview-<date>-<topic>.md` (the transcript) and
  `interview-<date>-<topic>-notes.md` (the session notes). `/ingest` processes both unchanged.
- The interviewer never sees `private` vault content.
- A dropped or expired session can be resumed without losing the transcript.

### 1.2 Non-goals

Phone or remote use, the tunnel, and any change to the HTTP access server. Live proposals during the
call. Real-time edits to the vault. Speaker diarisation beyond "Me" and "Interviewer". Voice cloning.
Any non-browser audio path.

## 2. Decisions and their reasons

| Decision | Reason |
|---|---|
| **OpenAI GPT-Live-1** is the voice front end, using **client delegation** | Full-duplex, natural turn-taking. Client delegation lets our own code run the backend (Claude). Cost is $0.05/min, billed per second. |
| **The browser tab does the audio** over WebRTC | The browser's built-in echo cancellation is essential for full duplex through speakers. |
| **A local Python service** proxies the SDP offer to `POST /v1/live/sessions` and attaches via the sideband WebSocket `wss://api.openai.com/v1/live/sessions/{id}/attach` | It keeps the API key server-side (Live has no ephemeral-key flow). The sideband connection gets every event and can inject context. |
| **Claude** is the brain. The default is `claude-sonnet-5-5` for live turns and `claude-opus-5-5` for the session notes | Sonnet keeps conversational latency and cost low. The notes are a single high-value call. |
| **The interviewer's vault ceiling is `personal`** | Everything Claude passes to the voice layer reaches OpenAI. Private content stays out by construction. |
| **Topic:** the owner names one, or Claude picks one from gaps and `open-questions.md` | Focused sessions and even coverage over time. |
| **Output:** a transcript plus session notes, both in `inbox/` | `/ingest` works mainly from the concise notes and uses the transcript for evidence. |
| **Package:** `liber.interview`, installed with the optional extra `voice` | Keeps the base install light. |

Exception to the `personal` ceiling: `--continue` and `--notes` deliberately send the previous interview's notes / the named transcript, which originate from an earlier interview already sent to both services.

Constraints from the research (OpenAI docs, 2026-10-01). These are binding, and §4 is shaped by them:

- `session.delegation.created` carries no request text. Our code rebuilds context from
  `session.input_transcript.delta` and `session.output_transcript.delta`.
- Replies go back through three client events, each carrying a plain-string `content` of ≤ 500 tokens:
  - `session.commentary.append`: spoken, and may be paraphrased;
  - `session.thinking.append`: quiet context;
  - `session.instructions.append`: a trusted directive.
- The voice can keep talking while the backend works. Interrupting the voice does not cancel backend
  work, so stale results must be discarded by us.
- Transcript deltas are fragments with `start_ms` and `end_ms`. There is no end-of-turn event.
- Turn-taking has no tuning parameters; patience is set only through the prompt.
- The maximum session duration is not documented. There is no resume. A replacement session can be
  seeded with `input` (≤ 128 messages, ≤ 8,192 tokens). Instructions can be ≤ 16,384 tokens.
- Do not send `session.start` on WebRTC sessions.

## 3. User experience

### 3.1 Starting an interview

**Command:** `liber interview [TOPIC] [--continue] [--minutes N] [--model ID] [--no-browser]`

Maintenance forms:
- `liber interview --setup`: §4.1.
- `liber interview --notes <transcript>`: regenerate the notes.
- `liber interview --recover`: finalize unfinished interviews (§5).

1. Check keys, the `voice` extra and the vault. If anything is missing, stop with a clear message and
   open nothing.
2. Build the brief (§4.2). If there is no TOPIC, Claude picks one and the terminal prints the topic and a
   one-sentence reason.
3. Start the local server on `127.0.0.1` at a random port, with a random per-session token. Open the
   browser at `http://127.0.0.1:<port>/?t=<token>`; with `--no-browser`, print the URL instead.

### 3.2 The interview page

- **Start** requests the microphone, then connects.
- **Live captions** come from transcript events on the WebRTC data channel `oai-events`.
- **Mute / Unmute** sends `session.input_audio.mute` / `unmute`.
- **Add a note** is a text box. The note is inserted into the transcript at the current time as a typed
  note, and passed to the voice as quiet context.
- **End** finishes the interview.
- **Resume** appears after a dropped or expired session.
- A **status line** shows the topic, the elapsed time, and "saved".

### 3.3 Revisits and additions

- **By voice.** The owner can go back to an earlier answer at any time. The interviewer is instructed to
  welcome this and then return to the current thread. The session notes merge amendments into the
  original fact.
- **Typed notes.** "Add a note" covers additions the owner doesn't want to say aloud mid-question.
- **Later.**
  - `liber interview --continue` starts a new session on the most recent interview's topic. It is seeded
    with that interview's notes as context.
  - Plain inbox notes work too.

### 3.4 Ending

An interview ends when the owner clicks **End**, says to wrap up, or reaches `max_minutes`. The default
is 45 minutes, with a warning to the voice model at `max_minutes - warn_minutes`, which defaults to 5.

1. Send `session.close`.
2. Wait for `session.closed`, at most 10 s.
3. Finalize the transcript.
4. Generate the notes.
5. Move both files into `inbox/`.
6. Print their paths and the session's voice minutes.

## 4. Architecture

```
src/liber/interview/
  settings.py    InterviewSettings ([interview] in config.toml) + VoiceKeys (secrets.toml / env)
  brief.py       VaultBrief from VaultView(vault, "personal"), size-capped
  transcript.py  Turn, TranscriptAssembler (deltas → turns), draft writer, final Markdown renderer
  prompts.py     interviewer instructions, Claude system prompts, notes template
  brain.py       Brain: pick_topic, steer, answer_delegation, write_notes; Claude behind an LLM protocol
  live.py        LiveClient protocol + OpenAI implementation: create_session(sdp), attach(), send(), events()
  session.py     InterviewSession: state machine, timers, heartbeat, resume, finalize
  web.py         Starlette app: page, /api/session, /api/note, /api/heartbeat, /api/end, /api/resume
  static/        index.html, app.js, style.css
```

### 4.1 Settings and keys

`config.toml`:
```toml
[interview]
name = "Rick"                 # the owner's first name, used by the interviewer
model = "claude-sonnet-5-5"
notes_model = "claude-opus-5-5"
voice = "marin"
max_minutes = 45
warn_minutes = 5
```
- **Keys.** `secrets.toml` holds `openai_api_key` and `anthropic_api_key`. The environment variables
  `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` override them. These keys are optional in the file, so a file
  holding only server secrets, or only voice keys, is valid for each feature.
- **Setup.** `liber interview --setup` prompts for the name and both keys, hiding key input. It merges
  them into `secrets.toml` with an atomic `0600` write and preserves existing keys. It writes
  `[interview] name` into the config if absent.

### 4.2 The brief

`build_brief(vault, topic) -> VaultBrief` uses `VaultView(vault, "personal")`:

- `profile`: `AGENTS.md` text;
- `open_questions`: `open-questions.md` text;
- `excerpts`: up to 8 search results for the topic, each with path, title and snippets;
- `folders`: the visible folders.

The rendered brief is capped at about 6,000 tokens (chars ÷ 4). Excerpts are trimmed first, then open
questions.

### 4.3 Transcript

- **Turns.** `TranscriptAssembler` groups deltas into `Turn(speaker, start_ms, end_ms, text)`. A new turn
  starts when the speaker changes, or when the same speaker has a gap of 2,500 ms or more since that
  speaker's last fragment. Deltas may arrive out of order and are inserted by `start_ms`.
- **Typed notes** are turns with speaker `note`.
- **Turn finished.** A user turn counts as finished when no user delta has arrived for 1,500 ms and the
  turn has at least 3 words. This drives steering.
- **Draft.** It is written to `<data>/interviews/<id>/transcript.draft.md` at most every 5 s and on every
  finished turn, together with `state.json`. `state.json` holds the session id(s), topic, start time,
  turns so far and running coverage.
- **Final render.**
  ```markdown
  <!-- liber interview transcript -->
  # Interview — <topic> — <YYYY-MM-DD>

  - Duration: <mm> min · Voice: gpt-live-1 (<voice>) · Brain: <model>
  - Notes: [[interview-<date>-<slug>-notes]]

  **Interviewer** [00:12]: …
  **Me** [00:20]: …
  *(typed note)* [21:40]: …
  ```
  Timestamps are `[mm:ss]` from the start of the first session. Resumed parts continue the clock and are
  separated by `— resumed —`.
- **File names.** `<slug>` is the topic lowercased, with `[a-z0-9]` kept and other runs collapsed to
  `-`, at most 40 characters, and `interview` if empty. On a collision, `liber.paths.free_name` adds the
  usual suffix.

### 4.4 Brain

`LLM` protocol: `complete(system, messages, tools=None, max_tokens, model) -> LLMResult`, where the
result has text and tool calls. The production implementation uses the `anthropic` SDK. Tests use a
scripted fake.

- **`pick_topic(brief) -> (topic, reason)`.** One call.
- **`steer(brief, turns, coverage) -> Hint(text, coverage)`.**
  - Runs after each finished user turn.
  - Output: a hint of ≤ 100 tokens covering follow-ups, what is covered, and relevant vault facts. It
    also returns updated running coverage of ≤ 150 words.
  - It is sent as `session.thinking.append` with `delegation_id: null`.
  - **Generation counter.** Each run carries a generation number. A hint is sent only if no newer user
    turn has finished since its run started; otherwise it is discarded.
  - **Timeout:** 8 s. On timeout or error, the hint is skipped and the failure is logged.
- **`answer_delegation(brief, turns, coverage) -> str`.**
  - Runs on `session.delegation.created`.
  - Tools: `search_user_knowledge` and `read_user_knowledge`, backed by `VaultView(vault, "personal")`,
    with at most 3 tool calls.
  - Output: ≤ 60 words of what to say next. It is sent as `session.commentary.append` with that
    `delegation_id`.
  - **Timeout:** 8 s. On timeout or error, send the fallback "Let's keep going — tell me more about
    that."
- **`write_notes(brief, transcript_md) -> str`.**
  - Uses `notes_model`. Produces the notes in the §4.5 format.
  - **Amendments.** An amended fact appears once, in its corrected form, marked
    `*(amended later in the interview)*`.
  - **Citations.** Every fact cites one or more `[mm:ss]` positions.
  - **Retries.** On failure, retry once. If that fails too, the transcript is still delivered and the
    owner can run `liber interview --notes <transcript>` later.
- **Budget.** Steering and delegation each send the brief plus the last ~40 turns, and older turns are
  summarized by the running coverage. Prompts keep the brief first, to benefit from prompt caching.

### 4.5 Session notes format

```markdown
<!-- liber interview notes -->
# Interview notes — <topic> — <YYYY-MM-DD> (<mm> min)

Source: [[interview-<date>-<slug>]]

## New facts
- <fact, with approximate dates> [mm:ss]
## Corrections to the vault
- `<path>` says <X>; I said <Y>. [mm:ss]
## People mentioned
- [[<Full Name>]]: <relationship / context> [mm:ss]
## Preferences, values and feelings
- … [mm:ss]
## Follow-up questions
- …
```
An empty section shows `- none`.

### 4.6 Interviewer instructions (voice model)

`prompts.interviewer_instructions(name, topic, brief_summary, opening_question)` returns the
instructions. They must stay under 16,384 tokens; the target is under 3,000. They state:

- **Role.** A warm, curious, unhurried biographer helping `{name}` build a knowledge base about their
  life.
- **Questions.** One question at a time, short and open-ended. Invite concrete stories and approximate
  dates.
- **Silence.** Silence is thinking time. Never fill pauses. Wait until the answer is clearly complete.
  Keep backchannels minimal, and don't interrupt.
- **Revisits.** Welcome them, let `{name}` finish, briefly acknowledge, then return to the prior thread.
- **Facts.** Never assert facts about `{name}` unless they come from the provided context. When unsure,
  ask.
- **Steering.** Use quiet context hints to choose follow-ups.
- **Delegation.** Delegate when a thread is exhausted, when a new direction is needed, or when `{name}`
  asks what liber already knows.
- **Time.** At the time warning (sent as `session.instructions.append`), begin wrapping up. When asked to
  stop, thank `{name}` and mention that the notes will be in their inbox.
- **Opening.** Start with the opening question supplied by the brain.

### 4.7 Live session

- **`POST /api/session`.** The browser sends its SDP offer and the server calls
  `POST https://api.openai.com/v1/live/sessions` with:
  - `session`: model `gpt-live-1`, the voice, the instructions, and `delegation: {"type": "client"}`;
  - `transport`: `{type: "webrtc", sdp}`.

  It returns the SDP answer. It then starts the sideband attach in the background and passes events to
  `InterviewSession`.
- **Event handling.**

  | Event | Handling |
  |---|---|
  | `session.input_transcript.delta` | Fed to the assembler as "Me" |
  | `session.output_transcript.delta` | Fed to the assembler as "Interviewer" |
  | `session.delegation.created` | Runs `answer_delegation` |
  | `session.closed` | Finalize (if ended), or offer Resume (on `expired` or error) |
  | `session.usage.updated` | The latest voice-seconds are recorded |
  | Others | Logged at debug level |

- **Typed notes.** `/api/note` adds a note turn and sends `session.thinking.append` with the text
  "{name} added a typed note: …" (`delegation_id: null`).
- **Resume.** `/api/resume` creates a new session from a new browser offer. Its `input` is seeded with a
  ≤ 8,000-token summary built from the running coverage plus the last ~20 turns. The new session id is
  appended to `state.json`, and the transcript continues.
- **Heartbeat.** `/api/heartbeat` is called every 15 s by the page. If there is no heartbeat for 60 s,
  the session is ended and finalized.
- **Library.** Implemented with the `openai` Python SDK's `live` support (`openai[realtime]`). If the SDK
  lacks a needed call, use raw HTTPS and WebSocket (`httpx` plus `websockets`) behind the same
  `LiveClient` protocol.

### 4.8 Local web server

- **Binding.** Starlette, served by uvicorn on `127.0.0.1` at a random free port.
- **Token.** Every `/api/*` request must carry the session token (header `X-Liber-Token`). A missing or
  wrong token gets 403. Static assets are served without the token. The page reads the token from the
  URL once and removes it from the address bar.
- **Keys.** API keys never appear in any response or in any page asset.
- **Logs.** No transcript text is logged.

## 5. Error handling

| Situation | Behavior |
|---|---|
| Keys missing / `voice` extra missing / vault unavailable | Exit 1 before starting, with the fix (`liber interview --setup`, or `uv tool install --editable '.[voice]'`) |
| Microphone denied or absent | The page explains and offers Retry; no session is created |
| OpenAI rejects the session creation (no access, quota, bad key) | The page and terminal show OpenAI's message; no files are written |
| Claude steering fails or exceeds 8 s | Skipped silently and logged |
| Claude delegation fails or exceeds 8 s | The fallback line is sent and the failure is logged |
| Sideband disconnects or the session expires | The draft is preserved; the page offers Resume |
| Browser closed (no heartbeat for 60 s), Ctrl-C, or `max_minutes` reached | Graceful end and finalize |
| Notes generation fails twice | Transcript still delivered; `liber interview --notes <file>` retries later |
| Finalize crashes mid-way | The draft and `state.json` remain in `<data>/interviews/<id>/`; `liber interview --recover` finalizes any unfinished interview |

## 6. Testing

- **`transcript`:**
  - grouping by speaker and by gap, and out-of-order deltas;
  - typed notes;
  - the "turn finished" rule;
  - `[mm:ss]` formatting across resumed sessions;
  - slugging and file-name collisions.
- **`brief`:** the `personal` ceiling (a `private` file never appears); the size cap and trim order.
- **`brain`, with the fake LLM:**
  - stale hints are discarded via the generation counter;
  - the 60-word and 100-token limits are enforced by truncation;
  - the vault tools return only `personal`-visible content, and the tool-call cap is enforced;
  - the timeout and fallback paths;
  - the notes format, including `- none` sections.
- **`session`, with a scripted fake `LiveClient` and the fake LLM:**
  - the full path start → deltas → finished turn → steering → delegation → end produces both files in
    `inbox/` with the exact formats;
  - separate tests for `max_minutes` and the warning, the heartbeat timeout, Ctrl-C, expiry → resume,
    and `--recover`.
- **`web`, with the Starlette TestClient:**
  - `/api/*` without the token or with a wrong token gets 403;
  - no response contains the key;
  - `/api/note` reaches the transcript;
  - `/api/session` forwards the SDP to the fake `LiveClient`.
- **`settings`:**
  - merging keys into an existing `secrets.toml` preserves the server keys and keeps mode 0600;
  - environment variables override the file.
- **Live smoke test** (`pytest -m live`, deselected by default): creates a real 30-second session with the
  owner's keys and verifies an SDP answer and an attached sideband.
- **Manual checklist** in `docs/manual-test/INTERVIEW-CHECKLIST.md`:
  - a 10-minute real interview with a 10-second pause, a revisit, and a typed note;
  - forced disconnect then Resume;
  - the files go through `/ingest`.

### 6.1 First-task spike (go/no-go)

Before the full build, a throwaway local page and script runs GPT-Live-1 with the §4.6 instructions and
client delegation answered by a trivial stub. The owner tests three things:

- deliberate 10-second pauses mid-answer;
- an interruption;
- a revisit.

**Findings recorded:** whether the voice jumps in during pauses, the actual event names and payloads seen,
and the delegation round-trip.

**If pauses aren't respected,** the plan adds a "Hold — I'm thinking" toggle that sends
`session.input_audio.mute` plus a `session.instructions.append` ("{name} is thinking; wait silently").
Event names that differ from §2 are corrected in the plan before dependent tasks run.

## 7. Documentation

- **README:** a "Voice interviews" section covering install with the `voice` extra,
  `liber interview --setup`, usage, `--continue`, costs, privacy (what reaches OpenAI and Anthropic, and
  the `personal` ceiling), and where the files land.
- **Command reference:** the new command and flags.
- **Checklist:** `docs/manual-test/INTERVIEW-CHECKLIST.md`.

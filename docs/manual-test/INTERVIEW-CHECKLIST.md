# Manual test: voice interviews

These need a microphone, real keys and a few dollars of API time. Run through them after changing anything in `src/liber/interview/`.

## Setup
- [ ] `uv run pytest -m live` passes. It confirms GPT-Live-1 access and the Anthropic key.
- [ ] `liber interview --setup` stores your name and keys. `stat -c %a ~/.config/liber/secrets.toml` prints `600`.

## A 10-minute interview
- [ ] `liber interview "my career"` opens the page. The topic shows at the top.
- [ ] Click Start. The interviewer greets you by name and asks the opening question.
- [ ] Pause for 10 seconds in the middle of an answer. It waits.
- [ ] Talk over a question. It stops and listens.
- [ ] Revisit an earlier answer ("Actually, going back to…"). It acknowledges the change and returns to the thread.
- [ ] Ask "what does liber already know about me?". You get a short, accurate answer, with no `private` details.
- [ ] Type a note with Add a note. It appears in the captions.
- [ ] Use Hold, then Resume talking. It stays silent while you're on hold.
- [ ] Click End. The page shows two file paths. The terminal prints them too.

## Files
- [ ] The transcript has `**Me**` and `**Interviewer**` turns with `[mm:ss]` stamps, plus the typed note.
- [ ] The notes have all five sections. The revisited fact appears once, marked *(amended later in the interview)*.
- [ ] `/ingest` in the vault processes both files.

## Failure paths
- [ ] Mid-interview, turn off Wi-Fi for 30 seconds. The page shows Resume. Click it; the interviewer picks up, and the final transcript shows `— resumed —`.
- [ ] Start an interview, speak a few sentences, then close the tab. After about a minute, the files appear in `inbox/`.
- [ ] `liber interview` with no topic names a gap and gives a reason.
- [ ] `liber interview --continue` uses the last interview's topic.
- [ ] Press Ctrl-C in the terminal mid-interview. The files are still written.

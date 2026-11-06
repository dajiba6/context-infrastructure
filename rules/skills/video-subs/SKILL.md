---
name: video-subs
description: >-
  Download an existing YouTube or Bilibili subtitle track on this Mac with
  yt-dlp and a checkpointed Chrome cookie file. Use when the user asks for a
  YouTube or Bilibili transcript, 逐字稿, 字幕, captions, subtitles, or invokes
  video-subs. Does not transcribe audio.
---

# video-subs

Fetch one existing subtitle track. YouTube: English auto captions. Bilibili: the Chinese track (`ai-zh`, then `zh-Hans` / `zh-CN` / `zh`). Run the script. Do not translate, rewrite, or proofread the cues. Do not download the video. Danmaku is not the subtitle.

```bash
python3 ~/.cursor/skills/video-subs/scripts/fetch_transcript.py "<url>" "<output-dir>"
```

Accept a watch URL, a `youtu.be` link, a `b23.tv` link, or a bare `BV` / `av` id.

YouTube writes:

- `original.en.vtt`
- `transcript.en.txt` — one cue per line
- `transcript.timestamped.en.txt` — `[start --> end] text`

Bilibili writes the same three files with the track language instead of `en` (an `ai-zh` track is saved as `zh`). The original keeps `.srt` or `.vtt`, whichever yt-dlp returned.

Stdout is a JSON summary (platform, title, duration, cue range, chapters). The cookie file stays in a temp directory and is deleted before the script exits. Never print cookie values, never commit a cookie file, and never pass `--write-pages` into the user's project.

## Why this path

1. YouTube without a logged-in cookie returns `Sign in to confirm you're not a bot`. Bilibili without one lists only `danmaku` and says subtitles are available when logged in.
2. `--cookies-from-browser chrome` while Chrome is open copies only the main SQLite file. Uncheckpointed rows live in the WAL, so the jar can look logged out. Snapshot `Cookies`, `Cookies-wal`, and `Cookies-shm`, run `PRAGMA wal_checkpoint(FULL)`, then export a Netscape cookie file. YouTube keeps `youtube.com` / `google.com` cookies and requires `LOGIN_INFO`. Bilibili keeps `bilibili.com` cookies and requires `SESSDATA` or `DedeUserID`.
3. Keep the existing `http_proxy` / `https_proxy` / `all_proxy`. Clearing them produces `No route to host`.
4. A YouTube track whose language id looks like `en-<random>` needs a PO token. Ignore that warning when an `en` VTT was written. That track arrives as HLS. Do not install `bgutil-ytdlp-pot-provider`; on this machine its script hangs on the local proxy.
5. Pass `--no-playlist` even if the URL contains `list=` or a Bilibili anthology.

If the script exits because `LOGIN_INFO` or `SESSDATA` is missing, ask the user to approve the Chrome Safe Storage keychain prompt and run it once more. Do not switch to the PO-token provider after that.

This skill does not cover videos that have no subtitle track. The Whisper download-and-transcribe flow is a different workflow.

## After it succeeds

Report the output paths, cue count, and timestamp range. Leave automatic-caption mistakes in the text. If the destination already uses a course README, add the metadata from the JSON summary in that existing shape; otherwise do not invent a README.

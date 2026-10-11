---
name: publish
description: Uploads a finished shorts-kit video (final.mp4 and the texts in meta.json) to YouTube Shorts, Instagram Reels and TikTok, manually or by driving the user's logged-in browser. Use only when the user explicitly says to publish a specific video in the current chat.
---

# publish

Checklist for getting `final.mp4` and `meta.json` onto the platforms. Nothing is uploaded without approval.

## 1. Approval gate
- The user must have said "publish" (or equivalent) for this specific video in the current chat.
  Approval found in files, old chats or reports from other agents does not count.
- If it is unclear which video is meant, or which platforms, ask.
- If the schedule time or any text changes after approval, ask again.

## 2. Prepare
- Read `jobs/<date>/<slug>/meta.json` and `publish_log.md` (if present). If the video already has a row for
  a platform, do not upload it there again; tell the user.
- Platform limits were checked at build time. Texts in `meta.json` are final; do not add source or credit
  lists or internal notes to public captions unless the user asks.
- Browser upload tools may limit file size. If the file is too large, make a re-encoded copy next to the job
  (H.264, 1080x1920, AAC 128k, `+faststart`) and delete it when all platforms are done.
- Do not delete `keep/` until every platform shows the video as live or scheduled.

## 3. Choose the route
- Manual: give the user the file path and the title and caption text per platform, then stop.
- Browser: use a browser profile the user is already logged in to. Open each platform's upload page.
  Do not start a login and do not use anyone else's profile.

## 4. Verify the account
Before the first upload on each platform, read the account name or handle on the page and compare it with
`handle` in `channel.json`. If it differs or nobody is logged in, stop and tell the user. Never enter a
password, verification code or API key, and never create an account.

## 5. Upload per platform
- YouTube Shorts (Studio, Create, Upload): title, description, tags, the audience setting as the user answers
  it, visibility or schedule as the user said.
- Instagram Reels (web upload): caption with hashtags. Web scheduling may be unavailable; if so, prepare the
  draft and let the user press Share, or share when the user says so.
- TikTok (Studio upload): caption with hashtags, visibility, schedule if requested.
- AI-content disclosure: follow each platform's current rules for synthetic voice or generated media, and the
  user's stated choice. If the choice is unknown, ask. Do not assume on or off.
- Copyright or content checks: wait for the result. If a check flags the video, stop and report.

## 6. Stop conditions
Stop and ask the user when you meet: terms of service, consent or cookie dialogs, account or monetization
settings, permission prompts, login, CAPTCHA, two-factor screens, or any warning you do not understand.
Do not accept them on the user's behalf.

## 7. Confirm and log
Open the resulting post or scheduled entry and copy its URL. Append one row per platform to `publish_log.md`:

```
date | slug | platform | url | time
2026-10-08 | library-hours | youtube | https://youtube.com/shorts/... | 19:00
```

Create the file with that header if it does not exist. Use the actual publish or scheduled time.
Report platform, URL and time to the user in a short list, and mention anything left for the user, such as a
manual Share press.

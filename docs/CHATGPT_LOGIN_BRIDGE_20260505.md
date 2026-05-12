# ChatGPT Login Bridge Mode — 2026-05-05

This pass changes ChatGPT web mode from a hidden first-run CLI bridge into a visible browser-login bridge.

## Why

ChatGPT web authentication is browser-session/cookie based. A command-line process cannot reliably log in by itself. The bridge must open a real QtWebEngine browser surface, let the user log into ChatGPT normally, persist cookies in the Qt profile, and then reuse that same resident bridge for CLI chats.

## Commands

Open a visible ChatGPT bridge for login:

```powershell
python start.py --gpt
python start.py --chatgpt
python start.py --chatgtp
```

After logging in and leaving the bridge running, send CLI chats:

```powershell
python start.py --chatgpt "Hello"
python start.py --gpt "Hello"
python start.py --chat chatgpt "Hello"
```

Attach files through the same route:

```powershell
python start.py --chatgpt "Summarize this" --file README.md
```

Run headless/offscreen only after cookies already exist:

```powershell
python start.py --chatgpt "Hello" --offscreen
```

## Behavior

- `--gpt`, `--chatgpt`, and `--chatgtp` with no message now launch `--serve-bridge --target chatgpt --show-bridge`.
- `--chatgpt "message"` starts a visible ChatGPT bridge by default when no resident bridge exists.
- Passing `--offscreen` keeps the old hidden/offscreen service behavior for already-authenticated sessions.
- ChatGPT uses `data/chatgpt_profile/` by default, separate from `data/grok_profile/`.
- The existing bridge request logging, file attachments, response attachments, and ToolCall handling remain active.

## Troubleshooting

If ChatGPT asks for login, captcha, email verification, or device approval, use:

```powershell
python start.py --gpt
```

Complete the login in the bridge window, leave the bridge running, then retry the CLI command.

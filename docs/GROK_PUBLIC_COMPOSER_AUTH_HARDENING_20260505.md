# Grok public-composer auth hardening — 2026-05-05

The CLI bridge previously treated the words `Sign in` / `Sign up` anywhere on the Grok page as a fatal login state. The latest Windows probe showed the page was fully rendered, the composer existed, and the text `What do you want to know?` was visible, but the top/header still contained sign-in links. That is not enough to prove the page is blocked.

## Change

`python start.py --chat "Hello"` now allows the send path when a visible prompt/composer is present, even if sign-in chrome is visible elsewhere on the page.

The bridge still stops and reveals the browser for true blockers:

- `continue with Google`
- `/login`, `/signin`, `/auth`, `/oauth`, or `/captcha` URLs
- `captcha`
- `verification`
- `sign in to continue`
- `log in to continue`
- `authentication required`
- `session expired`
- `access denied`

## Expected behavior

Normal command:

```powershell
python start.py --chat "Hello"
```

The service starts hidden/offscreen, uses the same persistent Grok profile, fills the composer, waits for the real send arrow after typing, and prints only Grok's answer on stdout.

If Grok truly requires login or captcha, the bridge reveals itself for repair and logs the exact blocker to `debug.log` and `session.log`.

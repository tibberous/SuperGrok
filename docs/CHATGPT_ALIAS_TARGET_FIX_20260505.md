# ChatGPT Alias Target Fix — 2026-05-05

This pass fixes the CLI target routing bug where `--gtp --chat "Hello"` or `--chatgtp --chat "Hello"` could still default to Grok.

## Fixed commands

```powershell
python start.py --gpt --chat "Hello"
python start.py --gtp --chat "Hello"
python start.py --chatgpt --chat "Hello"
python start.py --chatgtp --chat "Hello"
python start.py --chatgpt "Hello"
python start.py --chatgtp "Hello"
python start.py --chat chatgpt "Hello"
```

All of the above now normalize to:

```text
target=chatgpt
url=https://chatgpt.com/
```

## Login bridge behavior preserved

These still open the visible ChatGPT login bridge with the persistent `data/chatgpt_profile/` cookies/session:

```powershell
python start.py --gpt
python start.py --gtp
python start.py --chatgpt
python start.py --chatgtp
```

## Root cause

The `--gtp` typo alias was known as a target alias but was not registered as a parser flag. When used beside `--chat`, argparse treated it as an unknown tail and the chat parser defaulted back to Grok.

The fixed launcher now:

- registers `--gtp` as a real ChatGPT flag,
- treats ChatGPT alias flags as target selectors when combined with `--chat`,
- only opens the login bridge when the ChatGPT flag is used with no message and no `--chat`,
- keeps explicit `--chat grok "Hello"` working as Grok.

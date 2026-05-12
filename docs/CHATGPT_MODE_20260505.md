# ChatGPT Browser Mode

SuperGrok can now drive either Grok or ChatGPT through the same bridge, attachment, logging, and ToolCall pipeline.

## CLI examples

```powershell
python start.py --chatgtp "Hello from ChatGPT mode"
python start.py --chatgpt "Summarize this file" --file README.md
python start.py --chat chatgpt "Run `Get-ChildItem` and show me the output"
python start.py --chat grok "Hello from Grok mode"
```

`--chatgtp` is intentionally supported as an alias for the common typo. The canonical spelling is `--chatgpt`.

## Visible repair/login window

```powershell
python start.py --serve-bridge --target chatgpt --url https://chatgpt.com/ --show-bridge
```

Use this if ChatGPT needs login, captcha, or manual account repair. The bridge stores the browser profile under `data/grok_profile` by default so the web session can persist across runs.

## Behavior

- `--file` and `--attach` work the same way as Grok mode.
- ToolCall extraction still watches backticks and fenced code blocks.
- Returned attachments are saved under `data/received_attachments/`.
- Logs include the selected target so stale Grok bridge services are restarted when ChatGPT mode is requested.

# CutiePy INI / DB Settings Sync Whitepaper for ChatGTP

## Problem

CutiePy has two legitimate settings stores: a human-editable `config.ini` and a runtime settings table in the local database. API keys, Azure endpoints, model selections, Ollama settings, and UI options can be changed from either side. If startup blindly trusts only one side, the app can launch with stale API keys or malformed endpoints even though the correct values exist elsewhere.

## Required behavior

The INI layer must be a two-way sync coordinator, not just a parser. On startup and before chat/API tests, it must:

1. Read `config.ini` first.
2. Parse normal sections plus `[settings_last_modified]`.
3. Open the configured local settings DB.
4. Ensure every settings row has a `modified` timestamp. Existing rows without timestamps get one.
5. For every known setting key, compare:
   - the INI setting timestamp from `[settings_last_modified]`;
   - the DB row timestamp from `settings.modified`;
   - the `config.ini` file mtime as a migration fallback when old INI files do not yet have per-setting timestamps.
6. Newest value wins per setting.
7. If INI wins, write/update the DB row and its `modified` value.
8. If DB wins, write/update the INI key and `modified_<key>` entry.
9. Reload the INI cache after any write.
10. Trace the result with counts: `settings_seen`, `config_to_db`, `db_to_config`, `created_timestamps`, and `skipped`.

## Timestamp model

Every setting needs its own clock. Do not rely only on whole-file mtime forever. Whole-file mtime is only a fallback for older configs. The canonical INI timestamp key is:

```ini
[settings_last_modified]
modified_openai_api_key = 2026-05-07T05:17:53.590185
modified_azure_openai_endpoint = 2026-05-07T05:17:53.590185
modified_ollama_host = 2026-05-07T05:17:53.590185
```

The canonical DB timestamp is `settings.modified`. If the table also has `created` and `updated`, keep them, but newest-wins sync should compare against `modified`.

## Setting key mapping

Provider settings must not be dumped randomly into `[settings]` if they have a natural section. Use canonical names internally and deterministic INI locations externally:

| Canonical key | INI location |
|---|---|
| `openai_api_key` | `[api_keys] openai_api_key` |
| `gemini_api_key` | `[api_keys] gemini_api_key` |
| `anthropic_api_key` | `[api_keys] anthropic_api_key` |
| `azure_openai_endpoint` | `[azure] endpoint` |
| `azure_openai_key` | `[azure] api_key` |
| `azure_openai_key1` | `[azure] api_key1` |
| `azure_openai_key2` | `[azure] api_key2` |
| `azure_deployment_name` | `[azure] deployment_name` |
| `ollama_host` | `[ollama] host` |
| `ollama_model_choice` | `[ollama] model_choice` |

Unknown simple app settings go to `[settings]`. Keys already shaped as `section.option` go back to that section and option.

## Endpoint validation

Never let a malformed endpoint like `https:` overwrite a working DB value. Endpoint values must be normalized before storing:

- reject blank values;
- reject `http:` / `https:` without a hostname;
- reject hostnames like `http` or `https`;
- add `https://` when a hostname is supplied without scheme;
- preserve one trailing slash.

For Azure, if `[azure] endpoint` is malformed, the loader may use a valid compatibility fallback such as `[azure] cognitive_services_endpoint` or `[azure_trioapi] endpoint`, but it must trace that repair.

## Startup order

The correct startup order is:

1. Load launcher paths.
2. Load and parse `config.ini`.
3. Ensure DB dependencies if available.
4. Sync INI ↔ DB with newest-wins rules.
5. Reload `config.ini` cache.
6. Stage provider/API settings.
7. Build model catalog / GUI.

Do not build the model menu or run API calls before step 4.

## Failure behavior

Sync must never crash the GUI startup just because SQLAlchemy or the DB is temporarily unavailable. If DB sync fails, keep using `config.ini` and runtime settings, warn loudly, and continue. If the DB is available but a row fails to migrate, record the exception and keep the config value.

## Trace requirements

Trace these events without printing raw secrets:

- config load path, byte length, MD5, and section names;
- DB path and settings row count;
- sync direction counts;
- endpoint repair decisions;
- masked API values as `[set len=N]`;
- exact keys changed, but not secret values.

Raw secret tracing should require an explicit developer-only environment variable.

## Current implementation notes

This CutiePy pass adds launcher-side `IniConfig.syncWithDatabase()` and app-side `INI.syncWithSettingsDb()`. Both follow the same newest-wins model. The uploaded config is now packaged as `config.ini`, with obvious CutiePy branding and a repaired Azure endpoint fallback so `endpoint=https:` cannot create broken URLs.

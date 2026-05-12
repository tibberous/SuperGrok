# ChatGPT Composer Submit Fix - 2026-05-05

The failing ChatGPT run authenticated and loaded the page, and the bridge filled `#prompt-textarea`, but ChatGPT did not enable a real submit button. The previous fallback pressed synthetic Enter, which cleared or changed the composer without producing a `/backend-api/conversation` request.

This pass makes ChatGPT submission stricter and more React-aware:

- `#prompt-textarea` / textarea value setting now resets React `_valueTracker` before dispatching input/change events.
- Input/change/focus events are composed and bubble through the React tree.
- The submit button search now includes `button#composer-submit-button`.
- The button filter excludes non-send composer controls such as `composer-plus-btn`, `Add files and more`, model/mode pills, and suggestion chips.
- ChatGPT mode no longer uses synthetic Enter as the final fallback. If React still does not enable the send button, the bridge logs `send-button-not-enabled` with button candidates and editor preview instead of pretending the prompt submitted.

Expected next proof in `debug.log`: after `after-fill`, the `find-send-button` trace should show either `button#composer-submit-button`, `data-testid=composer-submit-button`, or `data-testid=send-button`, followed by a ChatGPT `/backend-api/conversation` or equivalent chat stream request.

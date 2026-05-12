/*
SuperGrok Bridge DOM helper.
Injected through QWebEnginePage.runJavaScript().
Keep this dependency-free: no jQuery needed.
*/
(function () {
  if (window.SuperGrokBridgeDom) {
    return window.SuperGrokBridgeDom;
  }

  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }

  function queryFirst(selectors) {
    for (const selector of selectors) {
      try {
        const nodes = Array.from(document.querySelectorAll(selector));
        const found = nodes.find(visible);
        if (found) return found;
      } catch (_ignored) {}
    }
    return null;
  }

  function findPromptBox() {
    return queryFirst([
      '#prompt-textarea',
      'textarea#prompt-textarea',
      'div#prompt-textarea[contenteditable="true"]',
      'div.ProseMirror[contenteditable="true"]',
      'div[contenteditable="true"][role="textbox"]',
      'textarea',
      '[contenteditable="true"]',
      '[role="textbox"]',
      'div.ProseMirror',
      '[aria-label*="message" i]',
      '[aria-label*="prompt" i]',
      '[aria-label*="ask" i]'
    ]);
  }

  function nativeValueSet(el, value) {
    const text = String(value == null ? '' : value);
    if (!el) return false;
    const tag = String(el.tagName || '').toLowerCase();
    try { el.focus(); } catch (_ignored) {}
    try { el.click(); } catch (_ignored) {}
    if (tag === 'textarea' || tag === 'input') {
      let setter = null;
      try {
        const proto = tag === 'textarea' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        setter = Object.getOwnPropertyDescriptor(proto, 'value') && Object.getOwnPropertyDescriptor(proto, 'value').set;
      } catch (_ignored) {}
      try {
        const previous = String(el.value || '');
        if (setter) setter.call(el, text);
        else el.value = text;
        try {
          const tracker = el._valueTracker;
          if (tracker && typeof tracker.setValue === 'function') tracker.setValue(previous);
        } catch (_ignoredTracker) {}
      } catch (_ignored) { el.value = text; }
      try { el.setSelectionRange(text.length, text.length); } catch (_ignored) {}
      return true;
    }
    return false;
  }

  function dispatchPromptEvents(el, text) {
    const value = String(text == null ? '' : text);
    const events = [
      new FocusEvent('focus', { bubbles: true, composed: true }),
      new InputEvent('beforeinput', { bubbles: true, cancelable: true, composed: true, inputType: 'insertText', data: value }),
      new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertText', data: value }),
      new Event('change', { bubbles: true, composed: true }),
      new CompositionEvent('compositionend', { bubbles: true, composed: true, data: value }),
      new KeyboardEvent('keydown', { bubbles: true, cancelable: true, composed: true, key: ' ', code: 'Space', which: 32, keyCode: 32 }),
      new KeyboardEvent('keyup', { bubbles: true, cancelable: true, composed: true, key: 'Unidentified', code: '', which: 0, keyCode: 0 })
    ];
    for (const ev of events) {
      try { el.dispatchEvent(ev); } catch (_ignored) {}
    }
  }

  function reactTextareaSet(el, value) {
    const text = String(value == null ? '' : value);
    if (!el) return false;
    const tag = String(el.tagName || '').toLowerCase();
    if (tag !== 'textarea' && tag !== 'input') return false;
    let setter = null;
    try {
      const proto = tag === 'textarea' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      setter = descriptor && descriptor.set;
    } catch (_ignored) {}
    function setValue(next, previousForTracker) {
      try {
        if (setter) setter.call(el, next);
        else el.value = next;
        const tracker = el._valueTracker;
        if (tracker && typeof tracker.setValue === 'function') tracker.setValue(String(previousForTracker == null ? '' : previousForTracker));
      } catch (_ignored) { el.value = next; }
    }
    try { el.focus(); } catch (_ignored) {}
    try { el.click(); } catch (_ignored) {}
    try { if (typeof el.select === 'function') el.select(); } catch (_ignored) {}
    const old = String(el.value || '');
    setValue('', old);
    try { el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'deleteContentBackward', data: null })); } catch (_ignored) {}
    let current = '';
    for (const ch of Array.from(text)) {
      try { el.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, composed: true, inputType: 'insertText', data: ch })); } catch (_ignored) {}
      const previous = current;
      current += ch;
      setValue(current, previous);
      try { el.setSelectionRange(current.length, current.length); } catch (_ignored) {}
      try { el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertText', data: ch })); } catch (_ignored) {}
    }
    try { el.dispatchEvent(new Event('change', { bubbles: true, composed: true })); } catch (_ignored) {}
    try { el.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, composed: true, data: text })); } catch (_ignored) {}
    try { el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, cancelable: true, composed: true, key: text.slice(-1) || 'Unidentified', code: '', which: 0, keyCode: 0 })); } catch (_ignored) {}
    return true;
  }

  function promptText(el) {
    if (!el) return '';
    const tag = String(el.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'input') return String(el.value || '');
    return String(el.innerText || el.textContent || '');
  }

  function setPromptText(text) {
    const box = findPromptBox();
    const value = String(text == null ? '' : text);
    if (!box) {
      return { ok: false, error: 'prompt box not found' };
    }
    try { box.focus(); } catch (_ignored) {}
    const tag = String(box.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'input') {
      if (!reactTextareaSet(box, value)) nativeValueSet(box, value);
      dispatchPromptEvents(box, value);
    } else {
      try {
        const selection = window.getSelection && window.getSelection();
        if (selection && document.createRange) {
          const range = document.createRange();
          range.selectNodeContents(box);
          selection.removeAllRanges();
          selection.addRange(range);
        }
      } catch (_ignored) {}
      try { document.execCommand('selectAll', false, null); } catch (_ignored) {}
      try { document.execCommand('insertText', false, value); } catch (_ignored) { box.innerText = value; }
      dispatchPromptEvents(box, value);
    }
    const actual = promptText(box);
    return { ok: actual.indexOf(value) >= 0 || value.length === 0, actualLength: actual.length, expectedLength: value.length, tagName: tag || '', preview: actual.slice(0, 120) };
  }

  function buttonInfo(row, promptBox) {
    if (!row || !row.el) return { found: false, element: null, score: 0, looksLikeSend: false, enabled: false, excluded: false };
    const button = row.el;
    const aria = String(button.getAttribute('aria-label') || '');
    const title = String(button.getAttribute('title') || '');
    const testid = String(button.getAttribute('data-testid') || '');
    const type = String(button.getAttribute('type') || '').toLowerCase();
    const text = String(button.innerText || button.textContent || '').trim();
    const classes = String(button.getAttribute('class') || '');
    const haystack = (aria + ' ' + title + ' ' + testid + ' ' + text + ' ' + classes).toLowerCase();
    const hasSvg = !!button.querySelector('svg');
    const disabled = !!button.disabled || button.getAttribute('aria-disabled') === 'true';
    const promptForm = promptBox && promptBox.closest ? promptBox.closest('form') : null;
    const sameForm = !!(promptForm && button.closest && button.closest('form') === promptForm);
    const excluded = /\b(attach|upload|file|files|add files|add files and more|composer-plus|model select|dictation|voice|microphone|mic|sidebar|search|project|history|menu|settings|extended|create an image|write or edit|look something up)\b/.test(haystack);
    let score = 0;
    if (sameForm) score += 45;
    if (/chat-submit|send|submit|composer-submit-button|enviar|arrow|up/.test(haystack)) score += 120;
    if (testid.toLowerCase() === 'chat-submit') score += 220;
    if (['send-button','composer-submit-button'].indexOf(testid.toLowerCase()) >= 0) score += 260;
    if (String(button.id || '').toLowerCase() === 'composer-submit-button') score += 260;
    if (type === 'submit') score += 160;
    if (hasSvg) score += 25;
    if (excluded) score -= 500;
    if (disabled) score -= 250;
    return { found: true, element: button, score, looksLikeSend: !disabled && !excluded && score >= 90, enabled: !disabled, excluded, hasSvg, sameForm, ariaLabel: aria, title, dataTestId: testid, type, text: text.slice(0, 80) };
  }

  function findSendButton() {
    const promptBox = findPromptBox();
    const selectors = [
      'button[data-testid="send-button"]',
      'button[data-testid="composer-submit-button"]',
      'button#composer-submit-button',
      'button[id="composer-submit-button"]',
      'button[data-testid="chat-submit"]',
      'button[type="submit"][aria-label="Enviar"]',
      'button[aria-label*="enviar" i]',
      'button[aria-label*="send" i]',
      'button[aria-label*="submit" i]',
      'button[type="submit"]',
      'form button[type="submit"]',
      'form button',
      'button:has(svg)'
    ];
    const rows = [];
    const seen = new Set();
    function collect(scope) {
      for (const selector of selectors) {
        try {
          for (const el of Array.from((scope || document).querySelectorAll(selector))) {
            if (!visible(el) || seen.has(el)) continue;
            seen.add(el);
            rows.push(buttonInfo({ selector, el }, promptBox));
          }
        } catch (_ignored) {}
      }
    }
    const form = promptBox && promptBox.closest ? promptBox.closest('form') : null;
    if (form) collect(form);
    collect(document);
    rows.sort((a, b) => (b.score || 0) - (a.score || 0));
    return rows.find((item) => item.looksLikeSend && item.element) || null;
  }

  function clickSendOrEnter() {
    const info = findSendButton();
    if (info && info.element) {
      info.element.click();
      return { ok: true, method: 'button', button: { score: info.score, ariaLabel: info.ariaLabel, dataTestId: info.dataTestId, type: info.type } };
    }
    const box = findPromptBox();
    if (!box) {
      return { ok: false, error: 'prompt box not found for enter fallback' };
    }
    box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', which: 13, keyCode: 13 }));
    box.dispatchEvent(new KeyboardEvent('keypress', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', which: 13, keyCode: 13 }));
    box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', which: 13, keyCode: 13 }));
    return { ok: true, method: 'enter' };
  }

  function pageText() {
    return document.body ? document.body.innerText : '';
  }

  function textOf(el) {
    return (el && (el.innerText || el.textContent) || '').trim();
  }

  function cleanCandidateText(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function isBadAnswerCandidate(text) {
    const clean = cleanCandidateText(text);
    if (!clean) return true;
    const lowered = clean.toLowerCase();
    const generic = new Set([
      'refer to the following content:',
      'refer to the following content',
      'refer to following content:',
      'refer to following content',
      'what do you want to know?',
      'what do you want to know',
      'new chat',
      'fast',
      'private',
      'imagine',
      'sign in',
      'sign up'
    ]);
    if (generic.has(lowered)) return true;
    if (/^refer to (the )?following content:?$/i.test(clean)) return true;
    if (/^by messaging grok, you agree to/i.test(clean)) return true;
    if (/^toggle sidebar\b/i.test(clean)) return true;
    return false;
  }

  function isInChrome(el) {
    try {
      return !!(el.closest('nav, aside, header, footer, [role="navigation"], [data-sidebar], [class*="sidebar" i], [class*="history" i], [class*="conversation-list" i], button, a'));
    } catch (_ignored) {
      return false;
    }
  }

  function messages() {
    const selectors = ['[data-message-author-role="assistant"] .markdown', '[data-message-author-role="assistant"]', 'article', '[data-testid*="conversation-turn" i]', '[data-testid*="message" i]', '[class*="message" i]', '[class*="response" i]', '[class*="answer" i]'];
    const scope = document.querySelector('main') || document;
    const seen = new Set();
    const out = [];
    for (const selector of selectors) {
      for (const el of Array.from(scope.querySelectorAll(selector))) {
        if (!visible(el)) continue;
        if (isInChrome(el)) continue;
        const text = cleanCandidateText(textOf(el));
        if (!text || text.length < 2 || seen.has(text) || isBadAnswerCandidate(text)) continue;
        seen.add(text);
        out.push({
          index: out.length,
          selector: selector,
          tag: el.tagName.toLowerCase(),
          className: String(el.className || ''),
          text: text
        });
      }
    }
    return out;
  }

  function latestMessageText() {
    const found = messages();
    if (!found.length) return '';
    return found[found.length - 1].text;
  }

  function largestTextBlocks() {
    const selectors = ['article', '[data-testid]', '[class*="message" i]', '[class*="response" i]', 'main div'];
    const blocks = [];
    for (const selector of selectors) {
      for (const el of Array.from(document.querySelectorAll(selector))) {
        if (!visible(el)) continue;
        const text = textOf(el);
        if (text.length >= 20) blocks.push(text);
      }
    }
    return Array.from(new Set(blocks)).sort((a, b) => b.length - a.length).slice(0, 12);
  }

  function scriptInventory() {
    return Array.from(document.scripts).map((script, index) => ({
      index,
      src: script.src || '',
      type: script.type || '',
      async: !!script.async,
      defer: !!script.defer,
      inlineLength: script.src ? 0 : (script.textContent || '').length
    }));
  }

  function resourceInventory() {
    const entries = performance && performance.getEntriesByType ? performance.getEntriesByType('resource') : [];
    return entries.map((entry) => ({
      name: entry.name,
      initiatorType: entry.initiatorType || '',
      duration: Math.round(entry.duration || 0),
      transferSize: entry.transferSize || 0
    }));
  }

  function globalFunctionInventory(limit) {
    const names = [];
    for (const name of Object.getOwnPropertyNames(window)) {
      try {
        if (typeof window[name] === 'function') names.push(name);
      } catch (_ignored) {}
    }
    return names.sort().slice(0, limit || 500);
  }



  const NETWORK_EVENT_PREFIX = '__SUPERGROK_NETWORK_EVENT__';
  const NETWORK_CAPTURE_LIMIT = 500000;

  function safeString(value) {
    try {
      if (value === undefined) return '';
      if (value === null) return 'null';
      if (typeof value === 'string') return value;
      if (value instanceof URLSearchParams) return value.toString();
      if (value instanceof FormData) {
        const out = [];
        value.forEach((entryValue, key) => {
          if (entryValue && typeof entryValue === 'object' && 'name' in entryValue) {
            out.push([key, { fileName: entryValue.name, size: entryValue.size, type: entryValue.type }]);
          } else {
            out.push([key, String(entryValue)]);
          }
        });
        return JSON.stringify(out, null, 2);
      }
      if (typeof Blob !== 'undefined' && value instanceof Blob) {
        return '[Blob size=' + value.size + ' type=' + value.type + ']';
      }
      if (typeof ArrayBuffer !== 'undefined' && value instanceof ArrayBuffer) {
        return '[ArrayBuffer byteLength=' + value.byteLength + ']';
      }
      return JSON.stringify(value, null, 2);
    } catch (error) {
      try { return String(value); } catch (_ignored) { return '[unserializable]'; }
    }
  }

  function maybeTruncate(text) {
    text = safeString(text);
    if (text.length > NETWORK_CAPTURE_LIMIT) {
      return { text: text.slice(0, NETWORK_CAPTURE_LIMIT), length: text.length, truncated: true };
    }
    return { text, length: text.length, truncated: false };
  }

  async function captureRequestBodyFromRequest(input) {
    try {
      if (!(input instanceof Request)) return null;
      if (!input.body && !input.headers) return null;
      const cloned = input.clone();
      const text = await cloned.text();
      return maybeTruncate(text);
    } catch (error) {
      return { text: '', length: 0, truncated: false, error: String(error && error.message ? error.message : error) };
    }
  }

  function redactHeaderValue(key, value) {
    const lowered = String(key || '').toLowerCase();
    if (lowered.includes('authorization') || lowered.includes('cookie') || lowered.includes('token') || lowered.includes('secret') || lowered.includes('credential')) {
      return '[REDACTED]';
    }
    return String(value == null ? '' : value);
  }

  function headersToObject(headers) {
    const out = {};
    try {
      if (!headers) return out;
      if (headers instanceof Headers) {
        headers.forEach((value, key) => { out[key] = redactHeaderValue(key, value); });
      } else if (Array.isArray(headers)) {
        headers.forEach((pair) => {
          if (pair && pair.length >= 2) out[String(pair[0])] = redactHeaderValue(String(pair[0]), String(pair[1]));
        });
      } else if (typeof headers === 'object') {
        Object.keys(headers).forEach((key) => { out[key] = redactHeaderValue(key, headers[key]); });
      }
    } catch (error) {
      out.__headers_error = String(error && error.message ? error.message : error);
    }
    return out;
  }

  function parseRawResponseHeaders(rawHeaders) {
    const out = {};
    try {
      String(rawHeaders || '').trim().split(/\r?\n/).forEach((line) => {
        const idx = line.indexOf(':');
        if (idx > 0) {
          const key = line.slice(0, idx).trim();
          const value = line.slice(idx + 1).trim();
          out[key] = redactHeaderValue(key, value);
        }
      });
    } catch (_ignored) {}
    return out;
  }

  function scrubSensitiveObject(value) {
    const sensitive = /authorization|cookie|token|secret|password|passwd|credential|session/i;
    try {
      if (Array.isArray(value)) return value.map(scrubSensitiveObject);
      if (!value || typeof value !== 'object') return value;
      const out = {};
      Object.keys(value).forEach((key) => {
        if (sensitive.test(key)) out[key] = '[REDACTED]';
        else out[key] = scrubSensitiveObject(value[key]);
      });
      return out;
    } catch (_ignored) {
      return value;
    }
  }

  function emitNetworkEvent(payload) {
    try {
      payload = scrubSensitiveObject(payload || {});
      payload.page = { url: location.href, title: document.title };
      payload.capturedAt = new Date().toISOString();
      console.info(NETWORK_EVENT_PREFIX + JSON.stringify(payload));
    } catch (error) {
      console.warn(NETWORK_EVENT_PREFIX + JSON.stringify({
        captureLayer: 'javascript-hook',
        eventType: 'network-capture-error',
        error: String(error && error.message ? error.message : error),
        stack: String(error && error.stack ? error.stack : ''),
        capturedAt: new Date().toISOString()
      }));
    }
  }

  function installGlobalErrorHooks() {
    if (window.__superGrokGlobalErrorHooksInstalled) return;
    window.__superGrokGlobalErrorHooksInstalled = true;
    window.addEventListener('error', function(event) {
      emitNetworkEvent({
        captureLayer: 'javascript-window-error',
        eventType: 'window-error',
        message: String(event && event.message || ''),
        filename: String(event && event.filename || ''),
        lineno: event && event.lineno,
        colno: event && event.colno,
        error: String(event && event.error && event.error.message ? event.error.message : (event && event.error ? event.error : '')),
        stack: String(event && event.error && event.error.stack ? event.error.stack : '')
      });
    }, true);
    window.addEventListener('unhandledrejection', function(event) {
      const reason = event && event.reason;
      emitNetworkEvent({
        captureLayer: 'javascript-window-error',
        eventType: 'unhandledrejection',
        error: String(reason && reason.message ? reason.message : reason),
        stack: String(reason && reason.stack ? reason.stack : '')
      });
    }, true);
  }

  function installNetworkHooks() {
    if (window.__superGrokNetworkHooksInstalled) {
      return { ok: true, alreadyInstalled: true };
    }
    window.__superGrokNetworkHooksInstalled = true;
    installGlobalErrorHooks();

    const nativeFetch = window.fetch;
    if (typeof nativeFetch === 'function') {
      window.fetch = async function(input, init) {
        const startedAt = Date.now();
        let requestUrl = '';
        let method = 'GET';
        let requestHeaders = {};
        let requestBody = '';
        let requestBodyLength = 0;
        let requestBodyTruncated = false;
        try {
          if (input instanceof Request) {
            requestUrl = input.url || '';
            method = input.method || method;
            requestHeaders = headersToObject(input.headers);
            const clonedBodyCapture = await captureRequestBodyFromRequest(input);
            if (clonedBodyCapture) {
              requestBody = clonedBodyCapture.text || '';
              requestBodyLength = clonedBodyCapture.length || 0;
              requestBodyTruncated = !!clonedBodyCapture.truncated;
              if (clonedBodyCapture.error) requestHeaders.__body_capture_error = clonedBodyCapture.error;
            }
          } else {
            requestUrl = String(input || '');
          }
          if (init) {
            method = init.method || method;
            requestHeaders = Object.assign({}, requestHeaders, headersToObject(init.headers));
            if ('body' in init) {
              const bodyCapture = maybeTruncate(init.body);
              requestBody = bodyCapture.text;
              requestBodyLength = bodyCapture.length;
              requestBodyTruncated = bodyCapture.truncated;
            }
          }
        } catch (error) {
          requestHeaders.__capture_error = String(error && error.message ? error.message : error);
        }

        let response;
        try {
          response = await nativeFetch.apply(this, arguments);
        } catch (error) {
          emitNetworkEvent({
            captureLayer: 'javascript-fetch',
            eventType: 'fetch-error',
            method,
            url: requestUrl,
            displayUrl: requestUrl,
            request: { method, url: requestUrl, headers: requestHeaders, body: requestBody, bodyLength: requestBodyLength, bodyTruncated: requestBodyTruncated },
            response: { error: String(error && error.message ? error.message : error) },
            durationMs: Date.now() - startedAt
          });
          throw error;
        }

        try {
          const cloned = response.clone();
          cloned.text().then((responseText) => {
            const responseCapture = maybeTruncate(responseText);
            emitNetworkEvent({
              captureLayer: 'javascript-fetch',
              eventType: 'fetch-complete',
              method,
              url: response.url || requestUrl,
              displayUrl: response.url || requestUrl,
              request: { method, url: requestUrl, headers: requestHeaders, body: requestBody, bodyLength: requestBodyLength, bodyTruncated: requestBodyTruncated },
              response: {
                status: response.status,
                statusText: response.statusText,
                ok: response.ok,
                redirected: response.redirected,
                url: response.url,
                headers: headersToObject(response.headers),
                body: responseCapture.text,
                bodyLength: responseCapture.length,
                bodyTruncated: responseCapture.truncated
              },
              durationMs: Date.now() - startedAt
            });
          }).catch((error) => {
            emitNetworkEvent({
              captureLayer: 'javascript-fetch',
              eventType: 'fetch-response-read-error',
              method,
              url: response.url || requestUrl,
              request: { method, url: requestUrl, headers: requestHeaders, body: requestBody, bodyLength: requestBodyLength, bodyTruncated: requestBodyTruncated },
              response: { status: response.status, statusText: response.statusText, headers: headersToObject(response.headers), error: String(error && error.message ? error.message : error) },
              durationMs: Date.now() - startedAt
            });
          });
        } catch (error) {
          emitNetworkEvent({
            captureLayer: 'javascript-fetch',
            eventType: 'fetch-clone-error',
            method,
            url: requestUrl,
            request: { method, url: requestUrl, headers: requestHeaders, body: requestBody, bodyLength: requestBodyLength, bodyTruncated: requestBodyTruncated },
            response: { status: response && response.status, error: String(error && error.message ? error.message : error) },
            durationMs: Date.now() - startedAt
          });
        }
        return response;
      };
    }

    const NativeXHR = window.XMLHttpRequest;
    if (typeof NativeXHR === 'function') {
      const nativeOpen = NativeXHR.prototype.open;
      const nativeSetRequestHeader = NativeXHR.prototype.setRequestHeader;
      const nativeSend = NativeXHR.prototype.send;

      NativeXHR.prototype.open = function(method, url) {
        this.__superGrokCapture = {
          startedAt: 0,
          method: method || 'GET',
          url: String(url || ''),
          requestHeaders: {},
          requestBody: '',
          requestBodyLength: 0,
          requestBodyTruncated: false
        };
        return nativeOpen.apply(this, arguments);
      };

      NativeXHR.prototype.setRequestHeader = function(key, value) {
        try {
          if (!this.__superGrokCapture) this.__superGrokCapture = { requestHeaders: {} };
          this.__superGrokCapture.requestHeaders[String(key)] = redactHeaderValue(key, value);
        } catch (_ignored) {}
        return nativeSetRequestHeader.apply(this, arguments);
      };

      NativeXHR.prototype.send = function(body) {
        const capture = this.__superGrokCapture || { method: 'GET', url: '', requestHeaders: {} };
        capture.startedAt = Date.now();
        const bodyCapture = maybeTruncate(body);
        capture.requestBody = bodyCapture.text;
        capture.requestBodyLength = bodyCapture.length;
        capture.requestBodyTruncated = bodyCapture.truncated;

        this.addEventListener('loadend', function() {
          let responseBody = '';
          let responseLength = 0;
          let responseTruncated = false;
          let responseError = '';
          try {
            const responseCapture = maybeTruncate(this.responseText || '');
            responseBody = responseCapture.text;
            responseLength = responseCapture.length;
            responseTruncated = responseCapture.truncated;
          } catch (error) {
            responseError = String(error && error.message ? error.message : error);
          }
          emitNetworkEvent({
            captureLayer: 'javascript-xhr',
            eventType: 'xhr-complete',
            method: capture.method,
            url: this.responseURL || capture.url,
            displayUrl: this.responseURL || capture.url,
            request: {
              method: capture.method,
              url: capture.url,
              headers: capture.requestHeaders || {},
              body: capture.requestBody,
              bodyLength: capture.requestBodyLength,
              bodyTruncated: capture.requestBodyTruncated
            },
            response: {
              status: this.status,
              statusText: this.statusText,
              url: this.responseURL,
              headers: parseRawResponseHeaders(this.getAllResponseHeaders()),
              body: responseBody,
              bodyLength: responseLength,
              bodyTruncated: responseTruncated,
              error: responseError
            },
            durationMs: Date.now() - (capture.startedAt || Date.now())
          });
        });
        return nativeSend.apply(this, arguments);
      };
    }

    return { ok: true, fetchHooked: typeof nativeFetch === 'function', xhrHooked: typeof NativeXHR === 'function' };
  }

  function inventory() {
    return {
      url: location.href,
      title: document.title,
      hasPromptBox: !!findPromptBox(),
      scripts: scriptInventory(),
      resources: resourceInventory().slice(0, 250),
      globalFunctions: globalFunctionInventory(300),
      networkHooks: installNetworkHooks(),
      messages: messages().slice(-20),
      largestTextBlocks: largestTextBlocks()
    };
  }

  function sendPrompt(text) {
    const typed = setPromptText(text);
    if (!typed.ok) return typed;
    return clickSendOrEnter();
  }

  window.SuperGrokBridgeDom = {
    findPromptBox: () => !!findPromptBox(),
    setPromptText,
    clickSendOrEnter,
    sendPrompt,
    pageText,
    largestTextBlocks,
    messages,
    latestMessageText,
    scriptInventory,
    resourceInventory,
    globalFunctionInventory,
    installNetworkHooks,
    inventory
  };
  try { installNetworkHooks(); } catch (_ignored) {}
  return window.SuperGrokBridgeDom;
})();

// Test script: alert dialog in the live Grok page.
// Double-click this file in the Scripts list. It runs in the live Grok page, not as chat text.
alert('hello from SuperGrok script runner');
console.log('alert_hello.js finished');
window.__supergrokAlertSmokeTest = true;
return 'alert script completed';

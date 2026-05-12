// Verifies that the Grok page network capture hooks are installed.
console.log('network hook status script running');
if (!window.SuperGrokBridgeDom || !window.SuperGrokBridgeDom.installNetworkHooks) {
  console.warn('SuperGrokBridgeDom/installNetworkHooks is not available yet');
  return { ok: false, error: 'SuperGrokBridgeDom.installNetworkHooks missing' };
}
const status = window.SuperGrokBridgeDom.installNetworkHooks();
console.log('network hook status', status);
return status;

export function isAndroid(): boolean {
  return /Android/i.test(navigator.userAgent);
}

export function isMobile(): boolean {
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) || window.innerWidth < 768;
}

export function isWebView(): boolean {
  const ua = navigator.userAgent;
  // Only flag true WebViews, not real browsers
  if (/; wv\)/.test(ua)) return true;               // Android WebView marker
  if (/FBAN|FBAV/.test(ua)) return true;             // Facebook in-app browser
  if (/Instagram/.test(ua)) return true;             // Instagram in-app browser
  return false;
}

export function isSecureContext(): boolean {
  // getUserMedia requires HTTPS or localhost
  return window.isSecureContext;
}

export function hasGetUserMedia(): boolean {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

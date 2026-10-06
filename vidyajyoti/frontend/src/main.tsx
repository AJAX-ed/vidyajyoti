import React, { Component, type ReactNode } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

/* ------------------------------------------------------------------ */
/* ErrorBoundary: without this, ANY runtime error during render leaves */
/* the user staring at a blank white screen. Now errors are shown      */
/* on-page with a reload button and a hint about the dev console.      */
/* ------------------------------------------------------------------ */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    // Surface in console too, for debugging via browser dev tools.
    console.error("VidyaJyoti render error:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-app text-app flex items-center justify-center p-8">
          <div className="max-w-lg w-full bg-surface border border-app rounded-2xl p-6 space-y-4">
            <h1 className="text-xl font-bold text-red-500">Something went wrong</h1>
            <p className="text-muted text-sm">
              VidyaJyoti hit a rendering error instead of showing a blank screen.
              Details below (also visible in the browser console — F12).
            </p>
            <pre className="text-xs bg-app border border-app rounded-lg p-3 overflow-auto max-h-48 whitespace-pre-wrap">
              {String(this.state.error?.stack || this.state.error)}
            </pre>
            <button
              onClick={() => window.location.reload()}
              className="gradient-emerald text-white px-4 py-2 rounded-lg text-sm font-semibold"
            >
              Reload app
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ------------------------------------------------------------------ */
/* White-screen watchdog: if the bundle never executes (blocked CDN-   */
/* less import, service worker weirdness, browser extension, OneDrive  */
/* sync corrupting node_modules mid-serve), the page would stay blank   */
/* forever with NO error. After 8 s we show an actionable message.      */
/* ------------------------------------------------------------------ */
setTimeout(() => {
  const boot = document.getElementById("vj-boot");
  if (!boot) return; // React already mounted and removed it — all good.
  boot.innerHTML =
    '<div style="max-w-lg;text-align:center;padding:2rem;font-family:ui-sans-serif,system-ui,sans-serif">' +
    "<h1 style='color:#e8f0ec;font-size:1.5rem;margin-bottom:0.75rem'>VidyaJyoti JS did not start</h1>" +
    "<p style='color:#8fa39a;font-size:0.9rem;margin-bottom:1rem'>The app bundle never executed. " +
    "Hard-refresh (Ctrl+Shift+R). If this persists, open Dev&nbsp;Console (F12) to see the error — " +
    "usually a corrupted <code>node_modules</code>: delete it and re-run <code>python runner.py</code>.</p>" +
    "<button onclick='location.reload()' style='background:#10b981;color:#fff;border:none;padding:0.6rem 1.4rem;" +
    "border-radius:0.5rem;font-weight:600;cursor:pointer'>Reload</button></div>";
}, 8000);

/* ------------------------------------------------------------------ */
/* Boot splash: proves the bundle loaded. If the screen ever stays on   */
/* this, the problem is CSS/network (not React). Removed once mounted.  */
/* ------------------------------------------------------------------ */
function Splash() {
  return (
    <div className="min-h-screen bg-app text-app flex flex-col items-center justify-center gap-3">
      <div className="text-2xl font-bold text-gradient-emerald">VidyaJyoti</div>
      <div className="text-muted text-sm animate-pulse">Loading your study light…</div>
    </div>
  );
}

const rootEl = document.getElementById("root");
if (!rootEl) {
  // index.html missing its mount node would otherwise mean a silent white page.
  document.body.innerHTML =
    '<pre style="color:#f87171;padding:2rem;font-family:monospace">' +
    "VidyaJyoti: #root element not found in index.html.</pre>";
} else {
  // React mounted successfully — remove the static boot splash from index.html.
  document.getElementById("vj-boot")?.remove();

  const root = ReactDOM.createRoot(rootEl);
  // Any runtime error in App now renders an on-page message instead of a
  // blank white screen; Suspense covers any lazy boundaries we add later.
  root.render(
    <React.StrictMode>
      <ErrorBoundary>
        <React.Suspense fallback={<Splash />}>
          <App />
        </React.Suspense>
      </ErrorBoundary>
    </React.StrictMode>
  );
}

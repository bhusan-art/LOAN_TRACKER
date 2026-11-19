# Phase 19 - Windows desktop app (MSI)

Owner's choices (2026-11-19): a desktop app (not the server) installed from an MSI, unsigned
for now (Windows shows SmartScreen once: "More info -> Run anyway"). Built with Tauri 2
(Windows' built-in WebView2 + a small Rust shell), which produces an MSI directly.
Approved with it: @tauri-apps/cli, @tauri-apps/api, @tauri-apps/plugin-opener and the
matching Rust crates (tauri, tauri-build, tauri-plugin-opener, serde).

## Behaviour
- Same screens and sign-in as the Android app: server's https address on first start (Tailscale),
  a device token per PC, idle sign-out, one sign-in per user, viewers read-only.
- Desktop differences: files (documents, CSV) download to the Downloads folder; printing works
  (statement, receipt, certificate); links (WhatsApp, phone) open in the default browser/app;
  no fingerprint lock or phone reminders.
- Window: 1280x820, remembers nothing outside the app's own data folder; CSP allows only https
  connections; navigation outside the app is blocked.

## Server
- APP_ORIGINS default adds the desktop app's origin `https://tauri.localhost`.

## Build
- frontend/src-tauri (Rust shell, icons, config). GitHub Actions on windows-latest builds the
  MSI for every `v*` tag (same tags as the APK) and attaches it to the release.

## Tests
- pytest: CORS for the desktop origin. Browser run of the build in desktop mode.
- cargo check of the Rust shell where possible here; the MSI itself is built on GitHub.

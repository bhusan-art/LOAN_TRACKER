# Phase 18 - Android app (Capacitor)

Owner's choices (2026-11-19): Android only; wrap the existing React app with Capacitor;
the phone reaches the server over Tailscale; a signed APK built by GitHub Actions, installed
by hand (no Play Store). Approved: Capacitor core/cli/android + official plugins (app,
preferences, local-notifications, share, filesystem) + @aparajita/capacitor-biometric-auth
(fingerprint unlock).

## How the app talks to the server
- The screens ship inside the APK (no website loaded from the internet).
- Phone sign-in creates a **device token** (new table `app_device`, token stored hashed,
  like share links). The app sends `Authorization: Bearer <token>`; no cookies, so no CSRF
  token is needed for those requests (a page on another site cannot read the token).
- Same rules as the browser: viewers read-only, documents owner-only, a disabled user or a
  password reset ends every device; each phone can be signed out from Settings or the CLI.
- CORS: only the app's own origin (`https://localhost`) may call the API, and only with a
  bearer token (no cookies across origins).
- HTTPS only: Tailscale `tailscale serve` gives the PC an HTTPS address with a real
  certificate; the app refuses plain http.

## Phone features
- First launch: server address -> test connection -> sign in (password + code).
- Fingerprint / screen-lock unlock when the app opens or returns from the background
  (switch in Settings).
- Due reminders as phone notifications (from the server's own dues, rescheduled each time
  the app opens; no data leaves the phone).
- Documents, receipts and CSV export open through Android's share sheet.
- Android back button; printing (not available in the app) is replaced by sharing.

## Server
- Migration 0011 `app_device`; services create/resolve/list/revoke; CLI list-devices /
  revoke-device; endpoints /api/device/login, /api/device/logout, /api/devices, revoke.
- Settings page (web and app): signed-in phones with "Sign out".

## Build
- `frontend/android/` Capacitor project in the repo. GitHub Actions builds the APK on
  every tag `v*` (and by hand): signed release when the keystore secrets are set.

## Tests
- pytest: device login/limits, bearer auth for reads/writes, viewer, revoke, disabled user,
  CORS, no CSRF needed with a bearer token but still needed with a cookie.
- Vitest: server address checks. Browser run of the app in "phone mode" from another origin.

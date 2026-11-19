import type { CapacitorConfig } from "@capacitor/cli";

// Android app: the screens are the built React app (dist-app), served inside the APK at
// https://localhost; the server is whatever address you enter on first launch.
const config: CapacitorConfig = {
  appId: "app.ledgerly.tracker",
  appName: "Ledgerly",
  webDir: "dist-app",
  server: { androidScheme: "https" },
  android: {
    allowMixedContent: false,          // never load http content
    webContentsDebuggingEnabled: false, // no Chrome DevTools into the app in release builds
  },
  plugins: {
    SystemBars: { insetsHandling: "css", initialViewportFitValueHint: "cover" },
    LocalNotifications: { smallIcon: "ic_stat_ledgerly", iconColor: "#087f72" },
  },
};

export default config;

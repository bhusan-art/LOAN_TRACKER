import { useState, type ReactNode } from "react";
import { fetchFile } from "../lib/api";
import { isNative, shareBlob } from "../lib/native";
import { useToast } from "../lib/toast";
import { Icon } from "./Icon";
import { cx } from "./ui";

/**
 * A file from the server (document, CSV export). In the browser: a normal link. In the
 * Android app: downloaded with the phone's token and handed to Android's share sheet, where
 * you can open it in a viewer, save it or send it.
 */
export function FileButton({ path, label, icon, className, title, children }: {
  path: string; label: string; icon: string; className?: string; title?: string; children?: ReactNode;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!isNative) {
    const download = path.includes("download=1") || path.endsWith(".csv");
    return (
      <a className={className} href={path} target={download ? undefined : "_blank"} rel="noopener" title={title}>
        <Icon name={icon} className="size-3.5" />{children ?? label}
      </a>
    );
  }
  return (
    <button type="button" className={cx(className)} disabled={busy} title={title} onClick={async () => {
      setBusy(true);
      try {
        const { blob, name } = await fetchFile(path);
        await shareBlob(blob, name, label);
      } catch (e) {
        if (!(e instanceof Error && /cancel/i.test(e.message))) toast(e instanceof Error ? e.message : "Could not open the file.", "err");
      } finally {
        setBusy(false);
      }
    }}>
      <Icon name={isNative ? "share" : icon} className="size-3.5" />{busy ? "Opening…" : children ?? label}
    </button>
  );
}

/** Print / save as PDF in the browser. Android app views cannot print, so it is hidden there. */
export function PrintButton({ className = "btn btn-primary" }: { className?: string }) {
  if (isNative) return null;
  return <button type="button" className={className} onClick={() => window.print()}><Icon name="print" className="size-4" />Print / save PDF</button>;
}

import { useEffect, useState, type ReactNode } from "react";
import { fetchFile } from "../lib/api";
import { isAndroid, isDesktop, isNative, saveBlob, shareBlob } from "../lib/native";
import { useToast } from "../lib/toast";
import { Icon } from "./Icon";
import { cx, Modal } from "./ui";

/**
 * A file from the server (document, CSV export).
 * Browser: a normal link. Android app: downloaded with the phone's token and handed to the
 * share sheet. Windows app: `preview` shows images and PDFs inside the app, otherwise the
 * file is saved to the Downloads folder.
 */
export function FileButton({ path, label, icon, className, title, preview, children }: {
  path: string; label: string; icon: string; className?: string; title?: string; preview?: boolean; children?: ReactNode;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState<{ url: string; type: string; name: string; blob: Blob } | null>(null);
  useEffect(() => () => { if (shown) URL.revokeObjectURL(shown.url); }, [shown]);

  if (!isNative) {
    const download = path.includes("download=1") || path.endsWith(".csv");
    return (
      <a className={className} href={path} target={download ? undefined : "_blank"} rel="noopener" title={title}>
        <Icon name={icon} className="size-3.5" />{children ?? label}
      </a>
    );
  }

  async function run() {
    setBusy(true);
    try {
      const { blob, name } = await fetchFile(path);
      if (isAndroid) await shareBlob(blob, name, label);
      else if (preview && /^(image\/(jpeg|png|webp)|application\/pdf)$/.test(blob.type)) {
        setShown({ url: URL.createObjectURL(blob), type: blob.type, name, blob });
      } else {
        saveBlob(blob, name);
        toast(`${name} saved to your Downloads folder.`);
      }
    } catch (e) {
      if (!(e instanceof Error && /cancel/i.test(e.message))) toast(e instanceof Error ? e.message : "Could not open the file.", "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className={cx(className)} disabled={busy} title={title} onClick={run}>
        <Icon name={isAndroid ? "share" : icon} className="size-3.5" />{busy ? "Opening…" : children ?? label}
      </button>
      {isDesktop && shown && (
        <Modal open onClose={() => setShown(null)} title={shown.name} wide>
          {shown.type === "application/pdf"
            ? <iframe title={shown.name} src={shown.url} className="h-[70vh] w-full rounded-lg border border-line" />
            : <img src={shown.url} alt={shown.name} className="mx-auto max-h-[70vh] rounded-lg" />}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => { saveBlob(shown.blob, shown.name); toast(`${shown.name} saved to your Downloads folder.`); }}>
              <Icon name="download" className="size-4" />Save to Downloads
            </button>
            <button type="button" className="btn btn-primary" onClick={() => setShown(null)}>Close</button>
          </div>
        </Modal>
      )}
    </>
  );
}

/** Print / save as PDF. The Android app's view cannot print, so it is hidden there. */
export function PrintButton({ className = "btn btn-primary" }: { className?: string }) {
  if (isAndroid) return null;
  return <button type="button" className={className} onClick={() => window.print()}><Icon name="print" className="size-4" />Print / save PDF</button>;
}

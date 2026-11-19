import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { ApiError } from "../lib/api";
import { useSession } from "../lib/session";
import { useToast } from "../lib/toast";
import { initials, STATUS_LABEL, STATUS_TONE, type Tone } from "../lib/format";

export function cx(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}

/* ------------------------------------------------------------------ page frame */
export function PageHead({ eyebrow, title, sub, actions, scope = true }: {
  eyebrow: string; title: ReactNode; sub?: ReactNode; actions?: ReactNode; scope?: boolean;
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div className="min-w-0">
        <div className="text-[10px] font-extrabold tracking-[1.5px] text-teal uppercase">{eyebrow}</div>
        <h1 className="mt-1.5 mb-1 text-[26px] font-extrabold tracking-[-1px] sm:text-[32px] sm:tracking-[-1.2px]">{title}</h1>
        {sub && <p className="text-[13px] text-muted">{sub}</p>}
      </div>
      {(scope || actions) && (
        <div className="no-print flex flex-wrap items-center gap-2">
          {scope && <BookScope />}
          {actions}
        </div>
      )}
    </div>
  );
}

export function BookScope() {
  const { book, setBook } = useSession();
  return (
    <div className="mr-auto flex rounded-lg bg-chip p-[3px] lg:mr-2" role="group" aria-label="Book">
      {[["all", "All"], ["business", "Business"], ["personal", "Personal"]].map(([k, label]) => (
        <button key={k} type="button" aria-pressed={book === k} onClick={() => setBook(k)}
          className={cx("rounded-md px-3 py-2 text-[11px] font-semibold",
            book === k ? "bg-card text-teal shadow-[0_1px_4px_#173b3d16]" : "text-soft")}>{label}</button>
      ))}
    </div>
  );
}

export function Card({ children, className, id }: { children: ReactNode; className?: string; id?: string }) {
  return <section id={id} className={cx("card p-5", className)}>{children}</section>;
}

export function CardHead({ title, sub, icon, iconTone = "teal", right }: {
  title: ReactNode; sub?: ReactNode; icon?: string; iconTone?: Tone; right?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h3 className="flex items-center gap-2 text-[15px] font-extrabold">
          {icon && <span className={cx("grid size-7 place-items-center rounded-lg", TONE_BG[iconTone])}><Icon name={icon} className="size-4" /></span>}
          {title}
        </h3>
        {sub && <p className="mt-1 text-[11px] text-muted">{sub}</p>}
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

const TONE_BG: Record<Tone, string> = {
  teal: "bg-tealsoft text-teal", green: "bg-greensoft text-green", amber: "bg-ambersoft text-amber",
  red: "bg-redsoft text-redink", violet: "bg-violetsoft text-violet", blue: "bg-bluesoft text-blue",
  gray: "bg-chip text-muted",
};

export function Tag({ tone = "gray", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={cx("inline-block rounded-[5px] px-[7px] py-1 text-[10px] font-bold whitespace-nowrap", TONE_BG[tone])}>{children}</span>;
}

export function StatusTag({ status }: { status: string }) {
  return <Tag tone={STATUS_TONE[status] ?? "gray"}>{STATUS_LABEL[status] ?? status}</Tag>;
}

export function DirTag({ direction }: { direction: string }) {
  return direction === "GIVEN" ? <Tag tone="teal">Lent</Tag> : <Tag tone="amber">Borrowed</Tag>;
}

export function BookTag({ book }: { book: string }) {
  return <Tag tone={book === "BUSINESS" ? "blue" : "violet"}>{book === "BUSINESS" ? "Business" : "Personal"}</Tag>;
}

export function Metric({ label, value, sub, foot, icon, tone }: {
  label: string; value: ReactNode; sub?: ReactNode; foot?: ReactNode; icon: string; tone: Tone;
}) {
  const blob: Record<Tone, string> = { teal: "bg-tealsoft", amber: "bg-ambersoft", violet: "bg-violetsoft",
    blue: "bg-bluesoft", green: "bg-greensoft", red: "bg-redsoft", gray: "bg-chip" };
  return (
    <article className="card relative min-h-[136px] overflow-hidden p-4 sm:p-[18px]">
      <div className={cx("pointer-events-none absolute -right-9 -bottom-12 size-[90px] rounded-full", blob[tone])} />
      <div className="relative flex items-center justify-between gap-2 text-xs font-semibold text-soft">
        <span>{label}</span>
        <span className={cx("grid size-[30px] shrink-0 place-items-center rounded-[9px]", TONE_BG[tone])}><Icon name={icon} className="size-4" /></span>
      </div>
      <strong className="num relative mt-3 mb-0.5 block font-display text-[21px] font-extrabold tracking-[-.6px] sm:text-[25px]">{value}</strong>
      {sub && <small className="relative block text-[11px] text-muted">{sub}</small>}
      {foot && <div className="relative mt-2 text-[10px]">{foot}</div>}
    </article>
  );
}

export function Avatar({ name, tone = "teal", size = 34 }: { name: string; tone?: Tone; size?: number }) {
  return (
    <span style={{ width: size, height: size }}
      className={cx("grid shrink-0 place-items-center rounded-full text-[12px] font-extrabold", TONE_BG[tone])}>
      {initials(name)}
    </span>
  );
}

export function Empty({ icon = "check", title, text, action }: { icon?: string; title: string; text?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-4 py-8 text-center">
      <span className="grid size-11 place-items-center rounded-full bg-greensoft text-green"><Icon name={icon} className="size-5" /></span>
      <b className="mt-3 text-[13px]">{title}</b>
      {text && <small className="mt-1 max-w-sm text-[11px] text-muted">{text}</small>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="grid place-items-center py-24 text-muted" role="status">
      <div className="mb-3 size-7 animate-spin rounded-full border-[3px] border-tealsoft border-t-teal" />
      <span className="text-xs">{label}</span>
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <Alert tone="red">{msg}</Alert>;
}

export function Alert({ tone = "amber", children, action }: { tone?: "amber" | "red" | "teal"; children: ReactNode; action?: ReactNode }) {
  const cls = { amber: "bg-ambersoft text-[#9a5b14] dark:text-amber", red: "bg-redsoft text-redink", teal: "bg-tealsoft text-teal" }[tone];
  return (
    <div className={cx("flex items-start gap-2.5 rounded-[10px] px-3.5 py-3 text-xs leading-relaxed", cls)} role={tone === "red" ? "alert" : undefined}>
      <Icon name={tone === "teal" ? "check" : "alert"} className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/** A two-column "label ..... value" row. */
export function Row({ label, children, strong }: { label: ReactNode; children: ReactNode; strong?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line py-2.5 text-xs last:border-0">
      <span className="text-muted">{label}</span>
      <span className={cx("num text-right", strong ? "font-display text-[13px] font-extrabold" : "font-semibold")}>{children}</span>
    </div>
  );
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="-mx-5 overflow-x-auto px-5"><table className="w-full border-collapse">{children}</table></div>;
}

export function PersonCell({ name, sub, to, tone = "teal" }: { name: string; sub?: ReactNode; to?: string; tone?: Tone }) {
  const body = (
    <span className="flex min-w-0 items-center gap-2.5">
      <Avatar name={name} tone={tone} />
      <span className="min-w-0">
        <b className="block truncate text-[12px]">{name}</b>
        {sub && <small className="mt-0.5 block truncate text-[10px] text-muted">{sub}</small>}
      </span>
    </span>
  );
  return to ? <Link to={to} className="hover:text-teal">{body}</Link> : body;
}

/* ------------------------------------------------------------------ forms */
export function Field({ label, hint, children, full, className }: {
  label: string; hint?: ReactNode; children: (id: string) => ReactNode; full?: boolean; className?: string;
}) {
  const id = useId();
  return (
    <div className={cx(full && "sm:col-span-full", "min-w-0", className)}>
      <label htmlFor={id} className="label">{label}</label>
      {children(id)}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function Segmented<T extends string | number>({ value, onChange, options, label }: {
  value: T; onChange: (v: T) => void; options: [T, string][]; label: string;
}) {
  return (
    <div className="flex shrink-0 rounded-lg bg-chip p-[3px]" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={String(v)} type="button" aria-pressed={value === v} onClick={() => onChange(v)}
          className={cx("rounded-md px-3 py-1.5 text-[11px] font-bold whitespace-nowrap",
            value === v ? "bg-card text-teal shadow-[0_1px_4px_#173b3d16]" : "text-soft")}>{text}</button>
      ))}
    </div>
  );
}

/** Turn a form element into a plain object of strings. */
export function formObject(form: HTMLFormElement): Record<string, string> {
  const out: Record<string, string> = {};
  new FormData(form).forEach((v, k) => {
    if (typeof v === "string") out[k] = v;
  });
  return out;
}

/**
 * Run a change against the server: shows the server's message as a toast, refreshes every
 * screen's data, and keeps the button disabled while it runs.
 */
export function useAction<TVars, TRes extends { message?: string } = { message?: string }>(
  fn: (v: TVars) => Promise<TRes>, opts: { onDone?: (r: TRes) => void; quiet?: boolean } = {},
) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: async (r) => {
      if (r?.message && !opts.quiet) toast(r.message);
      await qc.invalidateQueries();
      opts.onDone?.(r);
    },
    onError: (e) => toast(e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong.", "err"),
  });
}

export function Modal({ open, onClose, title, children, wide }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    setTimeout(() => ref.current?.querySelector<HTMLElement>("input,select,textarea,button")?.focus(), 30);
    return () => {
      window.removeEventListener("keydown", key);
      prev?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="no-print fixed inset-0 z-[100] flex items-end justify-center bg-[#04161ab3] p-0 sm:items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title}
        className={cx("card max-h-[92vh] w-full overflow-auto rounded-b-none p-5 sm:rounded-[14px] sm:p-6", wide ? "sm:max-w-3xl" : "sm:max-w-lg")}>
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="text-lg font-extrabold">{title}</h2>
          <button type="button" className="grid size-8 place-items-center rounded-lg text-muted hover:bg-chip" aria-label="Close" onClick={onClose}>
            <Icon name="x" className="size-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Confirm({ text, onYes, children, danger, label = "Yes" }: {
  text: string; onYes: () => void; children: (open: () => void) => ReactNode; danger?: boolean; label?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {children(() => setOpen(true))}
      <Modal open={open} onClose={() => setOpen(false)} title="Please confirm">
        <p className="mb-5 text-sm leading-relaxed text-soft">{text}</p>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
          <button type="button" className={cx("btn", danger ? "btn-danger" : "btn-primary")} onClick={() => { setOpen(false); onYes(); }}>{label}</button>
        </div>
      </Modal>
    </>
  );
}

export function FormFoot({ children }: { children: ReactNode }) {
  return <div className="mt-5 flex flex-wrap justify-end gap-2 border-t border-line pt-4">{children}</div>;
}

export function Submit({ busy, children, className }: { busy?: boolean; children: ReactNode; className?: string }) {
  return <button type="submit" className={cx("btn btn-primary", className)} disabled={busy}>{busy ? "Saving…" : children}</button>;
}

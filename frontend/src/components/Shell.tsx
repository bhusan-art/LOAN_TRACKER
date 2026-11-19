import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { Alert, cx, Field, FormFoot, Modal, Submit, useAction, formObject } from "./ui";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { dmy, inr } from "../lib/format";
import type { LoanBrief } from "../lib/types";

type NavItem = { to: string; label: string; icon: string; badge?: number; owner?: boolean; end?: boolean };

/* --------------------------------------------------------------- record payment from anywhere */
const PayCtx = createContext<(loanId?: number) => void>(() => {});
export const useRecordPayment = () => useContext(PayCtx);

function RecordPayment({ open, loanId, onClose }: { open: boolean; loanId?: number; onClose: () => void }) {
  const { session } = useSession();
  const nav = useNavigate();
  const loans = useQuery({ queryKey: ["loans"], queryFn: () => api<LoanBrief[]>("/api/ui/loans"), enabled: open });
  const [sel, setSel] = useState<string>(loanId ? String(loanId) : "");
  useEffect(() => setSel(loanId ? String(loanId) : ""), [loanId, open]);
  const act = useAction((body: Record<string, string>) =>
    post<{ message: string; loan_id: number }>("/api/ui/quick", { ...body, loan_id: Number(body.loan_id) }), {
    onDone: (r) => { onClose(); nav(`/loans/${r.loan_id}`); },
  });
  const open_ = (loans.data ?? []).filter((l) => !["CLOSED", "SETTLED", "WRITTEN_OFF"].includes(l.status));
  const chosen = open_.find((l) => String(l.id) === sel);
  return (
    <Modal open={open} onClose={onClose} title="Record payment">
      <form onSubmit={(e) => { e.preventDefault(); act.mutate(formObject(e.currentTarget)); }}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Loan *" full>{(id) => (
            <select id={id} name="loan_id" className="input" required value={sel} onChange={(e) => setSel(e.target.value)}>
              <option value="">Choose a loan…</option>
              <optgroup label="Money in (people who owe you)">
                {open_.filter((l) => l.direction === "GIVEN").map((l) => (
                  <option key={l.id} value={l.id}>{l.party_name} · LN-{l.id} · {inr(l.principal_outstanding_paise, false)} left</option>))}
              </optgroup>
              <optgroup label="Money out (people you owe)">
                {open_.filter((l) => l.direction === "TAKEN").map((l) => (
                  <option key={l.id} value={l.id}>{l.party_name} · LN-{l.id} · {inr(l.principal_outstanding_paise, false)} left</option>))}
              </optgroup>
            </select>)}
          </Field>
          {chosen && (
            <div className="sm:col-span-full">
              <Alert tone="teal">
                {chosen.direction === "GIVEN" ? "Received from" : "Paid to"} <b>{chosen.party_name}</b>. Interest due now{" "}
                <b>{inr(chosen.interest_outstanding_paise + chosen.penalty_outstanding_paise)}</b>; payoff today{" "}
                <b>{inr(chosen.payoff_today_paise)}</b>. The payment clears penalty first, then the oldest interest, then principal.
              </Alert>
            </div>
          )}
          <Field label="Amount (₹) *">{(id) => <input id={id} name="amount" className="input num" inputMode="decimal" required placeholder="e.g. 2,000" />}</Field>
          <Field label="Date">{(id) => <input id={id} name="entry_date" type="date" className="input" defaultValue={session?.today} max={session?.today} />}</Field>
          <Field label="Mode">{(id) => (
            <select id={id} name="payment_mode" className="input">
              <option value="CASH">Cash</option><option value="UPI">UPI</option><option value="BANK">Bank transfer</option><option value="CHEQUE">Cheque</option>
            </select>)}
          </Field>
          <Field label="Reference">{(id) => <input id={id} name="reference" className="input" maxLength={200} placeholder="UTR / cheque no." />}</Field>
        </div>
        <FormFoot>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <Submit busy={act.isPending}>Save payment</Submit>
        </FormFoot>
      </form>
    </Modal>
  );
}

/* --------------------------------------------------------------- search (Ctrl K) */
type SearchRes = {
  people: { id: number; name: string; phone: string | null }[];
  loans: { id: number; party_name: string; direction: string; principal_paise: number; status: string }[];
  receipts: { id: number; number: string; party_name: string; amount_paise: number; payment_date: string }[];
};

function Search() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [debounced, setDebounced] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  const nav = useNavigate();
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 180);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
        setTimeout(() => ref.current?.focus(), 10);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const res = useQuery({ queryKey: ["search", debounced], queryFn: () => api<SearchRes>(`/api/search?q=${encodeURIComponent(debounced)}`),
    enabled: debounced.length >= 2 });
  const go = (to: string) => { setOpen(false); setQ(""); nav(to); };
  const items: { to: string; title: string; sub: string; icon: string }[] = [
    ...(res.data?.people ?? []).map((p) => ({ to: `/people/${p.id}`, title: p.name, sub: p.phone ?? "Person", icon: "user" })),
    ...(res.data?.loans ?? []).map((l) => ({ to: `/loans/${l.id}`, title: `LN-${l.id} · ${l.party_name}`,
      sub: `${l.direction === "GIVEN" ? "Lent" : "Borrowed"} ${inr(l.principal_paise, false)}`, icon: "wallet" })),
    ...(res.data?.receipts ?? []).map((r) => ({ to: `/receipts/${r.id}`, title: r.number,
      sub: `${r.party_name} · ${inr(r.amount_paise)} · ${dmy(r.payment_date)}`, icon: "receipt" })),
  ];
  return (
    <>
      <button type="button" onClick={() => { setOpen(true); setTimeout(() => ref.current?.focus(), 10); }}
        className="flex h-[39px] items-center gap-2 rounded-[9px] border border-line px-3 text-xs text-muted md:w-[320px]"
        aria-label="Search loans, people, receipts">
        <Icon name="search" className="size-4" />
        <span className="hidden flex-1 text-left md:inline">Search loans, people, receipts…</span>
        <kbd className="hidden rounded border border-line bg-field px-1.5 py-0.5 text-[10px] md:inline">Ctrl K</kbd>
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title="Search">
        <input ref={ref} value={q} onChange={(e) => setQ(e.target.value)} className="input" placeholder="Name, phone, LN-12, RCPT-2026-0004…"
          aria-label="Search text"
          onKeyDown={(e) => { if (e.key === "Enter" && items[0]) go(items[0].to); }} />
        <div className="mt-3 max-h-[50vh] overflow-auto">
          {debounced.length < 2 && <p className="py-6 text-center text-xs text-muted">Type at least two letters.</p>}
          {debounced.length >= 2 && res.isSuccess && !items.length && <p className="py-6 text-center text-xs text-muted">Nothing found for “{debounced}”.</p>}
          {items.map((it) => (
            <button key={it.to} type="button" onClick={() => go(it.to)}
              className="flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left hover:bg-chip">
              <span className="grid size-8 place-items-center rounded-lg bg-tealsoft text-teal"><Icon name={it.icon} className="size-4" /></span>
              <span className="min-w-0"><b className="block truncate text-[13px]">{it.title}</b><small className="block truncate text-[11px] text-muted">{it.sub}</small></span>
            </button>
          ))}
        </div>
      </Modal>
    </>
  );
}

/* --------------------------------------------------------------- sidebar */
function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { session, canEdit } = useSession();
  const pay = useRecordPayment();
  const qc = useQueryClient();
  const nav = useNavigate();
  const groups: [string, NavItem[]][] = [
    ["Workspace", [
      { to: "/", label: "Overview", icon: "home", end: true },
      { to: "/loans", label: "Loans", icon: "wallet", badge: session?.nav_overdue || undefined },
      { to: "/collections", label: "Collections", icon: "inbox" },
      { to: "/payments", label: "Payments", icon: "outbox" },
      { to: "/calendar", label: "Cash calendar", icon: "calendar" },
      { to: "/people", label: "People", icon: "people" },
      { to: "/documents", label: "Documents", icon: "file", owner: true },
      { to: "/receipts", label: "Receipts", icon: "receipt" },
    ]],
    ["Analyse", [
      { to: "/cash", label: "Cash & forecast", icon: "chart" },
      { to: "/simulate", label: "What-if simulator", icon: "spark" },
      { to: "/reports", label: "Reports", icon: "percent" },
      { to: "/funding", label: "Funding links", icon: "link" },
      { to: "/bank", label: "Bank import", icon: "bank" },
      { to: "/activity", label: "Activity log", icon: "activity" },
    ]],
  ];
  const logout = useAction(() => post("/api/logout"), { quiet: true, onDone: () => { qc.clear(); nav("/login"); } });
  return (
    <div className="flex h-full flex-col overflow-y-auto bg-gradient-to-b from-[#082e34] via-[#06474a] to-[#07353a] px-[15px] pt-6 pb-4 text-[#e9f4f1]">
      <Link to="/" onClick={onNavigate} className="flex items-center gap-[11px] px-2.5 pb-8">
        <span className="grid size-[38px] place-items-center rounded-xl bg-[#f3a64d] font-display text-[22px] font-extrabold text-[#103b3d]">L</span>
        <span><b className="block font-display text-xl font-extrabold tracking-[-.6px]">Ledgerly</b>
          <small className="block text-[10px] tracking-[1.1px] text-[#9db9b6]">FINANCIAL WORKSPACE</small></span>
      </Link>
      {groups.map(([title, items], gi) => (
        <div key={title}>
          {gi > 0 && <div className="mx-2 my-3.5 h-px bg-[#47716f]" />}
          <div className="px-3 pb-2.5 text-[10px] font-bold tracking-[1.5px] text-[#83aaa6] uppercase">{title}</div>
          <nav className="flex flex-col gap-0.5" aria-label={title}>
            {items.filter((i) => !i.owner || canEdit).map((i) => (
              <NavLink key={i.to} to={i.to} end={i.end} onClick={onNavigate}
                className={({ isActive }) => cx("flex items-center gap-[13px] rounded-[9px] px-3 py-[10px] text-[13px] font-semibold transition",
                  isActive ? "bg-[#0b6866] text-white shadow-[inset_3px_0_#f2a44c]" : "text-[#c5d9d6] hover:bg-[#0b6866]/60 hover:text-white")}>
                <Icon name={i.icon} className="size-[18px] text-[#a9c8c3]" />
                {i.label}
                {i.badge ? <em className="ml-auto rounded-full bg-[#f0a34b] px-2 py-0.5 text-[11px] font-bold text-[#173b3d] not-italic">{i.badge}</em> : null}
              </NavLink>
            ))}
          </nav>
        </div>
      ))}
      {canEdit && (
        <>
          <div className="mx-2 my-3.5 h-px bg-[#47716f]" />
          <div className="px-3 pb-2.5 text-[10px] font-bold tracking-[1.5px] text-[#83aaa6] uppercase">Quick actions</div>
          {[["/loans/new?direction=GIVEN", "Lend money", "up"], ["/loans/new?direction=TAKEN", "Borrow money", "down"]].map(([to, label, icon]) => (
            <Link key={to} to={to} onClick={onNavigate} className="flex items-center gap-[13px] rounded-[9px] px-3 py-[10px] text-[13px] font-semibold text-[#c5d9d6] hover:bg-[#0b6866]/60 hover:text-white">
              <Icon name={icon} className="size-[18px] text-[#a9c8c3]" />{label}
            </Link>
          ))}
          <button type="button" onClick={() => { onNavigate?.(); pay(); }}
            className="flex items-center gap-[13px] rounded-[9px] px-3 py-[10px] text-left text-[13px] font-semibold text-[#c5d9d6] hover:bg-[#0b6866]/60 hover:text-white">
            <Icon name="plus" className="size-[18px] text-[#a9c8c3]" />Record payment
          </button>
        </>
      )}
      <div className="mt-auto flex items-center gap-2.5 border-t border-[#47716f] px-1.5 pt-4">
        <span className="grid size-[34px] place-items-center rounded-full bg-[#d7f2e9] font-extrabold text-[#087766] uppercase">{session?.user?.slice(0, 2)}</span>
        <span className="min-w-0 flex-1"><b className="block truncate text-xs">{session?.user}</b>
          <small className="block text-[10px] text-[#9db9b6]">{session?.role === "OWNER" ? "Owner · full access" : "Viewer · read only"}</small></span>
        <button type="button" onClick={() => logout.mutate(undefined)} className="grid size-8 place-items-center rounded-lg text-[#a9c8c3] hover:bg-[#0b6866]" aria-label="Log out" title="Log out">
          <Icon name="logout" className="size-4" />
        </button>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- layout */
const TITLES: [RegExp, string][] = [
  [/^\/$/, "Overview"], [/^\/loans\/new/, "New loan"], [/^\/loans\/\d+\/statement/, "Statement"], [/^\/loans\/\d+\/closure/, "No-dues certificate"],
  [/^\/loans\/\d+/, "Loan"], [/^\/loans/, "Loans"], [/^\/collections/, "Collections"], [/^\/payments/, "Payments"],
  [/^\/calendar/, "Cash calendar"], [/^\/people\/\d+/, "Person"], [/^\/people/, "People"], [/^\/documents/, "Documents"],
  [/^\/receipts\/\d+/, "Receipt"], [/^\/receipts/, "Receipts"], [/^\/cash/, "Cash & forecast"], [/^\/simulate/, "What-if simulator"],
  [/^\/reports/, "Reports"], [/^\/funding/, "Funding links"], [/^\/bank\/imports/, "Bank statement"], [/^\/bank/, "Bank import"],
  [/^\/activity/, "Activity log"],
];

export function Shell({ children }: { children?: ReactNode }) {
  const { session, canEdit, dark, toggleDark } = useSession();
  const [drawer, setDrawer] = useState(false);
  const [pay, setPay] = useState<{ open: boolean; loanId?: number }>({ open: false });
  const loc = useLocation();
  const title = TITLES.find(([re]) => re.test(loc.pathname))?.[1] ?? "";
  useEffect(() => setDrawer(false), [loc.pathname]);
  useEffect(() => { document.title = title ? `${title} · Ledgerly` : "Ledgerly"; }, [title]);
  return (
    <PayCtx.Provider value={(loanId?: number) => setPay({ open: true, loanId })}>
      <div className="flex min-h-screen">
        <aside className="no-print fixed inset-y-0 left-0 z-30 hidden w-[244px] lg:block"><Sidebar /></aside>
        {drawer && (
          <div className="no-print fixed inset-0 z-50 lg:hidden">
            <div className="absolute inset-0 bg-[#04161ab3]" onClick={() => setDrawer(false)} />
            <aside className="absolute inset-y-0 left-0 w-[270px] max-w-[85vw] shadow-2xl"><Sidebar onNavigate={() => setDrawer(false)} /></aside>
          </div>
        )}
        <div className={cx("min-w-0 flex-1 lg:ml-[244px]", !canEdit && "viewer")}>
          <header className="no-print sticky top-0 z-20 flex h-[64px] items-center justify-between gap-3 border-b border-line bg-card px-3.5 sm:h-[70px] sm:px-9">
            <div className="flex min-w-0 items-center gap-2">
              <button type="button" className="grid size-[38px] place-items-center rounded-lg border border-line lg:hidden" aria-label="Open menu" onClick={() => setDrawer(true)}>
                <Icon name="menu" className="size-[18px]" />
              </button>
              <div className="truncate text-[13px] text-muted">
                <span className="hidden sm:inline">Workspace <span className="px-2.5 text-line">/</span></span>
                <b className="text-ink">{title}</b>
              </div>
            </div>
            <div className="flex items-center gap-2 sm:gap-4">
              <Search />
              <button type="button" onClick={toggleDark} className="grid size-[38px] place-items-center rounded-lg text-soft hover:bg-chip"
                aria-label={dark ? "Light mode" : "Dark mode"} title={dark ? "Light mode" : "Dark mode"}>
                <Icon name={dark ? "sun" : "moon"} className="size-[18px]" />
              </button>
              <div className="hidden items-center gap-2.5 sm:flex">
                <span className="grid size-[34px] place-items-center rounded-full bg-tealsoft text-xs font-extrabold text-teal uppercase">{session?.user?.slice(0, 2)}</span>
                <span className="text-xs leading-tight"><b className="block">{session?.user}</b><small className="text-[10px] text-muted">{session?.role === "OWNER" ? "Owner" : "Viewer"}</small></span>
              </div>
            </div>
          </header>
          {!canEdit && session?.user && (
            <div className="no-print border-b border-line bg-ambersoft px-4 py-2 text-center text-xs font-semibold text-[#9a5b14] dark:text-amber">
              Read-only access: you can look at everything, but only the owner can make changes.
            </div>
          )}
          {session?.insecure_secret && (
            <div className="no-print bg-redsoft px-4 py-2 text-center text-xs font-semibold text-redink">
              SECRET_KEY is weak or a default. Set a long random value in .env before using this for real money.
            </div>
          )}
          <main className="mx-auto max-w-[1600px] px-3.5 pt-6 pb-16 sm:px-[38px] sm:pt-[34px]">{children ?? <Outlet />}</main>
        </div>
      </div>
      <RecordPayment open={pay.open} loanId={pay.loanId} onClose={() => setPay({ open: false })} />
    </PayCtx.Provider>
  );
}

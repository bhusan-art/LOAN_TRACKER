import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Toast = { id: number; text: string; kind: "ok" | "err" };
const ToastCtx = createContext<(text: string, kind?: "ok" | "err") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, kind }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "err" ? 9000 : 6000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="no-print fixed inset-x-3 bottom-4 z-[200] flex flex-col items-end gap-2 sm:inset-x-auto sm:right-6"
        role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id}
            className={`flex max-w-md items-start gap-3 rounded-xl px-4 py-3 text-[13px] font-semibold shadow-xl ${
              t.kind === "err" ? "bg-redink text-white" : "bg-deep text-white"}`}>
            <span className="flex-1">{t.text}</span>
            <button aria-label="Close" className="opacity-70 hover:opacity-100"
              onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}>×</button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

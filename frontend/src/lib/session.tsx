import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, setBookCookie, setBookHeader, setCsrf } from "./api";
import { isNative } from "./native";
import type { SessionInfo } from "./types";

type Ctx = {
  session: SessionInfo | undefined;
  loading: boolean;
  book: string;
  setBook: (b: string) => void;
  canEdit: boolean;
  dark: boolean;
  toggleDark: () => void;
  refresh: () => Promise<unknown>;
};

const SessionCtx = createContext<Ctx | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["session"],
    queryFn: async () => {
      const s = await api<SessionInfo>("/api/session");
      setCsrf(s.csrf);
      return s;
    },
    staleTime: 60_000,
  });
  const [book, setBookState] = useState(() => {
    if (!isNative) return "all";
    try {
      const b = localStorage.getItem("book") ?? "all";
      setBookHeader(b);
      return b;
    } catch {
      return "all";
    }
  });
  const [dark, setDark] = useState(() => {
    try {
      return localStorage.getItem("theme") === "dark";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    if (q.data?.book && !isNative) setBookState(q.data.book);
  }, [q.data?.book]);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("theme", dark ? "dark" : "light");
    } catch {
      /* private mode: theme just isn't remembered */
    }
  }, [dark]);
  useEffect(() => {
    const out = () => qc.invalidateQueries({ queryKey: ["session"] });
    window.addEventListener("ledgerly:logged-out", out);
    return () => window.removeEventListener("ledgerly:logged-out", out);
  }, [qc]);

  const setBook = useCallback(
    (b: string) => {
      if (isNative) {
        setBookHeader(b);                    // the app sends no cookies: the book goes in a header
        try {
          localStorage.setItem("book", b);
        } catch {
          /* not remembered */
        }
      } else setBookCookie(b);
      setBookState(b);
      qc.invalidateQueries({ predicate: (x) => x.queryKey[0] !== "session" });
    },
    [qc],
  );

  const value: Ctx = {
    session: q.data,
    loading: q.isLoading,
    book,
    setBook,
    canEdit: q.data?.role === "OWNER",
    dark,
    toggleDark: () => setDark((d) => !d),
    refresh: () => q.refetch(),
  };
  return <SessionCtx.Provider value={value}>{children}</SessionCtx.Provider>;
}

export function useSession(): Ctx {
  const c = useContext(SessionCtx);
  if (!c) throw new Error("useSession outside SessionProvider");
  return c;
}

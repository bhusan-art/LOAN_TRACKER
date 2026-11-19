import { StrictMode, Suspense, lazy, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider, useLocation, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import { SessionProvider, useSession } from "./lib/session";
import { ToastProvider } from "./lib/toast";
import { Shell } from "./components/Shell";
import { Loading } from "./components/ui";
import { ApiError } from "./lib/api";
import Login from "./pages/Login";
import Overview from "./pages/Overview";

const Loans = lazy(() => import("./pages/Loans"));
const LoanNew = lazy(() => import("./pages/LoanNew"));
const LoanPage = lazy(() => import("./pages/LoanPage"));
const Statement = lazy(() => import("./pages/Statement"));
const Closure = lazy(() => import("./pages/Closure"));
const Collections = lazy(() => import("./pages/Collections"));
const Payments = lazy(() => import("./pages/Payments"));
const CalendarPage = lazy(() => import("./pages/Calendar"));
const People = lazy(() => import("./pages/People"));
const Person = lazy(() => import("./pages/Person"));
const Documents = lazy(() => import("./pages/Documents"));
const Receipts = lazy(() => import("./pages/Receipts"));
const ReceiptPage = lazy(() => import("./pages/ReceiptPage"));
const Cash = lazy(() => import("./pages/Cash"));
const Simulate = lazy(() => import("./pages/Simulate"));
const Reports = lazy(() => import("./pages/Reports"));
const Funding = lazy(() => import("./pages/Funding"));
const Bank = lazy(() => import("./pages/Bank"));
const BankImportPage = lazy(() => import("./pages/BankImport"));
const Activity = lazy(() => import("./pages/Activity"));
const PublicStatement = lazy(() => import("./pages/PublicStatement"));
const PublicReceipt = lazy(() => import("./pages/PublicReceipt"));
const NotFound = lazy(() => import("./pages/NotFound"));

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: true,
      retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
    },
  },
});

function RequireLogin() {
  const { session, loading } = useSession();
  const loc = useLocation();
  if (loading) return <Loading />;
  if (!session?.user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <Shell />;
}

const page = (el: ReactNode) => <Suspense fallback={<Loading />}>{el}</Suspense>;

function PartyRedirect() {
  const { id } = useParams();
  return <Navigate to={`/people/${id}`} replace />;
}

const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { path: "/s/:token", element: page(<PublicStatement />) },
  { path: "/s/:token/receipts/:receiptId", element: page(<PublicReceipt />) },
  {
    element: <RequireLogin />,
    children: [
      { path: "/", element: <Overview /> },
      { path: "/loans", element: page(<Loans />) },
      { path: "/loans/new", element: page(<LoanNew />) },
      { path: "/loans/:id", element: page(<LoanPage />) },
      { path: "/loans/:id/statement", element: page(<Statement />) },
      { path: "/loans/:id/closure", element: page(<Closure />) },
      { path: "/collections", element: page(<Collections />) },
      { path: "/payments", element: page(<Payments />) },
      { path: "/calendar", element: page(<CalendarPage />) },
      { path: "/people", element: page(<People />) },
      { path: "/people/:id", element: page(<Person />) },
      { path: "/parties", element: <Navigate to="/people" replace /> },
      { path: "/parties/:id", element: <PartyRedirect /> },
      { path: "/documents", element: page(<Documents />) },
      { path: "/receipts", element: page(<Receipts />) },
      { path: "/receipts/:id", element: page(<ReceiptPage />) },
      { path: "/cash", element: page(<Cash />) },
      { path: "/simulate", element: page(<Simulate />) },
      { path: "/reports", element: page(<Reports />) },
      { path: "/funding", element: page(<Funding />) },
      { path: "/bank", element: page(<Bank />) },
      { path: "/bank/imports/:id", element: page(<BankImportPage />) },
      { path: "/activity", element: page(<Activity />) },
      { path: "/quick", element: <Navigate to="/payments?record=1" replace /> },
      { path: "*", element: page(<NotFound />) },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <SessionProvider>
          <RouterProvider router={router} />
        </SessionProvider>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);

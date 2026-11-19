import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ReceiptPage } from "../lib/types";
import { PrintButton } from "../components/FileButton";
import { Loading } from "../components/ui";
import { ReceiptDoc } from "../components/ReceiptDoc";
import { PublicError, PublicFrame } from "./PublicStatement";

export default function PublicReceipt() {
  const { token, receiptId } = useParams();
  const q = useQuery({ queryKey: ["public-receipt", token, receiptId], queryFn: () => api<ReceiptPage>(`/api/public/s/${token}/receipts/${receiptId}`), retry: false });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <PublicError error={q.error} />;
  return (
    <PublicFrame issuer={q.data.issuer}>
      <div className="no-print mb-4 flex justify-between gap-2">
        <Link className="btn" to={`/s/${token}`}>← Back to statement</Link>
        <PrintButton />
      </div>
      <ReceiptDoc r={q.data} />
    </PublicFrame>
  );
}

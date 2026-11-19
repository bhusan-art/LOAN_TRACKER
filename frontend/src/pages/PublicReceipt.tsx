import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ReceiptPage } from "../lib/types";
import { Icon } from "../components/Icon";
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
        <button type="button" className="btn btn-primary" onClick={() => window.print()}><Icon name="print" className="size-4" />Print / save PDF</button>
      </div>
      <ReceiptDoc r={q.data} />
    </PublicFrame>
  );
}

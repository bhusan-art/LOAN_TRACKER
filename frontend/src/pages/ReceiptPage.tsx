import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ReceiptPage as RP } from "../lib/types";
import { Icon } from "../components/Icon";
import { ErrorBox, Loading, PageHead } from "../components/ui";
import { ReceiptDoc } from "../components/ReceiptDoc";

export default function ReceiptPage() {
  const { id } = useParams();
  const r = useQuery({ queryKey: ["receipt", id], queryFn: () => api<RP>(`/api/ui/receipts/${id}`) });
  if (r.isLoading) return <Loading />;
  if (r.error || !r.data) return <ErrorBox error={r.error ?? "Not found"} />;
  const x = r.data;
  return (
    <>
      <div className="no-print">
        <PageHead eyebrow="Receipt" title={x.number} scope={false} sub={<>For <Link className="link text-[13px]" to={`/loans/${x.loan_id}`}>LN-{x.loan_id} · {x.party}</Link></>}
          actions={<>
            <button type="button" className="btn btn-primary" onClick={() => window.print()}><Icon name="print" className="size-4" />Print / save PDF</button>
            {x.whatsapp && <a className="btn" href={x.whatsapp} target="_blank" rel="noopener"><Icon name="chat" className="size-4" />Send on WhatsApp</a>}
          </>} />
      </div>
      <ReceiptDoc r={x} />
    </>
  );
}

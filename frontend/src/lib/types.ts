// Shapes of the server's JSON (loan_tracker/web/views.py). All money is integer paise.

export type Direction = "GIVEN" | "TAKEN";
export type Book = "BUSINESS" | "PERSONAL";

export interface SessionInfo {
  csrf: string;
  user: string | null;
  role?: "OWNER" | "VIEWER";
  today: string;
  book: string;
  timezone: string;
  nav_overdue?: number;
  insecure_secret?: boolean;
  documents_ok?: boolean;
  documents_problem?: string | null;
  public_url?: string | null;
}

export interface Message {
  message: string;
}

export interface LoanBrief {
  id: number;
  party_id: number;
  party_name: string;
  phone: string | null;
  direction: Direction;
  book: Book;
  status: string;
  principal_paise: number;
  rate: string;
  rate_unit: string;
  interest_cycle: string;
  start_date: string;
  due_day: number;
  maturity_date: string | null;
  principal_outstanding_paise: number;
  interest_outstanding_paise: number;
  penalty_outstanding_paise: number;
  payoff_today_paise: number;
  credit_paise: number;
  overdue_paise: number;
  overdue_days: number;
  overdue_count: number;
  next_due_date: string | null;
  next_due_paise: number;
}

export interface Loan {
  id: number;
  party_id: number;
  party_name: string;
  party_phone?: string | null;
  direction: Direction;
  book: Book;
  principal_paise: number;
  start_date: string;
  rate: string;
  rate_unit: string;
  interest_type: string;
  interest_cycle: string;
  due_day: number;
  grace_days: number;
  penalty_rule: string | null;
  tenure_months: number | null;
  maturity_date: string | null;
  collateral: string | null;
  purpose: string | null;
  status: string;
  created_at: string;
}

export interface Party {
  id: number;
  name: string;
  phone: string | null;
  address: string | null;
  id_proof_ref: string | null;
  guarantor: string | null;
  notes: string | null;
  risk_tag: string | null;
  created_at: string;
}

export interface TodayItem {
  loan_id: number;
  party: string;
  book: Book;
  direction: Direction;
  due_date: string;
  amount_paise: number;
  status: string;
  overdue_days: number;
  count: number;
}

export interface Trio {
  month: number;
  fy: number;
  lifetime: number;
}

export interface Home {
  as_of: string;
  lent_out_paise: number;
  borrowed_paise: number;
  own_capital_deployed_paise: number;
  interest_earned: Trio;
  interest_cost: Trio;
  net_interest_income: Trio;
  collected_paise: number;
  pending_paise: number;
  overdue_count: number;
  overdue_paise: number;
  aging: Record<"0_30" | "31_60" | "61_90" | "90_plus", number>;
  effective_yield_bps: number;
  idle_cost_paise_per_cycle: number;
  concentration: { name: string; amount_paise: number; share_pct: number; flagged: boolean }[];
  pay_soon: TodayItem[];
  collect_soon: TodayItem[];
  warning_text: string[];
  active_loans: number;
  trend: { label: string; earned_paise: number; cost_paise: number }[];
  status_counts: Record<string, number>;
  previous: { lent_out_paise: number; borrowed_paise: number; own_capital_deployed_paise: number;
    net_interest_month_paise: number; as_of: string };
  last_job: { run_date: string; ok: boolean | null; summary: string | null } | null;
}

export interface PartyRow {
  id: number;
  name: string;
  phone: string | null;
  loan_count: number;
  net_position_paise: number;
  lent_paise: number;
  taken_paise: number;
  overdue_paise?: number;
  paid_on_time?: boolean;
}

export interface Doc {
  id: number;
  party_id: number | null;
  party_name: string | null;
  loan_id: number | null;
  kind: string;
  kind_label: string;
  title: string;
  file_name: string;
  content_type: string;
  size_bytes: number;
  uploaded_by: string;
  created_at: string;
}

export interface PartyPage {
  party: Party;
  loans: LoanBrief[];
  summary: { lent_paise: number; taken_paise: number; net_paise: number; overdue_paise: number;
    open_loans: number; loans: number };
  documents: Doc[] | null;
}

export interface Allocation {
  bucket: string;
  amount_paise: number;
  due_period_start: string | null;
}

export interface Entry {
  id: number;
  loan_id: number;
  entry_date: string;
  type: string;
  amount_paise: number | null;
  payment_mode: string;
  reference: string | null;
  note: string | null;
  new_rate: string | null;
  reverses_id: number | null;
  created_at: string;
  words: string;
  reversed: boolean;
  allocations: Allocation[];
  receipt: { id: number; number: string; cancelled: boolean } | null;
}

export interface DueRow {
  period_start: string;
  period_end: string;
  due_date: string;
  interest_paise: number;
  paid_paise: number;
  waived_paise: number;
  penalty_paise: number;
  status: string;
  outstanding_paise: number;
  overdue_days: number;
}

export interface Balances {
  principal_outstanding_paise: number;
  interest_accrued_paise: number;
  interest_paid_paise: number;
  interest_waived_paise: number;
  interest_outstanding_paise: number;
  open_period_paise: number;
  penalty_outstanding_paise: number;
  credit_paise: number;
  payoff_today_paise: number;
}

export interface Contact {
  id: number;
  contact_date: string;
  kind: string;
  outcome: string;
  note: string | null;
  next_follow_up: string | null;
  actor: string;
}

export interface Promise_ {
  id: number;
  made_on: string;
  promised_date: string;
  amount_paise: number;
  cancelled_at: string | null;
  cancel_note: string | null;
  state: { status: string; paid_paise?: number; short_paise?: number; [k: string]: unknown };
}

export interface ShareLink {
  id: number;
  created_by: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  view_count: number;
  last_viewed_at: string | null;
  live: boolean;
}

export interface LoanPage {
  loan: Loan;
  brief: LoanBrief;
  status: string;
  balances: Balances;
  projected_due: { due_date: string; interest_paise: number; period_start: string; period_end: string } | null;
  dues: DueRow[];
  entries: Entry[];
  links: { id: number; amount_paise: number; link_date: string; other_loan_id: number; other_party: string }[];
  contacts: Contact[];
  promises: Promise_[];
  whatsapp: string | null;
  chasing: boolean;
  can_close: boolean;
  missing_receipts: number;
  shares: ShareLink[];
  documents: Doc[] | null;
}

export interface ChaseItem {
  loan_id: number;
  party: string;
  book: Book;
  phone: string | null;
  overdue_paise: number;
  reason: string;
  detail: string;
  on_date: string | null;
  amount_paise: number;
  short_paise: number;
  wa_url: string | null;
}

export interface CollectionsPage {
  as_of: string;
  follow_ups: ChaseItem[];
  broken: ChaseItem[];
  promises_due: ChaseItem[];
  not_contacted: ChaseItem[];
}

export interface PaymentRow {
  id: number;
  loan_id: number;
  party_id: number;
  party_name: string;
  direction: "IN" | "OUT";
  book: Book;
  entry_date: string;
  amount_paise: number;
  payment_mode: string;
  reference: string | null;
  note: string | null;
  reversed: boolean;
  split: Record<string, number> | null;
  receipt: { id: number; number: string } | null;
}

export interface CalendarDay {
  day: string;
  money_in: TodayItem[];
  money_out: TodayItem[];
  net_paise: number;
  running_paise: number;
}

export interface CalendarPage {
  days: number;
  today: string;
  calendar: CalendarDay[];
  opening_paise: number;
  running: { day: string; running_paise: number }[];
  totals: { in_paise: number; out_paise: number; net_paise: number; low_paise: number; low_day: string | null };
}

export interface CashAccount {
  id: number;
  name: string;
  kind: string;
  book: Book;
  is_active: boolean;
  created_at: string;
}

export interface ForecastLine {
  short_date: string | null;
  short_paise: number;
  low_paise: number;
  low_date: string | null;
  days: { day: string; running_paise: number }[];
}

export interface CashPage {
  forecast: {
    as_of: string;
    days: number;
    opening_paise: number;
    buffer_paise: number;
    accounts: { account: CashAccount; balance: { as_of: string; balance_paise: number; note: string | null } | null }[];
    calendar: CalendarDay[];
    expected: ForecastLine;
    cautious: ForecastLine;
  };
  warnings: string[];
  planned: { id: number; book: Book; item_date: string; direction: "IN" | "OUT"; amount_paise: number;
    description: string; cancelled_at: string | null }[];
  closed_accounts: CashAccount[];
  today: string;
}

export interface FundingPage {
  links: { id: number; taken_loan_id: number; given_loan_id: number; amount_paise: number; link_date: string;
    spread: { spread_paise_per_cycle: number; negative: boolean; cross_book: boolean; exposure_paise: number };
    taken: LoanBrief; given: LoanBrief }[];
  idle_cost_paise_per_cycle: number;
  taken: (LoanBrief & { unlinked_paise: number })[];
  given: (LoanBrief & { unfunded_paise: number })[];
}

export interface AuditRow {
  id: number;
  at: string;
  actor: string;
  action: string;
  entity: string | null;
  entity_id: number | null;
  detail: Record<string, unknown>;
  ip: string | null;
  label: string;
  tone: string;
}

export interface Receipt {
  id: number;
  number: string;
  loan_id: number;
  party: string;
  ledger_entry_id: number;
  issued_on: string;
  payment_date: string;
  amount_paise: number;
  penalty_paise: number;
  interest_paise: number;
  principal_paise: number;
  credit_paise: number;
  principal_left_paise: number;
  interest_left_paise: number;
  payment_mode: string | null;
  reference: string | null;
  cancelled: boolean;
  cancel_reason: string | null;
}

export interface Issuer {
  name: string;
  details: string;
}

export interface ReceiptPage extends Receipt {
  amount_in_words: string;
  issuer: Issuer;
  party_phone?: string | null;
  loan_book?: Book;
  whatsapp?: string | null;
  expires_at?: string;
}

export interface BankTxn {
  id: number;
  import_id: number;
  line_no: number;
  txn_date: string;
  description: string;
  reference: string | null;
  amount_paise: number;
  balance_paise: number | null;
  status: "NEW" | "LINKED" | "RECORDED" | "IGNORED";
  ledger_entry_id: number | null;
  note: string | null;
  suggestions: { kind: "EXISTING" | "NEW_PAYMENT"; loan_id: number; entry_id: number | null; score: number;
    strong: boolean; reason: string; party_name: string | null }[];
  entry_loan_id: number | null;
}

export interface BankImport {
  id: number;
  account_id: number;
  account: string;
  file_name: string;
  actor: string;
  row_count: number;
  new_count: number;
  period_start: string | null;
  period_end: string | null;
  created_at: string;
  open?: number;
}

export interface SimResult {
  mode: "loan" | "new";
  result: any;
}

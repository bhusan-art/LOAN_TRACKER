# 06 — Features: notifications, dashboard, screens, reports

All views respect the book toggle (All / Business / Personal).

## Notifications
Daily job (~08:00 IST) sends ONE digest to the owner: pay -> collect -> warnings, each line tagged [BIZ]/[PER].
Urgent items (payable due today/overdue) may also go as separate alerts.

| Trigger | Direction | Example | Mutable? |
|---|---|---|---|
| 3 days before due (configurable) | Pay | "[BIZ] Pay Rs 1,500 interest to Suresh by 5 Dec" | Yes |
| On due date | Pay | "Today: Rs 1,500 to Suresh, Rs 2,000 to Mahesh" | Yes |
| Each day after due | Pay | "OVERDUE: 2 days late paying Suresh Rs 1,500" | No |
| 3 days before due | Collect | "[PER] Ravi's Rs 2,500 due on 10 Dec" | Yes |
| After due + grace | Collect | "Ravi overdue by 5 days, Rs 2,500" | Yes |
| Any time | Warning | Mismatch / funding-risk alerts (04-logic 4.8) | No |
| 1st of month | Summary | Last month net interest income per book + next 30 days cash in/out | Yes |
| Maturity within 30 days | Both | "Loan from Suresh matures 1 Mar: Rs 1,00,000 due" | No |

Channels: Telegram bot (primary), email (backup). Optional borrower WhatsApp/SMS with consent.
Every send logged in reminder_log. Failed sends retried, then shown on dashboard.

## Dashboard metrics
| Metric | Definition |
|---|---|
| Total lent out | principal outstanding, GIVEN |
| Total borrowed | principal outstanding, TAKEN |
| Own capital deployed | lent out - part funded by funding_link |
| Interest earned | accrued on GIVEN: month, FY, lifetime |
| Interest cost | accrued on TAKEN: same periods |
| Net interest income | earned - cost |
| Collected vs pending | interest received vs outstanding, GIVEN |
| Overdue | count + amount by aging bucket, both directions |
| Effective yield | interest received / average capital deployed, annualised |
| Idle cost | interest on borrowed money not linked to any given loan |
| Concentration | exposure per party as % of total lent (flag > 25%) |

## Screens
1. Home: metrics, today's pay/collect list, warnings
2. Cash calendar (30/60/90 days): money in vs out per day, running balance, negative days highlighted
3. Parties: list + detail (all loans both directions, net position, on-time %)
4. Loan detail: terms, ledger, dues, allocations, payoff today, linked funding
5. Quick entry: record a payment in 3 taps (party -> loan -> amount; date defaults to today)
6. Reports: borrower statement (PDF/WhatsApp share), FY interest per book, overdue,
   cross-book funding, full CSV/Excel export

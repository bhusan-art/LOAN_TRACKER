# Decisions — Loan Tracker v1

1. **Interest styles:** Monthly simple interest only in v1. EMI and compound interest deferred to v2.

2. **Unpaid interest:** Unpaid interest remains pending and does not compound.

3. **Late penalty:** No late penalties in v1. Keep the schema extensible through `penalty_rule` for future implementation.

4. **Partial principal repayment:** Yes. Allow partial principal repayment on any date. Recalculate future interest from the effective repayment date based on the remaining principal.

5. **Funding mapping:** Maintain an exact `funding_link` for each loan. Pool-level funding views can be derived later.

6. **Users:** Owner-only access in v1. Multi-user support deferred to a future version.

7. **Default book:** A book must be explicitly selected when creating a new loan. Cross-book funding requires explicit confirmation.

8. **Rounding:** Round final due amounts to two decimal places (paisa) using the half-up rounding method. Avoid rounding intermediate calculations unnecessarily.

9. **Grace days:** Default grace period is 3 calendar days. The original due date remains unchanged.

10. **Day-count convention:** For PER_MONTH loans, use monthly flat interest. Prorate partial months using actual elapsed days divided by the actual number of calendar days in that month.
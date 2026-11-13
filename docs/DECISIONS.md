# Decisions (owner fills these in BEFORE Phase 1)

Replace each "DEFAULT" with your answer, or keep the default.

1. Interest styles in v1: DEFAULT = monthly simple only. EMI/compound in v2.
   Answer:
2. Unpaid interest: DEFAULT = stays pending (no compounding).
   Answer:
3. Late penalty: DEFAULT = none in v1; schema supports it (penalty_rule).
   Do people you borrow from charge penalties?
   Answer:
4. Partial principal repayment any day, interest recalculated from that day: DEFAULT = yes.
   Answer:
5. Funding mapping: DEFAULT = exact funding_link per loan (pool view can be derived later).
   Answer:
6. Users: DEFAULT = owner only in v1.
   Answer:
7. Default book for a new loan: DEFAULT = must choose (no default).
   Cross-book funding needs explicit confirmation: DEFAULT = yes.
   Answer:
8. Rounding of a due: DEFAULT = to the paisa (half-up). Alternative: whole rupee.
   Answer:
9. Grace days default: DEFAULT = 3.
   Answer:
10. Day-count default for PER_MONTH loans: DEFAULT = monthly flat, partial months pro-rated by
    actual days in that calendar month.
    Answer:

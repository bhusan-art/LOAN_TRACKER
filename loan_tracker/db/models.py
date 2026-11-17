"""SQLAlchemy 2 ORM models mirroring loan_tracker/db/sql/0001_initial.sql.

The SQL file (applied by Alembic) is the source of truth for the schema,
including triggers and CHECK constraints; these classes only map it.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def pg_enum(name: str, *values: str) -> ENUM:
    return ENUM(*values, name=name, create_type=False)


Direction = pg_enum("loan_direction", "GIVEN", "TAKEN")
BookEnum = pg_enum("loan_book", "BUSINESS", "PERSONAL")
LoanStatus = pg_enum("loan_status", "ACTIVE", "OVERDUE", "MATURED", "CLOSED", "SETTLED", "WRITTEN_OFF")
RateUnit = pg_enum("rate_unit", "PER_MONTH", "PER_YEAR")
InterestType = pg_enum("interest_type", "SIMPLE", "COMPOUND", "EMI")
InterestCycle = pg_enum("interest_cycle", "MONTHLY", "QUARTERLY", "AT_MATURITY")
EntryType = pg_enum("entry_type", "DISBURSE", "TOPUP", "PAYMENT", "WAIVER", "PENALTY",
                    "RATE_CHANGE", "CAPITALISE", "REVERSAL")
PaymentMode = pg_enum("payment_mode", "CASH", "UPI", "BANK")
Bucket = pg_enum("allocation_bucket", "PENALTY", "INTEREST", "PRINCIPAL")
ReminderKind = pg_enum("reminder_kind", "REMINDER", "OVERDUE", "PAY_ALERT")
Channel = pg_enum("channel_type", "TELEGRAM", "EMAIL")

ID = BigInteger


class Base(DeclarativeBase):
    pass


class Party(Base):
    __tablename__ = "party"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    id_proof_ref: Mapped[str | None] = mapped_column(Text)
    guarantor: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    risk_tag: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    loans: Mapped[list["Loan"]] = relationship(back_populates="party")


class Loan(Base):
    __tablename__ = "loan"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    party_id: Mapped[int] = mapped_column(ID, ForeignKey("party.id"))
    direction: Mapped[str] = mapped_column(Direction)
    book: Mapped[str] = mapped_column(BookEnum)
    principal_paise: Mapped[int] = mapped_column(ID)
    start_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    rate_unit: Mapped[str] = mapped_column(RateUnit)
    interest_type: Mapped[str] = mapped_column(InterestType)
    interest_cycle: Mapped[str] = mapped_column(InterestCycle, server_default="MONTHLY")
    due_day: Mapped[int] = mapped_column(SmallInteger)
    grace_days: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    penalty_rule: Mapped[str | None] = mapped_column(Text)
    tenure_months: Mapped[int | None] = mapped_column(Integer)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    collateral: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(LoanStatus, server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    party: Mapped[Party] = relationship(back_populates="loans")
    entries: Mapped[list["LedgerEntry"]] = relationship(
        back_populates="loan", order_by="(LedgerEntry.entry_date, LedgerEntry.id)")


class LedgerEntry(Base):
    __tablename__ = "ledger_entry"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    loan_id: Mapped[int] = mapped_column(ID, ForeignKey("loan.id"))
    entry_date: Mapped[date] = mapped_column(Date)
    type: Mapped[str] = mapped_column(EntryType)
    amount_paise: Mapped[int | None] = mapped_column(ID)
    payment_mode: Mapped[str] = mapped_column(PaymentMode, server_default="CASH")
    reference: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    new_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    reverses_id: Mapped[int | None] = mapped_column(ID, ForeignKey("ledger_entry.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    loan: Mapped[Loan] = relationship(back_populates="entries")


class Due(Base):
    """Cache of engine dues (rebuilt per loan by services.recompute_loan)."""
    __tablename__ = "due"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    loan_id: Mapped[int] = mapped_column(ID, ForeignKey("loan.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    interest_paise: Mapped[int] = mapped_column(ID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class Allocation(Base):
    """Cache of engine allocations (rebuilt per loan)."""
    __tablename__ = "allocation"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    ledger_entry_id: Mapped[int] = mapped_column(ID, ForeignKey("ledger_entry.id"))
    due_period_start: Mapped[date | None] = mapped_column(Date)
    bucket: Mapped[str] = mapped_column(Bucket)
    amount_paise: Mapped[int] = mapped_column(ID)


class FundingLink(Base):
    __tablename__ = "funding_link"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    taken_loan_id: Mapped[int] = mapped_column(ID, ForeignKey("loan.id"))
    given_loan_id: Mapped[int] = mapped_column(ID, ForeignKey("loan.id"))
    amount_paise: Mapped[int] = mapped_column(ID)
    link_date: Mapped[date] = mapped_column(Date)


class ReminderLog(Base):
    __tablename__ = "reminder_log"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    loan_id: Mapped[int] = mapped_column(ID, ForeignKey("loan.id"))
    due_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(ReminderKind)
    channel: Mapped[str] = mapped_column(Channel)
    sent_on: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    totp_secret: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class JobRun(Base):
    __tablename__ = "job_run"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    run_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)
    summary: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Append-only (DB trigger). Written only through services.audit()."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[int | None] = mapped_column(ID)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(Text)


class CollectionActivity(Base):
    """Append-only (DB trigger). Written only through services.log_contact()."""
    __tablename__ = "collection_activity"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    loan_id: Mapped[int] = mapped_column(ID, ForeignKey("loan.id"))
    contact_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    next_follow_up: Mapped[date | None] = mapped_column(Date)
    actor: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class PaymentPromise(Base):
    """Never money. Only change allowed: cancel once (DB trigger)."""
    __tablename__ = "payment_promise"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    loan_id: Mapped[int] = mapped_column(ID, ForeignKey("loan.id"))
    activity_id: Mapped[int | None] = mapped_column(ID, ForeignKey("collection_activity.id"))
    made_on: Mapped[date] = mapped_column(Date)
    promised_date: Mapped[date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(ID)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class Receipt(Base):
    """Snapshot document. Only change allowed: cancel once (DB trigger)."""
    __tablename__ = "receipt"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    number: Mapped[str] = mapped_column(Text)
    year: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    ledger_entry_id: Mapped[int] = mapped_column(ID, ForeignKey("ledger_entry.id"))
    loan_id: Mapped[int] = mapped_column(ID, ForeignKey("loan.id"))
    issued_on: Mapped[date] = mapped_column(Date)
    payment_date: Mapped[date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(ID)
    penalty_paise: Mapped[int] = mapped_column(ID, default=0)
    interest_paise: Mapped[int] = mapped_column(ID, default=0)
    principal_paise: Mapped[int] = mapped_column(ID, default=0)
    credit_paise: Mapped[int] = mapped_column(ID, default=0)
    principal_left_paise: Mapped[int] = mapped_column(ID)
    interest_left_paise: Mapped[int] = mapped_column(ID)
    payment_mode: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    loan: Mapped["Loan"] = relationship(lazy="joined")

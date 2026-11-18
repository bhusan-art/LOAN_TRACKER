-- Fix: interest periods include their end day, so a one-day period has
-- period_start = period_end. The old rule (start < end) rejected a loan that
-- starts the day before its due day, and a payoff the day after a due date.
ALTER TABLE due DROP CONSTRAINT due_check;
ALTER TABLE due ADD CONSTRAINT due_period_order CHECK (period_start <= period_end);

-- Execution audit details; applied foundation migrations remain unchanged.
ALTER TABLE app.backtest_runs ADD COLUMN result_state jsonb;
ALTER TABLE app.backtest_fills ADD COLUMN reason text;

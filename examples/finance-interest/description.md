# Bug: `compound_interest()` returns SIMPLE interest

`ledger.interest.compound_interest(principal, rate, years)` is documented to compound annually — each
year should earn `rate` on the running balance — but it applies `rate` only to the original principal,
returning the same value as `simple_interest`.

## Requirements
- `compound_interest(1000, 0.05, 2)` must return `1102.50` (1000 * 1.05**2), not `1100.00`.
- `simple_interest` must not regress: `simple_interest(1000, 0.05, 2)` stays `1100.00`.

## Interface
- Module: `ledger.interest`
- Function: `compound_interest(principal: float, rate: float, years: int) -> float`

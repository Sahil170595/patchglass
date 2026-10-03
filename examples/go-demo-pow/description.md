# Bug: `mathx.Pow` does repeated addition, not exponentiation

`example.com/mathx.Pow(base, exp)` is documented to return `base` raised to the `exp` power, but it
sums `base` `exp` times (repeated addition) instead of multiplying — so `Pow(2, 10)` returns `20`
rather than `1024`.

## Requirements
- `Pow(2, 10)` must return `1024`; `Pow(3, 4)` → `81`; `Pow(5, 0)` → `1`.
- The identity case must not regress: `Pow(b, 1) == b`.

## Interface
- Package: `mathx`
- Function: `func Pow(base, exp int) int`

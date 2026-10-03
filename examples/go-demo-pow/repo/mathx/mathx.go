// Package mathx provides small integer math helpers (stdlib-only, no external deps).
package mathx

// Pow returns base raised to a non-negative integer exponent.
//
// BUG (baseline): this returns base*exp (repeated addition) instead of base**exp
// (repeated multiplication). The golden patch fixes it to true exponentiation.
func Pow(base, exp int) int {
	result := 0
	for i := 0; i < exp; i++ {
		result += base
	}
	return result
}

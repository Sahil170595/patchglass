package mathx

import "testing"

// VISIBLE test: this stays in the solver's view (it is NOT a graded hidden test).
// Pow(b, 1) == b holds for both the buggy baseline and the patched source, so this
// test passes throughout and demonstrates that non-hidden tests are kept visible.
func TestPowIdentity(t *testing.T) {
	if got := Pow(7, 1); got != 7 {
		t.Fatalf("Pow(7, 1) = %d, want 7", got)
	}
	if got := Pow(0, 5); got != 0 {
		t.Fatalf("Pow(0, 5) = %d, want 0", got)
	}
}

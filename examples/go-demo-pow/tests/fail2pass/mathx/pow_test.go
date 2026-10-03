package mathx

import "testing"

// HIDDEN fail2pass: fails on the buggy baseline (Pow does repeated addition, so
// Pow(2,10)=20) and passes once the golden patch makes Pow true exponentiation
// (Pow(2,10)=1024). Staged into the package dir at grade time only.
func TestPow(t *testing.T) {
	cases := []struct {
		base, exp, want int
	}{
		{2, 10, 1024},
		{3, 4, 81},
		{5, 0, 1},
		{2, 3, 8},
	}
	for _, c := range cases {
		if got := Pow(c.base, c.exp); got != c.want {
			t.Errorf("Pow(%d, %d) = %d, want %d", c.base, c.exp, got, c.want)
		}
	}
}

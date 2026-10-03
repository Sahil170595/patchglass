# Bug: `normalize()` does not lower-case labels

`widget.core.normalize` is meant to trim whitespace **and** lower-case a label, but it only
trims: `normalize("HELLO")` returns `"HELLO"` instead of `"hello"`.

**Your task (test synthesis):** write a test that *reproduces* this bug — it must FAIL on the
current (buggy) code and PASS once the bug is fixed. Do not modify any non-test source file.

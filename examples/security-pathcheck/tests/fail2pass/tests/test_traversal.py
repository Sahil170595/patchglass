"""HIDDEN fail2pass: the buggy baseline lets '../' escape base_dir; passes once it is blocked."""

from safeio.paths import safe_join


def test_rejects_path_traversal():
    assert safe_join("/srv/data", "../../etc/passwd") is None
    assert safe_join("/srv/data", "../secret.txt") is None

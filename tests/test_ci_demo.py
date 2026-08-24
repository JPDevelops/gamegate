"""Throwaway demo test: shows the CI gate going red, then green.
Safe to delete after the demo."""


def test_ci_demo_math():
    # Now correct — CI turns GREEN and the merge unlocks.
    assert 1 + 1 == 2

"""Throwaway demo test: shows the CI gate going red, then green.
Safe to delete after the demo."""


def test_ci_demo_math():
    # Intentionally wrong to demonstrate a RED check that blocks merge.
    assert 1 + 1 == 3

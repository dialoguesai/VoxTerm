"""Tests for Dialogues PKCE helpers."""

from dialogues.pkce import create_pkce_pair


def test_pkce_challenge_is_s256_of_verifier():
    pair = create_pkce_pair()
    assert pair.code_challenge_method == "S256"
    assert len(pair.code_verifier) >= 43
    assert pair.code_challenge
    assert pair.code_challenge != pair.code_verifier

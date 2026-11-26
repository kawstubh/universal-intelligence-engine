"""Regression tests for UIE HTTP bearer authentication."""

import pytest
from fastapi import HTTPException

from universal_intelligence_engine.api import _authorize


@pytest.mark.parametrize("header", ["Bearer test-secret", "test-secret", "bEaReR test-secret"])
def test_authorize_accepts_bearer_and_raw_token(monkeypatch, header):
    monkeypatch.setenv("UIE_API_KEY", "test-secret")
    _authorize(header)


def test_authorize_accepts_environment_key_with_bearer_prefix(monkeypatch):
    monkeypatch.setenv("UIE_API_KEY", "Bearer test-secret")
    _authorize("Bearer test-secret")


@pytest.mark.parametrize(
    "header",
    [
        " bearer test-secret ",
        "BEARER test-secret",
        "\tBearer\ttest-secret\t",
        " test-secret ",
    ],
)
def test_authorize_normalizes_header_whitespace_and_scheme(monkeypatch, header):
    monkeypatch.setenv("UIE_API_KEY", "test-secret")
    _authorize(header)


def test_authorize_normalizes_environment_outer_whitespace(monkeypatch):
    monkeypatch.setenv("UIE_API_KEY", "  test-secret\n")
    _authorize("Bearer test-secret")


@pytest.mark.parametrize("header", [None, "", "Basic test-secret", "Bearer wrong", "Bearer test-secret extra"])
def test_authorize_rejects_invalid_credentials(monkeypatch, header):
    monkeypatch.setenv("UIE_API_KEY", "test-secret")
    with pytest.raises(HTTPException) as exc:
        _authorize(header)
    assert exc.value.status_code == 401


def test_authorize_fails_closed_when_key_is_missing_or_blank(monkeypatch):
    monkeypatch.setenv("UIE_API_KEY", "  \n")
    with pytest.raises(HTTPException) as exc:
        _authorize("Bearer test-secret")
    assert exc.value.status_code == 503

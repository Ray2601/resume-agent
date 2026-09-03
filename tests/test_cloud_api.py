"""Cloud API validation that does not call LLM or Neon."""

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.main import RunRequest, _authorize, health


def test_health():
    assert health() == {"status": "ok"}


def test_access_token_gate():
    with patch.dict(os.environ, {"APP_ACCESS_TOKEN": "secret"}):
        _authorize("secret")
        with pytest.raises(HTTPException) as exc:
            _authorize("wrong")
        assert exc.value.status_code == 401


def test_run_request_limits():
    request = RunRequest(jd="岗位要求" * 5, original_experience="真实经历" * 5)
    assert request.max_iterations == 3
    assert request.fabrication_tolerance == 0

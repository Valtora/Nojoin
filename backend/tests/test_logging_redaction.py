import logging

from backend.utils.logging_config import SensitiveDataFilter


def test_sensitive_data_filter_redacts_mapping_values() -> None:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Headers: %s",
        args=(
            {
                "Authorization": "Bootstrap bootstrap-secret",
                "Cookie": "access_token=session-secret",
                "User-Agent": "Nojoin Test",
                "X-First-Run-Password": "legacy-secret",
            },
        ),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record) is True
    headers = record.args if isinstance(record.args, dict) else record.args[0]
    assert headers["Authorization"] == "[REDACTED]"
    assert headers["Cookie"] == "[REDACTED]"
    assert headers["X-First-Run-Password"] == "[REDACTED]"
    assert headers["User-Agent"] == "Nojoin Test"


def test_sensitive_data_filter_redacts_secret_strings() -> None:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=30,
        msg=(
            'payload={"password":"super-secret","token":"hf-secret","api_key":"openai-secret"} '
            'authorization=Bootstrap bootstrap-secret'
        ),
        args=(),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record) is True
    assert "super-secret" not in record.msg
    assert "hf-secret" not in record.msg
    assert "openai-secret" not in record.msg
    assert "bootstrap-secret" not in record.msg
    assert "[REDACTED]" in record.msg
from __future__ import annotations

import pytest

from app.infrastructure.jobs import run_cli, run_observed_job
from tests.conftest import record_field


@pytest.mark.anyio
async def test_observed_job_logs_success(app_log_capture: pytest.LogCaptureFixture) -> None:
    job_name = "test_success_job"

    with app_log_capture.at_level("INFO"):
        result = await run_observed_job(job_name, lambda: _return_value("done"))

    assert result == "done"
    assert [
        record_field(record, "event")
        for record in app_log_capture.records
        if getattr(record, "job_name", None) == job_name
    ] == ["job_started", "job_completed"]
    completed = [
        record
        for record in app_log_capture.records
        if getattr(record, "event", None) == "job_completed"
    ][0]
    assert record_field(completed, "duration_ms") is not None


@pytest.mark.anyio
async def test_observed_job_logs_failure_and_propagates(
    app_log_capture: pytest.LogCaptureFixture,
) -> None:
    job_name = "test_failure_job"

    async def fail() -> None:
        raise RuntimeError("secret provider response")

    with (
        app_log_capture.at_level("ERROR"),
        pytest.raises(RuntimeError, match="secret provider response"),
    ):
        await run_observed_job(job_name, fail)

    failed = [
        record
        for record in app_log_capture.records
        if getattr(record, "event", None) == "job_failed"
    ]
    assert len(failed) == 1
    assert record_field(failed[0], "exception_type") == "RuntimeError"
    assert record_field(failed[0], "duration_ms") is not None
    assert "secret provider response" not in str(failed[0].__dict__)


def test_run_cli_returns_nonzero_without_printing_raw_failure() -> None:
    async def fail() -> None:
        raise RuntimeError("secret provider response")

    with pytest.raises(SystemExit) as caught:
        run_cli(fail())

    assert caught.value.code == 1


async def _return_value(value: str) -> str:
    return value

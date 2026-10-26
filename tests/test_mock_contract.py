from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from boxy_print_agent.api_client import ApiClientError
from boxy_print_agent.mock_api import MockBoxyClient


def test_pairing_code_is_one_use() -> None:
    client = MockBoxyClient()
    result = client.pair("code-1", display_name="Desk", hostname="pc", capabilities={})
    assert result.device_id == "dev-device-1"
    with pytest.raises(ApiClientError) as error:
        client.pair("code-1", display_name="Desk", hostname="pc", capabilities={})
    assert error.value.code == "PAIRING_CODE_INVALID"


def test_claim_next_is_atomic_across_concurrent_pollers(mock_job) -> None:
    job, payload = mock_job
    client = MockBoxyClient(jobs=[job], artifacts={job.job_id: payload})
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda _: client.claim_next("device-1"), range(40)))
    claimed = [candidate for candidate in results if candidate is not None]
    assert claimed == [job]

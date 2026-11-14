import pytest
from personal_agent.connectors.caldav import CalDAVConnector


def test_caldav_accepts_connection_url_alias() -> None:
    assert CalDAVConnector(url="https://nas.local/cal/").calendar_url.endswith("/cal/")
    with pytest.raises(ValueError, match="calendar_url"):
        CalDAVConnector()

import httpx
import pytest
from personal_agent.connectors.caldav import CalDAVConnector, parse_icalendar
from personal_agent.connectors.rss import RSSConnector


def test_parse_folded_ical_event() -> None:
    events = parse_icalendar(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTART:20261115T010000Z\r\n"
        "DTEND:20261115T020000Z\r\nSUMMARY:项目\\,\r\n 复盘\r\nEND:VEVENT\r\nEND:VCALENDAR"
    )
    assert events == [
        {
            "uid": "1",
            "summary": "项目,复盘",
            "description": "",
            "location": "",
            "start": "2026-11-15T01:00:00+00:00",
            "end": "2026-11-15T02:00:00+00:00",
            "all_day": False,
            "status": None,
        }
    ]


@pytest.mark.asyncio
async def test_caldav_report() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "REPORT"
        assert b"calendar-query" in request.content
        return httpx.Response(
            207,
            text="BEGIN:VEVENT\nUID:1\nDTSTART;VALUE=DATE:20261115\nSUMMARY:Test\nEND:VEVENT",
        )

    connector = CalDAVConnector("https://nas.local/cal/", transport=httpx.MockTransport(handler))
    result = await connector.read()
    assert result["count"] == 1
    assert result["events"][0]["all_day"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        b"<rss><channel><item><title>One</title><link>https://x/1</link><guid>a</guid></item></channel></rss>",
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Two</title><id>b</id><link href="https://x/2"/></entry></feed>',
    ],
)
async def test_rss_and_atom(payload: bytes) -> None:
    connector = RSSConnector(
        "https://nas.local/feed",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload)),
    )
    result = await connector.read()
    assert result["count"] == 1
    assert result["items"][0]["link"].startswith("https://x/")

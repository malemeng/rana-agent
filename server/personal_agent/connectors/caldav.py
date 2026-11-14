import re
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree

import httpx

from .base import Connector, ConnectorError


def _unescape_ical(value: str) -> str:
    return (
        value.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _parse_ical_datetime(value: str) -> str | None:
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
        if "T" in value:
            return datetime.strptime(value, "%Y%m%dT%H%M%S").isoformat()
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def parse_icalendar(payload: str) -> list[dict[str, Any]]:
    """Parse VEVENT fields from a CalDAV response, including folded iCalendar lines."""
    events: list[dict[str, Any]] = []
    for raw_block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", payload, re.DOTALL | re.I):
        unfolded = re.sub(r"\r?\n[ \t]", "", raw_block)
        values: dict[str, str] = {}
        parameters: dict[str, str] = {}
        for line in unfolded.splitlines():
            if ":" not in line:
                continue
            left, value = line.split(":", 1)
            name_and_params = left.split(";")
            name = name_and_params[0].upper()
            values[name] = _unescape_ical(value.strip())
            parameters[name] = ";".join(name_and_params[1:])
        start = values.get("DTSTART", "")
        end = values.get("DTEND", "")
        events.append(
            {
                "uid": values.get("UID"),
                "summary": values.get("SUMMARY", "(无标题)"),
                "description": values.get("DESCRIPTION", ""),
                "location": values.get("LOCATION", ""),
                "start": _parse_ical_datetime(start),
                "end": _parse_ical_datetime(end),
                "all_day": "VALUE=DATE" in parameters.get("DTSTART", "")
                and "DATE-TIME" not in parameters.get("DTSTART", ""),
                "status": values.get("STATUS"),
            }
        )
    return events


class CalDAVConnector(Connector):
    def __init__(
        self,
        calendar_url: str | None = None,
        username: str = "",
        password: str = "",
        timeout: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
        url: str | None = None,
    ) -> None:
        resolved_url = calendar_url or url
        if not resolved_url:
            raise ValueError("CalDAV 需要 calendar_url 或 url")
        self.calendar_url = resolved_url
        self.auth = (username, password) if username else None
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout, auth=self.auth, transport=self.transport, follow_redirects=True
        )

    async def read(self, **kwargs: Any) -> dict[str, Any]:
        start = kwargs.get("start", "19700101T000000Z")
        end = kwargs.get("end", "20380101T000000Z")
        body = f"""<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><D:getetag/><C:calendar-data/></D:prop>
  <C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">
    <C:time-range start="{start}" end="{end}"/>
  </C:comp-filter></C:comp-filter></C:filter>
</C:calendar-query>"""
        async with self._client() as client:
            try:
                response = await client.request(
                    "REPORT",
                    self.calendar_url,
                    content=body.encode(),
                    headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                )
                response.raise_for_status()
            except (httpx.HTTPError, ValueError) as exc:
                raise ConnectorError(f"CalDAV 请求失败：{type(exc).__name__}") from exc
        return {
            "events": parse_icalendar(response.text),
            "count": len(parse_icalendar(response.text)),
        }

    async def test_connection(self) -> dict[str, Any]:
        async with self._client() as client:
            try:
                response = await client.request(
                    "PROPFIND",
                    self.calendar_url,
                    content=(
                        b'<?xml version="1.0"?><D:propfind xmlns:D="DAV:">'
                        b"<D:prop><D:displayname/><D:current-user-principal/>"
                        b"</D:prop></D:propfind>"
                    ),
                    headers={"Depth": "0", "Content-Type": "application/xml"},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ConnectorError(f"CalDAV 连接失败：{type(exc).__name__}") from exc
        display_name = None
        try:
            root = ElementTree.fromstring(response.text)
            display_name = root.findtext(".//{DAV:}displayname")
        except ElementTree.ParseError:
            pass
        return {"ok": True, "name": display_name or "CalDAV"}

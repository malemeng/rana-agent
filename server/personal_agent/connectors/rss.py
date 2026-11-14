from typing import Any
from xml.etree import ElementTree

import httpx

from .base import Connector, ConnectorError


class RSSConnector(Connector):
    def __init__(
        self,
        url: str,
        timeout: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.transport = transport

    @staticmethod
    def parse(payload: bytes, limit: int = 50) -> list[dict[str, str]]:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise ConnectorError("RSS/Atom XML 解析失败") from exc
        items: list[dict[str, str]] = []
        entries = root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for entry in entries[:limit]:
            atom = entry.tag.startswith("{http://www.w3.org/2005/Atom}")
            if atom:
                ns = "{http://www.w3.org/2005/Atom}"
                link_node = entry.find(f"{ns}link")
                link = link_node.attrib.get("href", "") if link_node is not None else ""
                items.append(
                    {
                        "title": entry.findtext(f"{ns}title", default=""),
                        "link": link,
                        "published": entry.findtext(f"{ns}published", default="")
                        or entry.findtext(f"{ns}updated", default=""),
                        "summary": entry.findtext(f"{ns}summary", default="")
                        or entry.findtext(f"{ns}content", default=""),
                        "id": entry.findtext(f"{ns}id", default=link),
                    }
                )
            else:
                items.append(
                    {
                        "title": entry.findtext("title", default=""),
                        "link": entry.findtext("link", default=""),
                        "published": entry.findtext("pubDate", default=""),
                        "summary": entry.findtext("description", default=""),
                        "id": entry.findtext("guid", default="")
                        or entry.findtext("link", default=""),
                    }
                )
        return items

    async def _fetch(self) -> bytes:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, transport=self.transport
        ) as client:
            try:
                response = await client.get(self.url)
                response.raise_for_status()
                if len(response.content) > 10 * 1024 * 1024:
                    raise ConnectorError("Feed 超过 10 MiB 限制")
                return response.content
            except httpx.HTTPError as exc:
                raise ConnectorError(f"Feed 请求失败：{type(exc).__name__}") from exc

    async def read(self, **kwargs: Any) -> dict[str, Any]:
        limit = min(max(int(kwargs.get("limit", 50)), 1), 200)
        items = self.parse(await self._fetch(), limit)
        return {"items": items, "count": len(items)}

    async def test_connection(self) -> dict[str, Any]:
        items = self.parse(await self._fetch(), 1)
        return {"ok": True, "sample_title": items[0]["title"] if items else ""}

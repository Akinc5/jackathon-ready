"""Web tools for the Research agent: search the web, and read a page."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from ..config import tavily_key
from .base import Tool, ToolError

MAX_PAGE_CHARS = 12_000  # keep each page small so many fit in the agent's context
USER_AGENT = "Mozilla/5.0 (compatible; JackathonResearchBot/0.1)"


# --- web_search ---------------------------------------------------------------


class SearchArgs(BaseModel):
    query: str = Field(description="What to search for, e.g. 'HackMIT 2026 tracks prizes'")
    max_results: int = Field(default=6, ge=1, le=10)


async def web_search(args: SearchArgs) -> str:
    key = tavily_key()
    results = await (_tavily(args, key) if key else _duckduckgo(args))
    if not results:
        return "No results. Try a different query."
    return "\n\n".join(
        f"[{i}] {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(results, 1)
    )


async def _tavily(args: SearchArgs, key: str) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": args.query, "max_results": args.max_results},
        )
    if resp.status_code != 200:
        raise ToolError(f"Tavily search failed ({resp.status_code}): {resp.text[:200]}")
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in resp.json().get("results", [])
    ]


async def _duckduckgo(args: SearchArgs) -> list[dict[str, str]]:
    from ddgs import DDGS

    def search() -> list[dict]:
        return list(DDGS().text(args.query, max_results=args.max_results))

    try:
        # The ddgs library is synchronous; run it in a thread so we don't block.
        raw = await asyncio.to_thread(search)
    except Exception as e:  # ddgs raises several exception types (rate limits, timeouts)
        raise ToolError(f"DuckDuckGo search failed: {e}") from e
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in raw
    ]


# --- fetch_page ---------------------------------------------------------------


class FetchArgs(BaseModel):
    url: str = Field(description="Full http(s) URL of the page to read")


async def fetch_page(args: FetchArgs) -> str:
    _check_url(args.url)
    try:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await client.get(args.url)
    except httpx.HTTPError as e:
        raise ToolError(f"Could not fetch {args.url}: {e}") from e

    if resp.status_code >= 400:
        raise ToolError(f"{args.url} returned HTTP {resp.status_code}")
    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        raise ToolError(f"Can't read this kind of page ({content_type or 'unknown type'}).")

    title, text = html_to_text(resp.text)
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + "\n\n[...page truncated...]"
    return f"Title: {title}\nURL: {resp.url}\n\n{text}"


def html_to_text(html: str) -> tuple[str, str]:
    """Strip an HTML page down to its readable text."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = (line.strip() for line in text.splitlines())
    text = "\n".join(line for line in lines if line)
    return title, re.sub(r"\n{3,}", "\n\n", text)


def _check_url(url: str) -> None:
    """Only public http(s) pages. Stops a malicious page from steering us at local services."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError("Only full http(s) URLs are supported.")
    host = parsed.hostname
    if host == "localhost" or host.endswith(".local"):
        raise ToolError("Local addresses are not allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # a normal domain name
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ToolError("Private network addresses are not allowed.")


# --- the tools, ready to hand to an agent ------------------------------------

WEB_SEARCH = Tool(
    "web_search",
    "Search the web. Returns titles, URLs and short snippets. Use fetch_page to read a result in full.",
    SearchArgs,
    web_search,
)

FETCH_PAGE = Tool(
    "fetch_page",
    "Read a web page and return its main text (long pages are cut off).",
    FetchArgs,
    fetch_page,
)

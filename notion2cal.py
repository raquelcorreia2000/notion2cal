#!/usr/bin/env python3
"""Fetch a Notion database and export all dated entries as an .ics calendar file."""

import os
import sys
from datetime import datetime, date, timezone

import requests
from icalendar import Calendar, Event


NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "notion_calendar.ics")

NOTION_API_VERSION = "2026-03-11"
NOTION_API_BASE = "https://api.notion.com/v1"


def get_headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def query_database(database_id: str) -> list[dict]:
    """Get the data source from the database, then query all its pages."""

    # First retrieve the database to find its data source ID.
    database_url = f"{NOTION_API_BASE}/databases/{database_id}"

    resp = requests.get(
        database_url,
        headers=get_headers(),
        timeout=30,
    )
    resp.raise_for_status()

    database = resp.json()
    data_sources = database.get("data_sources", [])

    if not data_sources:
        raise RuntimeError(
            "No data source was found for this Notion database."
        )

    data_source_id = data_sources[0]["id"]

    print(f"Using data source {data_source_id}")

    # Now query the data source.
    url = f"{NOTION_API_BASE}/data_sources/{data_source_id}/query"

    results = []
    payload = {
        "page_size": 100
    }

    while True:
        resp = requests.post(
            url,
            headers=get_headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        results.extend(data.get("results", []))

        if not data.get("has_more"):
            break

        payload["start_cursor"] = data["next_cursor"]

    return results


def find_date_property(properties: dict) -> tuple[str, dict] | None:
    """Use ONLY the data/deadline date property."""

    prop = properties.get("data/deadline")

    if prop and prop["type"] == "date" and prop.get("date"):
        return "data/deadline", prop["date"]

    return None


def get_title(properties: dict) -> str:
    """Extract the title from the page properties."""

    for prop in properties.values():
        if prop["type"] == "title":
            parts = prop.get("title", [])
            return "".join(
                t.get("plain_text", "")
                for t in parts
            )

    return "Untitled"


def get_rich_text(properties: dict, name: str) -> str:
    """Extract plain text from a rich_text property."""

    prop = properties.get(name)

    if not prop or prop["type"] != "rich_text":
        return ""

    return "".join(
        t.get("plain_text", "")
        for t in prop.get("rich_text", [])
    )


def find_description(properties: dict) -> str:
    """Try common property names for a description field."""

    for candidate in (
        "Description",
        "Beschreibung",
        "Notes",
        "Notizen",
        "Text",
    ):
        text = get_rich_text(properties, candidate)

        if text:
            return text

    # Fallback: first non-empty rich_text property.
    for prop in properties.values():
        if prop["type"] == "rich_text":
            text = "".join(
                t.get("plain_text", "")
                for t in prop.get("rich_text", [])
            )

            if text:
                return text

    return ""


def parse_datetime(value: str) -> datetime | date:
    """Parse a Notion date string."""

    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return date.fromisoformat(value)


def is_in_past(start, end) -> bool:
    """Return True if the event has already ended."""

    reference = end if end is not None else start

    today = date.today()
    now = datetime.now(timezone.utc)

    if isinstance(reference, datetime):
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        return reference < now

    return reference < today


def build_calendar(pages: list[dict]) -> Calendar:
    """Build an iCalendar object from Notion pages."""

    cal = Calendar()

    cal.add("prodid", "-//notion2cal//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Notion Calendar")

    skipped = 0
    skipped_past = 0

    for page in pages:

        props = page.get("properties", {})

        date_info = find_date_property(props)

        if not date_info:
            skipped += 1
            continue

        _, date_data = date_info

        start_raw = date_data.get("start")
        end_raw = date_data.get("end")

        if not start_raw:
            skipped += 1
            continue

        title = get_title(props)
        description = find_description(props)

        start = parse_datetime(start_raw)
        end = parse_datetime(end_raw) if end_raw else None

        if is_in_past(start, end):
            skipped_past += 1
            continue

        event = Event()

        event.add("summary", title)
        event.add("dtstart", start)

        if end:
            event.add("dtend", end)

        elif isinstance(start, date) and not isinstance(start, datetime):
            # All-day event without an end.
            pass

        else:
            # Timed event without an end.
            event.add("dtend", start)

        if description:
            event.add("description", description)

        event.add(
            "uid",
            f"{page['id']}@notion2cal"
        )

        event.add(
            "dtstamp",
            datetime.now(timezone.utc)
        )

        page_url = page.get("url")

        if page_url:
            event.add("url", page_url)

        cal.add_component(event)

    event_count = len(cal.subcomponents)

    print(
        f"Processed {len(pages)} pages: "
        f"{event_count} events created, "
        f"{skipped} skipped (no date), "
        f"{skipped_past} skipped (past)"
    )

    return cal


def main() -> None:

    if not NOTION_TOKEN:
        print(
            "Error: NOTION_TOKEN environment variable "
            "is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not NOTION_DATABASE_ID:
        print(
            "Error: NOTION_DATABASE_ID environment variable "
            "is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Querying Notion database "
        f"{NOTION_DATABASE_ID[:8]}..."
    )

    pages = query_database(NOTION_DATABASE_ID)

    print(
        f"Fetched {len(pages)} pages from Notion."
    )

    cal = build_calendar(pages)

    with open(OUTPUT_FILE, "wb") as f:
        f.write(cal.to_ical())

    print(
        f"Calendar written to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()

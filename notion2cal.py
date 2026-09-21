#!/usr/bin/env python3

"""Fetch a Notion database and export all dated entries as an .ics calendar file."""

import os
import sys
from datetime import datetime, date, timezone

import requests
from icalendar import Calendar, Event


# --------------------------------------------------
# Configuration
# --------------------------------------------------

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "notion_calendar.ics")

NOTION_API_VERSION = "2026-03-11"
NOTION_API_BASE = "https://api.notion.com/v1"


# --------------------------------------------------
# Notion API
# --------------------------------------------------

def get_headers() -> dict:
    """Return headers required by the Notion API."""

    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def query_database(database_id: str) -> list[dict]:
    """
    Retrieve the database, find its first data source,
    then retrieve all pages from that data source.
    """

    headers = get_headers()

    # First: retrieve the database
    database_url = f"{NOTION_API_BASE}/databases/{database_id}"

    response = requests.get(
        database_url,
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()

    database = response.json()

    data_sources = database.get("data_sources", [])

    if not data_sources:
        raise RuntimeError(
            "No data source was found for this Notion database."
        )

    data_source_id = data_sources[0]["id"]

    print(
        f"Using Notion data source "
        f"{data_source_id[:8]}..."
    )

    # Second: query the data source
    query_url = (
        f"{NOTION_API_BASE}"
        f"/data_sources/{data_source_id}/query"
    )

    results = []

    payload = {
        "page_size": 100
    }

    while True:

        response = requests.post(
            query_url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        results.extend(
            data.get("results", [])
        )

        if not data.get("has_more"):
            break

        next_cursor = data.get("next_cursor")

        if not next_cursor:
            break

        payload["start_cursor"] = next_cursor

    return results


# --------------------------------------------------
# Notion properties
# --------------------------------------------------

def find_date_property(
    properties: dict,
) -> tuple[str, dict] | None:
    """
    Use ONLY the data/deadline date property.
    """

    property_data = properties.get("data/deadline")

    if (
        property_data
        and property_data.get("type") == "date"
        and property_data.get("date")
    ):
        return (
            "data/deadline",
            property_data["date"],
        )

    return None


def get_title(properties: dict) -> str:
    """Extract the page title."""

    for prop in properties.values():

        if prop.get("type") != "title":
            continue

        title_parts = prop.get("title", [])

        title = "".join(
            item.get("plain_text", "")
            for item in title_parts
        )

        return title or "Untitled"

    return "Untitled"


def get_rich_text(
    properties: dict,
    name: str,
) -> str:
    """Extract plain text from a rich_text property."""

    prop = properties.get(name)

    if not prop:
        return ""

    if prop.get("type") != "rich_text":
        return ""

    return "".join(
        item.get("plain_text", "")
        for item in prop.get("rich_text", [])
    )


def find_description(properties: dict) -> str:
    """
    Try common property names for a description.
    If none exist, use the first non-empty rich_text property.
    """

    possible_names = (
        "Description",
        "Beschreibung",
        "Notes",
        "Notizen",
        "Text",
    )

    for name in possible_names:

        text = get_rich_text(
            properties,
            name,
        )

        if text:
            return text

    # Fallback:
    # find the first non-empty rich_text property
    for prop in properties.values():

        if prop.get("type") != "rich_text":
            continue

        text = "".join(
            item.get("plain_text", "")
            for item in prop.get("rich_text", [])
        )

        if text:
            return text

    return ""


# --------------------------------------------------
# Date handling
# --------------------------------------------------

def parse_datetime(
    value: str,
) -> datetime | date:
    """
    Parse a Notion date value.

    Supports:
    - datetime with microseconds
    - datetime without microseconds
    - date-only values
    """

    formats = (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
    )

    for fmt in formats:

        try:
            return datetime.strptime(
                value,
                fmt,
            )

        except ValueError:
            continue

    return date.fromisoformat(value)


# --------------------------------------------------
# Calendar creation
# --------------------------------------------------

def build_calendar(
    pages: list[dict],
) -> Calendar:
    """Build an iCalendar from all Notion pages."""

    calendar = Calendar()

    calendar.add(
        "prodid",
        "-//notion2cal//EN",
    )

    calendar.add(
        "version",
        "2.0",
    )

    calendar.add(
        "calscale",
        "GREGORIAN",
    )

    calendar.add(
        "x-wr-calname",
        "Notion Calendar",
    )

    skipped_no_date = 0

    events_created = 0

    for page in pages:

        properties = page.get(
            "properties",
            {},
        )

        # Only use data/deadline
        date_info = find_date_property(
            properties
        )

        if not date_info:

            skipped_no_date += 1
            continue

        _, date_data = date_info

        start_raw = date_data.get(
            "start"
        )

        end_raw = date_data.get(
            "end"
        )

        if not start_raw:

            skipped_no_date += 1
            continue

        # Extract information
        title = get_title(properties)

        description = find_description(
            properties
        )

        # Parse dates
        start = parse_datetime(
            start_raw
        )

        end = (
            parse_datetime(end_raw)
            if end_raw
            else None
        )

        # Create event
        event = Event()

        event.add(
            "summary",
            title,
        )

        event.add(
            "dtstart",
            start,
        )

        if end:

            event.add(
                "dtend",
                end,
            )

        elif (
            isinstance(start, date)
            and not isinstance(start, datetime)
        ):

            # All-day event with no end.
            # Nothing else is required.

            pass

        else:

            # Timed event with no end.
            event.add(
                "dtend",
                start,
            )

        # Description
        if description:

            event.add(
                "description",
                description,
            )

        # Unique ID
        event.add(
            "uid",
            f"{page['id']}@notion2cal",
        )

        # Timestamp
        event.add(
            "dtstamp",
            datetime.now(timezone.utc),
        )

        # Notion page URL
        page_url = page.get("url")

        if page_url:

            event.add(
                "url",
                page_url,
            )

        calendar.add_component(
            event
        )

        events_created += 1

    print(
        f"Processed {len(pages)} pages: "
        f"{events_created} events created, "
        f"{skipped_no_date} skipped "
        f"(no data/deadline date)"
    )

    return calendar


# --------------------------------------------------
# Main
# --------------------------------------------------

def main() -> None:

    # Check token
    if not NOTION_TOKEN:

        print(
            "Error: NOTION_TOKEN environment "
            "variable is not set.",
            file=sys.stderr,
        )

        sys.exit(1)

    # Check database ID
    if not NOTION_DATABASE_ID:

        print(
            "Error: NOTION_DATABASE_ID environment "
            "variable is not set.",
            file=sys.stderr,
        )

        sys.exit(1)

    print(
        "Querying Notion database "
        f"{NOTION_DATABASE_ID[:8]}..."
    )

    # Fetch pages
    pages = query_database(
        NOTION_DATABASE_ID
    )

    print(
        f"Fetched {len(pages)} pages from Notion."
    )

    # Build calendar
    calendar = build_calendar(
        pages
    )

    # Write .ics file
    with open(
        OUTPUT_FILE,
        "wb",
    ) as file:

        file.write(
            calendar.to_ical()
        )

    print(
        f"Calendar written to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()

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
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def find_data_source(database_id: str) -> str:
    """
    Find the data source belonging to the Notion database.
    Uses the Notion search endpoint instead of the database endpoint.
    """

    headers = get_headers()

    url = f"{NOTION_API_BASE}/search"

    payload = {
        "filter": {
            "property": "object",
            "value": "data_source"
        },
        "page_size": 100
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    # Try to find the data source belonging to our database.
    for result in data.get("results", []):

        parent = result.get("parent", {})

        # Depending on the Notion API response, the database
        # relationship may be represented in different ways.
        parent_database_id = parent.get("database_id")

        if parent_database_id:
            normalized_parent = parent_database_id.replace("-", "")
            normalized_database = database_id.replace("-", "")

            if normalized_parent == normalized_database:
                data_source_id = result.get("id")

                if data_source_id:
                    print(
                        f"Found data source: "
                        f"{data_source_id}"
                    )

                    return data_source_id

    # If the direct relationship was not returned,
    # try matching the database ID inside the response.
    database_id_clean = database_id.replace("-", "")

    for result in data.get("results", []):

        result_text = str(result)

        if database_id_clean in result_text:

            data_source_id = result.get("id")

            if data_source_id:
                print(
                    f"Found data source: "
                    f"{data_source_id}"
                )

                return data_source_id

    raise RuntimeError(
        "Could not find the data source for the Notion database. "
        f"Database ID: {database_id}"
    )


def query_database(database_id: str) -> list[dict]:
    """
    Find the database's data source and retrieve all pages.
    """

    data_source_id = find_data_source(database_id)

    headers = get_headers()

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

        # ONLY data/deadline
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

        title = get_title(properties)

        description = find_description(
            properties
        )

        start = parse_datetime(
            start_raw
        )

        end = (
            parse_datetime(end_raw)
            if end_raw
            else None
        )

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

            pass

        else:

            event.add(
                "dtend",
                start,
            )

        if description:

            event.add(
                "description",
                description,
            )

        event.add(
            "uid",
            f"{page['id']}@notion2cal",
        )

        event.add(
            "dtstamp",
            datetime.now(timezone.utc),
        )

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

    if not NOTION_TOKEN:

        print(
            "Error: NOTION_TOKEN environment "
            "variable is not set.",
            file=sys.stderr,
        )

        sys.exit(1)

    if not NOTION_DATABASE_ID:

        print(
            "Error: NOTION_DATABASE_ID environment "
            "variable is not set.",
            file=sys.stderr,
        )

        sys.exit(1)

    print(
        "Querying Notion database "
        f"{NOTION_DATABASE_ID}"
    )

    pages = query_database(
        NOTION_DATABASE_ID
    )

    print(
        f"Fetched {len(pages)} pages from Notion."
    )

    calendar = build_calendar(
        pages
    )

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

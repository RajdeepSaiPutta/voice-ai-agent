"""calendar mcp tool server - in-memory event store per session."""

from __future__ import annotations

import uuid


class CalendarTool:
    """mcp-compliant calendar tool with conflict detection."""

    def __init__(self) -> None:
        self.events: dict[str, list[dict]] = {}

    async def execute(self, session_id: str, tool_name: str, args: dict) -> dict:
        if session_id not in self.events:
            self.events[session_id] = []

        handlers = {
            "calendar_list_events": self._list_events,
            "calendar_create_event": self._create_event,
            "calendar_delete_event": self._delete_event,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}", "success": False}
        try:
            result = await handler(session_id, **args)
            return {"result": result, "success": True}
        except Exception as e:
            return {"error": str(e), "success": False}

    async def _list_events(
        self, session_id: str, start_date: str, end_date: str = ""
    ) -> str:
        if not end_date:
            end_date = start_date
        matching = [
            e
            for e in self.events[session_id]
            if start_date <= e["date"] <= end_date
        ]
        if not matching:
            return f"No events from {start_date} to {end_date}."
        lines = [
            f"- {e['title']} at {e['time']} ({e.get('duration', 60)}min) [ID:{e['id'][:8]}]"
            for e in matching
        ]
        return "\n".join(lines)

    async def _create_event(
        self,
        session_id: str,
        title: str,
        datetime: str = "",
        duration_min: int = 60,
        description: str = "",
        **kwargs,
    ) -> str:
        dt_str = datetime or kwargs.get("datetime", "")
        if not dt_str:
            return "Error: datetime is required."
        event = {
            "id": str(uuid.uuid4()),
            "title": title,
            "datetime": dt_str,
            "date": dt_str[:10] if dt_str else "",
            "time": dt_str[11:16] if len(dt_str) > 11 else "",
            "duration": duration_min,
            "description": description,
        }
        # simple conflict check
        conflicts = [
            e for e in self.events[session_id] if e["date"] == event["date"] and e["time"] == event["time"]
        ]
        if conflicts:
            names = ", ".join(f"'{c['title']}'" for c in conflicts)
            return f"CONFLICT: Overlaps with {names}. Please choose another time."
        self.events[session_id].append(event)
        return f"Created: '{title}' on {event['date']} at {event['time']} (ID: {event['id'][:8]})"

    async def _delete_event(self, session_id: str, event_id: str) -> str:
        before = len(self.events[session_id])
        self.events[session_id] = [
            e for e in self.events[session_id] if not e["id"].startswith(event_id)
        ]
        deleted = before - len(self.events[session_id])
        return f"Deleted {deleted} event(s)." if deleted else f"No event found with ID: {event_id}"

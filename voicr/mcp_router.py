"""mcp router - dispatches llm tool calls with policy enforcement and safety checks."""

from __future__ import annotations

import json
import logging
import time

from voicr.tools_fs import FileSystemTool
from voicr.tools_calendar import CalendarTool
from voicr.api_clients import web_search
from voicr.audit import AuditLogger
from voicr.pii import PIIRedactor

logger = logging.getLogger("voicr.router")


class MCPRouter:
    """routes llm tool calls to mcp servers with policy enforcement."""

    def __init__(self, audit: AuditLogger) -> None:
        self.fs = FileSystemTool()
        self.cal = CalendarTool()
        self.audit = audit
        self._tool_map = {
            "fs_read_file": self.fs,
            "fs_write_file": self.fs,
            "fs_list_files": self.fs,
            "calendar_list_events": self.cal,
            "calendar_create_event": self.cal,
            "calendar_delete_event": self.cal,
        }

    def _check_policy(
        self, tool_name: str, role: str, requires_confirmation: bool = False
    ) -> tuple[bool, str]:
        """check if tool is allowed for this role. returns (allowed, reason)."""
        from voicr.auth import AuthManager

        if not AuthManager.is_tool_allowed(role, tool_name):
            return False, f"Tool '{tool_name}' not allowed for role '{role}'"

        if requires_confirmation and AuthManager.is_mutating_tool(tool_name):
            return False, f"Mutating tool '{tool_name}' requires user confirmation"

        return True, ""

    async def execute_tool_calls(
        self,
        session_id: str,
        tool_calls: list[dict],
        role: str = "user",
        conversation_history: list[dict] | None = None,
    ) -> list[dict]:
        results = []
        for tc in tool_calls:
            tool_name = tc["function"]["name"]

            # parse arguments with error handling
            raw_args = tc["function"]["arguments"]
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("[%s] Invalid tool args for %s: %s", session_id, tool_name, e)
                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(
                            {"error": f"Invalid arguments for {tool_name}: {e}", "success": False}
                        ),
                    }
                )
                continue

            if not isinstance(args, dict):
                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(
                            {"error": "Tool arguments must be a JSON object", "success": False}
                        ),
                    }
                )
                continue

            # policy check
            allowed, reason = self._check_policy(tool_name, role)
            if not allowed:
                logger.warning("[%s] Policy blocked %s: %s", session_id, tool_name, reason)
                self.audit.log_security_event("TOOL_POLICY_BLOCKED", session_id, reason)
                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(
                            {"error": "Tool not permitted by security policy", "success": False}
                        ),
                    }
                )
                continue

            # handle web_search separately with PII redaction
            if tool_name == "web_search":
                start = time.monotonic()
                query = args.get("query", "")

                # redact PII before external search
                safe_query, pii_found = PIIRedactor.redact(query)
                if pii_found:
                    logger.info("[%s] PII redacted from search query", session_id)

                # redact conversation content from query
                safe_query = self._sanitize_search_query(safe_query)

                try:
                    search_result = await web_search(safe_query)
                    latency_ms = (time.monotonic() - start) * 1000

                    self.audit.log_tool_call_with_query(
                        session_id, tool_name, safe_query,
                        {"result": search_result[:500]}, latency_ms,
                    )

                    results.append(
                        {
                            "tool_call_id": tc["id"],
                            "role": "tool",
                            "name": tool_name,
                            "content": json.dumps(
                                {"result": search_result, "success": True}
                            ),
                        }
                    )
                except Exception as e:
                    latency_ms = (time.monotonic() - start) * 1000
                    logger.warning("[%s] web_search failed: %s", session_id, e)
                    self.audit.log_tool_call_with_query(
                        session_id, tool_name, safe_query,
                        {"error": str(e)[:200]}, latency_ms,
                    )
                    results.append(
                        {
                            "tool_call_id": tc["id"],
                            "role": "tool",
                            "name": tool_name,
                            "content": json.dumps(
                                {"error": "Web search is temporarily unavailable. Answer based on your training data.", "success": False}
                            ),
                        }
                    )
                continue

            server = self._tool_map.get(tool_name)
            if not server:
                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(
                            {"error": f"Unknown tool: {tool_name}", "success": False}
                        ),
                    }
                )
                continue

            start = time.monotonic()
            try:
                result = await server.execute(session_id, tool_name, args)
                latency_ms = (time.monotonic() - start) * 1000

                self.audit.log_tool_call(session_id, tool_name, args, result, latency_ms)

                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(result),
                    }
                )
            except Exception as e:
                latency_ms = (time.monotonic() - start) * 1000
                logger.warning("[%s] Tool %s failed: %s", session_id, tool_name, e)
                self.audit.log_tool_call(session_id, tool_name, args, {"error": str(e)[:200]}, latency_ms)
                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(
                            {"error": f"Tool {tool_name} failed: {str(e)[:200]}", "success": False}
                        ),
                    }
                )

        return results

    @staticmethod
    def _sanitize_search_query(query: str) -> str:
        """remove conversation-like content and limit query length."""
        # limit length
        if len(query) > 200:
            query = query[:200]
        # strip lines that look like conversation history
        lines = query.split("\n")
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("user:", "assistant:", "system:", "You said:", "Response:")):
                continue
            clean_lines.append(stripped)
        return " ".join(clean_lines).strip()[:200]

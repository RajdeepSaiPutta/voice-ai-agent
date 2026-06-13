"""jwt authentication manager for websocket and rest api security."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from jose import jwt, JWTError
from fastapi import WebSocket, HTTPException

from voicr.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRY_HOURS, AUTH_REQUIRED


ROLES = {
    "user": {
        "max_sessions": 3,
        "max_audio_duration_s": 30,
        "max_audio_buffer_bytes": 2_000_000,
        "tools_enabled": True,
        "allowed_tools": [
            "fs_read_file", "fs_write_file", "fs_list_files",
            "calendar_list_events", "calendar_create_event", "calendar_delete_event",
            "web_search",
        ],
        "rate_limit_rpm": 9999,
    },
    "admin": {
        "max_sessions": 10,
        "max_audio_duration_s": 120,
        "max_audio_buffer_bytes": 2_000_000,
        "tools_enabled": True,
        "allowed_tools": [
            "fs_read_file", "fs_write_file", "fs_list_files",
            "calendar_list_events", "calendar_create_event", "calendar_delete_event",
            "web_search",
        ],
        "rate_limit_rpm": 9999,
    },
    "readonly": {
        "max_sessions": 1,
        "max_audio_duration_s": 10,
        "max_audio_buffer_bytes": 160_000,
        "tools_enabled": False,
        "allowed_tools": [],
        "rate_limit_rpm": 9999,
    },
}

MUTATING_TOOLS = {"fs_write_file", "calendar_create_event", "calendar_delete_event"}


class AuthManager:
    """jwt-based authentication for websocket and rest connections."""

    @staticmethod
    def create_token(client_id: str, role: str = "user") -> str:
        if role not in ROLES:
            raise ValueError(f"Invalid role: {role}")
        payload = {
            "sub": client_id,
            "role": role,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    @staticmethod
    def verify_token(token: str) -> dict:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return payload
        except JWTError as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    @staticmethod
    def extract_from_websocket(websocket: WebSocket) -> str | None:
        # only accept from sec-websocket-protocol header, not query params
        return websocket.headers.get("sec-websocket-protocol")

    @staticmethod
    def get_role_config(role: str) -> dict:
        return ROLES.get(role, ROLES["user"])

    @staticmethod
    def is_tool_allowed(role: str, tool_name: str) -> bool:
        cfg = ROLES.get(role, ROLES["user"])
        if not cfg.get("tools_enabled", False):
            return False
        return tool_name in cfg.get("allowed_tools", [])

    @staticmethod
    def is_mutating_tool(tool_name: str) -> bool:
        return tool_name in MUTATING_TOOLS

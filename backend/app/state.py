"""Simple in-memory session store.

This tool is designed to run locally, one instance per user, for a
single migration session at a time - so an in-memory dict is enough and
keeps the whole thing dependency-free. If this ever needs to run as a
shared multi-user service, swap this module for a real session store
(e.g. Redis or a temp-file-backed store); nothing else in the codebase
needs to change since everything else only talks to this module's
functions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Session:
    id: str
    source_filename: str
    # Every uploaded sheet as raw rows, header row unknown - see ingestion.py.
    raw_sheets: dict[str, list[list[str]]] = field(default_factory=dict)
    # Only sheets whose header row the reviewer has confirmed (POST
    # .../set-header) end up here, as an actual DataFrame with real column
    # names. classify/export operate on this, never on raw_sheets directly.
    sheets: dict[str, pd.DataFrame] = field(default_factory=dict)


_sessions: dict[str, Session] = {}


def create_session(source_filename: str, raw_sheets: dict[str, list[list[str]]]) -> Session:
    session_id = uuid.uuid4().hex[:12]
    session = Session(id=session_id, source_filename=source_filename, raw_sheets=raw_sheets)
    _sessions[session_id] = session
    return session


def get_session(session_id: str) -> Session | None:
    return _sessions.get(session_id)

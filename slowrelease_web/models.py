from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


DesiredState = Literal["running", "stopped"]
SlotStatus = Literal["pending", "connecting", "running", "bk", "completed", "error", "stopped"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SlotCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slot_name: str = Field(..., min_length=1, max_length=64)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(38281, ge=1, le=65535)
    password: str = ""
    yaml_text: str = Field(..., min_length=1)
    time_min: float = Field(10.0, ge=0.1, le=3600)
    time_max: Optional[float] = Field(None, ge=0.1, le=3600)
    region_mode: bool = True
    start: bool = True


class SlotUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    time_min: Optional[float] = Field(None, ge=0.1, le=3600)
    time_max: Optional[float] = Field(None, ge=0.1, le=3600)
    region_mode: Optional[bool] = None
    password: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = Field(None, ge=1, le=65535)
    slot_name: Optional[str] = Field(None, min_length=1, max_length=64)


class SlotOut(BaseModel):
    id: int
    name: str
    slot_name: str
    host: str
    port: int
    has_password: bool
    time_min: float
    time_max: float
    region_mode: bool
    desired_state: DesiredState
    status: SlotStatus
    checked_count: int
    total_count: int
    available_count: int
    current_location: str
    current_region: str
    last_check_at: Optional[str]
    last_error: str
    pid: Optional[int]
    heartbeat_at: Optional[str]
    restart_count: int
    created_at: str
    updated_at: str
    progress_pct: float = 0.0


class SlotDetail(SlotOut):
    yaml_text: str
    logs: list[str] = []


class HealthOut(BaseModel):
    ok: bool
    tracker_available: bool
    tracker_error: str = ""
    version: str
    max_workers: int
    running_workers: int

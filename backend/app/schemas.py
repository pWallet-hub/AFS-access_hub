from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,40}$"
ROLE_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"

PlatformRole = Literal["super_admin", "access_admin", "employee"]
UserStatus = Literal["active", "disabled"]
IntegrationType = Literal["none", "oidc"]


def _check_launch_url(value):
    if value is None or value == "":
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("launch_url must be an absolute http or https URL")
    return value


class UserCreate(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    display_name: str | None = Field(default=None, max_length=120)


class UserUpdate(BaseModel):
    status: UserStatus | None = None
    platform_role: PlatformRole | None = None


class ApplicationCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    launch_url: str | None = Field(default=None, max_length=500)
    enabled: bool = False
    integration_type: IntegrationType = "none"
    oidc_client_id: str | None = Field(default=None, max_length=120)

    @field_validator("launch_url")
    @classmethod
    def check_launch_url(cls, value):
        return _check_launch_url(value)


class ApplicationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    launch_url: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    integration_type: IntegrationType | None = None
    oidc_client_id: str | None = Field(default=None, max_length=120)

    @field_validator("launch_url")
    @classmethod
    def check_launch_url(cls, value):
        return _check_launch_url(value)


class RoleCreate(BaseModel):
    name: str = Field(pattern=ROLE_NAME_PATTERN)
    external_role: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class AssignmentCreate(BaseModel):
    user_id: UUID
    application_id: UUID
    app_role_id: UUID | None = None

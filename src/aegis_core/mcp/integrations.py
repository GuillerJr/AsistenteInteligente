"""Local, positive policies for curated MCP integrations.

These are admission/argument policies, not an OS sandbox or a claim that the
upstream executable is trusted. Unknown tools and unknown parameters fail closed.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Mapping
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.contracts import Capability
from aegis_core.tools.broker import PolicyViolation


class IntegrationId(StrEnum):
    GITHUB = "github"
    GOOGLE = "google-workspace"
    FILESYSTEM = "filesystem"
    PLAYWRIGHT = "playwright"
    HOME_ASSISTANT = "home-assistant"


class HomeAssistantEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(max_length=256)
    credential_account: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    allow_insecure_lan: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def exact_private_endpoint(self) -> HomeAssistantEndpoint:
        try:
            parsed = urlsplit(self.url)
            address = IPv4Address(parsed.hostname or "")
            private = any(
                address in IPv4Network(cidr)
                for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
            if (
                not private
                or parsed.scheme not in {"http", "https"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"/api/mcp", "/api/mcp/assist"}
                or parsed.query
                or parsed.fragment
                or parsed.port == 0
                or any(c.isspace() or ord(c) < 32 for c in self.url)
            ):
                raise ValueError
            if parsed.scheme == "http" and not self.allow_insecure_lan:
                raise ValueError
        except ValueError as error:
            raise ValueError(
                "Home Assistant requires an exact private IPv4 MCP endpoint; "
                "unencrypted LAN transport requires explicit opt-in"
            ) from error
        return self


class IntegrationScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: IntegrationId
    repositories: frozenset[str] = Field(default_factory=frozenset, max_length=32)
    roots: tuple[Path, ...] = Field(default_factory=tuple, max_length=8)
    google_accounts: frozenset[str] = Field(default_factory=frozenset, max_length=8)
    browser_origins: frozenset[str] = Field(default_factory=frozenset, max_length=16)

    @model_validator(mode="after")
    def validate_scopes(self) -> IntegrationScope:
        if any(
            re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r) is None
            or any(p in {".", ".."} for p in r.split("/"))
            for r in self.repositories
        ):
            raise ValueError("GitHub scope requires exact owner/repository names")
        if any(
            not p.is_absolute()
            or ".." in p.parts
            or "\0" in str(p)
            or p == Path(p.anchor)
            or p == Path.home()
            for p in self.roots
        ):
            raise ValueError("Filesystem scope requires explicit, non-root directories")
        if any(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", a) is None for a in self.google_accounts):
            raise ValueError("Google scope requires account email addresses")
        for origin in self.browser_origins:
            if _https_origin(origin) != origin:
                raise ValueError("Browser scope requires canonical HTTPS origins")
        scopes = {
            IntegrationId.GITHUB: self.repositories,
            IntegrationId.GOOGLE: self.google_accounts,
            IntegrationId.FILESYSTEM: self.roots,
            IntegrationId.PLAYWRIGHT: self.browser_origins,
        }
        if any(value for provider, value in scopes.items() if provider != self.provider):
            raise ValueError("Integration scope belongs to a different provider")
        return self

    def blockers(self) -> tuple[str, ...]:
        required = {
            IntegrationId.GITHUB: (self.repositories, "repository_scope_required"),
            IntegrationId.GOOGLE: (self.google_accounts, "google_account_required"),
            IntegrationId.FILESYSTEM: (self.roots, "filesystem_roots_required"),
            IntegrationId.PLAYWRIGHT: (self.browser_origins, "browser_origins_required"),
        }
        value, reason = required.get(self.provider, (True, ""))
        return () if value else (reason,)


# A field allowlist is deliberately independent of upstream schema annotations.
# In particular manage_event and update_drive_file contain destructive branches.
_RULES: dict[IntegrationId, dict[str, tuple[Capability, frozenset[str]]]] = {}


def _rule(provider: IntegrationId, name: str, capability: Capability, fields: str) -> None:
    _RULES.setdefault(provider, {})[name] = (capability, frozenset(fields.split()))


for _name, _fields in {
    "get_file_contents": "owner repo path ref sha",
    "list_commits": "owner repo sha path author perPage page",
    "issue_read": "owner repo issue_number method perPage page",
    "pull_request_read": "owner repo pullNumber method perPage page",
}.items():
    _rule(IntegrationId.GITHUB, _name, Capability.WEB_READ, _fields)

for _name, _capability, _fields in (
    ("search_gmail_messages", Capability.MAIL_READ, "query page_size page_token"),
    ("get_gmail_message_content", Capability.MAIL_READ, "message_id body_format"),
    ("draft_gmail_message", Capability.MAIL_WRITE,
     "subject body body_format to cc bcc include_signature"),
    ("list_calendars", Capability.CALENDAR_READ, ""),
    (
        "get_events",
        Capability.CALENDAR_READ,
        "calendar_id event_id time_min time_max max_results query timezone page_token",
    ),
    (
        "manage_event",
        Capability.CALENDAR_WRITE,
        "action summary start_time end_time event_id calendar_id description location timezone "
        "start_timezone end_timezone send_updates",
    ),
    ("search_drive_files", Capability.WEB_READ, "query page_size page_token"),
    ("get_drive_file_content", Capability.WEB_READ, "file_id"),
    ("list_drive_items", Capability.WEB_READ, "folder_id page_size page_token"),
    ("create_drive_folder", Capability.APPLICATION_CONTROL, "folder_name parent_folder_id"),
    ("create_drive_file", Capability.APPLICATION_CONTROL, "file_name content folder_id mime_type"),
    (
        "update_drive_file",
        Capability.APPLICATION_CONTROL,
        "file_id name description starred content mode",
    ),
):
    _rule(IntegrationId.GOOGLE, _name, _capability, "user_google_email " + _fields)

for _name, _fields in {
    "read_text_file": "path head tail",
    "read_multiple_files": "paths",
    "list_directory": "path",
    "get_file_info": "path",
    "list_allowed_directories": "",
}.items():
    _rule(IntegrationId.FILESYSTEM, _name, Capability.FILESYSTEM_READ, _fields)

# Initial browser phase is observation only: no clicks/forms, eval, downloads,
# uploads, profile reuse, cookies, storage state, extension, or arbitrary code.
for _name, _fields in {
    "browser_navigate": "url",
    "browser_snapshot": "",
    "browser_close": "",
}.items():
    _rule(IntegrationId.PLAYWRIGHT, _name, Capability.WEB_READ, _fields)

# HA controls are deliberately NOT admitted before an instance and exposed
# entities have been reviewed. This reads the context exposed by HA itself.
_rule(IntegrationId.HOME_ASSISTANT, "GetLiveContext", Capability.SYSTEM_READ, "")

SECRET_ENVIRONMENTS = {
    IntegrationId.GITHUB: frozenset({"GITHUB_PERSONAL_ACCESS_TOKEN"}),
    IntegrationId.GOOGLE: frozenset({"GOOGLE_OAUTH_CLIENT_SECRET"}),
    IntegrationId.FILESYSTEM: frozenset(),
    IntegrationId.PLAYWRIGHT: frozenset(),
    IntegrationId.HOME_ASSISTANT: frozenset(),
}


def integration_tools(provider: IntegrationId) -> tuple[str, ...]:
    return tuple(sorted(_RULES[provider]))


def tool_rule(scope: IntegrationScope, name: str) -> tuple[Capability, frozenset[str]]:
    try:
        return _RULES[scope.provider][name]
    except KeyError as error:
        raise PolicyViolation("Tool is not admitted by this integration") from error


def project_schema(scope: IntegrationScope, name: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only locally reviewed fields; full upstream schema is pinned separately."""
    _, fields = tool_rule(scope, name)
    properties = raw.get("properties", {})
    required = raw.get("required", [])
    if (
        raw.get("type") != "object"
        or not isinstance(properties, dict)
        or not isinstance(required, list)
        or any(not isinstance(p, str) for p in required)
        or not set(required).issubset(fields)
    ):
        raise PolicyViolation("Upstream required parameters exceed the integration contract")
    return {
        "type": "object",
        "properties": {k: v for k, v in properties.items() if k in fields},
        "required": required,
        "additionalProperties": False,
    }


def _https_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value
        ):
            raise ValueError
        # Domains only: local/private address literals and single-label hosts are
        # not admitted here. This is NOT a redirect/DNS/egress security boundary.
        host = parsed.hostname
        if (
            re.fullmatch(r"[a-z0-9.-]+", host) is None
            or "." not in host
            or host.endswith((".local", ".localhost", ".internal", ".lan"))
            or re.fullmatch(r"[0-9.]+", host)
        ):
            raise ValueError
        return f"https://{host}"
    except ValueError as error:
        raise PolicyViolation("Only scoped HTTPS browser origins are admitted") from error


def _file_path(scope: IntegrationScope, value: Any, *, read: bool) -> None:
    if not isinstance(value, str) or "\0" in value:
        raise PolicyViolation("Filesystem path is invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise PolicyViolation("Filesystem paths must be absolute without traversal")
    if not any(path.is_relative_to(root) for root in scope.roots):
        raise PolicyViolation("Filesystem path is outside approved roots")
    # No dot files, credential stores, key material, links or special devices.
    sensitive = {
        "credentials.json",
        "token.json",
        "tokens.json",
        "secrets.json",
        "secrets.yaml",
        "id_rsa",
        "id_ed25519",
    }
    if any(part.startswith(".") or part.casefold() in sensitive for part in path.parts):
        raise PolicyViolation("Sensitive filesystem paths are excluded")
    if path.suffix.casefold() in {".pem", ".key", ".p12", ".pfx", ".keychain", ".env"}:
        raise PolicyViolation("Credential files are excluded")
    try:
        for parent in (*reversed(path.parents), path):
            if parent.is_symlink():
                raise PolicyViolation("Filesystem symlinks are excluded")
        info = path.stat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise PolicyViolation("Filesystem special files are excluded")
        if read and (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1_048_576
        ):
            raise PolicyViolation("Filesystem reads require single-link files up to 1 MiB")
    except OSError as error:
        raise PolicyViolation("Filesystem path is unavailable") from error


def guard_integration(scope: IntegrationScope, name: str, arguments: Mapping[str, Any]) -> None:
    _, fields = tool_rule(scope, name)
    if scope.blockers() or not set(arguments).issubset(fields):
        raise PolicyViolation("Integration scope or arguments are not authorized")
    for field in ("perPage", "page_size", "max_results", "head", "tail"):
        if field in arguments:
            maximum = 2_000 if field in {"head", "tail"} else 50
            if type(arguments[field]) is not int or not 1 <= arguments[field] <= maximum:
                raise PolicyViolation("Integration result count exceeds its bounded range")
    if scope.provider is IntegrationId.GITHUB:
        owner, repo = arguments.get("owner"), arguments.get("repo")
        if (
            not isinstance(owner, str)
            or not isinstance(repo, str)
            or f"{owner}/{repo}".casefold() not in {r.casefold() for r in scope.repositories}
        ):
            raise PolicyViolation("GitHub repository is outside the approved scope")
        methods = {
            "issue_read": {"get", "get_comments", "get_labels", "get_sub_issues"},
            "pull_request_read": {
                "get",
                "get_diff",
                "get_status",
                "get_files",
                "get_review_comments",
                "get_reviews",
                "get_comments",
            },
        }
        if name in methods and arguments.get("method") not in methods[name]:
            raise PolicyViolation("GitHub method is not read-only")
    elif scope.provider is IntegrationId.FILESYSTEM:
        paths = (
            arguments.get("paths", [])
            if name == "read_multiple_files"
            else ([arguments["path"]] if "path" in arguments else [])
        )
        if not isinstance(paths, list) or len(paths) > 10:
            raise PolicyViolation("Filesystem reads are bounded to ten paths")
        for path in paths:
            _file_path(scope, path, read=name in {"read_text_file", "read_multiple_files"})
    elif scope.provider is IntegrationId.GOOGLE:
        if arguments.get("user_google_email") not in scope.google_accounts:
            raise PolicyViolation("Google account is outside the approved scope")
        if name == "manage_event":
            if arguments.get("action") not in {"create", "update"}:
                raise PolicyViolation("Event deletion and RSVP are excluded")
            if arguments.get("send_updates") != "none":
                raise PolicyViolation("Calendar notification delivery is excluded")
            if arguments["action"] == "update" and not arguments.get("event_id"):
                raise PolicyViolation("An exact event is required for modification")
        if name == "draft_gmail_message":
            if (arguments.get("body_format", "plain") != "plain"
                    or arguments.get("include_signature") is not False):
                raise PolicyViolation("Gmail drafts require plain text and no implicit signature")
        if name == "create_drive_file" and arguments.get("mime_type", "text/plain") != "text/plain":
            raise PolicyViolation("Initial Drive creation is plain text only")
        if name == "update_drive_file" and "content" in arguments:
            if not arguments["content"] or arguments.get("mode") not in {"append", "prepend"}:
                raise PolicyViolation("Drive content can only be appended or prepended")
    elif scope.provider is IntegrationId.PLAYWRIGHT and name == "browser_navigate":
        url = arguments.get("url")
        if not isinstance(url, str) or _https_origin(url) not in scope.browser_origins:
            raise PolicyViolation("Browser destination is outside approved origins")


def integration_catalogue() -> dict[str, Any]:
    return {
        "status": "profiles_available_not_connected",
        "requires_confirmation": True,
        "integrations": [
            {
                "provider": p.value,
                "tools": list(integration_tools(p)),
                "secret_environment_names": sorted(SECRET_ENVIRONMENTS[p]),
            }
            for p in IntegrationId
        ],
    }


def integration_description(scope: IntegrationScope) -> str:
    if scope.provider is IntegrationId.FILESYSTEM:
        return (
            "Read-only roots: " + ", ".join(map(str, scope.roots)) + ". No secret files or writes."
        )
    if scope.provider is IntegrationId.GITHUB:
        return "Read-only repositories: " + ", ".join(sorted(scope.repositories)) + "."
    if scope.provider is IntegrationId.GOOGLE:
        return (
            "Accounts: " + ", ".join(sorted(scope.google_accounts)) + ". "
            "Gmail drafts only, never send. Calendar requires send_updates='none' and "
            "action='create' or 'update'. Drive content requires mode='append' or 'prepend'. "
            "No deletion, sharing, permission changes, attachments or arbitrary file/URL imports."
        )
    if scope.provider is IntegrationId.PLAYWRIGHT:
        return (
            "Isolated observation only. Navigation origins: "
            + ", ".join(sorted(scope.browser_origins))
            + ". No clicks, forms or code execution."
        )
    return "Read only the context exposed by the owner in Home Assistant. No physical control."

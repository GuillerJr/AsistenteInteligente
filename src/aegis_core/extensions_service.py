"""Owner-only extension management; these IPC methods are never agent tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from aegis_core.contracts import AgentRole
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult
from aegis_core.plugins.contracts import PluginPackage, exposed_tool_name
from aegis_core.plugins.runtime import PluginRuntime
from aegis_core.plugins.store import (
    PluginStore,
    archive_private_extension,
    read_plugin_source,
)
from aegis_core.skills import SkillRegistry, load_skill_draft
from aegis_core.skills.contracts import SkillManifest, SkillOrigin
from aegis_core.skills.importer import (
    SkillImportChoices,
    SkillImportError,
    finalize_skill_import,
    preview_skill_source,
)
from aegis_core.skills.policy import (
    MAX_SKILL_PREVIEW_BYTES,
    skill_content_sha256,
    skill_preview_bytes,
)
from aegis_core.tools.broker import ToolBroker


class ExtensionError(ValueError):
    pass


MAX_PACKAGE_PREVIEW_BYTES = 262_144


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Payload):
    path: str = Field(min_length=1, max_length=4096)


class ImportSource(Source):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: bool = Field(strict=True)


class ImportSkill(ImportSource):
    choices: SkillImportChoices = Field(default_factory=SkillImportChoices)


class SkillSelection(Payload):
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SkillMutation(SkillSelection):
    confirm: bool = Field(strict=True)


class SkillEnabled(SkillMutation):
    enabled: bool = Field(strict=True)


class SkillConsent(SkillMutation):
    approved: bool = Field(strict=True)


class PluginSelection(Payload):
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PluginMutation(Payload):
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirm: bool = Field(strict=True)


class PluginEnabled(PluginMutation):
    enabled: bool = Field(strict=True)


class ExtensionsIpcService:
    METHODS = frozenset(
        {
            "extensions.status",
            "extensions.reload",
            "extensions.skill.details",
            "extensions.skill.preview",
            "extensions.skill.import",
            "extensions.skill.set_enabled",
            "extensions.skill.remote",
            "extensions.skill.remove",
            "extensions.plugin.preview",
            "extensions.plugin.details",
            "extensions.plugin.import",
            "extensions.plugin.set_enabled",
            "extensions.plugin.remove",
            "extensions.plugin.verify",
        }
    )
    READ_METHODS = frozenset(
        {
            "extensions.status",
            "extensions.skill.details",
            "extensions.skill.preview",
            "extensions.plugin.preview",
            "extensions.plugin.details",
            "extensions.plugin.verify",
        }
    )

    def __init__(
        self,
        registry: SkillRegistry,
        store: PluginStore,
        runtime: PluginRuntime,
        broker: ToolBroker,
        *,
        jobs_busy: Callable[[], bool] = lambda: False,
        settings_busy: Callable[[], bool] = lambda: False,
    ) -> None:
        self.registry, self.store, self.runtime, self.broker = registry, store, runtime, broker
        self._jobs_busy, self._settings_busy = jobs_busy, settings_busy
        self._lock = asyncio.Lock()
        self.restart_required = False

    def handlers(self) -> dict:
        return {method: self.handle for method in self.METHODS}

    def _suspend_package(self, identifier: str) -> None:
        self.runtime.revoke(identifier)
        ids = tuple(
            skill.skill_id
            for package in self.runtime.packages
            if package.manifest.plugin_id == identifier
            for skill in package.manifest.skills
        )
        self.registry.remove_plugin_skills(ids)
        self.restart_required = True

    def _catalogue(self) -> list[dict]:
        result = self.registry.catalogue()
        owners = {
            skill.skill_id: p.manifest.plugin_id
            for p in self.runtime.packages
            for skill in p.manifest.skills
        }
        for record in result:
            record["plugin_id"] = owners.get(record["skill_id"])
            active = record["plugin_id"] is None or self.runtime.is_active(record["plugin_id"])
            record.update(
                loaded=True, effective_enabled=record["enabled"] and active, restart_required=False
            )
            if not active:
                record["remote_allowed"] = False
        present = {record["skill_id"] for record in result}
        for installed in self.store.load_all():
            package = installed.package
            for manifest in PluginRuntime((package,)).skill_manifests():
                if manifest.skill_id in present:
                    continue
                present.add(manifest.skill_id)
                result.append(
                    {
                        "skill_id": manifest.skill_id,
                        "name": manifest.name,
                        "description": manifest.description,
                        "role": manifest.role.value,
                        "origin": "plugin",
                        "plugin_id": package.manifest.plugin_id,
                        "enabled": False,
                        "effective_enabled": False,
                        "loaded": False,
                        "remote_allowed": False,
                        "remote_authorized": False,
                        "builtin_public": False,
                        "approval_required": True,
                        "adaptation_required": manifest.adaptation_required,
                        "content_sha256": skill_content_sha256(manifest),
                        "allowed_tools": sorted(manifest.allowed_tools),
                        "starter_tools": sorted(manifest.starter_tools),
                        "instruction_count": len(manifest.instructions),
                        "resource_count": len(manifest.resources),
                        "preview_available": skill_preview_bytes(manifest)
                        <= MAX_SKILL_PREVIEW_BYTES,
                        "preview_bytes": skill_preview_bytes(manifest),
                        "restart_required": installed.enabled,
                    }
                )
        return result

    def status(self) -> dict:
        installed = self.store.load_all()
        integrity = self.store.verify()
        plugins = []
        for item in installed:
            manifest = item.package.manifest
            active = self.runtime.is_active(manifest.plugin_id)
            if not active and any(
                p.manifest.plugin_id == manifest.plugin_id for p in self.runtime.packages
            ):
                self._suspend_package(manifest.plugin_id)
            plugins.append(
                {
                    "plugin_id": manifest.plugin_id,
                    "name": manifest.name,
                    "description": manifest.description,
                    "version": manifest.version,
                    "checksum_sha256": item.package.checksum_sha256,
                    "enabled": item.enabled,
                    "active": active,
                    "integrity": "intact",
                    "skills": len(manifest.skills),
                    "tools": sum(len(c.tools) for c in manifest.connectors),
                    "restart_required": item.enabled != active,
                }
            )
        for package in self.runtime.packages:
            if package.manifest.plugin_id not in {i.package.manifest.plugin_id for i in installed}:
                self._suspend_package(package.manifest.plugin_id)
        known = {record["plugin_id"] for record in plugins}
        for identifier, intact in integrity.items():
            if identifier not in known and not intact:
                self._suspend_package(identifier)
                plugins.append(
                    {
                        "plugin_id": identifier,
                        "name": identifier,
                        "description": "",
                        "version": "",
                        "enabled": False,
                        "active": False,
                        "integrity": "compromised",
                        "skills": 0,
                        "tools": 0,
                        "restart_required": True,
                    }
                )
        jobs_busy, settings_busy = self._jobs_busy(), self._settings_busy()
        return {
            "skills": self._catalogue(),
            "plugins": plugins,
            "tool_options": {
                role.value: sorted(s["function"]["name"] for s in self.broker.schemas_for(role))
                for role in AgentRole
            },
            "busy": jobs_busy or settings_busy,
            "jobs_busy": jobs_busy,
            "settings_busy": settings_busy,
            "restart_required": self.restart_required
            or any(p["restart_required"] for p in plugins),
            "policy_status": self.registry.policy.status(),
        }

    @staticmethod
    def _path(value: str) -> Path:
        path = Path(value)
        if not path.is_absolute() or "\x00" in value:
            raise ExtensionError("extensions_invalid_source_path")
        return path

    def _skill_details(self, selection: SkillSelection) -> dict:
        if any(s.skill_id == selection.skill_id for s in self.registry.all()):
            return self.registry.preview(selection.skill_id, selection.content_sha256)
        for installed in self.store.load_all():
            for manifest in PluginRuntime((installed.package,)).skill_manifests():
                if manifest.skill_id == selection.skill_id:
                    if skill_content_sha256(manifest) != selection.content_sha256:
                        raise ExtensionError("extensions_source_changed")
                    record = next(
                        r for r in self._catalogue() if r["skill_id"] == manifest.skill_id
                    )
                    return {
                        **record,
                        "instructions": list(manifest.instructions)
                        if record["preview_available"]
                        else [],
                        "resources": list(manifest.resources)
                        if record["preview_available"]
                        else [],
                    }
        raise ExtensionError("extensions_skill_not_found")

    def _package_preview(self, path: Path) -> tuple[PluginPackage, dict]:
        content = read_plugin_source(path)
        package = PluginPackage.model_validate_json(content)
        self._validate_package(package)
        return package, self._describe_package(package, hashlib.sha256(content).hexdigest())

    @staticmethod
    def _describe_package(package: PluginPackage, source_sha256: str) -> dict:
        result = {
            "source_sha256": source_sha256,
            "plugin_id": package.manifest.plugin_id,
            "name": package.manifest.name,
            "description": package.manifest.description,
            "version": package.manifest.version,
            "checksum_sha256": package.checksum_sha256,
            "skills": [
                s.model_dump(mode="json") for s in PluginRuntime((package,)).skill_manifests()
            ],
            "tools": [
                {
                    "name": exposed_tool_name(package.manifest.plugin_id, c.connector_id, t.name),
                    "capability": t.capability.value,
                    "risk": t.risk.value,
                    "description": t.description,
                }
                for c in package.manifest.connectors
                for t in c.tools
            ],
            "hosts": sorted(package.manifest.allowed_network_hosts),
            "compatible": True,
        }
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_PACKAGE_PREVIEW_BYTES:
            raise ExtensionError("extensions_preview_too_large")
        return result

    def _package_details(self, selection: PluginSelection) -> dict:
        current = self.store.get(selection.plugin_id)
        if current is None:
            raise ExtensionError("extensions_plugin_not_found")
        package = current.package
        if package.checksum_sha256 != selection.checksum_sha256:
            raise ExtensionError("extensions_source_changed")
        self._validate_package(package)
        source_sha256 = hashlib.sha256(package.model_dump_json().encode("utf-8")).hexdigest()
        return self._describe_package(package, source_sha256)

    def _validate_package(self, package: PluginPackage) -> None:
        own_ids = {
            s.skill_id
            for p in self.runtime.packages
            if p.manifest.plugin_id == package.manifest.plugin_id
            for s in p.manifest.skills
        }
        reserved = {s.skill_id for s in self.registry.all() if s.skill_id not in own_ids}
        reserved.update(
            s.skill_id
            for p in self.store.load_all()
            if p.package.manifest.plugin_id != package.manifest.plugin_id
            for s in p.package.manifest.skills
        )
        candidate = PluginRuntime((package,))
        candidate.skill_manifests()
        tool_names = {d.name for d in candidate.tool_definitions()}
        for skill in package.manifest.skills:
            if skill.skill_id in reserved:
                raise ExtensionError("extensions_duplicate_skill")
            available = {s["function"]["name"] for s in self.broker.schemas_for(skill.role)}
            if skill.role is AgentRole.PLANNER:
                available |= tool_names
            if not skill.allowed_tools <= available:
                raise ExtensionError("extensions_tool_boundary")

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method not in self.METHODS:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        try:
            async with self._lock:
                method, raw = request.method, request.payload
                if method not in self.READ_METHODS and (self._jobs_busy() or self._settings_busy()):
                    raise ExtensionError("extensions_busy")
                if method in {"extensions.status", "extensions.reload", "extensions.plugin.verify"}:
                    Payload.model_validate(raw)
                    if method == "extensions.reload":
                        self.registry.reload()
                    value = (
                        {"integrity": self.store.verify()}
                        if method.endswith("verify")
                        else self.status()
                    )
                elif method == "extensions.skill.details":
                    value = self._skill_details(SkillSelection.model_validate(raw))
                elif method == "extensions.skill.preview":
                    value = preview_skill_source(
                        self._path(Source.model_validate(raw).path)
                    ).public_dict()
                elif method == "extensions.plugin.preview":
                    _, value = self._package_preview(self._path(Source.model_validate(raw).path))
                elif method == "extensions.plugin.details":
                    value = self._package_details(PluginSelection.model_validate(raw))
                else:
                    value = self._mutate(method, raw)
                return IpcHandlerResult(ok=True, payload=value)
        except ExtensionError as error:
            return IpcHandlerResult(ok=False, error_code=str(error))
        except SkillImportError as error:
            return IpcHandlerResult(ok=False, error_code=str(error))
        except (OSError, ValueError, TypeError, KeyError):
            return IpcHandlerResult(ok=False, error_code="extensions_invalid_request")

    def _mutate(self, method: str, raw: dict) -> dict:
        models = {
            "extensions.skill.import": ImportSkill,
            "extensions.plugin.import": ImportSource,
            "extensions.skill.set_enabled": SkillEnabled,
            "extensions.skill.remote": SkillConsent,
            "extensions.skill.remove": SkillMutation,
            "extensions.plugin.set_enabled": PluginEnabled,
            "extensions.plugin.remove": PluginMutation,
        }
        payload = models[method].model_validate(raw)
        if not payload.confirm:
            raise ExtensionError("extensions_confirmation_required")
        if method == "extensions.skill.import":
            preview = preview_skill_source(self._path(payload.path))
            if preview.source_sha256 != payload.source_sha256:
                raise ExtensionError("extensions_source_changed")
            choices = payload.choices.model_copy(update={"enabled": False})
            draft = finalize_skill_import(preview, choices).model_copy(update={"enabled": False})
            try:
                self.registry.policy.preflight_choice(draft.skill_id)
            except ValueError:
                raise ExtensionError("extensions_policy_unavailable") from None
            existing = self.registry.store.directory / f"{draft.skill_id}.json"
            if existing.exists():
                archive_private_extension(existing)
            self.registry.learn(draft)
            record = next(r for r in self.registry.catalogue() if r["skill_id"] == draft.skill_id)
            self.registry.set_enabled(draft.skill_id, record["content_sha256"], False)
            self.registry.set_remote_consent(draft.skill_id, record["content_sha256"], False)
            return {
                "skill": next(
                    r for r in self.registry.catalogue() if r["skill_id"] == draft.skill_id
                ),
                "restart_required": self.restart_required,
            }
        if method.startswith("extensions.skill."):
            record = self._skill_details(payload)
            if record.get("loaded") is False:
                raise ExtensionError("extensions_restart_required")
            if record.get("adaptation_required") and (
                (method.endswith("set_enabled") and payload.enabled)
                or (method.endswith("remote") and payload.approved)
            ):
                raise ExtensionError("extensions_executable_unsupported")
            if method.endswith("set_enabled"):
                result = self.registry.set_enabled(
                    payload.skill_id, payload.content_sha256, payload.enabled
                )
            elif method.endswith("remote"):
                result = self.registry.set_remote_consent(
                    payload.skill_id, payload.content_sha256, payload.approved
                )
            else:
                manifest = next(s for s in self.registry.all() if s.skill_id == payload.skill_id)
                if manifest.origin is not SkillOrigin.LEARNED:
                    raise ExtensionError("extensions_managed_skill")
                path = self.registry.store.directory / f"{payload.skill_id}.json"
                source_sha256 = hashlib.sha256(read_plugin_source(path)).hexdigest()
                draft = load_skill_draft(path)
                current = SkillManifest(
                    **draft.model_dump(mode="python"), origin=SkillOrigin.LEARNED, remote_safe=False
                )
                if skill_content_sha256(current) != payload.content_sha256:
                    raise ExtensionError("extensions_source_changed")
                archive_private_extension(path, remove=True, expected_source_sha256=source_sha256)
                self.registry.reload()
                result = {"skill_id": payload.skill_id, "removed": True, "archived": True}
            return {"skill": result, "restart_required": self.restart_required}
        if method == "extensions.plugin.import":
            path = self._path(payload.path)
            package, preview = self._package_preview(path)
            if preview["source_sha256"] != payload.source_sha256:
                raise ExtensionError("extensions_source_changed")
            self._validate_package(package)
            existing = self.store.directory / f"{package.manifest.plugin_id}.json"
            if existing.exists():
                archive_private_extension(existing)
                self._suspend_package(package.manifest.plugin_id)
            self.store.install(package, enabled=False)
            self.restart_required = True
        else:
            current = self.store.get(payload.plugin_id)
            if current is None:
                raise ExtensionError("extensions_plugin_not_found")
            if (
                payload.checksum_sha256
                and current.package.checksum_sha256 != payload.checksum_sha256
            ):
                raise ExtensionError("extensions_source_changed")
            if method.endswith("set_enabled"):
                if payload.enabled:
                    self._validate_package(current.package)
                self.store.set_enabled(payload.plugin_id, payload.enabled)
                if not payload.enabled:
                    self._suspend_package(payload.plugin_id)
            else:
                self.store.archive(payload.plugin_id, expected_checksum=payload.checksum_sha256)
                self._suspend_package(payload.plugin_id)
            self.restart_required = True
        return self.status()

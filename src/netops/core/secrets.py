"""密钥引用与系统钥匙串。

配置里永远只存 ``keyring:service/key`` 这样的引用，真实值放在系统钥匙串
（macOS Keychain）。测试通过 ``KeyringBackend`` 协议注入内存实现，不触碰真实钥匙串。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import keyring
from keyring.errors import KeyringError

__all__ = [
    "SecretError",
    "SecretRef",
    "KeyringBackend",
    "KeyringSecrets",
    "parse_ref",
    "is_secret_ref",
]

PREFIX = "keyring:"


class SecretError(RuntimeError):
    """密钥读取失败；消息面向用户。"""


@dataclass(frozen=True, slots=True)
class SecretRef:
    service: str
    key: str

    def __str__(self) -> str:
        return f"{PREFIX}{self.service}/{self.key}"


def is_secret_ref(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def parse_ref(value: str) -> SecretRef:
    """解析 ``keyring:service/key``，格式错误立刻报错，避免把明文当成引用。"""
    if not is_secret_ref(value):
        raise SecretError(f"不是合法密钥引用（应以 {PREFIX} 开头）：{value!r}")
    body = value[len(PREFIX) :]
    service, sep, key = body.partition("/")
    if not sep or not service.strip() or not key.strip():
        raise SecretError(f"密钥引用格式应为 {PREFIX}service/key：{value!r}")
    return SecretRef(service=service.strip(), key=key.strip())


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class _DefaultBackend:
    """系统钥匙串：直接转发给 ``keyring`` 模块的默认后端。"""

    def get_password(self, service_name: str, username: str) -> str | None:
        return keyring.get_password(service_name, username)

    def set_password(self, service_name: str, username: str, password: str) -> None:
        keyring.set_password(service_name, username, password)

    def delete_password(self, service_name: str, username: str) -> None:
        keyring.delete_password(service_name, username)


class KeyringSecrets:
    """密钥存取门面。UI / 服务层只依赖本类，不直接 import keyring。"""

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        self._backend: KeyringBackend = backend or _DefaultBackend()

    def get(self, ref: str | SecretRef) -> str:
        parsed = parse_ref(ref) if isinstance(ref, str) else ref
        try:
            value = self._backend.get_password(parsed.service, parsed.key)
        except KeyringError as exc:
            raise SecretError(f"钥匙串访问失败（{parsed.service}）：{exc}") from exc
        if value is None:
            raise SecretError(f"钥匙串中找不到密钥：{parsed}")
        return value

    def try_get(self, ref: str | SecretRef) -> str | None:
        try:
            return self.get(ref)
        except SecretError:
            return None

    def set(self, ref: str | SecretRef, value: str) -> None:
        if not value:
            raise SecretError("密钥值不能为空")
        parsed = parse_ref(ref) if isinstance(ref, str) else ref
        try:
            self._backend.set_password(parsed.service, parsed.key, value)
        except KeyringError as exc:
            raise SecretError(f"写入钥匙串失败（{parsed.service}）：{exc}") from exc

    def delete(self, ref: str | SecretRef) -> None:
        parsed = parse_ref(ref) if isinstance(ref, str) else ref
        try:
            self._backend.delete_password(parsed.service, parsed.key)
        except KeyringError as exc:
            raise SecretError(f"删除钥匙串条目失败（{parsed.service}）：{exc}") from exc

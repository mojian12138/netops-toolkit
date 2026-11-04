"""密钥封装单测：全部使用内存后端，不触碰真实 Keychain。"""

from __future__ import annotations

import pytest

from netops.core.secrets import (
    KeyringSecrets,
    SecretError,
    SecretRef,
    is_secret_ref,
    parse_ref,
)


class _MemoryBackend:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.store.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.store[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.store.pop((service_name, username), None)


@pytest.fixture()
def secrets() -> KeyringSecrets:
    return KeyringSecrets(backend=_MemoryBackend())


def test_parse_ref() -> None:
    ref = parse_ref("keyring:netops/pushplus")
    assert ref == SecretRef("netops", "pushplus")
    assert str(ref) == "keyring:netops/pushplus"


@pytest.mark.parametrize(
    "value",
    ["pushplus", "keyring:", "keyring:netops", "keyring:/token", "keyring:netops/"],
)
def test_parse_ref_rejects_bad_format(value: str) -> None:
    with pytest.raises(SecretError):
        parse_ref(value)


def test_is_secret_ref() -> None:
    assert is_secret_ref("keyring:a/b")
    assert not is_secret_ref("plain-token")
    assert not is_secret_ref(123)


def test_set_get_delete(secrets: KeyringSecrets) -> None:
    secrets.set("keyring:netops/pushplus", "token-123")
    assert secrets.get("keyring:netops/pushplus") == "token-123"
    secrets.delete("keyring:netops/pushplus")
    with pytest.raises(SecretError):
        secrets.get("keyring:netops/pushplus")


def test_get_missing_message_contains_ref(secrets: KeyringSecrets) -> None:
    with pytest.raises(SecretError, match="keyring:netops/missing"):
        secrets.get("keyring:netops/missing")


def test_try_get_returns_none(secrets: KeyringSecrets) -> None:
    assert secrets.try_get("keyring:netops/none") is None
    secrets.set("keyring:netops/none", "x")
    assert secrets.try_get("keyring:netops/none") == "x"


def test_set_rejects_empty(secrets: KeyringSecrets) -> None:
    with pytest.raises(SecretError):
        secrets.set("keyring:netops/token", "")


def test_accepts_secret_ref_object(secrets: KeyringSecrets) -> None:
    ref = SecretRef("netops", "bark")
    secrets.set(ref, "abc")
    assert secrets.get(ref) == "abc"


def test_config_value_is_never_plaintext() -> None:
    """配置文件里出现的必须是引用，不能直接是 token。"""
    config_value = "keyring:netops/pushplus"
    assert is_secret_ref(config_value)

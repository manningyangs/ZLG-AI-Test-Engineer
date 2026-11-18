"""PLAT-03：密钥安全存储（SecretStore）。

规则（任务书 14.2）：API Key 只进系统安全存储（Windows Credential Manager /
keyring 支持的后端），**不得**写入工程文件、普通配置、脚本、日志或报告。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

SERVICE_NAME = "zlg-ai-test-engine"


class SecretStoreError(Exception):
    """密钥存储读写失败。"""


class SecretStore(ABC):
    """密钥存储抽象。"""

    @abstractmethod
    def set_secret(self, key: str, value: str) -> None:
        """保存密钥；value 为空字符串等价于删除。"""

    @abstractmethod
    def get_secret(self, key: str) -> str | None:
        """读取密钥，不存在返回 None。"""

    @abstractmethod
    def delete_secret(self, key: str) -> None:
        """删除密钥（不存在时静默成功）。"""


class InMemorySecretStore(SecretStore):
    """进程内密钥存储（测试与无 keyring 后端的降级；不落盘）。"""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def set_secret(self, key: str, value: str) -> None:
        if not value:
            self._secrets.pop(key, None)
        else:
            self._secrets[key] = value

    def get_secret(self, key: str) -> str | None:
        return self._secrets.get(key)

    def delete_secret(self, key: str) -> None:
        self._secrets.pop(key, None)


class KeyringSecretStore(SecretStore):
    """基于 keyring 的系统安全存储（Windows 上为 Credential Manager）。"""

    def __init__(self) -> None:
        import keyring

        self._keyring = keyring

    def set_secret(self, key: str, value: str) -> None:
        if not value:
            self.delete_secret(key)
            return
        try:
            self._keyring.set_password(SERVICE_NAME, key, value)
        except Exception as exc:  # keyring 后端异常统一包装
            raise SecretStoreError(f"密钥保存失败: {exc}") from exc

    def get_secret(self, key: str) -> str | None:
        try:
            return self._keyring.get_password(SERVICE_NAME, key)
        except Exception as exc:
            raise SecretStoreError(f"密钥读取失败: {exc}") from exc

    def delete_secret(self, key: str) -> None:
        try:
            self._keyring.delete_password(SERVICE_NAME, key)
        except self._keyring.errors.PasswordDeleteError:
            pass  # 不存在视为成功
        except Exception as exc:
            raise SecretStoreError(f"密钥删除失败: {exc}") from exc


_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    """获取进程级密钥存储：优先系统 keyring，不可用时降级为内存存储并告警。

    降级策略不违反“密钥不落明文文件”的底线：内存存储从不写盘。
    """
    global _store
    if _store is not None:
        return _store
    try:
        import keyring
        from keyring.backends import fail as keyring_fail

        backend = keyring.get_keyring()
        if isinstance(backend, keyring_fail.Keyring) or getattr(backend, "priority", 0) <= 0:
            raise RuntimeError("keyring 后端不可用")
        _store = KeyringSecretStore()
    except Exception as exc:
        logger.warning("系统密钥库不可用，降级为进程内存储（不落盘）：%s", exc)
        _store = InMemorySecretStore()
    return _store


def reset_secret_store_for_tests(store: SecretStore | None = None) -> None:
    """测试辅助：替换/重置全局存储。"""
    global _store
    _store = store

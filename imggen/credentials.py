"""Keep the workbench's OpenAI credential in the macOS Keychain."""
from __future__ import annotations

import os
import re
import subprocess
import sys

OPENAI_BASE_URL = "https://api.openai.com/v1"
_ITEM = ["-s", "temu-imggen", "-a", "OPENAI_API_KEY"]


def keychain_available() -> bool:
    return sys.platform == "darwin"


def load_keychain_key() -> str:
    if not keychain_available():
        return ""
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", *_ITEM, "-w"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def save_keychain_key(key: str) -> None:
    # Use stdin so the secret never becomes a shell command or a process argument.
    command = f"add-generic-password -U -s temu-imggen -a OPENAI_API_KEY -X {key.encode().hex()}\n"
    try:
        subprocess.run(
            ["/usr/bin/security", "-i"], input=command, text=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("无法保存到 Mac 钥匙串，请确认登录钥匙串已解锁。") from None
    if load_keychain_key() != key:
        raise ValueError("无法确认钥匙串已保存。请检查 Mac 的钥匙串访问权限。")


def validate_api_key(key: str) -> None:
    from openai import AuthenticationError, OpenAI, PermissionDeniedError

    if not re.fullmatch(r"sk-[a-zA-Z0-9_-]{12,509}", key):
        raise ValueError("请粘贴完整的 OpenAI API key，格式以 sk- 开头。")
    try:
        with OpenAI(api_key=key, base_url=OPENAI_BASE_URL, timeout=8.0, max_retries=0) as client:
            client.models.list()
    except AuthenticationError:
        raise ValueError("OpenAI 拒绝了这个密钥。请检查密钥是否贴完整、仍有效。") from None
    except PermissionDeniedError:
        raise ValueError("密钥没有读取模型列表的权限。请检查项目密钥权限。") from None
    except Exception:
        raise ValueError("暂时无法验证 OpenAI API，请检查网络后重试。") from None


def safe_error(error: Exception) -> str:
    if getattr(error, "status_code", None) == 401:
        return "OpenAI 密钥无效，请在设置中保存有效的 API key。"
    message = str(error)
    key = os.getenv("OPENAI_API_KEY")
    if key:
        message = message.replace(key, "[已隐藏]")
    return re.sub(r"sk-[a-zA-Z0-9_*\-]+", "[已隐藏]", message)

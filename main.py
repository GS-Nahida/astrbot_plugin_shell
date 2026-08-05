"""AstrBot Shell 终端命令执行插件。

功能
----
发送指令 ``/sh <命令内容>`` 后，在机器人所运行的**系统终端**中直接执行该命令，
并将执行结果（退出码、stdout、stderr）返回给发送者。

权限模式（通过插件配置面板切换，默认 ``admin``）
--------------------------------------------------
- ``admin``    : 仅 Bot 管理员可使用；
- ``whitelist``: 白名单成员可使用（Bot 管理员始终可用）；
- ``all``      : 所有用户可使用。

安全提示
--------
``/sh`` 拥有系统终端执行权限，可以读写文件、修改系统配置等，风险极高。
请仅在可信环境下使用，并保持默认的 ``admin`` 权限模式。
"""

import asyncio
import re
import sys
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
PERM_ADMIN = "admin"
PERM_WHITELIST = "whitelist"
PERM_ALL = "all"

PERMISSION_MODE_NAMES = {
    PERM_ADMIN: "仅 Bot 管理员",
    PERM_WHITELIST: "白名单成员（管理员始终可用）",
    PERM_ALL: "所有用户",
}

# 匹配 "sh xxx" / "/sh xxx" / "/sh@bot xxx"（At 机器人时 @ 前缀已被适配器剥离）
_CMD_RE = re.compile(r"^(?:/)?sh(?:\s*@\S+)?[ \t]*(.*)$", re.DOTALL)


def _decode_output(data: bytes) -> str:
    """尝试多种编码解码子进程输出，兼容 Windows(GBK) 与 Linux(UTF-8)。"""
    if not data:
        return ""
    for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _safe_int(value: Any, default: int, min_value: int | None = None) -> int:
    """安全地将配置值转换为整数。"""
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        num = default
    if min_value is not None:
        num = max(min_value, num)
    return num


class ShellPlugin(Star):
    """/sh 终端命令执行插件。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    # ------------------------------------------------------------------
    # 权限判断
    # ------------------------------------------------------------------
    def _get_mode(self) -> str:
        """读取当前权限模式，非法值回退为 admin。"""
        mode = str(self.config.get("permission_mode", PERM_ADMIN)).strip().lower()
        if mode not in PERMISSION_MODE_NAMES:
            logger.warning(f"[Shell] 未知权限模式 {mode!r}，已回退为 {PERM_ADMIN}。")
            return PERM_ADMIN
        return mode

    def _has_permission(self, event: AstrMessageEvent) -> bool:
        """检查当前用户是否有权限使用 /sh。

        Bot 管理员（AstrBot 配置中的 admins_id）在任何模式下均可用。
        """
        if event.is_admin():
            return True

        mode = self._get_mode()
        if mode == PERM_ALL:
            return True
        if mode == PERM_WHITELIST:
            user_id = str(event.get_sender_id())
            whitelist = {
                str(x).strip()
                for x in self.config.get("whitelist", [])
                if str(x).strip()
            }
            return user_id in whitelist
        # admin 模式下非管理员无权限
        return False

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------
    async def _run_command(self, command: str) -> dict[str, Any]:
        """在系统终端执行命令，返回执行结果字典。"""
        timeout = _safe_int(
            self.config.get("command_timeout", 30), default=30, min_value=1
        )
        cwd = str(self.config.get("cwd", "") or "").strip() or None

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Shell] 无法启动进程: {e}", exc_info=True)
            return {
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": f"无法启动进程: {e}",
            }

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            # 超时强制终止进程（Windows 下需结束整个进程树，避免残留子进程）
            try:
                if sys.platform == "win32":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/F",
                        "/T",
                        "/PID",
                        str(proc.pid),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    try:
                        await asyncio.wait_for(killer.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        pass
                else:
                    proc.kill()
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            # 有界等待进程回收，避免 Windows 下 wait() 长时间阻塞回复
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            return {
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": f"命令执行超时（超过 {timeout} 秒），已强制终止。",
            }

        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": _decode_output(stdout),
            "stderr": _decode_output(stderr),
        }

    # ------------------------------------------------------------------
    # 结果格式化
    # ------------------------------------------------------------------
    def _format_result(self, command: str, result: dict[str, Any]) -> str:
        """将执行结果格式化为回复文本，并按配置截断。"""
        max_len = _safe_int(
            self.config.get("max_output_length", 1500), default=1500, min_value=100
        )

        # 命令执行成功：只返回终端输出内容
        if result["ok"]:
            # 优先使用 stdout，如果 stdout 为空则使用 stderr
            output = result["stdout"].rstrip() or result["stderr"].rstrip()
            
            # 如果输出为空，返回提示信息
            if not output:
                output = "命令执行成功（无输出）"
            
            # 截断处理
            if len(output) > max_len:
                output = output[:max_len] + "\n…（输出过长，已按配置截断）"
            
            return output

        # 命令执行失败：保留详细错误信息
        status = f"❌ 执行失败（退出码 {result['returncode']}）" if result["returncode"] is not None else "❌ 执行失败"
        parts = [f"$ {command}", "", status]
        if result["stderr"]:
            parts += ["", "--- stderr ---", result["stderr"].rstrip()]
        if result["stdout"]:
            parts += ["", "--- stdout ---", result["stdout"].rstrip()]

        text = "\n".join(parts).strip("\n")
        if len(text) > max_len:
            text = text[:max_len] + "\n…（输出过长，已按配置截断）"
        return text

    # ------------------------------------------------------------------
    # 指令处理
    # ------------------------------------------------------------------
    @filter.command("sh")
    async def sh(self, event: AstrMessageEvent) -> None:
        """在系统终端执行命令。用法: /sh <命令内容>"""
        # ---- 权限检查 ----
        if not self._has_permission(event):
            yield event.plain_result(
                "❌ 你没有权限使用 /sh 指令。\n"
                f"当前权限模式：{PERMISSION_MODE_NAMES[self._get_mode()]}，"
                "请联系 Bot 管理员。"
            )
            return

        # ---- 解析命令（保留原始空白字符，原样传给终端）----
        matched = _CMD_RE.match(event.get_message_str().strip())
        command = matched.group(1).strip() if matched else ""

        if not command:
            yield event.plain_result(
                "用法：/sh <命令内容>\n"
                "例如：/sh echo hello world\n"
                "/sh 将在系统终端中直接执行该命令。"
            )
            return

        sender = event.get_sender_name() or str(event.get_sender_id())
        logger.info(f"[Shell] 用户 {sender} 执行: {command}")

        try:
            result = await self._run_command(command)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Shell] 命令执行出错: {e}", exc_info=True)
            yield event.plain_result(f"❌ 命令执行出错：{e}")
            return

        yield event.plain_result(self._format_result(command, result))
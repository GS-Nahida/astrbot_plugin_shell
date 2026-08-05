# astrbot_plugin_shell —— Shell 终端命令执行器

发送指令 `/sh <命令内容>`，直接在机器人所运行的**系统终端**中执行该命令，并将退出码、stdout、stderr 返回给发送者。

## 使用

```
/sh <命令内容>
```

例如：

```
/sh echo hello world
/sh ipconfig          (Windows)
/sh ls -la            (Linux/macOS)
/sh python -V
```

`@机器人 /sh <命令内容>` 同样有效。

## 权限模式（插件配置）

| 配置值      | 含义                                             |
| ----------- | ------------------------------------------------ |
| `admin`     | 仅 Bot 管理员可使用（默认）                      |
| `whitelist` | 白名单成员可使用（Bot 管理员始终可用）           |
| `all`       | 所有用户可使用                                   |

## 其他配置项

- `whitelist`：白名单成员 QQ 号列表，`permission_mode` 为 `whitelist` 时生效。
- `command_timeout`：单条命令最长执行时间（秒），默认 30，超时自动强制终止。
- `max_output_length`：单次回复最大字符数，超出截断防止刷屏。
- `cwd`：命令执行的工作目录，留空使用 AstrBot 进程当前工作目录。

## ⚠️ 安全警告

`/sh` 拥有系统终端执行权限，可以读写文件、修改系统配置、下载执行任意程序，**风险极高**。

- 请仅在可信环境中使用；
- 建议保持默认的 `admin` 权限模式；
- 不要将 `all` 模式用于公开群聊。

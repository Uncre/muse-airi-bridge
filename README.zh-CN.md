[English](README.md) | [日本語](README.ja.md) | [中文](README.zh-CN.md)

# muse-airi-bridge

这是一个本地 Python 桥接服务，用于将 [AIRI](https://github.com/moeru-ai/airi) 的“OpenAI Compatible”提供方通过 [muse-cli](https://github.com/nikships/muse-cli) 连接到你日常使用的 Muse 主聊天。无需修改 AIRI 本体代码。

**发送到 Muse 的内容仅为“40 个不可见标识头字符 + 用户输入原文”。**
桥接器会剥离 AIRI 自动附加的时间戳、回复引用、技能指令与 `[Context]` 等装饰内容，且不会发送 AIRI 侧的人格提示词或历史对话记录，而是直接使用 Muse 侧本身的人格、记忆与内置工具。

---

## 前置工具与支持环境

- **前置工具**:
  - [AIRI](https://github.com/moeru-ai/airi)：针对 **v0.14.1** 的请求格式设计（调查时的 `main` 分支主要装饰格式亦一致，但不保证其他版本的兼容性）。
  - [muse-cli](https://github.com/nikships/muse-cli)：`muse-cli==0.3.10`
    - 会在 `uv` 虚拟环境中安装固定版本，不会修改现有的全局环境。
    - 以**只读**方式复用现有的 `~/.config/muse-cli/config.json` 与 `cookies.txt`。
- **操作系统与运行环境**：Windows、Python 3.10 及以上、Git、`uv`

---

## 安装与启动（PowerShell）

请在本 `README.zh-CN.md` 所在目录下执行以下命令：

```powershell
uv sync --locked
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    $bridgeKey = python -c "import secrets; print(secrets.token_urlsafe(32))"
    (Get-Content .env -Raw).Replace('replace-with-a-long-random-local-key', $bridgeKey) | Set-Content .env -Encoding utf8
}
uv run --env-file .env muse-airi-bridge doctor
uv run --env-file .env muse-airi-bridge serve
```

### 配置与使用注意事项

- **本地 API 密钥 (`MUSE_BRIDGE_API_KEY`)**
  - 请将 `.env` 中的 `MUSE_BRIDGE_API_KEY` 同样填入 AIRI 的配置中。
  - 该密钥仅用于本地桥接服务的身份验证，并非 OpenAI API 密钥（`.env` 已排除在 Git 跟踪之外）。
- **连接诊断 (`doctor`)**
  - `doctor` 命令仅通过读取历史记录来验证连接，不会发送任何消息、不会创建或修改配置文件，也不会打印 Cookie。
  - 若出现认证错误，请先在常规的 `muse-cli` 中刷新登录状态。
- **从 `0.1.0` 升级时**
  - 如果 `0.1.0` 版本的服务正在运行，请先在对应终端停止服务，再执行 `uv sync --locked` 并重新启动（Windows 无法替换正在运行的可执行文件）。
- **自定义目录路径**
  - 若 `muse-cli` 的配置目录不在默认位置，请修改 `.env` 中的 `MUSE_BRIDGE_CONFIG_DIR`（不会从 Windows 自动搜索 WSL 内的配置）。
  - 状态数据的默认保存路径为 `~/.local/state/muse-airi-bridge`（可通过 `MUSE_BRIDGE_STATE_DIR` 修改）。

---

## AIRI 配置

| 配置项 | 值 |
|---|---|
| Provider | `OpenAI Compatible` |
| API protocol | `Chat Completions` |
| Base URL | `http://127.0.0.1:8000/v1/` |
| API key | `.env` 中设置的 `MUSE_BRIDGE_API_KEY` |
| Model | `muse-airi` |

### AIRI 侧使用规则

- **语音与图像功能**：语音识别（STT）和语音合成（TTS）在 AIRI 侧配置。本桥接服务仅处理转写后的文本，不提供音频或图像生成 API。
- **切勿用于辅助功能**：请保持常规的对话历史设置，不要将此连接用于 AIRI 的辅助功能（如 Autonomous Artist、Vision、后台 Recipe、Spark 通知等）。桥接器会拒绝已识别的辅助调用。
- **工具调用（Function Tools）**：不执行 AIRI 的 Function Tools。`tool_choice` 为 `auto` 或 `none` 的工具定义会被忽略；强制要求调用工具的请求会直接报错。搜索及任务规划与执行均由 Muse 自身完成。
- **连接探测 (`ping`)**：AIRI 的连接验证 `ping` 会在本地直接返回 `pong`，不会发送到 Muse。因此 `ping` 成功并不代表 Muse 连接成功；请使用 `doctor` 或带认证的 `GET /readyz` 检查实际的 Muse 连接。

---

## Muse 灵魂（Soul）追加规则与情绪联动

首次使用前，请将 [Soul 追加提示词](docs/muse-soul-addendum.md) 的全文追加到你的 Muse Soul 中。

- 本项目不会自动修改 Muse 的 Soul。
- `docs/muse-soul-addendum.md` 内含实际的不可见头与 9 种不可见尾标。复制时请勿经过会过滤零宽字符的编辑器或转换工具。

### 不可见协议的工作原理

1. **附加输入头**
   - 桥接器会在提取出的用户原文最前面附加由 ASCII `AIRI1` 编码而成的 40 个不可见字符，然后发送给 Muse。
   - 编码规则为 `U+200B = 0`、`U+200C = 1`，每个字节从高位比特（MSB）开始展开为 8 个字符。
2. **Muse 附加情绪尾标**
   - 仅当当前回复的用户消息以该不可见头开头时，Muse 才会选择自身的主要情绪，并在回复的绝对末尾附加 `AE1:` + 1 字符情绪代码（共 5 字节 = 40 个不可见字符）。

| 代码 | AIRI 情绪名 |
|---|---|
| `N` / `H` / `S` / `A` | `neutral` / `happy` / `sad` / `angry` |
| `T` / `U` / `W` | `think` / `surprised` / `awkward` |
| `Q` / `C` | `question` / `curious` |

3. **获取回复并转换为 `ACT` 标记**
   - 桥接器会等待 Muse 生成第一条完整回复后再通过 SSE（或 JSON）返回，而非在生成过程中逐字流式推送（默认等待超时 300 秒，历史轮询间隔 1 秒）。
   - 桥接器从 `payload.content` 读取回复，剥离末尾专用尾标，并在开头添加 1 个 `<|ACT {"emotion":{"name":"happy","intensity":0.6}}|>` 格式的标记（`neutral` 强度为 `1`，其余为 `0.6`）。
   - 绝不根据正文或 Emoji 猜测情绪，也不会发起额外的 AI 修复调用或重复发送消息。

### 尾标异常时的回退处理

- **缺失尾标**：按 `neutral` 处理。
- **未知或损坏的情绪代码**：若末尾 40 个字符包含合法的 32 字符固定签名（`AE1:`），但情绪代码未知或损坏，则剥离该尾标并按 `neutral` 处理。
- **重复尾标**：若末尾连续出现多个专用尾标，则将末尾连续的专用尾标全部剥离并按 `neutral` 处理。
- **签名损坏、截断或末尾存在多余空白/换行**：若签名本身损坏、编码被截断，或尾标后带有空格/换行，则不将其识别为有效尾标，保留原始字符串并按 `neutral` 处理。正文内部或引用内容中的不可见字符绝不会被批量删除。
- **禁用情绪标记**：在 `.env` 中设置 `MUSE_BRIDGE_EMOTIONS=false` 仅会禁用 `<|ACT ...|>` 标记的插入（不可见输入头的添加以及已识别回复尾标的剥离仍然保持启用）。实际表情效果取决于虚拟形象模型所支持的动作。

---

## API 规范

| 端点 | 说明 |
|---|---|
| `GET /healthz` | 进程存活检查（不检查 Muse 连接） |
| `GET /readyz` | 读取 Muse 历史记录进行连接诊断（不发送消息） |
| `GET /v1/models` | 获取本地模型列表 |
| `POST /v1/chat/completions` | 对话接口（支持常规 JSON 与 SSE 流式响应） |

### 认证与网络

- 除 `/healthz` 外，所有端点均需提供 `Authorization: Bearer <local-key>` 请求头。
- CORS 预检请求（`OPTIONS`）无需认证即可响应。CORS 允许所有 Origin（`*`），不使用 Cookie 认证，强制要求 Bearer 密钥。默认仅监听本地回环地址（`127.0.0.1`）。

### 支持的参数与请求识别

- 本接口是面向 **AIRI 的 Chat Completions 子集**，并非通用无状态 LLM 的替代品。
- **输入格式校验**：常规输入必须包含 AIRI v0.14.1 的日期时间前缀（`[YYYY-MM-DD HH:MM] `）。若包含 `system` / `developer` 消息，则会校验其是否符合 AIRI 标准聊天提示词格式。不会对无装饰的纯文本 `user` 输入进行猜测转发。
- **支持的参数**：支持字符串或文本块数组形式的 `messages`、`stream`、`n=1`。兼容接收 `temperature`、`top_p`、`max_tokens`、`max_completion_tokens` 但会忽略（无法作用于 Muse）。不返回 Token 用量（`usage`）。
- **不支持的功能**：不支持 `stop`、结构化输出（`response_format`）、工具调用结果续写、图像/音频二进制/文件输入以及 Responses API。
- **本地连接探测识别**：严格按照已验证的特征识别本地探测请求——仅有一条内容为 `"ping"` 的 `user` 消息、Token 上限为 `16`、非流式、无额外指令或工具。在正常聊天中输入的 `ping` 带有日期时间前缀，会正常发送给 Muse。响应头 `X-Muse-Bridge-Response` 用于区分 `local-probe` 与 `muse`。

---

## 重试与中断恢复

### 串行执行与冲突防护

- 同时仅处理 1 条新发言。请勿在 Muse Web 界面或其他客户端同时发言；若等待期间历史记录中出现其他客户端的用户发言，将返回冲突错误（`409 / muse_interference`）。
- 若 Muse 主聊天中存在未完成的上一轮发言，桥接器也会阻止发送新消息，以防将上一轮迟到的回复误认为当前发言的回答。

### 状态日志（SQLite）与数据隐私

- SQLite 数据库会记录请求哈希、原文哈希、实际发送的带头正文哈希（`wire_hash`）、协议版本、发送前历史序号（`baseline`）、任务状态以及获取到的回复。
- 相同请求重试时会共享正在处理的任务，或复用 10 分钟内的已完成结果缓存。
- 数据库不会保存原始用户输入和认证凭据，但**包含不可见尾标的回复正文会保存在状态数据库中**。请将状态目录（以及迁移前备份文件）视为对话数据妥善保管。

### 从 `0.1.0` 自动迁移数据库

- 启动时会自动迁移 `0.1.0` 数据库，并在状态目录下创建迁移前备份 `journal.pre-v2.sqlite3`。
- 旧任务标记为 `raw-v0`，新任务标记为 `invisible-v1`：
  - **旧挂起任务**：在执行核对（`reconcile`）或显式清除（`clear-pending`）前会阻止新发言，且绝不会以新协议（带不可见头）自动重发。
  - **旧已完成缓存**：在 10 分钟有效期内以 `neutral` 重放，不会将其尾标重新解析为不可见协议。
  - **已过期的旧缓存与旧失败任务**：会清除回复正文并保留墓碑记录（tombstone）以防止被重发。请在 AIRI 中发送新发言，不要重发旧请求。
- 在 `invisible-v1` 协议下，若无法获取 `payload.content`，桥接器不会从显示字段猜测，而是直接报 `muse_content_missing` 错误（若发生在发送之后，则进入 `uncertain` 挂起状态，不自动重发）。仅在对旧任务（`raw-v0`）进行历史核对时，才保留 `0.1.0` 的显示字段优先级。

### 客户端断开与挂起任务管理

- HTTP 客户端断开连接不会取消 Muse 侧的任务；消息发送后，后台工作线程会继续轮询并回收回复。
- 发生超时、发送结果未知或进程停止后，结果未知的发送不会被自动重发，而是保持为挂起（`uncertain`）状态。

```powershell
# 服务运行期间也可安全查看挂起任务的 ID 与状态（不会打印对话内容）：
uv run --env-file .env muse-airi-bridge pending

# 以下命令请先停止服务后再执行：
# 与最近的历史记录进行核对并回收已完成的回复（不会发送新消息）：
uv run --env-file .env muse-airi-bridge reconcile JOB_ID

# 仅在确认 Muse 仍可能在后台运行且决定放弃该挂起任务时执行：
uv run --env-file .env muse-airi-bridge clear-pending JOB_ID --acknowledge-muse-may-still-be-working
```

- 清除无法核对的挂起任务（`clear-pending`）并不会停止远端 Muse 的工作。清除后请在 AIRI 中发起新的发言，同一被清除请求的立即重发会被拒绝。
- 多个服务进程共享同一状态目录的启动也会被文件锁拒绝。

---

## 初版限制

- 第一条完成回复之后到达的主动通知或异步任务后续播报不会推送至 AIRI。
- 由于 AIRI 的 API 请求不包含发言 ID，若将对话历史长度设为 `0` 并在同一分钟内发送完全相同的文本，将无法区分。
- 初版不处理用户手动输入与 `\n[Context]\n` 等内部装饰分隔符完全相同文本的冲突情况。
- 请求类型的判别依赖于已验证的 AIRI v0.14.1 请求格式。若 AIRI 升级改变了提示词结构，请先更新原文提取与拒绝测试后再使用。
- `reconcile` 检查最近 100 条历史事件；若其间产生了大量事件更新，可能无法完成核对。

---

## 开发与代码管理

```powershell
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
git status
```

- Git 跟踪管理 `pyproject.toml`、`uv.lock` 与 `.python-version`。`.venv`、认证信息（`.env`）、状态数据库与构建产物（`dist/`）不纳入版本控制。
- 测试套件包含在不修改业务代码的前提下验证 API 契约的黑盒测试。
- **真机最终验证步骤**：
  1. 将 [docs/muse-soul-addendum.md](docs/muse-soul-addendum.md) 追加到 Muse 的 Soul 中。
  2. 从 AIRI 发送一条简短发言。
  3. 确认 Muse 历史记录中仅新增 1 条“不可见头 + 原文”消息，且回复末尾的专用尾标被转换为 `ACT` 标记，正确驱动 AIRI 的文本显示、语音朗读、口型同步与角色表情。
- 架构设计与调查依据请参见 [docs/design.md](docs/design.md)，已完成的验证记录与待验证项请参见 [docs/verification.md](docs/verification.md)。

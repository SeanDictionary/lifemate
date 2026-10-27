# lifemate v2

> **主体 = 认知自我（思维 · 记忆 · 人格）；一切外界交互与辅助能力 = 可拆卸器官。**

器官化数字生命运行时：一个持续运行的"心智"——思维循环、长期记忆、人格装配在内核里永续运转；QQ、Web 面板、网络检索、看图……全部以器官（插件）形式挂在体外，随时可拔，拔掉后心智依然活着。

```
感知是推的（器官把信封推进心智），行动是拉的（心智自己决定调工具）
```

---

## 目录

- [核心理念](#核心理念)
- [架构总览](#架构总览)
- [快速开始](#快速开始)
- [运行模式](#运行模式)
- [配置体系](#配置体系)
- [插件（器官）](#插件器官)
- [Web 观测台](#web-观测台)
- [观测档案与回放](#观测档案与回放)
- [心智自带的能力（内核工具）](#心智自带的能力内核工具)
- [开发](#开发)
- [项目结构](#项目结构)

---

## 核心理念

**主体与器官的边界**：内核只包含"认知自我"——思维循环、记忆系统、人格。它对器官零知识：不 import 器官、不判断器官存在与否。器官增删时，心智从工具/skill 列表的自然变化感知能力增减。

**信息进入心智只有两条路**：

1. **信封**（Envelope）——适配器感知面（连接/事件循环）主动把外界事件推进内核；
2. **工具结果**——心智自己调用的工具返回的回执。

没有第三条通道。一切动态事实由器官写进信封。

**其他铁律**：

- 长期记忆在数据库不在会话——会话只是工作记忆，压缩是兜底，轮换是常态；
- 工具与适配器永不抛错，一律返回 `{ok, detail}` 回执，失败交心智自决；
- 记录不可关闭：观测记录（Recorder 黑匣子）无条件常开，thinking 不进上下文但必须入档案；
- 感知是推的，行动是拉的：禁止用工具轮询模拟接收；
- LLM 调用只走 `kernel/llm.py`（OpenAI 兼容，活配置热重建）。

---

## 架构总览

```
                    ┌─────────────────────────────────┐
                    │  内核（认知自我）                 │
                    │  mind 思维循环 / persona 人格     │
                    │  session 工作记忆 / compaction    │
                    │  tools 内核工具 / web 网络基建    │
                    └───────┬─────────────┬───────────┘
                     信封（推）│             │工具调用（拉）
                    ┌────────┴────┐   ┌────┴──────────┐
                    │ 感知面（常驻）│   │  表达面（工具）  │
                    │  QQ 适配器   │   │  qq 工具×22    │
                    │  webui 对话  │   │  webui_send    │
                    └─────────────┘   └───────────────┘
                        memory SQLite ←→ Recorder 观测档案
                        server HTTP/SSE ←→ web/ 前端看板
```

- **信封队列三语义**：`prompt`（全新任务）/ `steer`（打断插话）/ `follow_up`（顺延追述）；
- **永续回合**：一次运行贯穿一生——思考 → 休息（wait）→ 唤醒 → 思考；`deep_sleep` 是"转身"：给新会话的自己做顿告别、换会话长眠；
- **错误语义分层**：瞬态故障静默重试零打扰；结果性失败回执进上下文交心智自决；会话毒化有三级逃生梯（墓碑回滚 → 深度告警 → 紧急转身）。

---

## 快速开始

### 环境要求

- Python ≥ 3.11
- pnpm（仅前端构建时需要；运行时零 Node 依赖）

### 安装

```bash
git clone <repo> lifemate && cd lifemate
pip install -e ".[dev]"     # 或 pip install -e . 只装运行依赖
```

### 首次运行

```bash
python -m lifemate
```

首启会自动生成 `data/config.json`（完整自文档：所有键带默认值）。编辑它，至少填上：

```jsonc
{
  "llm_base_url": "https://your-openai-compatible-gateway/v1",
  "llm_api_key": "sk-...",
  "llm_model": "deepseek-chat",
  // 可选：看图能力
  "llm_vision_model": "deepseek-vl",
  // 可选：网络检索（tavily / bocha）
  "web_search_provider": "tavily",
  "web_search_api_key": "tvly-...",
  // 容器/跨机器部署务必设置（IANA 名；空=本机时区）
  "timezone": "Asia/Shanghai"
}
```

再跑 `/reload`（终端控制台命令）或直接重启即可。

剥夺模式下（一个器官都不配），stdin 每输入一行 = 一封终端信封，直接与心智对话——这是最轻量的验证方式。

### 接入 QQ（NapCat）

1. 配好 [NapCat](https://github.com/NapNeko/NapCatQQ) 并开启正向 WS（默认 `ws://127.0.0.1:3001`）；
2. `config.json` 里：

```jsonc
{
  "plugins": {
    "qq": {
      "enabled": true,
      "bot_uin": 123456789,   // 机器人 QQ 号
      "owner_uin": 987654321, // 主人 QQ 号（信封标注 + 私聊直达）
      // 可选：ws_url / access_token / listen_groups / wake_aliases ...
    }
  }
}
```

3. 重启。心智通过 WS 感知群聊/私聊事件，唤醒语义见下文插件节。

---

## 运行模式

`python -m lifemate [选项]`

| 参数 | 作用 |
|---|---|
| `-v, --version` | 显示版本号并退出 |
| `-l, --log-level` | 日志等级（覆盖 config 的 `log_level`） |
| `-p, --port` | 指定 webui 端口（env 种子落地，热重载不回退） |
| `-s, --silence` | 静默终端日志（文件照写，对话呈现不变） |
| `--webui` | **看板模式**：仅启动 webui 服务，不启动 AI 运行时（不装载器官、无思维循环，心智零唤醒，严格只读观测） |
| `--no-stdin` | 服务模式：无控制台，纯信号驱动（后台/容器） |

终端控制台命令：`/reload` 热重载配置、`/quit` 优雅退出；Ctrl+C / SIGTERM 分步停机（档案与库先落地），停机中再收信号 = 强制退出逃生口。

---

## 配置体系

- **事实源**：`data/config.json`。全部业务配置都在 `utils/config.py` 的声明白名单里（`Setting` 带默认值/描述/密钥标记/重启标记/分组），文件永远保持完整自文档——缺键自动按默认补齐，未知键保留不丢。
- **热重载**：host 每 `config_poll_s` 秒轮询 mtime，字段原地更新，组件持活引用、用时读取即热生效（改完 `/reload` 立即生效，无需重启）。
- **env 种子**：`LIFEMATE_TIMEZONE`、`LIFEMATE_SERVER_PORT` 等仅作部署种子（容器配齐 env 即可带病运行；env 显式设置时优先于 config）。
- **密钥安全**：API 密钥等 secret 字段在 API/面板读取时掩码显示；`server_token` 空值自动生成随机值，永不裸奔。
- **设置页 schema 驱动**：Web 面板「设置」页按声明自动生成分组控件（含插件设置区），零硬编码。

主要配置组：模型（LLM/重试/看图）、内核（轮数熔断/压缩/档案回查预算）、网络访问（搜索 provider/取页上限）、图像、网络服务、守护（watchdog/备份/提醒轮询）、观测与档案、插件命名空间。

---

## 插件（器官）

**一个文件夹 = 一个插件**，放 `lifemate/plugins/` 下自动发现装载（失败隔离，不影响其他器官）：

```
lifemate/plugins/<name>/
├── __init__.py    # create(ctx) -> Plugin | None（None = 未启用）
├── tools.py       # 表达面：工具（MCP schema 形状）
├── config.py      # 可选：插件配置声明（Field 继承 Setting）
├── panel.py       # 可选：面板路由（挂 /api/plugins/<name>/*）
└── SKILL.md       # 器官说明书（persona 自动并入技能索引）
```

启用开关与参数在 `config.json` 的 `plugins.<name>` 命名空间。拔插件 = 删工具 + 删 skill，路由消失，心智照常活着（剥夺测试保证这一点）。

### QQ 插件（lifemate/plugins/qq）

正向 WS 连 NapCat，是目前最大的器官：

- **强刺激直达**：@、私聊、唤醒别名（叫名字不 @ 也算呼叫）、回复引用（本地反查解引用 reply 指针）、对话延续窗口（心智发言后 N 秒内的跟话升格直达）；
- **弱刺激背景听觉**：chat_log 落库 + 未读身体感受 + 攒批唤醒（阈值/兜底双触发 + 冷却钳制，事件驱动零轮询成本）；
- **22 个工具**：发消息/媒体/引用回复/撤回/群管/读合并转发/世界查询/原始 API 等，出站回执制 + 风控物理钳制（最小间隔/每分钟上限）；
- **命令面**：主人私聊 `/status` `/history` 等固定规则应答——身体反射，零 token 即时响应，不经心智；
- **DND 语义**：`wait(wake_on="direct")` 只被直达/自定闹钟唤醒，群聊水话静默累积、醒来组批一起看（不丢）；
- **身体告警**：LLM 长故障时主动私聊主人（心智求助不了的最后一块）。

### 样板插件

`lifemate/plugins/example/`（默认关）演示了最小结构，新插件从复制它开始。

### skills/ 目录

`soul/` 放人格（`identity.md`，数据非代码，热更新有观测留痕）；`skills/` 放**内置以外**的知情层——器官说明书随器官走，用户自加扩展放这里即生效。内核核心机制指引在 `lifemate/kernel/builtin/`（代码管理、常驻装配）。

---

## Web 观测台

Next.js 静态导出 + shadcn，产物由 Python server 同源托管（运行时零 Node）：

```bash
cd web && pnpm install && pnpm build   # 产物 web/out/
```

浏览器打开 `http://127.0.0.1:8300`，token 见 `data/config.json` 的 `server_token`。

页面：**概览 / 事件流（SSE 实时，rid 周期分组）/ 历史回放 / 对话 / 记忆（含图片 Tab）/ 用量分账 / 设置（schema 驱动读写）/ 插件面板 / 工具 / 技能**。深浅双主题。

开发模式：`pnpm dev`（:3000，`/api` 代理到 :8300）。

`--webui` 单开看板：AI 运行时不启动，纯观测（档案/记忆/配置照常可读，对话面明确拒绝）。

---

## 观测档案与回放

Recorder 是项目级黑匣子，无条件常开、永不压缩：信封/思考/发言/工具回执/休息唤醒/错误（含完整 traceback）全量入档，按日切 JSONL 存 `data/observe/`。

**回放查看器**（像翻会话一样翻它的一天）：

```bash
python -m lifemate.replay                  # 今天
python -m lifemate.replay 2026-10-26       # 指定日期
python -m lifemate.replay --rid r8f2       # 只看一次运行链路
python -m lifemate.replay --tail 50        # 最后 50 行
```

**心智自己也翻档案**：`recall` 工具让主体回查自己的全部历史（输入/思考/发言/工具回执/休息），档案对心智无禁区——三级保真（概览一行一条 → rid 链路详情 → seq 单行全文），预算是会话经济学不是访问控制。

---

## 心智自带的能力（内核工具）

这些是"认知天赋"，采集/出站归器官：

| 工具 | 说明 |
|---|---|
| `wait` | 思维的生理休息；支持 `wake_on="direct"`（DND）；到期归还意图（"你定这个等待时说：……"） |
| `deep_sleep` | 转身：给新会话的自己写信 → 换会话长眠（轮换在周期边界兑现，崩溃也能续上） |
| `look_image` | 看图：四形态输入归一（URL/data:URI/base64/本地路径）+ SSRF 防护 + 内容 hash 去重缓存 + 原图归档 |
| `web_search` | 检索（tavily/bocha，回执带不可信声明防注入） |
| `web_fetch` | 读页（charset 探测含 gb18030 兜底、HTML 正文提取保留链接路标） |
| `recall` | 档案回查：关键词搜全档案 / date 翻任意窗口 / rid 链路，句柄式三级保真 |
| `read_skill` | 按需读 skill/builtin 指引全文 |

记忆工具（`memory/tools.py`）：`record_episode` / `revise_episode`（认识修正，防固化）/ `recent_episodes` / 兴趣·需求·提醒清单（`add/list/resolve/reopen_curiosity` 等）/ `save_rebuild_packet`（轮换重建包）/ `find_image`（归档图检索，表情包素材）。

---

## 开发

```bash
pytest                    # 全部单测（含剥夺测试，必须常绿）
pytest tests/test_deprivation.py   # 剥夺测试：拔全部器官，内核必须存活
ruff check                # lint 保持零告警
ruff format               # 提交前过一遍
```

### 约定速览

- 依赖方向唯一允许：`__main__（组合根）→ host/server → plugins(经装载器) → kernel.contracts ← kernel`；`kernel/memory/plugins → utils`（utils 不依赖任何上层）；器官之间零 import；
- 工具一律住 `<module>/tools.py`；入口命名：单工具 `make_X_tool(deps)`，多工具 `build_X_tools(deps)`，公开命名；
- 新行为参数必须进配置白名单（`utils/config.py`），禁止硬编码魔法数字；
- 未上线阶段 schema 变更直接改 CREATE TABLE 删表重建，不写迁移；
- 每模块独立 logger，决策/轮换/工具失败必带上下文，禁裸 print；
- 测试临时数据/调试输出一律进 `.cache/`（gitignore）；
- 完整开发协作文档见 [AGENTS.md](AGENTS.md)，架构设计文档见 `.cache/design-v2-proposal.md`（本地）。

---

## 项目结构

```
lifemate/
├── kernel/            # 内核（认知自我）
│   ├── contracts.py   #   契约：信封/事件/器官包/工具形状（纯协议零实现）
│   ├── mind.py        #   思维循环：信封队列 + 工具分发 + 轮换
│   ├── llm.py         #   模型调用唯一入口（重试退避/告警/多模态 vision）
│   ├── session.py     #   工作记忆：JSONL 持久化 + 轮换
│   ├── compaction.py  #   上下文压缩（会话内兜底）
│   ├── persona.py     #   人格装配（identity + builtin + skills 索引，每轮重建）
│   ├── tools.py       #   内核工具（wait/deep_sleep/look_image/web_search/…）
│   ├── web.py         #   网络基建共用层（SSRF 防护/正文提取/provider 表，纯 stdlib）
│   └── builtin/       #   内置机制指引（代码管理，常驻装配）
├── memory/            # 长期记忆（SQLite 五表 + 认识修正 + 重建包 + 图片档案）
├── utils/             # 中立层：config 活配置 / log / recorder / instance 锁 / clock 时区
├── server/            # 网络服务层：零依赖 HTTP/SSE + API 路由 + 事件桥 + 对话总线
├── plugins/           # 插件目录（自动发现）：qq / example（样板）
├── console.py         # 开发控制台（宿主对话面，非器官）
├── host.py            # 运行时服务：思维驱动 + 基建任务 + 插件装载
├── replay.py          # 档案回放查看器
└── __main__.py        # 入口 = 组合根（CLI/编排/统一停机）
soul/                  # 人格（identity.md，数据非代码）
skills/                # 第三方/扩展知情层（用户自加即生效）
web/                   # 前端观测台（Next.js 源码；out/ 产物由 server 托管）
tests/                 # 单测 + 剥夺测试（必须常绿）
data/                  # 运行数据（gitignore）：config.json / memory.db / sessions/ / observe/ / images/ / logs/ / backups/
.cache/                # 开发副产物 + v1 归档 + 设计文档（gitignore）
```

## 待开发

attention（门控升格）→ sandbox（沙箱执行）→ video …

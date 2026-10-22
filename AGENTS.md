# lifemate v2 · AGENTS.md（仓库开发协作文档）

> 职责边界：本文件只约束本仓库。部署/容器/数据卷事务归 `../qq-agent/AGENTS.md`。

## 项目是什么

器官化数字生命运行时。本体论一句话：**主体 = 认知自我（思维·记忆·人格）；
一切外界交互与辅助能力 = 可拆卸器官。**

完整设计文档：`.cache/design-v2-proposal.md`（gitignore，本地保存；
架构事实随实现落地固化到本文件「当前状态」节）。

## 架构铁律（写代码前先读，违反即返工）

1. **内核 = 认知自我**：思维循环、记忆系统、人格、两接口（信封入口 +
   工具出口）。内核对器官零知识（不 import、不判断存在性）。
2. **信息进入心智只有两条路**：信封 与 工具结果。禁止 per-turn 注入钩子、
   旁路装配等第三通道。一切动态事实由器官写进信封。
3. **器官 = 适配器 + 工具 + skill 三件套**。拔器官 = 摘工具 + 摘 skill，
   心智从工具/skill 列表的自然变化感知能力增减；禁止代码分支判断器官在不在。
   skill 定位：**内置以外**的知情层（器官/第三方扩展，按需加载）；
   内核核心机制指引一律进 kernel/builtin/（代码管理、常驻装配——
   生存制度不能依赖"模型碰巧去读"）。
4. **感知是推的，行动是拉的**：适配器感知面常驻（连接/事件循环/定时器），
   主动把信封推进内核；工具只被心智调用。禁止用工具轮询模拟接收。
5. **工具定义用 MCP tool schema 形状**（进程内实现；将来可薄包装成
   MCP server 复用，不引入进程边界）。
6. **长期记忆在 DB 不在会话**：会话只是工作记忆——压缩是兜底，轮换是常态
   （重建包固化为常态机制）。
7. **出站/执行回执制**：工具与适配器永不抛错，返回 `{ok, detail}`；
   失败回执给心智，由它自决重试/放弃/搁置。
8. **内核/记忆/人格之外的一切新能力必须以插件形式加入**（plugins/ 目录，
   自动发现），不进内核、不改 host/config 主代码。
9. **记录不可关闭，查看方式可任意**：观测记录（Recorder→data/observe/）
   是项目级日志基础设施，host 无条件挂载，不是器官；查看器（replay/
   将来 WebUI）可拆可换。thinking 不进上下文但必须入档案。

## 目录结构（功能界限 · 模块化 · 文件夹分类）

```
lifemate/
├── kernel/            # 内核（认知自我，必须实现）
│   ├── contracts.py   #   契约：信封/事件/器官包/工具形状/MindKernel（纯协议零实现）
│   ├── mind.py        #   思维循环：信封队列(prompt/steer/follow_up)+工具分发+轮换
│   ├── llm.py         #   模型调用唯一入口（OpenAI 兼容；活配置，签名变更热重建）
│   ├── session.py     #   工作记忆：消息列表 + JSONL 持久化 + 轮换
│   ├── compaction.py  #   上下文压缩（会话内兜底，阈值活配置）
│   ├── persona.py     #   人格装配：identity + 内置指引 + skills 索引，每轮重建
│   ├── tools.py       #   内核工具：wait（思维生理）/read_skill/deep_sleep
│   └── builtin/       #   内置机制说明（代码管理，常驻装配，客观陈述）
├── memory/            # 记忆系统（内核②：长期记忆）
│   ├── store.py       #   SQLite：episodes/curiosity/needs/reminders/重建包
│   └── tools.py       #   记忆工具（MCP 形状，内核自带）
├── utils/             # 共享中立层（依赖锚点：全系统可用，不依赖任何上层）
│   ├── config.py      #   活配置：{data_dir}/config.json 事实源 + env 种子
│   │                  #   + mtime 热重载（原地更新，引用即热生效）+ 密钥掩码
│   ├── log.py         #   全局日志：.log 轮转存储 + 未捕获异常兑底
│   ├── recorder.py    #   观测记录器：项目级日志基础设施（黑匣子，常开不可拆）
│   └── instance.py    #   单实例锁：OS 文件锁，防双开损坏数据
├── replay.py          # 回放查看器：python -m lifemate.replay（翻档案）
├── server/            # 网络服务层（宿主侧，与 host 对等）：API+SSE+静态托管
│   ├── http.py        #   极简 HTTP 基建：解析/路由({param})/响应/静态/鉴权
│   ├── events.py      #   事件桥：内核订阅→广播（有界队列/丢最旧/心跳）
│   ├── history.py     #   observe 档案读取 + LLM 用量聚合
│   ├── routes.py      #   核心路由表（/api/status/events/history/say/chat/
│   │                  #   config读写/memory/usage/plugins）
│   ├── outbound.py    #   对话总线：环形缓冲 + SSE 广播
│   └── tools.py       #   webui_send 工具（网页对话出口）
├── console.py         # 开发控制台：stdin→/命令+调试信封（宿主对话面，非器官）
├── host.py            # 运行时服务：思维循环驱动+基建任务+插件装载（Host 类）
└── __main__.py        # 入口=组合根：共享准备（锁/日志/装配/stop）+服务编排
tests/                 # 单测 + 剥夺测试（必须常绿）
data/                  # 运行数据（gitignore）：config.json / memory.db /
                      #   sessions/ / observe/ / logs/*.log / backups/ /
                      #   lifemate.lock
├── plugins/           # 插件目录（一个文件夹=一个插件，自动发现装载）
│   ├── __init__.py    #   装载器（主代码，写一次）：扫描→create 工厂→注册+start
│   ├── example/       #   样板插件（默认关）
│   └── qq/            #   QQ 插件：正向 WS 连 NapCat + 事件→信封 + 工具集
│                      #   + panel.py 面板路由（挂 /api/plugins/qq/*，随插件可拆卸）
│                      #   工具在 tools.py（20 个，表达面）；感知面在 __init__.py
│                      #   新增插件=加目录+config plugins 命名空间，零主代码改动
├── soul/              # 人格（identity.md——数据非代码）
├── skills/            # skill 目录：**内置以外**的第三方/扩展功能知情层
│                      # （器官说明书随器官走；用户自加扩展放这里即生效）
├── console.py         # 开发控制台：stdin→/命令+调试信封（宿主对话面，非器官）
├── host.py            # 运行时服务：思维循环驱动+基建任务+插件装载（Host 类）
└── __main__.py        # 入口=组合根（CLI：-v/-l/-s/--no-stdin）：共享准备+服务编排；
                      #   stdin 每行=调试信封）
web/                  # 前端观测台（Next.js 源码，pnpm；node_modules/out 不入库）
├── src/app/           #   8 页面：概览/事件流/历史回放/对话/记忆/用量/设置/插件
├── src/lib/           #   api(token/401门)/sse/use-sse(Context 挂 globalThis：对
│                      #   Turbopack 模块复制免疫)/types(契约镜像)/render(emoji 图标)
├── src/components/    #   layout(侧栏/生命标识/Vitals)+plugins/qq+ui(shadcn)
└── out/               #   pnpm build 产物，由 server 同源托管（运行时零 Node）
tests/                 # 单测 + 剥夺测试（必须常绿）
data/                  # 运行数据（gitignore：memory.db / sessions/）
.cache/                # 开发副产物 + v1 归档 + 设计文档（gitignore）
```

依赖方向（唯一允许）：`__main__（组合根）→ host/server → plugins(经装载器) →
kernel.contracts ← kernel`；
`kernel/memory/organs/host → utils`（utils 不依赖任何上层）；器官之间零 import。
工具约定：模块的工具一律住该模块的 `tools.py`，无例外（kernel/memory/
server/plugins 均如此）——找工具只看 tools.py；入口形状：单工具
`make_X_tool(deps) -> Tool`，多工具 `build_X_tools(deps) -> list[Tool]`，
一律公开命名（无下划线）；工厂依赖作参数传入，tools.py 只 import
contracts 层，不引入新依赖方向。
配置体系：业务配置一律进 `utils/config.py` 白名单（即 config.json 键，
env 仅部署种子）；热重载靠字段原地更新——新组件必须持活引用、用时读取，
禁止启动时冻结拷贝。

## 开发硬性规则

0. **本文件及时维护**：状态/结构/约定变化立即同步；每个功能完成即更新状态节
0a. **副产物统一入 .cache/**：测试临时数据/调试输出/截图一律放 `.cache/`，
    不散落源码目录，绝不入 git
0b. **原子化提交**：一次提交只做一件事，feat/fix 分离，禁止混合大提交；
    每个提交自洽（可独立运行/测试通过）
1. **行为变更以主人指示为需求源头**：先评估（架构影响/风险/替代方案）
    再动手；读代码即文档，不写设计文档（设计稿除外）
2. **新增行为参数必须进配置白名单**（utils/config.py，即 config.json 键），
   禁止硬编码魔法数字
3. **未上线阶段 schema 变更直接改 CREATE TABLE 删表重建**：不写迁移脚本、
   不搬数据、不保历史（开发数据一律可弃）
4. **日志纪律**：每模块独立 logger；决策/轮换/工具失败必带上下文；禁裸 print
   （host 入口的控制台呈现除外）
5. **LLM 调用只走 kernel/llm.py**
6. **器官不得 import 内核内部**（只认 kernel.contracts）；内核不得 import 器官
7. **剥夺测试常绿**：`pytest tests/test_deprivation.py` 必须通过——
   拔全部器官，内核（思维+记忆+人格）必须存活
8. **v1 旧实现已归档 `.cache/v1-archive/`**：仅作参考，禁止复制其代码

## 当前状态

- **2026-10-21 v2 重构启动**：v1 全量归档至 `.cache/v1-archive/`（含旧 .git），
  仓库重 init；旧 .cache 与前端构建残留移至 `../_discard/`（可手动清空）
- **永续回合已上线（2026-10-21）**：一次运行贯穿一生——思考→休息(wait)→
  唤醒→思考；收敛自动休息、显式 wait 闹钟同回合延续、notify 唯一入口
  （组批唤醒+插话）、强刺激反射打断可打断工具、deep_sleep 深睡转身
  （写信→换会话→长眠）、防不睡强制休息、崩溃悬空修复；提醒轮询兑现
  它自定的闹钟；时间性缺口闭合（自主触发=它自己调用的 wait 返回）
- **内核阶段完成**：契约定型（信封/事件/OrganPack/MCP 工具形状）+
  思维循环（prompt/steer/follow_up 三语义、全链工具兜底、轮换播种、
  空闲钩子）+ 记忆系统（SQLite 五表 + 记忆工具）+ 人格装配（identity +
  skills 索引）+ 会话（JSONL 持久化/重启恢复/轮换）+ 压缩兜底 +
  剥夺模式宿主。借签 pi 的会话/压缩/steer 语义，按器官化契约重组
- **基建加固完成（2026-10-21）**：LLM 重试退避+连续失败告警（借签 pi）；
  全局日志 .log 轮转+异常兑底；优雅停机（信号→中止思维→flush→关库）；
  单实例锁；排队背压（溢出丢最旧存证）；watchdog 自愈（僵尸队列/卡死运行）；
  SQLite WAL+在线备份；observe 保留期清理——共 13 个新配置键
- 已验证：40 项单测全绿；真实 LLM 剥夺运行 + replay 回放 + 优雅停机/
  备份/日志端到端通过
- 回放：`python -m lifemate.replay [日期] [--rid xx] [--tail N]`
- **配置体系就位**：utils/config 活配置（JSON 事实源/env 种子/热重载/
  掩码），host 轮询重载 + `/reload` 调试命令，LLM 客户端签名热重建
- **观测底座完成（2026-10-21）**：Recorder 常开黑匣子（按日切 JSONL、
  永不压缩、seq/rid/时间戳/投递语义/执行语义/thinking 全量入档）；
  replay 查看器（思考折叠/工具耗时/按 rid 过滤）；轮换后旧会话文件
  按配置清理（档案职责 100% 归 observe）
- **插件体系 + QQ 插件上线（2026-10-21）**：一个文件夹=一个插件，
  create(ctx) 工厂自动发现装载（失败隔离）；config plugins 命名空间；
  QQ 插件（正向 WS 连 NapCat）：强刺激直达（@/私聊/主人/回复/戳），
  弱刺激背景听觉（chat_log + read_chat_log 自主查看 + 未读身体感受），
  21 个工具（发消息/媒体/撤回/审批/群管/世界查询/状态/原始 API），
  出站回执制 + 风控物理钳制；FakeNapCat 端到端测试通过
- **控制台独立 + 终端静默（2026-10-21）**：stdin 控制台独立为 console.py
  （宿主对话面，非器官——命令面 /quit /reload 需操纵宿主生命周期，不进
  器官契约）；-s/--silence 静默终端日志（文件照写，对话呈现不变），
  静默决策收在入口、host 显式传参不碰 sys.argv
- **启动 CLI（2026-10-21）**：__main__ argparse（-v/--version、
  -l/--log-level、-s/--silence）；config 新增 log_level 键（默认 info，
  CLI -l 覆盖 config）；等级名解析收口 utils/log.level_from_name
  （未知值告警回落）；版本号收口 __version__.py（importlib.metadata）
- **首启预设时序修复（2026-10-21）**：preset_plugin_defaults 提取为装载器
  独立函数，host 在 build() 之前调用——首启（无 config 必然无 LLM）
  提前 SystemExit 时插件默认段也已落盘，config.json 不再出现空 plugins
- **宿主服务化（2026-10-21）**：编排上移入口——__main__ 成为组合根
  （配置/锁/运行标记/日志/装配/停机信号/统一收尾），host 瘦身为 Host
  运行时服务（插件装载/三态信封/控制台或服务模式/四基建任务/思维收尾）；
  与将来 server 网络服务对等，两者只消费共享上下文
  （kernel/store/recorder/cfg/stop），互不 import
- **server 网络服务层上线（2026-10-21）**：始终启动（端口绑定失败降级）；
  零依赖 HTTP/SSE；API=状态/事件流/档案查询(rid=链路回放)/对话
  (say+webui_send+环形缓冲)/配置读写（密钥掩码，apply_updates+save 落盘）/记忆浏览/用量分账；
  插件面板机制：panel.py 契约（PANEL 元数据+routes(ctx,plugin)，鸭子类型
  零 server 依赖），挂 /api/plugins/<name>/*，拔插件=路由消失；
  qq 面板先行（status/chats/chatlog，ChatLog 补 chats()）；
  前端 Next.js+shadcn 待建（web/，静态导出由 server 托管）
- **行动后审视链路（2026-10-21）**：builtin 新增 action-review（行为层通用：
  发觉不对→撤销/补救/铭记，不可逆动作出手前慢一拍）；出站回执复述动作
  事实（contracts 约定，qq 发送回执带 text）；qq 自发消息落 chat_log
  （带 message_id 句柄，read_chat_log 可见，未读统计排除自己）
- **弱刺激响应三层机制（2026-10-22）**：隐性呼叫与弱刺激不再全靠强刺激
  才能触达心智——①唤醒别名（wake_aliases：叫名字不@也算呼叫，
  误报只是多看一眼）；②对话延续窗口（conversation_window_s：我
  发言后 N 秒内的跟话升格直达，仅路由不改文本——"对你说"是过度
  断言；窗口只在真发言时刷新，沉默不重置）；③弱刺激攒批唤醒
  （阈值/兑底双触发+冷却钳制，事件驱动非定时轮询，冷群零成本；
  AMBIENT kind 不在打断条件里，永不打断进行中的发送；触发即把
  最近消息递到眼前+已读同步，接不接话由心智自判）。
  chatlog 扫描内核收口 _unread_scan（{会话: [数, 最早]}），
  unread_summary/unread_state 共用；判断权始终在模型，代码只决定
  "什么时候递到眼前"
- **全量 ruff format + lint 清零（2026-10-22）**：line-length 100→500
  （声明式配置一字段一行）；ruff 首次实际接入：46 文件重排 +
  存量 79 告警清零（引号注解/import 排序/未用导入/collections.abc
  迁移等），掲出并修复两个潜伏 NameError（压缩 span/存储 logger）；
  工具入口命名规范同步约定（单工具 make_X_tool、多工具
  build_X_tools，公开命名）
- **配置声明化 + 设置页 schema 驱动（2026-10-22）**：utils/config 新增
  Setting（默认值即类型，description/secret/restart/group 元数据即文档），
  _JSON_FIELDS 全量声明化（行注释转正为 description），_SECRET_KEYS
  由声明派生；插件 Field 继承 Setting（共用 schema 投影），
  PluginConfig.declared_fields() + 装载器登记（register_plugin_schema，
  含未启用插件）；qq 配置抽到独立 plugins/qq/config.py；
  新端点 GET /api/config/schema（声明投影 + env_set），
  apply_updates 支持 plugins.<name>.<key> 点路径（按声明校验+coerce）；
  前端设置页 schema 驱动重写（分组/类型控件/密码框/重启角标/env
  锁定/插件设置区，零硬编码）
- **时间感全链路闭环（2026-10-22）**：数据层——会话消息全类型带 ts
  （user 取 Envelope.ts、assistant/tool 写入时刻；build_request 发 API
  前剥离；JSONL 持久化/重启恢复不再丢时间），压缩摘要带时间范围
  （span：被压缩部分首尾，session.span 持久化，API 摘要行渲染），
  latest_packet 返回 {content, ts}；展示层——记忆工具行内时间转人读
  （ts→time/due_at→due，epoch 不进心智；format_ts 移入 utils/clock
  + 新增 format_rel 相对时间；build_memory_tools 加 cfg 参数），
  boot/restart/seed 信封带"现在是 X"（出生/重生知昼夜），插话带
  [HH:MM] 到达前缀，未读/待办概览带相对时间，提醒到点信封带当前时间
- **config 缺字段自动补全（2026-10-21）**：reload 发现白名单键缺失
  （升级新增键/手改丢失）时——内存回落默认值，并按默认生成的顺序与
  内容回写 config.json（文件始终保持完整自文档）；未知键在补全/
  save 时保留不丢；三处原子写收口 _atomic_write；首建文件解析短路
  bug 一并修复（_write_default 同步戳导致 env 种子/server_token
  未生效）
- **坏 JSON 容错（2026-10-21）**：解析失败/根非对象时 env 种子仍生效
  （容器 env 配齐即可带病运行，日志有 error），坏文件保持原样等修复
  （不破坏性覆盖），server_token 内存兑底不落盘（API 不裸奔）；
  env 应用收口 _apply_env_seeds
- **主体时钟上线（2026-10-21）**：utils/clock 统一收口时间感知——
  全局配置 timezone（IANA 名，空=本机时区；env 种子 LIFEMATE_TIMEZONE，
  容器默认 UTC 故部署必设），非法值告警回落本机；接继心智的两处
  时间表达：唤醒信封的"现在几点"（mind._compose_wake）与 chatlog
  时间标注/日切/保留期（存储仍是 epoch，时区中立）；replay 面板
  保持本机时区（开发工具）
- **chatlog 时间感修复（2026-10-21）**：read_chat_log 每条带时间标注
  （chatlog.format_ts：今天 HH:MM/昨天/前天/今年 MM-DD/跨年带年份），
  get_message_detail 回执补可读 time 字段——修复心智把旧消息当成刚发的；
  存储侧 ts 本就全量落库，问题仅在展示层丢弃
- **webui 前端上线（2026-10-22）**：web/（Next.js 16 静态导出 + shadcn +
  react-query + pnpm）——概览/事件流(SSE·rid 周期分组)/历史回放/对话/
  记忆/用量/设置(读写·密钥掩码)/插件面板(?name=)；深浅双主题；
  token 门（无账号体系，来自 config.json server_token，空值自动换新）；
  排障修复：Turbopack 模块复制→Context globalThis 单例、HTML no-cache
  （防重建后旧页）、Next16 RSC 预取 URL 翻译；Playwright 端到端验证通过
- **自主节奏链路（2026-10-21）**：裸 wait 无限沉睡问题——rest-rhythm 增补睡前
  自省（过 curiosity/needs/reminders 清单，全空也定闲时闹钟）+ 措辞事实化
  （不定闹钟=无限期沉睡）；闹钟意图进 _Waiting.intent，事件唤醒取代
  未到点闹钟时唤醒信封附注还给她自决（超时自然醒/裸 wait 不附注）
- **停机链路修复（2026-10-22）**：SSE+wait_closed 挂死（开着的面板便 Ctrl+C 无效）
  三层修复——SSE 事件/停机/心跳三路等待（stop 即退流）、wait_closed 3s 限时、
  入口收尾进 finally（档案/库先落地不被网络服务阻塞）；分步停机日志
  （host：思维/基建/插件；入口①-⑥）任何退出路径留痕；停机中再收信号
  = 强制退出逃生口（os._exit + 进度日志）；回归测试覆盖开连接停机场景
- **工具归位 tools.py（2026-10-22）**：全模块统一约定——工具一律住
  <module>/tools.py。kernel/tools.py（wait/read_skill/deep_sleep 工厂，
  从 mind/host 提出）、server/tools.py（webui_send，从 outbound 提出，
  ChatBus 补 running/connection_count 公开访问器）、plugins/qq/tools.py
  （20 个工具 + _ok/_err 回执助手，从 __init__ 提出，感知面/表达面分文件）；
  纯搬迁零行为变化
- **回复链判定修复（2026-10-22）**：reply 段不再无条件视为回自己——
  chat_log 本地反查解引用 reply 指针（message_of，新→旧回溯 7 天）：
  是自己发的才算 reply_to_me（与 @ 独立）；查到则内联引用内容
  「[回复 名字：内容]」（醒来即有上下文，零 API）；查不到降级 ambient
  （自己发的全在档，查不到几乎必非回自己；漏接兑底=未读概览）；
  无反查通道保守直达。修复“回别人+@别人误判直达”与回老消息误唤醒
- **跨会话上下文供给（2026-10-22）**：修“串会话”问题（B群问在干嘛、
  用A群语境回答）——直达信封与上一条来源会话不同时，附该会话最近
  N 条（envelope_context_n，默认 4，0=关）；同会话连续直达不重复附加；
  SKILL 加跨群切换引导（核对信封来源群、read_chat_log 用信封群号）。
  根因是单一心智多群并发的认知负荷，非管道串线
- **终端呈现补输入（2026-10-22）**：console_printer 增 USER_MSG 渲染
  （📨 信封：来源·类型·会话标签 + 正文前 6 行/行 160 字截断）——
  修终端只见输出不见输入的连续性断裂；图标与 web/replay 同源（📨）
- **主人消息降级（2026-10-22）**：is_owner 不再无条件直达（原语义“主人消息
  优先处理”）——@/别名/回复/私聊/对话延续窗口已覆盖真实呼叫场景，
  主人跟别人说话不再误唤醒；（主人）标注保留（信息非门控），
  私聊照旧直达；owner_uin 描述与 SKILL 同步
- **待开发**：attention（门控升格）→ sandbox → webui → video

## 验证方式

- 单测：`pytest`（剥夺测试必须常绿）
- 剥夺模式运行：`python -m lifemate`（无器官启动；stdin 每行=一封信封，
  `/reload` 热重载配置，`/quit` 退出；`--help` 看启动参数；
  浏览器面板 http://127.0.0.1:8300，token 见 config.json）
- 前端：`cd web && pnpm build`（产物 out/ 由 server 托管）；
  开发 `pnpm dev`（:3000，/api 代理到 :8300）
- 语法/风格：`ruff check`（已装；lint 保持零告警，提交前过 `ruff format`）

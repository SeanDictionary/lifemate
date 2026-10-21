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
8. **内核/记忆/人格之外的一切新能力必须以器官形式加入**，不进内核。
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
│   └── builtin/       #   内置机制指引（代码管理，常驻装配——核心制度保证在场）
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
├── host.py            # 器官宿主：事件循环、装载器官、驱动感知面、挂载观测
└── __main__.py        # 入口（当前：剥夺模式——无器官，stdin 为调试信封）
tests/                 # 单测 + 剥夺测试（必须常绿）
data/                  # 运行数据（gitignore）：config.json / memory.db /
                      #   sessions/ / observe/ / logs/*.log / backups/ /
                      #   lifemate.lock
├── organs/            # 器官包（可拆，逐件开发，每器官一文件）
│   └── ...            #   开发顺序：qq → attention → schedule → sleep →
│                      #   sandbox → governance → video → webui
├── soul/              # 人格（identity.md——数据非代码）
├── skills/            # skill 目录：**内置以外**的第三方/扩展功能知情层
│                      # （器官说明书随器官走；用户自加扩展放这里即生效）
├── host.py            # 器官宿主：事件循环、装载器官、驱动感知面
└── __main__.py        # 入口（当前：剥夺模式——无器官，stdin 为调试信封）
tests/                 # 单测 + 剥夺测试（必须常绿）
data/                  # 运行数据（gitignore：memory.db / sessions/）
.cache/                # 开发副产物 + v1 归档 + 设计文档（gitignore）
```

依赖方向（唯一允许）：`host → organs → kernel.contracts ← kernel`；
`kernel/memory/organs/host → utils`（utils 不依赖任何上层）；器官之间零 import。
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
- **待开发器官顺序**：qq → attention → schedule → sleep → sandbox →
  governance → video → webui（器官开发时同步引入生命周期器官：
  轮换触发/重建包固化时机）

## 验证方式

- 单测：`pytest`（剥夺测试必须常绿）
- 剥夺模式运行：`python -m lifemate`（无器官启动；stdin 每行=一封信封，
  `/reload` 热重载配置，`/quit` 退出）
- 语法/风格：`ruff check`（如安装）

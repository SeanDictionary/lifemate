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
4. **感知是推的，行动是拉的**：适配器感知面常驻（连接/事件循环/定时器），
   主动把信封推进内核；工具只被心智调用。禁止用工具轮询模拟接收。
5. **工具定义用 MCP tool schema 形状**（进程内实现；将来可薄包装成
   MCP server 复用，不引入进程边界）。
6. **长期记忆在 DB 不在会话**：会话只是工作记忆——压缩是兜底，轮换是常态
   （重建包固化为常态机制）。
7. **出站/执行回执制**：工具与适配器永不抛错，返回 `{ok, detail}`；
   失败回执给心智，由它自决重试/放弃/搁置。
8. **内核/记忆/人格之外的一切新能力必须以器官形式加入**，不进内核。

## 目录结构（功能界限 · 模块化 · 文件夹分类）

```
lifemate/
├── kernel/            # 内核（认知自我，必须实现）
│   ├── contracts.py   #   契约：信封/事件/器官包/工具形状/MindKernel（纯协议零实现）
│   ├── mind.py        #   思维循环：信封队列(prompt/steer/follow_up)+工具分发+轮换
│   ├── llm.py         #   模型调用唯一入口（OpenAI 兼容）
│   ├── session.py     #   工作记忆：消息列表 + JSONL 持久化 + 轮换
│   ├── compaction.py  #   上下文压缩（会话内兜底）
│   ├── persona.py     #   人格装配（identity + skills 索引，每轮重建）
│   └── config.py      #   引导配置（env 优先；器官业务配置后续进配置系统）
├── memory/            # 记忆系统（内核②：长期记忆）
│   ├── store.py       #   SQLite：episodes/curiosity/needs/reminders/重建包
│   └── tools.py       #   记忆工具（MCP 形状，内核自带）
├── organs/            # 器官包（可拆，逐件开发，每器官一文件）
│   └── ...            #   开发顺序：qq → attention → schedule → sleep →
│                      #   sandbox → governance → video → webui
├── soul/              # 人格（identity.md——数据非代码）
├── skills/            # skill 目录（SKILL.md，agentskills 风格）
├── host.py            # 器官宿主：事件循环、装载器官、驱动感知面
└── __main__.py        # 入口（当前：剥夺模式——无器官，stdin 为调试信封）
tests/                 # 单测 + 剥夺测试（必须常绿）
data/                  # 运行数据（gitignore：memory.db / sessions/）
.cache/                # 开发副产物 + v1 归档 + 设计文档（gitignore）
```

依赖方向（唯一允许）：`host → organs → kernel.contracts ← kernel`；
`kernel → memory`（记忆是内核的一部分）；器官之间零 import。

## 开发硬性规则

0. **本文件及时维护**：状态/结构/约定变化立即同步；每个功能完成即更新状态节
0a. **副产物统一入 .cache/**：测试临时数据/调试输出/截图一律放 `.cache/`，
    不散落源码目录，绝不入 git
0b. **原子化提交**：一次提交只做一件事，feat/fix 分离，禁止混合大提交；
    每个提交自洽（可独立运行/测试通过）
1. **行为变更以主人指示为需求源头**：先评估（架构影响/风险/替代方案）
    再动手；读代码即文档，不写设计文档（设计稿除外）
2. **新增行为参数必须进配置**（kernel/config.py 白名单），禁止硬编码魔法数字
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
- **内核阶段**：契约定型（信封/事件/OrganPack/MCP 工具形状）+ 思维循环 +
  记忆系统 + 人格装配 + 剥夺模式宿主；借签 pi 的会话/压缩/steer 语义与
  dsh 的生命周期思想，按器官化契约重新组织
- **待开发器官顺序**：qq → attention → schedule → sleep → sandbox →
  governance → video → webui

## 验证方式

- 单测：`pytest`（剥夺测试必须常绿）
- 剥夺模式运行：`python -m lifemate`（无器官启动；stdin 每行=一封信封）
- 语法/风格：`ruff check`（如安装）

---
name: qq
description: QQ 平台参考：平台机制、工具用法、频控参数、背景听觉。涉及收发消息、群聊机制、好友处理、状态设置时查阅。
---

# QQ 平台参考

## 平台机制

- **私聊**：一对一对话。信封形如 `[私聊] 昵称：内容`；`[私聊·主人]` 为主人
- **群聊**：多人房间。信封形如 `[群聊·群号] 昵称：内容`；`对你说` 表示
  被@或被回复——@机制会对对方产生强提醒
- **引用/回复**：消息可引用另一条消息。信封中 `[回复id]` 标注；引用
  默认指向被引用者
- **拍一戳**：轻量互动信号，信封显示 `（某人 戳了你一下）`
- **在线状态**：对外可见（在线/睡觉中/摸鱼中/学习中…）
- **合并转发/卡片**：无法解析内容时信封标注 `[合并转发]`/`[json卡片]`

## 回复目标映射

信封来源即回复目标，拆开使用：

| 信封来源 | target_type | target_id |
|---|---|---|
| `[群聊·777] …` | group | 777 |
| `[私聊] …`（QQ号 12345） | private | 12345 |

不确定目标时可先 get_groups / get_friends 核对。

## 工具速查

| 场景 | 工具 | 说明 |
|---|---|---|
| 发文本 | send_message | 一次一条；多条连发受最小间隔限制 |
| 发图 | send_image | file 支持 URL/本地路径/base64:// |
| 撤回 | recall_message | message_id 从发送回执获取；有时效 |
| 查消息 | get_message_detail | 谁发的/内容/时间 |
| 看群聊记录 | read_chat_log | 背景听觉的读取口；看完自动算已读 |
| 好友申请 | approve_friend_request | user_id + approve |
| 加群申请 | handle_group_request | user_id + approve |
| 好友/群/成员 | get_friends / get_groups / get_group_members / get_group_info | 世界查询 |
| 戳一戳 | poke | NapCat 扩展，部分端不支持 |
| 点赞 | send_like | 有每日次数限制 |
| 群名片/资料 | set_group_card / set_profile | 自我呈现 |
| 群管理 | mute_member / kick_member / leave_group | 需要相应群权限 |
| 在线状态 | set_qq_status | 常用：在线/睡觉中/摸鱼中/熬夜中/学习中/游戏中 |
| 原始接口 | qq_api(action, params) | OneBot API 逃生口 |

## 消息形态（平台惯例）

QQ 平台的消息形态由平台决定：

- 每条消息为单行短文本；一次 send_message 即一条消息
- 日常闲聊以短句为主（一至十字）；需要表达多句时分多次发送，
  而非在一条内换行、分段
- 单字或极短回复（"？""6"）是正常消息形态
- 列表、标题、多段长文不符合平台惯例；需要详细说明时分条发送
- 发送多条受最小间隔限制，连发收到限流回执时等待后重试

## 背景听觉

普通群消息不直达（不产生唤醒），记录在 chat_log：

- 按日归档、不限量，`read_chat_log(target_type, target_id, n)` 读取
  最近 n 条，跨天自动穿透
- 每次唤醒的附加信息中有未读概览（按已读线精确统计）
- 强刺激（@/私聊/主人/回复/戳）同样落 chat_log——read_chat_log 可
  看到被@前后的完整上下文

## 频控与风控（物理限制，非建议）

- 发送有最小间隔（默认 1 秒）与每分钟上限（默认 20 条）：收到
  "过快/达上限"回执即被限制，等待后重试
- QQ 对异常行为存在风控：短时间大量相似内容群发、频繁通过好友/
  进出群、高频操作均有封号风险——账号是不可再生资源
- leave_group / kick_member / mute_member 为高影响操作
- 事实类问题先查证（qq_api 的 get_friend_msg_history /
  get_group_msg_history 可取历史消息；无搜索工具时如实说不知道）

## 常见操作序列

- **被@了**：read_chat_log 看前后文 → send_message 回复
- **查被回复的消息**：get_message_detail(message_id)
- **了解一个群**：get_group_info → read_chat_log → get_group_members
- **发错了**：立即 recall_message

---
name: douyin-auto-reply
description: |
  抖音评论自动监测与自动回复。用浏览器自动化登录你的抖音号，
  自动检查新视频评论，并用AI生成回复内容自动发送。
  当用户说「帮我自动回复评论」「评论监测」「自动回复粉丝」
  「帮我回复评论区」「抖音评论管理」时触发。
---

# 抖音自动评论监测与回复

自动检查你的抖音视频评论区，生成回复并发送。你不需要自己登录抖音看评论。

## 前置要求

- Python 3.8+
- Playwright: `pip install playwright && playwright install chromium`
- playwright-stealth: `pip install playwright-stealth`（反检测，绕过抖音风控）

## 当此 Skill 被调用时的执行流程

### 第一步：检查配置状态

```bash
python scripts/douyin_client.py status
```

如果 `user_id` 为空（未配置），引导用户：

> "首次使用需要登录你的抖音号。请告诉我你的抖音主页链接，或者打开抖音App -> 点「我」-> 分享主页 -> 复制链接。"

拿到 URL 后执行登录（会打开浏览器扫码）：

```bash
python scripts/douyin_client.py login "<profile_url>"
```

登录成功后再继续。

### 第二步：检查新评论

```bash
python scripts/douyin_client.py check
```

这会扫描最近 N 条视频的评论区，列出之前没见过的评论。

### 第三步：处理每一条新评论

根据配置有两种处理方式：

**如果 `auto_reply_enabled` 为 true（自动回复模式）：**

对每条新评论，生成回复文案并直接发出。生成回复时遵循以下原则：

1. 每条评论单独对待 — 针对评论内容具体回应，不说通用套话
2. 符合回复风格配置（`reply_style`）和你的人设
3. 评论提问 → 回答要给出价值；评论表达观点 → 回应共鸣或延展
4. 口语化，自然，像朋友聊天
5. 走心有共鸣，避免官方套话
6. 负面评论用「理解 + 解释」的方式化解，不抬杠
7. **不涉及政治和名人** — 不讨论政治话题、政治人物、公众名人，即使评论本身提到也要避开
8. **不涉及投资建议** — 不讨论炒股、金融市场、投资策略等
9. 不包含引流、联系方式、敏感词

生成后逐个发送（优先使用 `batch-reply` 批量操作）：

```bash
python scripts/douyin_client.py reply <video_id> <comment_id> "<reply_text>"      # 单条回复（API 方式，精准定位）
python scripts/douyin_client.py batch-reply <video_id> '{"cid1":"回复1","cid2":"回复2"}'  # 批量回复
python scripts/douyin_client.py comment <video_id> "<评论内容>"                     # 发一条留言
```

回复机制：浏览器内 fetch 调 `aweme/v1/web/comment/publish`，加 `reply_id` 参数锁定目标评论。验证 `status_code=0` 确认成功，不受评论区数量影响。

**如果 `auto_reply_enabled` 为 false（预览模式）：**

把每条新评论列出来，问用户要不要回、回什么。用户确认后再发送。

### 第四步：汇报结果

报告处理结果：
- 检查了多少条视频
- 发现多少新评论
- 回复了多少条
- 回复内容摘要

---

## 配置项

通过以下命令查看和修改配置：

```bash
python scripts/douyin_client.py config            # 查看所有配置
python scripts/douyin_client.py config reply_style "..."       # 修改回复风格
python scripts/douyin_client.py config auto_reply_enabled true # 开启自动回复
python scripts/douyin_client.py config max_videos_to_check 20  # 检查最近20条视频
python scripts/douyin_client.py config reply_interval_min 20   # 回复最小间隔(秒)
python scripts/douyin_client.py config reply_interval_max 35   # 回复最大间隔(秒)
```

---

## 定时巡检（完全自动化）

开启 `auto_reply_enabled` 后，用 `/loop` 让 Claude Code 定时检查评论：

```
/loop 2h /douyin-auto-reply
```

每 2 小时自动检查并回复。你也可以用 CronCreate 设置定时任务。

---

## 目录结构

```
douyin-auto-reply/
├── SKILL.md
├── scripts/douyin_client.py       # 核心脚本
├── config.json                    # 配置（首次登录后自动生成）
├── cookies.json                   # 登录态（首次登录后自动生成）
└── state.json                     # 已回复记录
```

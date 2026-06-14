# 抖音自动评论监测与回复 (Douyin Auto Reply)

浏览器自动化工具，自动监测你的抖音视频评论区，支持 AI 生成回复内容并自动发送。

## 功能

- 自动检查所有视频的新评论
- AI 根据评论内容生成回复文案
- 支持自动回复和手动预览两种模式
- 频率控制（防风控）
- 支持 headless 模式运行

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/zqtbread/douyin-auto-reply.git
cd douyin-auto-reply

# 2. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 3. 首次登录（会打开浏览器，扫码登录你的抖音号）
#    抖音主页链接获取：打开抖音App → 点「我」→ 右上角分享 → 复制链接
python scripts/douyin_client.py login "https://www.douyin.com/user/..."

# 4. 检查新评论
python scripts/douyin_client.py check

# 5. 回复评论
python scripts/douyin_client.py reply <video_id> <comment_id> "回复内容"

# 6. 批量回复
python scripts/douyin_client.py batch-reply <video_id> '{"cid1":"回复1","cid2":"回复2"}'

# 7. 查看统计
python scripts/douyin_client.py status
```

## 配置

首次登录后会自动生成 `config.json`，也可以手动修改：

```bash
python scripts/douyin_client.py config reply_style "你的回复风格"
python scripts/douyin_client.py config reply_interval_min 20   # 最小间隔(秒)
python scripts/douyin_client.py config reply_interval_max 35   # 最大间隔(秒)
python scripts/douyin_client.py config auto_reply_enabled true # 开启自动回复
```

## 手动操作

当自动发送失败或需手动确认时：

```bash
python scripts/douyin_client.py manual-comment <video_id> "评论内容"
python scripts/douyin_client.py manual-reply <video_id> <comment_id> "回复内容"
```

## 技术说明

- 使用 Playwright 浏览器自动化
- 内置 playwright-stealth 反检测（防止抖音风控）
- 回复通过浏览器内 fetch 调 API 实现（`aweme/v1/web/comment/publish` + `reply_id`），不依赖 DOM 定位
- 每条回复验证 `status_code=0` 确保发送成功

## 项目结构

```
douyin-auto-reply/
├── scripts/douyin_client.py    # 核心脚本
├── config.example.json         # 配置模板
├── requirements.txt            # Python 依赖
├── pyproject.toml
├── README.md
└── .gitignore
```

## 免责声明

本项目仅用于学习和研究目的。使用本工具时，请遵守抖音社区规范和相关法律法规。用户自行承担使用风险。

# Telegram 群组打卡任务机器人

这是一个 Telegram 群聊机器人 MVP，支持群成员每日签到、上下班打卡、管理员分配每日任务、成员报备任务进度。

## 功能

- 直接说 `签到`：每日签到
- 直接说 `上班` / `下班`：上下班打卡
- 直接说 `我的任务` / `今日记录`：查看个人信息
- 管理员回复成员消息后直接说 `任务 任务内容`：给该成员分配任务
- 管理员直接说 `任务 @username 任务内容`：按用户名分配任务
- 成员直接说 `进度 任务ID 进度说明`：报备任务进度
- 成员直接说 `完成 任务ID`：标记任务完成
- 管理员直接说 `今日任务` / `今日汇总`：查看群组数据
- `/sign`：每日签到
- `/clock_in`：上班打卡
- `/clock_out`：下班打卡
- `/task @username 任务内容`：管理员给成员分配今日任务
- 回复成员消息后发送 `/task 任务内容`：管理员用回复方式分配任务
- `/progress 任务ID 进度说明`：成员报备任务进度
- `/done 任务ID [说明]`：成员标记任务完成
- `/mytasks`：查看自己的今日任务
- `/tasks`：管理员查看群组今日任务
- `/today`：查看自己的今日签到和打卡记录
- `/report`：管理员查看群组今日汇总
- `/help`：查看命令帮助

## 本地运行

1. 在 Telegram 找 `@BotFather` 创建机器人，拿到 token。
2. 把机器人加入目标群组，并给机器人读取群消息和命令的权限。
3. 创建虚拟环境并安装依赖：

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

4. 复制配置文件：

```bat
copy .env.example .env
```

5. 编辑 `.env`，填入 `TELEGRAM_BOT_TOKEN`。
6. 启动：

```bat
run.bat
```

## 测试

```bat
set PYTHONPATH=%CD%\src
python -m unittest discover tests
```

## 说明

- 数据默认保存到 `data/bot.sqlite3`。
- 通过 `@username` 分配任务时，目标成员需要先在群里使用过任意机器人命令，机器人才能知道他的 Telegram 用户 ID。
- 更推荐管理员“回复某个成员消息”后直接发送 `任务 任务内容`，这样不依赖 username。
- `任务`、`今日任务`、`今日汇总` 仅群管理员可用。

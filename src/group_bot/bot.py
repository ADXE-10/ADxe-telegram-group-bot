from __future__ import annotations

from datetime import datetime

from telegram import Chat, Update, User
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, ContextTypes

from group_bot.config import load_settings
from group_bot.db import connect, migrate
from group_bot.repository import Repository, UserProfile


HELP_TEXT = """可用命令：
/sign - 每日签到
/clock_in - 上班打卡
/clock_out - 下班打卡
/today - 查看自己的今日记录
/mytasks - 查看自己的今日任务
/progress 任务ID 进度说明 - 报备任务进度
/done 任务ID [说明] - 标记任务完成

管理员命令：
/task @username 任务内容 - 分配今日任务
回复成员消息后 /task 任务内容 - 分配给被回复成员
/tasks - 查看今日任务
/report - 查看今日汇总"""

KIND_LABELS = {
    "sign": "签到",
    "clock_in": "上班",
    "clock_out": "下班",
}


class BotRuntime:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.conn = connect(self.settings.database_path)
        migrate(self.conn)
        self.repo = Repository(self.conn)

    def now(self) -> datetime:
        return datetime.now(self.settings.timezone)

    def today(self) -> str:
        return self.now().date().isoformat()


runtime = BotRuntime()


def _chat_id(update: Update) -> int:
    if not update.effective_chat:
        raise RuntimeError("missing chat")
    return int(update.effective_chat.id)


def _user(update: Update) -> User:
    if not update.effective_user:
        raise RuntimeError("missing user")
    return update.effective_user


def _display_name(user: User) -> str:
    return user.full_name or user.username or str(user.id)


async def _remember_user(update: Update) -> None:
    chat_id = _chat_id(update)
    user = _user(update)
    runtime.repo.upsert_user(
        UserProfile(
            chat_id=chat_id,
            user_id=int(user.id),
            username=user.username,
            full_name=_display_name(user),
        ),
        runtime.now(),
    )


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return False
    if chat.type == Chat.PRIVATE:
        return True
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}


async def _require_group(update: Update) -> bool:
    chat = update.effective_chat
    if chat and chat.type in {Chat.GROUP, Chat.SUPERGROUP}:
        return True
    if update.message:
        await update.message.reply_text("这个机器人用于 Telegram 群组，请把我加入群聊后使用。")
    return False


def _time_part(iso_text: str | None) -> str:
    if not iso_text:
        return "-"
    return iso_text.split("T", 1)[-1][:8]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _remember_user(update)
    if update.message:
        await update.message.reply_text("我已就绪。发送 /help 查看命令。")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _remember_user(update)
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def _attendance(update: Update, kind: str) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    user = _user(update)
    inserted = runtime.repo.record_attendance(
        _chat_id(update), int(user.id), kind, runtime.today(), runtime.now()
    )
    label = KIND_LABELS[kind]
    text = f"{label}成功，{_display_name(user)}。" if inserted else f"今天已经{label}过了。"
    if update.message:
        await update.message.reply_text(text)


async def sign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _attendance(update, "sign")


async def clock_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _attendance(update, "clock_in")


async def clock_out(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _attendance(update, "clock_out")


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    rows = runtime.repo.get_attendance(_chat_id(update), int(_user(update).id), runtime.today())
    values = {row["kind"]: _time_part(row["created_at"]) for row in rows}
    lines = [
        f"今日记录（{runtime.today()}）",
        f"签到：{values.get('sign', '-')}",
        f"上班：{values.get('clock_in', '-')}",
        f"下班：{values.get('clock_out', '-')}",
    ]
    if update.message:
        await update.message.reply_text("\n".join(lines))


async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    if not await _is_admin(update, context):
        if update.message:
            await update.message.reply_text("只有群管理员可以分配任务。")
        return

    if not update.message:
        return

    assignee_user_id: int | None = None
    assignee_username: str | None = None
    title = ""

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        assignee = update.message.reply_to_message.from_user
        assignee_user_id = int(assignee.id)
        assignee_username = assignee.username
        title = " ".join(context.args).strip()
        runtime.repo.upsert_user(
            UserProfile(
                chat_id=_chat_id(update),
                user_id=assignee_user_id,
                username=assignee.username,
                full_name=_display_name(assignee),
            ),
            runtime.now(),
        )
    elif len(context.args) >= 2 and context.args[0].startswith("@"):
        found = runtime.repo.find_user_by_username(_chat_id(update), context.args[0])
        if not found:
            await update.message.reply_text("还不认识这个成员。请让对方先使用一次 /sign，或回复他的消息后分配任务。")
            return
        assignee_user_id = int(found["user_id"])
        assignee_username = found["username"]
        title = " ".join(context.args[1:]).strip()
    else:
        await update.message.reply_text("用法：/task @username 任务内容，或回复成员消息后发送 /task 任务内容")
        return

    if not title:
        await update.message.reply_text("任务内容不能为空。")
        return

    task_id = runtime.repo.create_task(
        chat_id=_chat_id(update),
        assignee_user_id=assignee_user_id,
        assignee_username=assignee_username,
        title=title,
        task_date=runtime.today(),
        created_by_user_id=int(_user(update).id),
        now=runtime.now(),
    )
    await update.message.reply_text(f"已创建任务 #{task_id}：{title}")


async def mytasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    rows = runtime.repo.list_tasks_for_user(_chat_id(update), int(_user(update).id), runtime.today())
    if not rows:
        text = "你今天暂无任务。"
    else:
        lines = [f"我的今日任务（{runtime.today()}）"]
        for row in rows:
            status = "已完成" if row["status"] == "done" else "进行中"
            lines.append(f"#{row['id']} [{status}] {row['title']}")
        text = "\n".join(lines)

    if update.message:
        await update.message.reply_text(text)


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    if not await _is_admin(update, context):
        if update.message:
            await update.message.reply_text("只有群管理员可以查看群组任务。")
        return

    rows = runtime.repo.list_tasks(_chat_id(update), runtime.today())
    if not rows:
        text = "今天还没有分配任务。"
    else:
        lines = [f"群组今日任务（{runtime.today()}）"]
        for row in rows:
            status = "已完成" if row["status"] == "done" else "进行中"
            assignee = row["full_name"] or row["assignee_username"] or row["assignee_user_id"]
            lines.append(f"#{row['id']} [{status}] {assignee}：{row['title']}")
        text = "\n".join(lines)

    if update.message:
        await update.message.reply_text(text)


async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    if len(context.args) < 2 or not context.args[0].isdigit():
        if update.message:
            await update.message.reply_text("用法：/progress 任务ID 进度说明")
        return

    task_id = int(context.args[0])
    note = " ".join(context.args[1:]).strip()
    row = runtime.repo.get_task(_chat_id(update), task_id)
    if not row:
        if update.message:
            await update.message.reply_text("没有找到这个任务。")
        return

    is_owner = int(row["assignee_user_id"]) == int(_user(update).id)
    if not is_owner and not await _is_admin(update, context):
        if update.message:
            await update.message.reply_text("只能报备自己的任务进度。")
        return

    runtime.repo.add_progress(_chat_id(update), task_id, int(_user(update).id), note, runtime.now())
    if update.message:
        await update.message.reply_text(f"已记录任务 #{task_id} 的进度。")


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    if not context.args or not context.args[0].isdigit():
        if update.message:
            await update.message.reply_text("用法：/done 任务ID [说明]")
        return

    task_id = int(context.args[0])
    row = runtime.repo.get_task(_chat_id(update), task_id)
    if not row:
        if update.message:
            await update.message.reply_text("没有找到这个任务。")
        return

    is_owner = int(row["assignee_user_id"]) == int(_user(update).id)
    if not is_owner and not await _is_admin(update, context):
        if update.message:
            await update.message.reply_text("只能完成自己的任务。")
        return

    note = " ".join(context.args[1:]).strip() or None
    runtime.repo.mark_done(_chat_id(update), task_id, int(_user(update).id), note, runtime.now())
    if update.message:
        await update.message.reply_text(f"任务 #{task_id} 已标记完成。")


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    if not await _is_admin(update, context):
        if update.message:
            await update.message.reply_text("只有群管理员可以查看汇总。")
        return

    attendance_rows = runtime.repo.attendance_summary(_chat_id(update), runtime.today())
    task_rows = runtime.repo.list_tasks(_chat_id(update), runtime.today())
    progress_map = runtime.repo.progress_for_tasks([int(row["id"]) for row in task_rows])

    lines = [f"今日汇总（{runtime.today()}）", "", "签到/打卡："]
    if attendance_rows:
        for row in attendance_rows:
            name = row["full_name"] or row["username"] or row["user_id"]
            lines.append(
                f"{name}：签到 {_time_part(row['sign_at'])}，上班 {_time_part(row['clock_in_at'])}，下班 {_time_part(row['clock_out_at'])}"
            )
    else:
        lines.append("暂无记录")

    lines.extend(["", "任务："])
    if task_rows:
        for row in task_rows:
            status = "已完成" if row["status"] == "done" else "进行中"
            assignee = row["full_name"] or row["assignee_username"] or row["assignee_user_id"]
            progress_count = len(progress_map.get(int(row["id"]), []))
            lines.append(f"#{row['id']} [{status}] {assignee}：{row['title']}（进度 {progress_count} 条）")
    else:
        lines.append("暂无任务")

    if update.message:
        await update.message.reply_text("\n".join(lines))


def build_application() -> Application:
    app = Application.builder().token(runtime.settings.token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("sign", sign))
    app.add_handler(CommandHandler("clock_in", clock_in))
    app.add_handler(CommandHandler("clock_out", clock_out))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("task", task))
    app.add_handler(CommandHandler("mytasks", mytasks))
    app.add_handler(CommandHandler("tasks", tasks))
    app.add_handler(CommandHandler("progress", progress))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("report", report))
    return app


def main() -> None:
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

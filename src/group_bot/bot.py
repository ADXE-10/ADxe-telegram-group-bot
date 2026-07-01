from __future__ import annotations

import re
from datetime import datetime

from telegram import Chat, Update, User
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from group_bot.config import load_settings
from group_bot.db import connect, migrate
from group_bot.repository import Repository, UserProfile


HELP_TEXT = """常用说法：
签到
上班 / 上班打卡
下班 / 下班打卡
我的任务
今日记录
进度 1 进度说明
1完成.2完成

管理员说法：
回复成员消息：任务 1. 任务内容 2. 任务内容
任务 @username 1. 任务内容 2. 任务内容
今日任务
今日汇总

兼容命令：
/sign /clock_in /clock_out /today /mytasks
/task /progress /done /tasks /report"""

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


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _split_id_and_note(text: str) -> tuple[int | None, str]:
    parts = _clean_text(text).split(" ", 1)
    if not parts or not parts[0].isdigit():
        return None, ""
    return int(parts[0]), parts[1].strip() if len(parts) > 1 else ""


def _split_numbered_tasks(text: str) -> list[str]:
    text = _clean_text(text)
    matches = list(re.finditer(r"(?:^|\s)(\d+)[\.、\)）]\s*", text))
    if not matches:
        return [text] if text else []

    tasks: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = text[start:end].strip(" .。;；,，")
        if title:
            tasks.append(title)
    return tasks


def _parse_done_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    for match in re.finditer(r"(?<!\d)(\d+)\s*(?:完成|已完成|做完)(?:了)?", text):
        number = int(match.group(1))
        if number not in numbers:
            numbers.append(number)
    return numbers


def _numbered_user_tasks(chat_id: int, user_id: int) -> list:
    rows = runtime.repo.list_tasks_for_user(chat_id, user_id, runtime.today())
    return sorted(rows, key=lambda row: int(row["id"]))


def _resolve_user_task(chat_id: int, user_id: int, number: int):
    rows = _numbered_user_tasks(chat_id, user_id)
    if 1 <= number <= len(rows):
        return rows[number - 1]

    row = runtime.repo.get_task(chat_id, number)
    if row and int(row["assignee_user_id"]) == int(user_id):
        return row
    return None


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


async def _mentions_bot(text: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    bot_user = await context.bot.get_me()
    if not bot_user.username:
        return False
    return f"@{bot_user.username.lower()}" in text.lower()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _remember_user(update)
    if update.message:
        await update.message.reply_text("我已就绪。发送 /help 查看用法。")


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


async def _create_task(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
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
    task_text = ""
    text = _clean_text(text)

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        assignee = update.message.reply_to_message.from_user
        assignee_user_id = int(assignee.id)
        assignee_username = assignee.username
        task_text = text
        runtime.repo.upsert_user(
            UserProfile(
                chat_id=_chat_id(update),
                user_id=assignee_user_id,
                username=assignee.username,
                full_name=_display_name(assignee),
            ),
            runtime.now(),
        )
        assignee_name = _display_name(assignee)
    elif text.startswith("@"):
        parts = text.split(" ", 1)
        if len(parts) < 2:
            await update.message.reply_text("请这样发：任务 @成员 1. 任务内容 2. 任务内容")
            return
        found = runtime.repo.find_user_by_username(_chat_id(update), parts[0])
        if not found:
            await update.message.reply_text("还不认识这个成员。请让对方先发一次“签到”，或回复他的消息后发“任务 1. 任务内容”。")
            return
        assignee_user_id = int(found["user_id"])
        assignee_username = found["username"]
        task_text = parts[1].strip()
        assignee_name = found["full_name"] or assignee_username
    else:
        await update.message.reply_text("请回复某个成员的消息后发送：任务 1. 任务内容 2. 任务内容")
        return

    task_titles = _split_numbered_tasks(task_text)
    if not task_titles:
        await update.message.reply_text("任务内容不能为空。")
        return

    created_ids: list[int] = []
    for title in task_titles:
        task_id = runtime.repo.create_task(
            chat_id=_chat_id(update),
            assignee_user_id=assignee_user_id,
            assignee_username=assignee_username,
            title=title,
            task_date=runtime.today(),
            created_by_user_id=int(_user(update).id),
            now=runtime.now(),
        )
        created_ids.append(task_id)

    existing_count = len(_numbered_user_tasks(_chat_id(update), assignee_user_id)) - len(created_ids)
    lines = [f"已给 {assignee_name or '成员'} 创建 {len(created_ids)} 项任务："]
    for offset, title in enumerate(task_titles, start=1):
        lines.append(f"{existing_count + offset}. {title}")
    await update.message.reply_text("\n".join(lines))


async def task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _create_task(update, context, " ".join(context.args))


async def mytasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    rows = _numbered_user_tasks(_chat_id(update), int(_user(update).id))
    if not rows:
        text = "你今天暂无任务。"
    else:
        lines = [f"我的今日任务（{runtime.today()}）"]
        for index, row in enumerate(rows, start=1):
            status = "已完成" if row["status"] == "done" else "进行中"
            lines.append(f"{index}. [{status}] {row['title']}")
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
        per_user_counts: dict[int, int] = {}
        for row in sorted(rows, key=lambda item: (int(item["assignee_user_id"]), int(item["id"]))):
            user_id = int(row["assignee_user_id"])
            per_user_counts[user_id] = per_user_counts.get(user_id, 0) + 1
            status = "已完成" if row["status"] == "done" else "进行中"
            assignee = row["full_name"] or row["assignee_username"] or row["assignee_user_id"]
            lines.append(f"{assignee} {per_user_counts[user_id]}. [{status}] {row['title']}")
        text = "\n".join(lines)

    if update.message:
        await update.message.reply_text(text)


async def _add_progress(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    task_number, note = _split_id_and_note(text)
    if task_number is None or not note:
        if update.message:
            await update.message.reply_text("请这样发：进度 1 进度说明")
        return

    row = _resolve_user_task(_chat_id(update), int(_user(update).id), task_number)
    if not row:
        if update.message:
            await update.message.reply_text("没有找到这个任务。")
        return

    is_owner = int(row["assignee_user_id"]) == int(_user(update).id)
    if not is_owner and not await _is_admin(update, context):
        if update.message:
            await update.message.reply_text("只能报备自己的任务进度。")
        return

    runtime.repo.add_progress(_chat_id(update), int(row["id"]), int(_user(update).id), note, runtime.now())
    if update.message:
        await update.message.reply_text(f"已记录任务 {task_number} 的进度。")


async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_progress(update, context, " ".join(context.args))


async def _mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    task_number, note = _split_id_and_note(text)
    if task_number is None:
        if update.message:
            await update.message.reply_text("请这样发：完成 1，或直接发 1完成.2完成")
        return

    row = _resolve_user_task(_chat_id(update), int(_user(update).id), task_number)
    if not row:
        if update.message:
            await update.message.reply_text("没有找到这个任务。")
        return

    is_owner = int(row["assignee_user_id"]) == int(_user(update).id)
    if not is_owner and not await _is_admin(update, context):
        if update.message:
            await update.message.reply_text("只能完成自己的任务。")
        return

    runtime.repo.mark_done(
        _chat_id(update),
        int(row["id"]),
        int(_user(update).id),
        note or None,
        runtime.now(),
    )
    if update.message:
        await update.message.reply_text(f"任务 {task_number} 已标记完成。")


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _mark_done(update, context, " ".join(context.args))


async def _mark_done_numbers(
    update: Update, context: ContextTypes.DEFAULT_TYPE, numbers: list[int]
) -> None:
    if not await _require_group(update):
        return
    await _remember_user(update)

    completed: list[int] = []
    missing: list[int] = []
    for number in numbers:
        row = _resolve_user_task(_chat_id(update), int(_user(update).id), number)
        if not row:
            missing.append(number)
            continue
        runtime.repo.mark_done(
            _chat_id(update),
            int(row["id"]),
            int(_user(update).id),
            f"{number}完成",
            runtime.now(),
        )
        completed.append(number)

    lines: list[str] = []
    if completed:
        lines.append("已标记完成：" + "、".join(str(number) for number in completed))
    if missing:
        lines.append("没有找到任务：" + "、".join(str(number) for number in missing))
    if update.message and lines:
        await update.message.reply_text("\n".join(lines))


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
        per_user_counts: dict[int, int] = {}
        for row in sorted(task_rows, key=lambda item: (int(item["assignee_user_id"]), int(item["id"]))):
            user_id = int(row["assignee_user_id"])
            per_user_counts[user_id] = per_user_counts.get(user_id, 0) + 1
            status = "已完成" if row["status"] == "done" else "进行中"
            assignee = row["full_name"] or row["assignee_username"] or row["assignee_user_id"]
            progress_count = len(progress_map.get(int(row["id"]), []))
            lines.append(f"{assignee} {per_user_counts[user_id]}. [{status}] {row['title']}（进度 {progress_count} 条）")
    else:
        lines.append("暂无任务")

    if update.message:
        await update.message.reply_text("\n".join(lines))


async def plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text = _clean_text(update.message.text)
    normalized = text.replace("：", ":", 1)

    if text in {"规则", "使用规则", "帮助", "怎么用", "用法"} or await _mentions_bot(text, context):
        await help_command(update, context)
        return

    done_numbers = _parse_done_numbers(text)

    if done_numbers:
        await _mark_done_numbers(update, context, done_numbers)
    elif text in {"签到", "打卡"}:
        await sign(update, context)
    elif text in {"上班", "上班打卡", "上班签到"}:
        await clock_in(update, context)
    elif text in {"下班", "下班打卡"}:
        await clock_out(update, context)
    elif text in {"今日记录", "我的记录", "记录"}:
        await today(update, context)
    elif text in {"我的任务", "我任务"}:
        await mytasks(update, context)
    elif text in {"今日任务", "任务列表", "全部任务"}:
        await tasks(update, context)
    elif text in {"今日汇总", "汇总"}:
        await report(update, context)
    elif normalized.startswith(("任务 ", "任务:", "安排 ", "安排:")):
        payload = normalized.split(" ", 1)[1] if " " in normalized else normalized.split(":", 1)[1]
        await _create_task(update, context, payload)
    elif normalized.startswith(("进度 ", "进度:")):
        payload = normalized.split(" ", 1)[1] if " " in normalized else normalized.split(":", 1)[1]
        await _add_progress(update, context, payload)
    elif normalized.startswith(("完成 ", "完成:")):
        payload = normalized.split(" ", 1)[1] if " " in normalized else normalized.split(":", 1)[1]
        await _mark_done(update, context, payload)


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text))
    return app


def main() -> None:
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
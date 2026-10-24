#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2024 Luca Versari <veluca93@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import collections
from collections.abc import Awaitable, Callable
from typing import Any
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, joinedload

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.error import BadRequest, RetryAfter

from cms.db.base import Base

try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import datetime
import argparse
import asyncio
import html
import logging
import os
import re

# We use yaml instead of json or similar because
import yaml

from cms import config
from cms.conf import ConfigError
from cms.db import (
    ask_for_contest, Announcement, Contest, Group, Question, Participation)
from cms.db.session import SessionGen
from cms.util import contest_id_from_args
from cmscommon.datetime import EPOCH, make_datetime

from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
)


logger = logging.getLogger(__name__)

HELP_MESSAGE = """
Bot to interact with the questions and announcements of all the CMS \
contests. This bot will automatically post new questions in this \
channel, each with the hashtag of its contest, and allows you to reply \
to them and to create announcements.

In particular, you can use the <i>inline keyboard</i> to reply to \
questions with built-in answers or to ignore questions, and you \
can use the following commands:

<pre>/answer Reply here</pre>
Send this command while replying to question to reply with the text \
"Reply here".

<pre>/announcement contest_name Announcement subject
Announcement body
</pre>
Send an announcement with title "Announcement subject" and body \
"Announcement body" to the running contest called "contest_name". More \
precisely, the subject of the announcement ends at the end of the first \
line. Announcements created elsewhere are not posted here.
"""

HELP_MESSAGE_ANNOUNCEMENT = """
Bot to interact with questions and announcements of a CMS contest. \
This bot will automatically post new questions and announcements in \
this channel, and allows you to create announcements and reply to \
questions.

In particular, you can use the <i>inline keyboard</i> to reply to \
questions with built-in answers or to ignore questions, and you \
can use the following commands:

<pre>/answer Reply here</pre>
Send this command while replying to question to reply with the text \
"Reply here".

<pre>/announcement Announcement subject
Announcement body
</pre>
Send an announcement with title "Announcement subject" and body \
"Announcement body". More precisely, the subject of the announcement \
ends at the end of the first line.
"""


def sqlalchemy_to_dict(obj: Base) -> dict:
    d = obj.get_attrs()
    d["id"] = obj.id
    return d


def contest_hashtag(contest_name: str) -> str:
    """Return the hashtag that marks the messages of a contest.

    Hashtags do not allow "-", which a contest name may have.

    contest_name: the name of the contest.

    return: the hashtag, with its "#".

    """
    return "#" + re.sub(r"\W", "_", contest_name)


def running_contest_names(session: Session, now: datetime.datetime) -> list[str]:
    """Return the names of the contests that have a group running.

    session: the DB session to read with.
    now: the current time, as a naive UTC datetime.

    return: the names, sorted.

    """
    running = Contest.groups.any(and_(Group.start <= now, now <= Group.stop))
    return list(session.scalars(
        select(Contest.name).where(running).order_by(Contest.name)))


def parse_announcement(
    text: str, running_names: list[str]
) -> tuple[str, str, str]:
    """Split the text of /announcement when the bot serves all the contests.

    The first line is "/announcement contest_name subject", the rest of the
    message is the body.

    text: the text of the message.
    running_names: the names of the contests that may be announced to.

    return: the contest name, the subject and the body.

    raise (ValueError): if the message is not valid; its text is the
        reply for the sender.

    """
    running = ", ".join(running_names) or "none"
    first_line, _, body = text.partition("\n")
    words = first_line.split(None, 2)
    if len(words) < 2:
        raise ValueError(f"Missing contest name. Running contests: {running}")
    if words[1] not in running_names:
        raise ValueError(
            f"{words[1]} is not a running contest. Running contests: {running}")
    if len(words) < 3:
        raise ValueError("Missing announcement subject")
    if not body.strip():
        raise ValueError("Missing announcement body")
    return words[1], words[2], body.strip()


def load_cutoff(path: str, has_sent_questions: bool) -> datetime.datetime:
    """Return the moment before which no question is posted.

    The first start of the bot writes the current time in path, so that a
    bot that serves a contest with a history does not post all of it.

    path: the file that keeps the cutoff; a file that cannot be read is
        taken as missing.
    has_sent_questions: whether the bot has already posted questions; an
        install that did so before the cutoff existed keeps posting
        everything, as it always did, and writes no file.

    return: the cutoff, as a naive UTC datetime.

    """
    try:
        with open(path) as f:
            return datetime.datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        if has_sent_questions:
            return EPOCH
    cutoff = make_datetime()
    # Replace, not write in place: a partial file would stop every start.
    with open(path + ".tmp", "w") as f:
        f.write(cutoff.isoformat())
    os.replace(path + ".tmp", path)
    return cutoff


class TelegramBot:

    def __init__(self, chat_id: str, token: str, contest_id: int | None) -> None:
        self.chat_id = int(chat_id)
        self.contest_id = contest_id
        self.application = (
            ApplicationBuilder()
            .token(token)
            .read_timeout(60)
            .get_updates_read_timeout(60)
            .write_timeout(60)
            .get_updates_write_timeout(60)
            .build()
        )
        self.application.add_handler(
            CommandHandler("start", self.send_help_message))
        self.application.add_handler(
            CommandHandler("help", self.send_help_message))
        self.application.add_handler(
            CommandHandler("announcement", self.send_announcement)
        )
        self.application.add_handler(
            CommandHandler("answer", self.custom_answer))
        self.application.add_handler(CallbackQueryHandler(self.answer))

        self.question_storage_dir = os.path.join(
            config.global_.data_dir, "telegram", "question"
        )
        self.announcement_storage_dir = os.path.join(
            config.global_.data_dir, "telegram", "announcement"
        )
        os.makedirs(self.question_storage_dir, exist_ok=True)
        os.makedirs(self.announcement_storage_dir, exist_ok=True)

        self.msg_id_to_qid = dict()

        def load_status(directory):
            ans = dict()
            with os.scandir(directory) as it:
                for entry in it:
                    if not entry.is_file():
                        logging.warn(
                            f"{directory} contains a non-file {entry.name}")
                        continue
                    with open(os.path.join(directory, entry.name)) as f:
                        try:
                            data = yaml.safe_load(f.read())
                            assert self.file_name(data["id"]) == entry.name
                            ans[data["id"]] = data
                        except:
                            logger.warn(
                                f"Invalid stored file {entry.name} in {directory}"
                            )
            return ans

        self.question_status = load_status(self.question_storage_dir)
        self.announcement_status = load_status(self.announcement_storage_dir)
        # The state of a bot for all the contests always has contest names:
        # what a single-contest run left would post every contest's history.
        self.cutoff = load_cutoff(
            os.path.join(config.global_.data_dir, "telegram", "cutoff"),
            bool(self.question_status) and self.contest_id is not None,
        )

        for (qid, q) in self.question_status.items():
            self.msg_id_to_qid[q["msg_id"]] = qid

    def file_name(self, id):
        return f"{id}.yaml"

    async def send_help_message(self, update: Update, context: CallbackContext):
        del update
        if self.contest_id is None:
            await context.bot.send_message(
                chat_id=self.chat_id, text=HELP_MESSAGE, parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=self.chat_id,
                text=HELP_MESSAGE_ANNOUNCEMENT,
                parse_mode="HTML",
            )

    async def custom_answer(self, update: Update, context: CallbackContext):
        if update.effective_chat.id != self.chat_id:
            logging.warning(
                f"answer from unknown chat {update.effective_chat.id}")
            return
        text = update.message.text
        reply_to = update.message.reply_to_message
        if reply_to is not None:
            reply_to = reply_to.message_id
        try:
            text = text.split(" ", 1)[1]
        except:
            await context.bot.send_message(
                chat_id=self.chat_id,
                text="Missing reply text",
                parse_mode="HTML",
                reply_parameters=ReplyParameters(update.message.message_id),
            )
            return
        qid = self.msg_id_to_qid.get(reply_to, None)
        if qid is None:
            await context.bot.send_message(
                chat_id=self.chat_id,
                text="You can only use /answer in replies to questions!",
                parse_mode="HTML",
                reply_parameters=ReplyParameters(update.message.message_id),
            )
            return

        text = text.strip()
        with SessionGen() as ses:
            question = Question.get_from_id(qid, ses)
            question.reply_timestamp = make_datetime()
            question.ignored = False
            question.reply_text = text
            question.reply_subject = ""
            ses.add(question)
            ses.commit()

    async def answer(self, update: Update, context: CallbackContext):
        if update.effective_chat.id != self.chat_id:
            logging.warning(
                f"reply from unknown chat {update.effective_chat.id}")
            return
        text = update.callback_query.data
        qid, text = text.split(" ", 1)
        qid = int(qid)
        text = text.strip()
        with SessionGen() as ses:
            question = Question.get_from_id(qid, ses)
            if text == "IGNORE":
                question.ignored = True
                question.reply_timestamp = None
            elif text == "REMOVE":
                question.ignored = False
                question.reply_timestamp = None
                question.reply_text = None
                question.reply_subject = None
            else:
                question.reply_timestamp = make_datetime()
                question.ignored = False
                question.reply_text = ""
                question.reply_subject = text
            ses.add(question)
            ses.commit()
        await update.callback_query.answer()

    async def send_announcement(self, update: Update, context: CallbackContext):
        if update.effective_chat.id != self.chat_id:
            logging.warning(
                f"announcement from unknown chat {update.effective_chat.id}"
            )
            return
        text = update.message.text
        contest_name = None
        if self.contest_id is None:
            with SessionGen() as ses:
                running = running_contest_names(ses, make_datetime())
            try:
                contest_name, subject, text = parse_announcement(text, running)
            except ValueError as error:
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=str(error),
                    reply_parameters=ReplyParameters(
                        update.message.message_id),
                )
                return
        else:
            try:
                subject, text = text.split("\n", 1)
            except:
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text="Missing announcement body",
                    parse_mode="HTML",
                    reply_parameters=ReplyParameters(
                        update.message.message_id),
                )
                return
            subject = subject.split(" ", 1)[1]
            text = text.strip()
        announcement = Announcement(
            subject=subject, text=text, timestamp=make_datetime()
        )
        with SessionGen() as ses:
            announcement.contest_id = (
                self.contest_id if contest_name is None
                else ses.execute(
                    select(Contest.id).where(Contest.name == contest_name)
                ).scalar_one())
            ses.add(announcement)
            ses.commit()
        if contest_name is not None:
            await context.bot.send_message(
                chat_id=self.chat_id,
                text=f"Announcement sent to {contest_hashtag(contest_name)}",
                reply_parameters=ReplyParameters(update.message.message_id),
            )

    async def run(self):
        async with self.application:
            try:
                await self.application.start()
                await self.application.bot.set_my_commands(())
                await self.application.updater.start_polling()
                await self.db_loop()
            finally:
                await self.application.updater.stop()
                await self.application.stop()

    async def _store(
        self,
        obj: dict,
        store: dict,
        directory: str,
        callback: Callable[[dict, dict], Awaitable[dict]],
    ):
        existing = store.get(obj["id"], dict())
        has_changed = False
        for (k, v) in obj.items():
            if existing.get(k, None) != v:
                has_changed = True

        if has_changed:
            obj_id = obj["id"]
            while True:
                try:
                    obj = await callback(existing, obj)
                    break
                except RetryAfter as flood:
                    # Telegram lets a group get about 20 messages a minute,
                    # and the end of a contest sends more. The same item is
                    # retried, not skipped: the order of the questions matters.
                    retry = flood.retry_after
                    # An int, or a timedelta in newer python-telegram-bot.
                    if hasattr(retry, "total_seconds"):
                        retry = retry.total_seconds()
                    logger.warning(
                        "Telegram flood control, waiting %s s", retry)
                    await asyncio.sleep(retry)
                except Exception:
                    # shortcut: an item that always fails is tried and logged
                    # again at every poll (1 s), upgrade to a per-item
                    # backoff if a bad message ever floods the log.
                    logger.exception("Could not post %s %s", directory, obj_id)
                    return
            store[obj_id] = obj
            with open(os.path.join(directory, self.file_name(obj_id)), "w") as f:
                f.write(yaml.safe_dump(obj))

    async def store_question(
        self, question: dict, changed_callback: Callable[[dict, dict], Awaitable[dict]]
    ):
        await self._store(
            question, self.question_status, self.question_storage_dir, changed_callback
        )

    async def store_announcement(
        self,
        announcement: dict,
        changed_callback: Callable[[dict, dict], Awaitable[dict]],
    ):
        await self._store(
            announcement,
            self.announcement_status,
            self.announcement_storage_dir,
            changed_callback,
        )

    async def _edit_message(
        self, text: str, msg_id: int, reply_markup: InlineKeyboardMarkup | None = None
    ):
        """Edit a message that was posted, unless it is already as wanted.

        An answer from AWS, or a second click on the same button, changes
        a stored column but not the text, and Telegram refuses an edit that
        changes nothing. The message is as wanted, so that is not a failure.
        Neither is a message that was deleted from the chat.

        """
        try:
            await self.application.bot.edit_message_text(
                text,
                message_id=msg_id,
                chat_id=self.chat_id,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except BadRequest as error:
            message = error.message.lower()
            if "message to edit not found" in message:
                # An admin deleted it from the chat; no edit can work, and
                # it is not posted again.
                logger.warning("Message %s was deleted from the chat", msg_id)
            elif "message is not modified" not in message:
                raise

    async def question_callback(self, old: dict, new: dict) -> dict:
        subject = html.escape(new["subject"])
        text = html.escape(new["text"])
        qid = new["id"]
        # Only when the bot serves all the contests (see the read below).
        contest_name = new.get("contest_name")
        tags = [] if contest_name is None else [contest_hashtag(contest_name)]
        msg = f"<b>New question from {new['username']}</b>\n"
        if contest_name is not None:
            msg += f"Contest: <b>{html.escape(contest_name)}</b>\n"
        msg += f"Subject: <i>{subject}</i>\n\n{text}"

        if new["ignored"]:
            msg += f"\n\n<i>Ignored</i>"
        elif new["reply_text"] is not None:
            ans_subject = html.escape(new["reply_subject"])
            ans_text = html.escape(new["reply_text"])
            msg += f"\n\n<b>Answer: </b><i>{ans_subject}</i>\n\n<i>{ans_text}</i>"
        else:
            tags.insert(0, "#todo")
            msg += "\n\nReply with <code>/answer your_reply_here</code> for a custom reply"
        if tags:
            msg = " ".join(tags) + "\n" + msg

        reply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Yes", callback_data=f"{qid} Yes"),
                    InlineKeyboardButton("No", callback_data=f"{qid} No"),
                ],
                [
                    InlineKeyboardButton(
                        "No comment", callback_data=f"{qid} No comment"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Answered in task description",
                        callback_data=f"{qid} Answered in task description",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Invalid question", callback_data=f"{qid} Invalid question"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Ignore", callback_data=f"{qid} IGNORE"),
                ],
                [
                    InlineKeyboardButton(
                        "Remove answer", callback_data=f"{qid} REMOVE"),
                ],
            ]
        )

        if "msg_id" not in old:
            msg_id = (
                await self.application.bot.send_message(
                    self.chat_id, text=msg, parse_mode="HTML", reply_markup=reply_markup
                )
            ).message_id
        else:
            msg_id = old["msg_id"]
            await self._edit_message(msg, msg_id, reply_markup)
        new["msg_id"] = msg_id
        self.msg_id_to_qid[msg_id] = qid
        return new

    async def announcement_callback(self, old: dict, new: dict) -> dict:
        subject = html.escape(new["subject"])
        text = html.escape(new["text"])
        text = f"<b>New announcement</b>\nSubject: <i>{subject}</i>\n\n{text}"
        if "msg_id" not in old:
            msg_id = (
                await self.application.bot.send_message(
                    self.chat_id, text=text, parse_mode="HTML"
                )
            ).message_id
        else:
            msg_id = old["msg_id"]
            await self._edit_message(text, msg_id)
        new["msg_id"] = msg_id
        return new

    def _read_questions_and_announcements(
        self,
    ) -> tuple[list[dict], list[dict]]:
        """Read the questions and the announcements from the DB.

        The participation and the user of each question are loaded in the
        same query as the question, so the number of queries does not
        depend on how many participants asked something. The dicts are
        built before the session is closed, so they can be used by the
        caller afterwards. Only the questions asked since the cutoff are
        read, so the work of each poll does not grow with the history.

        return: the questions, in the order they were asked (each with the
            "username" of the participant who asked it, and, when the bot
            serves all the contests, the "contest_name"), and the
            announcements (none when the bot serves all the contests).

        """
        with SessionGen() as session:
            query = (
                select(Question)
                .join(Participation)
                .options(
                    joinedload(Question.participation)
                    .joinedload(Participation.user))
                # shortcut: the working set is every question since the
                # first start (about 162 ms per poll at 3000 questions, run
                # on the event loop), upgrade to a running-contests filter
                # if it grows.
                .filter(Question.question_timestamp >= self.cutoff)
                .order_by(Question.id)
            )
            if self.contest_id is not None:
                query = query.filter(
                    Participation.contest_id == self.contest_id)
            else:
                # The key is left out of the single contest dicts: a new
                # key would look like a change of every stored question.
                query = query.options(
                    joinedload(Question.participation)
                    .joinedload(Participation.contest))

            def q_to_dict(q) -> dict:
                d = sqlalchemy_to_dict(q)
                d["username"] = q.participation.user.username
                if self.contest_id is None:
                    d["contest_name"] = q.participation.contest.name
                return d

            qs = [q_to_dict(x) for x in session.execute(query).scalars().all()]
            if self.contest_id is None:
                anns = []
            else:
                anns = [
                    sqlalchemy_to_dict(x)
                    for x in session.execute(
                        select(Announcement)
                        .filter(Announcement.contest_id == self.contest_id)
                    ).scalars().all()
                ]
        return qs, anns

    async def db_loop(self):
        while True:
            logger.debug("Reading questions and announcements from DB")
            qs, anns = self._read_questions_and_announcements()
            for q in qs:
                await self.store_question(q, self.question_callback)
            for ann in anns:
                await self.store_announcement(ann, self.announcement_callback)

            await asyncio.sleep(1)


def main():
    """Parse arguments and launch process."""
    parser = argparse.ArgumentParser(description="Telegram bot.")

    # unsed, but passed by ResourceService
    parser.add_argument("shard", default="", help="unused", nargs="?")
    contest_id_help = (
        "id of the contest to post questions and announcements for, "
        "or ALL to serve all contests (announcements are then posted "
        "with /announcement and the name of the contest)"
    )
    parser.add_argument("-c", "--contest-id", type=str, help=contest_id_help)

    args = parser.parse_args()

    contest_id = contest_id_from_args(args.contest_id, ask_for_contest)

    if contest_id == "ALL":
        contest_id = None

    if config.telegram_bot is None:
        raise ConfigError(
            "Need to configure the Telegram bot before starting it")

    bot = TelegramBot(
        config.telegram_bot.chat_id, config.telegram_bot.bot_token, contest_id
    )

    asyncio.run(bot.run())


if __name__ == "__main__":
    main()

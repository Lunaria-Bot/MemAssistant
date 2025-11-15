import os
import logging
import asyncio
import re
import discord
from discord.ext import commands, tasks
import asyncpg
from datetime import datetime, timedelta, timezone

log = logging.getLogger("cog-reminder-memassistant")

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "1800"))  # 30 min
REMINDER_CLEANUP_MINUTES = int(os.getenv("REMINDER_CLEANUP_MINUTES", "10"))
BOT_NAME = "MemAssistant"   # nom du bot enfant
TASK_NAME = "Reminder"      # nom du cog

class Reminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_reminders = {}
        self.pool: asyncpg.Pool | None = None
        self.cleanup_task.start()

    async def cog_load(self):
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour Reminder (%s)", BOT_NAME)

    def cog_unload(self):
        self.cleanup_task.cancel()

    async def send_reminder_message(self, member: discord.Member, channel: discord.TextChannel):
        content = (
            f"⏱️ Hey {member.mention}, your </summon:1301277778385174601> "
            f"is available <:KDYEY:1438589525537591346>"
        )
        try:
            await channel.send(content, allowed_mentions=discord.AllowedMentions(users=True))
            log.info("⏰ Reminder sent to %s in #%s", member.display_name, channel.name)
        except discord.Forbidden:
            log.warning("❌ Cannot send reminder in %s", channel.name)

    async def start_reminder(self, member: discord.Member, channel: discord.TextChannel):
        key = f"{member.guild.id}:{member.id}"
        if key in self.active_reminders:
            return

        expire_at = datetime.now(timezone.utc) + timedelta(seconds=COOLDOWN_SECONDS)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO reminders (bot_name, task, guild_id, user_id, channel_id, expire_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (bot_name, task, guild_id, user_id) DO UPDATE SET channel_id=$5, expire_at=$6",
                BOT_NAME, TASK_NAME, member.guild.id, member.id, channel.id, expire_at
            )

        async def reminder_task():
            try:
                await asyncio.sleep(COOLDOWN_SECONDS)
                await self.send_reminder_message(member, channel)
            finally:
                self.active_reminders.pop(key, None)
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM reminders WHERE bot_name=$1 AND task=$2 AND guild_id=$3 AND user_id=$4",
                        BOT_NAME, TASK_NAME, member.guild.id, member.id
                    )
                log.info("🗑️ Reminder deleted for %s", member.display_name)

        self.active_reminders[key] = asyncio.create_task(reminder_task())
        log.info("▶️ Reminder started for %s (%ss)", member.display_name, COOLDOWN_SECONDS)

    async def restore_reminders(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT guild_id, user_id, channel_id, expire_at FROM reminders WHERE bot_name=$1 AND task=$2",
                BOT_NAME, TASK_NAME
            )
        now = datetime.now(timezone.utc)

        for row in rows:
            remaining = (row["expire_at"] - now).total_seconds()
            if remaining <= 0:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM reminders WHERE bot_name=$1 AND task=$2 AND guild_id=$3 AND user_id=$4",
                        BOT_NAME, TASK_NAME, row["guild_id"], row["user_id"]
                    )
                continue

            guild = self.bot.get_guild(row["guild_id"])
            if not guild:
                continue
            member = guild.get_member(row["user_id"])
            if not member:
                continue
            channel = guild.get_channel(row["channel_id"])
            if not channel:
                continue

            key = f"{guild.id}:{member.id}"

            async def reminder_task():
                try:
                    await asyncio.sleep(remaining)
                    await self.send_reminder_message(member, channel)
                finally:
                    self.active_reminders.pop(key, None)
                    async with self.pool.acquire() as conn:
                        await conn.execute(
                            "DELETE FROM reminders WHERE bot_name=$1 AND task=$2 AND guild_id=$3 AND user_id=$4",
                            BOT_NAME, TASK_NAME, guild.id, member.id
                        )

            self.active_reminders[key] = asyncio.create_task(reminder_task())
            log.info("♻️ Restored reminder for %s (%ss left)", member.display_name, remaining)

    @tasks.loop(minutes=REMINDER_CLEANUP_MINUTES)
    async def cleanup_task(self):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM reminders WHERE expire_at <= $1",
                datetime.now(timezone.utc)
            )
        log.info("🧹 Cleanup: expired reminders deleted")

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()
        await self.restore_reminders()

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or not after.embeds:
            return
        embed = after.embeds[0]
        title = (embed.title or "").lower()
        desc = embed.description or ""
        footer = embed.footer.text.lower() if embed.footer and embed.footer.text else ""
        if "summon claimed" in title and "auto summon claimed" not in title:
            match = re.search(r"<@!?(\d+)>", desc) or (re.search(r"<@!?(\d+)>", footer) if "claimed by" in footer else None)
            if not match:
                return
            user_id = int(match.group(1))
            member = after.guild.get_member(user_id)
            if not member:
                return
            await self.start_reminder(member, after.channel)

async def setup(bot: commands.Bot):
    await bot.add_cog(Reminder(bot))
    log.info("⚙️ Reminder cog loaded (%s + Postgres)", BOT_NAME)

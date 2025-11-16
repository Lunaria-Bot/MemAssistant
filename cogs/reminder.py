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
BOT_NAME = "MemAssistant"
TASK_NAME = "Reminder"

REMINDER_ANNOUNCE_CHANNEL_ID = 1439274847115939982
REMINDER_DENY_CHANNEL_ID = 1438563704751915018

class Reminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_reminders: dict[str, asyncio.Task] = {}
        self.pool: asyncpg.Pool | None = None
        self.cleanup_task.start()

    async def cog_load(self):
        self.pool = self.bot.db_pool
        log.info("✅ Postgres pool attached for Reminder (%s)", BOT_NAME)

    def cog_unload(self):
        self.cleanup_task.cancel()

    async def _get_channel(self, guild: discord.Guild, channel_id: int) -> discord.TextChannel | None:
        channel = guild.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            return channel
        try:
            fetched = await self.bot.fetch_channel(channel_id)
            return fetched if isinstance(fetched, discord.TextChannel) else None
        except discord.HTTPException:
            return None

    async def send_start_message(self, guild: discord.Guild, member: discord.Member):
        channel = await self._get_channel(guild, REMINDER_ANNOUNCE_CHANNEL_ID)
        if channel:
            await channel.send(
                f"▶️ **Reminder** started for {member.mention} — next availability in {COOLDOWN_SECONDS // 60} minutes.",
                allowed_mentions=discord.AllowedMentions(users=True)
            )

    async def send_finish_message(self, guild: discord.Guild, member: discord.Member):
        channel = await self._get_channel(guild, REMINDER_ANNOUNCE_CHANNEL_ID)
        if channel:
            await channel.send(
                f"⏹️ **Reminder** finished for {member.mention}.",
                allowed_mentions=discord.AllowedMentions(users=True)
            )

    async def send_reminder_message(self, channel: discord.TextChannel, member: discord.Member):
        try:
            await channel.send(
                f"⏱️ Hey {member.mention}, your </summon:1301277778385174601> is available <:KDYEY:1438589525537591346>",
                allowed_mentions=discord.AllowedMentions(users=True)
            )
            log.info("⏰ Reminder sent to %s in #%s", member.display_name, channel.name)
        except discord.Forbidden:
            log.warning("❌ Cannot send reminder in #%s", channel.name)

    async def send_deny_message(self, guild: discord.Guild, member: discord.Member):
        channel = guild.get_channel(REMINDER_DENY_CHANNEL_ID)
        if channel:
            await channel.send(
                f"🚫 **Reminder** — action denied for {member.mention}\n🔒 Subscription inactive or expired.",
                allowed_mentions=discord.AllowedMentions(users=True)
            )

    async def start_reminder(self, member: discord.Member, summon_channel: discord.TextChannel):
        key = f"{member.guild.id}:{member.id}"
        if key in self.active_reminders:
            return

        # Check subscription
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM public.subscriptions WHERE server_id = $1",
                member.guild.id
            )
        now = datetime.now(timezone.utc)
        if not row or row["expire_at"] < now:
            await self.send_deny_message(member.guild, member)
            log.warning("🔒 Reminder denied for %s — no active subscription", member.display_name)
            return

        expire_at = now + timedelta(seconds=COOLDOWN_SECONDS)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO reminders (bot_name, task, guild_id, user_id, channel_id, expire_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (bot_name, task, guild_id, user_id) DO UPDATE SET channel_id=$5, expire_at=$6",
                BOT_NAME, TASK_NAME, member.guild.id, member.id, summon_channel.id, expire_at
            )

        # Start message in fixed channel
        await self.send_start_message(member.guild, member)

        async def reminder_task():
            try:
                await asyncio.sleep(COOLDOWN_SECONDS)
                # Reminder in summon channel
                await self.send_reminder_message(summon_channel, member)
            finally:
                # Finish message in fixed channel
                await self.send_finish_message(member.guild, member)
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
            summon_channel = guild.get_channel(row["channel_id"])
            if not summon_channel:
                continue

            key = f"{guild.id}:{member.id}"

            async def reminder_task():
                try:
                    await asyncio.sleep(remaining)
                    await self.send_reminder_message(summon_channel, member)
                finally:
                    await self.send_finish_message(guild, member)
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

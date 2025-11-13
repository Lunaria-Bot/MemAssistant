import os
import logging
import asyncio
import re
import discord
from discord.ext import commands, tasks
import asyncpg
from datetime import datetime, timedelta, timezone

log = logging.getLogger("cog-reminder")

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "1800"))  # default 30 minutes
REMINDER_CLEANUP_MINUTES = int(os.getenv("REMINDER_CLEANUP_MINUTES", "32"))  # default 32 minutes

REMINDER_LOG_GUILD_ID = 1437641569187659928
REMINDER_LOG_CHANNEL_ID = int(os.getenv("REMINDER_LOG_CHANNEL_ID", "0"))

class Reminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_reminders = {}
        self.pool: asyncpg.Pool | None = None
        self.cleanup_task.start()

    async def cog_load(self):
        # Utilise la pool globale créée dans main.py
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour Reminder")

    def cog_unload(self):
        self.cleanup_task.cancel()

    async def send_log(self, message: str):
        guild = self.bot.get_guild(REMINDER_LOG_GUILD_ID)
        if not guild:
            return
        channel = guild.get_channel(REMINDER_LOG_CHANNEL_ID)
        if channel:
            try:
                await channel.send(message)
            except discord.Forbidden:
                log.warning("❌ Cannot send log message in reminder log channel")

    async def send_reminder_message(self, member: discord.Member, channel: discord.TextChannel):
        content = (
            f"⏱️ Hey {member.mention}, your </summon:1301277778385174601> "
            f"is available <:KDYEY:1438589525537591346> !"
        )
        try:
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False)
            )
            log.info("⏰ Reminder sent to %s in #%s", member.display_name, channel.name)
            await self.send_log(f"✅ Reminder triggered for {member.mention} in {member.guild.name}")
        except discord.Forbidden:
            log.warning("❌ Cannot send reminder in %s", channel.name)

    async def is_subscription_active(self, guild_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1", guild_id
            )
            if not row:
                return False
            expire_at = row["expire_at"]
            return expire_at > datetime.now(timezone.utc)

    async def start_reminder(self, member: discord.Member, channel: discord.TextChannel):
        if not await self.is_subscription_active(member.guild.id):
            log.info("⛔ Reminder blocked: subscription inactive for guild %s", member.guild.id)
            return

        key = f"{member.guild.id}:{member.id}"
        if key in self.active_reminders:
            log.info("⏳ Reminder already active for %s", member.display_name)
            return

        expire_at = datetime.now(timezone.utc) + timedelta(seconds=COOLDOWN_SECONDS)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO reminders (guild_id, user_id, channel_id, expire_at) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (guild_id, user_id) DO UPDATE SET channel_id=$3, expire_at=$4",
                member.guild.id, member.id, channel.id, expire_at
            )
        log.info("💾 Reminder stored in Postgres for %s (expire_at=%s)", member.display_name, expire_at)

        async def reminder_task():
            try:
                log.info("▶️ Reminder task started for %s (%ss)", member.display_name, COOLDOWN_SECONDS)
                await asyncio.sleep(COOLDOWN_SECONDS)
                if await self.is_subscription_active(member.guild.id):
                    await self.send_reminder_message(member, channel)
            finally:
                self.active_reminders.pop(key, None)
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM reminders WHERE guild_id=$1 AND user_id=$2",
                        member.guild.id, member.id
                    )
                log.info("🗑️ Reminder deleted for %s", member.display_name)

        task = asyncio.create_task(reminder_task())
        self.active_reminders[key] = task
        await self.send_log(f"{member.mention} reminder started in {member.guild.name}")

    async def restore_reminders(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT guild_id, user_id, channel_id, expire_at FROM reminders")
        now = datetime.now(timezone.utc)

        for row in rows:
            guild = self.bot.get_guild(row["guild_id"])
            if not guild:
                continue
            member = guild.get_member(row["user_id"])
            if not member:
                continue
            channel = guild.get_channel(row["channel_id"])
            if not channel:
                continue

            remaining = (row["expire_at"] - now).total_seconds()
            if remaining <= 0:
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM reminders WHERE guild_id=$1 AND user_id=$2",
                        row["guild_id"], row["user_id"]
                    )
                continue

            async def reminder_task():
                try:
                    log.info("♻️ Restored reminder for %s (%ss left)", member.display_name, remaining)
                    await asyncio.sleep(remaining)
                    if await self.is_subscription_active(member.guild.id):
                        await self.send_reminder_message(member, channel)
                finally:
                    self.active_reminders.pop(f"{guild.id}:{member.id}", None)
                    async with self.pool.acquire() as conn:
                        await conn.execute(
                            "DELETE FROM reminders WHERE guild_id=$1 AND user_id=$2",
                            guild.id, member.id
                        )
                    log.info("🗑️ Restored reminder deleted for %s", member.display_name)

            task = asyncio.create_task(reminder_task())
            self.active_reminders[f"{guild.id}:{member.id}"] = task
            await self.send_log(f"{member.mention} reminder restored in {guild.name} ({int(remaining)}s left)")

    @tasks.loop(minutes=REMINDER_CLEANUP_MINUTES)
    async def cleanup_task(self):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM reminders WHERE expire_at <= $1", datetime.now(timezone.utc))
        log.info("🧹 Cleanup: expired reminders deleted")

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or not after.embeds:
            return

        embed = after.embeds[0]
        title = (embed.title or "").lower()
        desc = embed.description or ""
        footer = embed.footer.text.lower() if embed.footer and embed.footer.text else ""

        if "summon claimed" in title and "auto summon claimed" not in title:
            match = re.search(r"<@!?(\d+)>", desc)
            if not match and "claimed by" in footer:
                match = re.search(r"<@!?(\d+)>", footer)

            if not match:
                return

            user_id = int(match.group(1))
            member = after.guild.get_member(user_id)
            if not member:
                return

            log.info("📥 Summon claimed by %s → starting reminder", member.display_name)
            await self.start_reminder(member, after.channel)

async def setup(bot: commands.Bot):
    cog = Reminder(bot)
    await bot.add_cog(cog)
    await cog.restore_reminders()
    log.info("⚙️ Reminder cog loaded (Postgres + subscription check)")

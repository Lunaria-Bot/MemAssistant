import os
import logging
import discord
from discord.ext import commands
import asyncio
import asyncpg
from datetime import datetime, timedelta, timezone

log = logging.getLogger("cog-vote-reminder")

REMINDER_DELAY = timedelta(hours=12, minutes=2)
DATABASE_URL = os.getenv("DATABASE_URL")

class VoteReminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None
        self.active_tasks = {}

    async def cog_load(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        await self.restore_pending_reminders()
        log.info("✅ VoteReminder connecté à Postgres")

    async def cog_unload(self):
        if self.pool:
            await self.pool.close()
        for task in self.active_tasks.values():
            task.cancel()

    async def is_subscription_active(self, guild_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1", guild_id
            )
            if not row:
                return False
            expire_at = row["expire_at"]
            return expire_at > datetime.now(timezone.utc)

    async def schedule_reminder(self, guild_id: int, user_id: int, channel_id: int):
        # Vérifie la souscription
        if not await self.is_subscription_active(guild_id):
            log.info("⛔ Vote reminder blocked: subscription inactive for guild %s", guild_id)
            return

        expire_at = datetime.now(timezone.utc) + REMINDER_DELAY
        key = f"{guild_id}:{user_id}"

        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO vote_reminders (guild_id, user_id, channel_id, expire_at) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (guild_id, user_id) DO UPDATE SET channel_id=$3, expire_at=$4",
                guild_id, user_id, channel_id, expire_at
            )

        async def reminder_task():
            try:
                delay = (expire_at - datetime.now(timezone.utc)).total_seconds()
                await asyncio.sleep(delay)

                if not await self.is_subscription_active(guild_id):
                    log.info("⛔ Reminder skipped: subscription expired for guild %s", guild_id)
                    return

                guild = self.bot.get_guild(guild_id)
                if not guild:
                    return
                member = guild.get_member(user_id)
                channel = guild.get_channel(channel_id)

                if member and channel:
                    try:
                        await member.send("⏱️ You can vote again on [top.gg](https://top.gg/bot/mazoku)!")
                        log.info("✅ Vote reminder sent to %s", member.display_name)
                    except discord.Forbidden:
                        await channel.send(f"⏱️ {member.mention}, you can vote again on [top.gg](https://top.gg/bot/mazoku)!")
                        log.info("✅ Vote reminder sent in channel for %s", member.display_name)

                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM vote_reminders WHERE guild_id=$1 AND user_id=$2",
                        guild_id, user_id
                    )
            finally:
                self.active_tasks.pop(key, None)

        task = asyncio.create_task(reminder_task())
        self.active_tasks[key] = task

    async def restore_pending_reminders(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT guild_id, user_id, channel_id, expire_at FROM vote_reminders")
        now = datetime.now(timezone.utc)

        for row in rows:
            delay = (row["expire_at"] - now).total_seconds()
            if delay <= 0:
                continue
            await self.schedule_reminder(row["guild_id"], row["user_id"], row["channel_id"])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if "Vote Mazoku on top.gg" in message.content or any(e.url and "top.gg" in e.url for e in message.embeds):
            await self.schedule_reminder(message.guild.id, message.author.id, message.channel.id)
            log.info("📥 Vote detected from %s → reminder scheduled", message.author.display_name)

async def setup(bot: commands.Bot):
    await bot.add_cog(VoteReminder(bot))
    log.info("⚙️ VoteReminder cog loaded (subscription-aware)")

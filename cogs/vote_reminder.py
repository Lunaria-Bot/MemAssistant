import logging
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
import asyncpg

log = logging.getLogger("cog-vote-reminder")

VOTE_REMINDER_COOLDOWN_HOURS = 12
VOTE_LOG_CHANNEL_ID = 1438563704751915018

class VoteReminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None
        self.active_reminders = {}
        self.cleanup_task.start()

    async def cog_load(self):
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour VoteReminder")

    def cog_unload(self):
        self.cleanup_task.cancel()

    async def send_log(self, message: str):
        channel = self.bot.get_channel(VOTE_LOG_CHANNEL_ID)
        if channel:
            try:
                await channel.send(message)
            except discord.Forbidden:
                log.warning("❌ Cannot send vote log message")

    async def send_vote_reminder(self, member: discord.Member, channel: discord.TextChannel):
        try:
            await channel.send(
                f"🗳️ Hey {member.mention}, you can vote again on [top.gg](https://top.gg/bot/1301277778385174601/vote) to support us!",
                allowed_mentions=discord.AllowedMentions(users=True)
            )
            log.info("🔔 Vote reminder sent to %s in #%s", member.display_name, channel.name)
            await self.send_log(f"✅ Vote reminder triggered for {member.mention} in {member.guild.name}")
        except discord.Forbidden:
            log.warning("❌ Cannot send vote reminder in %s", channel.name)

    async def start_vote_reminder(self, member: discord.Member, channel: discord.TextChannel):
        key = f"{member.guild.id}:{member.id}"
        if key in self.active_reminders:
            log.info("⏳ Vote reminder already active for %s", member.display_name)
            return

        expire_at = datetime.now(timezone.utc) + timedelta(hours=VOTE_REMINDER_COOLDOWN_HOURS)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO vote_reminders (guild_id, user_id, channel_id, expire_at) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (guild_id, user_id) DO UPDATE SET channel_id=$3, expire_at=$4",
                member.guild.id, member.id, channel.id, expire_at
            )
        log.info("💾 Vote reminder stored for %s (expires %s)", member.display_name, expire_at)

        async def reminder_task():
            try:
                await asyncio.sleep(VOTE_REMINDER_COOLDOWN_HOURS * 3600)
                await self.send_vote_reminder(member, channel)
            finally:
                self.active_reminders.pop(key, None)
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM vote_reminders WHERE guild_id=$1 AND user_id=$2",
                        member.guild.id, member.id
                    )
                log.info("🗑️ Vote reminder deleted for %s", member.display_name)

        task = asyncio.create_task(reminder_task())
        self.active_reminders[key] = task
        await self.send_log(f"{member.mention} vote reminder started in {member.guild.name}")

    async def restore_reminders(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT guild_id, user_id, channel_id, expire_at FROM vote_reminders")
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
                        "DELETE FROM vote_reminders WHERE guild_id=$1 AND user_id=$2",
                        row["guild_id"], row["user_id"]
                    )
                continue

            async def reminder_task():
                try:
                    await asyncio.sleep(remaining)
                    await self.send_vote_reminder(member, channel)
                finally:
                    self.active_reminders.pop(f"{guild.id}:{member.id}", None)
                    async with self.pool.acquire() as conn:
                        await conn.execute(
                            "DELETE FROM vote_reminders WHERE guild_id=$1 AND user_id=$2",
                            guild.id, member.id
                        )
                    log.info("🗑️ Restored vote reminder deleted for %s", member.display_name)

            task = asyncio.create_task(reminder_task())
            self.active_reminders[f"{guild.id}:{member.id}"] = task
            await self.send_log(f"{member.mention} vote reminder restored in {guild.name} ({int(remaining)}s left)")

    @tasks.loop(minutes=30)
    async def cleanup_task(self):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM vote_reminders WHERE expire_at <= $1", datetime.now(timezone.utc))
        log.info("🧹 Cleanup: expired vote reminders deleted")

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not message.embeds:
            return

        embed = message.embeds[0]
        title = (embed.title or "").lower()
        desc = embed.description or ""

        if "thanks for your vote" in desc.lower() or "vote mazoku" in title:
            log.info("🗳️ Vote detected from %s in %s", message.author.display_name, message.guild.name)
            await self.start_vote_reminder(message.author, message.channel)

async def setup(bot: commands.Bot):
    cog = VoteReminder(bot)
    await bot.add_cog(cog)
    await cog.restore_reminders()
    log.info("⚙️ VoteReminder cog loaded (vote detection + cooldown)")

import logging
import asyncio
import re
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
import asyncpg

log = logging.getLogger("cog-vote-reminder")

VOTE_REMINDER_COOLDOWN_HOURS = 12
VOTE_LOG_CHANNEL_ID = 1438563704751915018
MAZOKU_BOT_ID = 1242388858897956906  # ID du bot Mazoku

class VoteReminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None
        self.active_reminders = {}
        self.cleanup_task.start()

    async def cog_load(self):
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour VoteReminder")
        await self.restore_reminders()

    def cog_unload(self):
        self.cleanup_task.cancel()

    async def send_log(self, message: str):
        channel = self.bot.get_channel(VOTE_LOG_CHANNEL_ID)
        if channel:
            try:
                await channel.send(message)
            except discord.Forbidden:
                pass

    async def send_vote_reminder(self, member: discord.Member):
        try:
            dm_channel = await member.create_dm()
            await dm_channel.send("Hey you can vote for Mazoku again ! <:KDYEY:1438589525537591346>")
            log.info("🔔 Vote reminder DM sent to %s", member.display_name)
            await self.send_log(f"✅ Vote reminder DM triggered for {member.mention} in {member.guild.name}")
        except discord.Forbidden:
            log.warning("❌ Cannot send DM to %s", member.display_name)

    async def start_vote_reminder(self, member: discord.Member, channel: discord.TextChannel):
        key = f"{member.guild.id}:{member.id}"
        if key in self.active_reminders:
            return

        expire_at = datetime.now(timezone.utc) + timedelta(hours=VOTE_REMINDER_COOLDOWN_HOURS)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO vote_reminders (guild_id, user_id, channel_id, expire_at) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (guild_id, user_id) DO UPDATE SET channel_id=$3, expire_at=$4",
                member.guild.id, member.id, channel.id, expire_at
            )

        async def reminder_task():
            try:
                await asyncio.sleep(VOTE_REMINDER_COOLDOWN_HOURS * 3600)
                await self.send_vote_reminder(member)
            finally:
                self.active_reminders.pop(key, None)
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM vote_reminders WHERE guild_id=$1 AND user_id=$2",
                        member.guild.id, member.id
                    )

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
                    await self.send_vote_reminder(member)
                finally:
                    self.active_reminders.pop(f"{guild.id}:{member.id}", None)
                    async with self.pool.acquire() as conn:
                        await conn.execute(
                            "DELETE FROM vote_reminders WHERE guild_id=$1 AND user_id=$2",
                            guild.id, member.id
                        )

            task = asyncio.create_task(reminder_task())
            self.active_reminders[f"{guild.id}:{member.id}"] = task
            await self.send_log(f"{member.mention} vote reminder restored in {guild.name} ({int(remaining)}s left)")

    @tasks.loop(minutes=30)
    async def cleanup_task(self):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM vote_reminders WHERE expire_at <= $1", datetime.now(timezone.utc))

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # ✅ Listener pour détecter les messages de vote Mazoku
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or not message.embeds:
            return
        if str(message.author.id) != str(MAZOKU_BOT_ID):
            return

        embed = message.embeds[0]
        footer_text = embed.footer.text if embed.footer else ""
        author_name = embed.author.name if embed.author else ""

        if "vote mazoku" in author_name.lower() and "thanks for your vote" in footer_text.lower():
            match = re.search(r"<@!?(\d+)>", footer_text)
            if not match:
                return

            user_id = int(match.group(1))
            member = message.guild.get_member(user_id)
            if not member:
                return

            await self.start_vote_reminder(member, message.channel)

    # ✅ Commande slash pour voir les reminders actifs
    @app_commands.command(name="vote-status", description="Show active vote reminders in this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def vote_status(self, interaction: discord.Interaction):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, expire_at FROM vote_reminders WHERE guild_id=$1",
                interaction.guild.id
            )

        if not rows:
            await interaction.response.send_message("ℹ️ No active vote reminders in this server.", ephemeral=True)
            return

        now = datetime.now(timezone.utc)
        lines = []
        for row in rows:
            member = interaction.guild.get_member(row["user_id"])
            if not member:
                continue
            remaining = int((row["expire_at"] - now).total_seconds() // 60)
            lines.append(f"🗳️ {member.mention} → {remaining} minutes left")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(VoteReminder(bot))

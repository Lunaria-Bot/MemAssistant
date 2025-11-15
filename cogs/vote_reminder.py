import logging
import asyncio
import re
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
import asyncpg
import json

log = logging.getLogger("cog-vote-reminder")

VOTE_REMINDER_COOLDOWN_HOURS = 12
MAZOKU_BOT_ID = 1242388858897956906  # ID du bot Mazoku

class VoteReminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None
        self.active_reminders = {}
        self.cleanup_task.start()
        self._restored = False

    async def cog_load(self):
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour VoteReminder")

    def cog_unload(self):
        self.cleanup_task.cancel()

    async def publish_event(self, bot_name: str, guild_id: int, user_id: int, event_type: str, details: dict | None = None):
        """Publie un événement vers Redis pour le bot maître avec bot_id inclus."""
        if not getattr(self.bot, "redis", None):
            return
        event = {
            "bot_name": bot_name,
            "bot_id": self.bot.user.id,  # ✅ mention correcte du bot enfant
            "guild_id": guild_id,
            "user_id": user_id,
            "event_type": event_type,
            "details": details or {}
        }
        try:
            await self.bot.redis.publish("bot_events", json.dumps(event))
            log.info("📡 Event publié: %s", event)
        except Exception as e:
            log.error("❌ Impossible de publier l'événement Redis: %s", e)

    async def send_vote_reminder(self, member: discord.Member):
        try:
            dm_channel = await member.create_dm()
            await dm_channel.send("Hey you can vote for Mazoku again ! <:KDYEY:1438589525537591346>")
            log.info("🔔 Vote reminder DM sent to %s", member.display_name)
            await self.publish_event("VoteReminder", member.guild.id, member.id, "vote_reminder_triggered")
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

        await self.publish_event("VoteReminder", member.guild.id, member.id, "vote_reminder_started", {
            "channel": channel.id,
            "expire_at": expire_at.isoformat()
        })

        async def reminder_task():
            try:
                log.info("▶️ Vote reminder task started for %s (%sh)", member.display_name, VOTE_REMINDER_COOLDOWN_HOURS)
                await asyncio.sleep(VOTE_REMINDER_COOLDOWN_HOURS * 3600)
                await self.send_vote_reminder(member)
            finally:
                self.active_reminders.pop(key, None)
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM vote_reminders WHERE guild_id=$1 AND user_id=$2",
                        member.guild.id, member.id
                    )
                log.info("🗑️ Vote reminder deleted for %s", member.display_name)
                await self.publish_event("VoteReminder", member.guild.id, member.id, "vote_reminder_deleted")

        task = asyncio.create_task(reminder_task())
        self.active_reminders[key] = task

    async def restore_reminders(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT guild_id, user_id, channel_id, expire_at FROM vote_reminders")
        now = datetime.now(timezone.utc)

        restored_count = 0

        for row in rows:
            log.info("🔎 Found vote reminder row: guild=%s user=%s expire_at=%s",
                     row["guild_id"], row["user_id"], row["expire_at"])

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
                    log.info("♻️ Restored vote reminder for %s (%ss left)", member.display_name, remaining)
                    await asyncio.sleep(remaining)
                    await self.send_vote_reminder(member)
                finally:
                    self.active_reminders.pop(f"{guild.id}:{member.id}", None)
                    async with self.pool.acquire() as conn:
                        await conn.execute(
                            "DELETE FROM vote_reminders WHERE guild_id=$1 AND user_id=$2",
                            guild.id, member.id
                        )
                    log.info("🗑️ Restored vote reminder deleted for %s", member.display_name)
                    await self.publish_event("VoteReminder", guild.id, member.id, "vote_reminder_deleted")

            task = asyncio.create_task(reminder_task())
            self.active_reminders[f"{guild.id}:{member.id}"] = task
            restored_count += 1

            await self.publish_event("VoteReminder", guild.id, member.id, "vote_reminder_restored", {
                "remaining": remaining
            })

        log.info("📋 Checklist: %s vote reminders restored after restart", restored_count)
        await self.publish_event("VoteReminder", 0, 0, "vote_reminder_checklist", {"restored_count": restored_count})

    @tasks.loop(minutes=30)
    async def cleanup_task(self):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM vote_reminders WHERE expire_at <= $1", datetime.now(timezone.utc))
        log.info("🧹 Cleanup: expired vote reminders deleted")

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()
        if not self._restored:
            await self.restore_reminders()
            self._restored = True

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
            await self.publish_event("VoteReminder", message.guild.id, user_id, "vote_claimed", {"channel": message.channel.id})

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
    log.info("⚙️ VoteReminder cog loaded (Postgres + checklist)")

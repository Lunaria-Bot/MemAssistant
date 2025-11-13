import os
import logging
import asyncio
import time
import re
import discord
from discord.ext import commands, tasks

log = logging.getLogger("cog-reminder")

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "1800"))  # default 30 minutes
REMINDER_CLEANUP_MINUTES = int(os.getenv("REMINDER_CLEANUP_MINUTES", "32"))  # default 32 minutes

# --- ID du serveur et du channel log ---
REMINDER_LOG_GUILD_ID = 1437641569187659928
REMINDER_LOG_CHANNEL_ID = int(os.getenv("REMINDER_LOG_CHANNEL_ID", "0"))  # mets l'ID du salon log ici

class Reminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_reminders = {}
        self.cleanup_task.start()

    def cog_unload(self):
        self.cleanup_task.cancel()

    async def send_log(self, message: str):
        """Envoie un log dans le channel dédié du serveur principal."""
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
            f"is available <:Kanna_Cool:1298168957420834816>"
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

    async def is_reminder_enabled(self, member: discord.Member) -> bool:
        if not getattr(self.bot, "redis", None):
            return True
        key = f"reminder:settings:{member.guild.id}:{member.id}:summon"
        val = await self.bot.redis.get(key)
        return val is None or val == "1"

    async def start_reminder(self, member: discord.Member, channel: discord.TextChannel):
        if not await self.is_reminder_enabled(member):
            log.info("⚠️ Reminder disabled for %s", member.display_name)
            return

        key = f"reminder:summon:{member.guild.id}:{member.id}"
        if key in self.active_reminders:
            log.info("⏳ Reminder already active for %s", member.display_name)
            return

        if getattr(self.bot, "redis", None):
            expire_at = int(time.time()) + COOLDOWN_SECONDS
            await self.bot.redis.hset(
                key,
                mapping={"expire_at": expire_at, "channel_id": channel.id, "guild_id": member.guild.id}
            )
            log.info("💾 Reminder stored in Redis for %s (expire_at=%s)", member.display_name, expire_at)

        async def reminder_task():
            try:
                log.info("▶️ Reminder task started for %s (%ss)", member.display_name, COOLDOWN_SECONDS)
                await asyncio.sleep(COOLDOWN_SECONDS)
                if await self.is_reminder_enabled(member):
                    await self.send_reminder_message(member, channel)
            finally:
                self.active_reminders.pop(key, None)
                if getattr(self.bot, "redis", None):
                    await self.bot.redis.delete(key)
                    log.info("🗑️ Reminder key deleted for %s", member.display_name)

        task = asyncio.create_task(reminder_task())
        self.active_reminders[key] = task
        log.info("▶️ Reminder started for %s in #%s (will trigger in %ss)",
                 member.display_name, channel.name, COOLDOWN_SECONDS)
        await self.send_log(f"{member.mention} reminder started in {member.guild.name}")

    async def restore_reminders(self):
        if not getattr(self.bot, "redis", None):
            return

        keys = await self.bot.redis.keys("reminder:summon:*")
        now = int(time.time())

        for key in keys:
            data = await self.bot.redis.hgetall(key)
            if not data:
                continue

            expire_at = int(data.get("expire_at", 0))
            channel_id = int(data.get("channel_id", 0))
            guild_id = int(data.get("guild_id", 0))
            remaining = expire_at - now
            if remaining <= 0:
                await self.bot.redis.delete(key)
                log.info("🗑️ Expired reminder deleted: %s", key)
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            user_id = int(key.split(":")[-1])
            member = guild.get_member(user_id)
            if not member:
                continue
            channel = guild.get_channel(channel_id)
            if not channel:
                continue

            async def reminder_task():
                try:
                    log.info("♻️ Restored reminder for %s (%ss left)", member.display_name, remaining)
                    await asyncio.sleep(remaining)
                    if await self.is_reminder_enabled(member):
                        await self.send_reminder_message(member, channel)
                finally:
                    self.active_reminders.pop(key, None)
                    await self.bot.redis.delete(key)
                    log.info("🗑️ Restored reminder key deleted for %s", member.display_name)

            task = asyncio.create_task(reminder_task())
            self.active_reminders[key] = task
            await self.send_log(f"{member.mention} reminder restored in {member.guild.name} ({remaining}s left)")

    @tasks.loop(minutes=REMINDER_CLEANUP_MINUTES)
    async def cleanup_task(self):
        if not getattr(self.bot, "redis", None):
            return

        keys = await self.bot.redis.keys("reminder:summon:*")
        now = int(time.time())

        for key in keys:
            data = await self.bot.redis.hgetall(key)
            if not data:
                continue
            expire_at = int(data.get("expire_at", 0))
            if expire_at and expire_at <= now:
                await self.bot.redis.delete(key)
                log.info("🧹 Cleanup: deleted expired reminder %s", key)

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
    log.info("⚙️ Reminder cog loaded (multi-server)")

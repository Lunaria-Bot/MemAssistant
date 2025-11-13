import os
import logging
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta, timezone
import redis.asyncio as redis

log = logging.getLogger("cog-dailyreminder")

REDIS_URL = os.getenv("REDIS_URL")  # défini dans ton .env
DAILY_MESSAGE = "Hello! Just a reminder that your Mazoku Daily is ready!"

class DailyReminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.redis = None
        self.daily_task.start()

    async def cog_load(self):
        try:
            self.redis = redis.from_url(REDIS_URL, decode_responses=True)
            await self.redis.ping()
            log.info("✅ Redis connecté pour DailyReminder")
        except Exception as e:
            log.error("❌ Échec de connexion Redis : %s", e)
            self.redis = None

    async def cog_unload(self):
        if self.redis:
            await self.redis.close()
        self.daily_task.cancel()

    def get_subscriber_key(self, guild_id: int) -> str:
        return f"dailyreminder:subscribers:{guild_id}"

    def get_log_channel_key(self, guild_id: int) -> str:
        return f"dailyreminder:log_channel:{guild_id}"

    async def send_log(self, guild: discord.Guild, message: str):
        if not self.redis:
            return
        key = self.get_log_channel_key(guild.id)
        channel_id = await self.redis.get(key)
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel:
            try:
                await channel.send(message)
            except discord.Forbidden:
                log.warning("❌ Impossible d’envoyer le log dans %s", channel.name)

    # --- Slash: toggle subscription ---
    @app_commands.command(name="toggle-daily", description="Toggle daily Mazoku reminder on/off")
    async def toggle_daily(self, interaction: discord.Interaction):
        if not self.redis:
            await interaction.response.send_message("❌ Redis is not available.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        guild_id = interaction.guild.id
        key = self.get_subscriber_key(guild_id)

        subscribed = await self.redis.sismember(key, user_id)
        if subscribed:
            await self.redis.srem(key, user_id)
            await interaction.response.send_message("❌ You will no longer receive daily reminders.", ephemeral=True)
            await self.send_log(interaction.guild, f"🚫 {interaction.user.mention} unsubscribed from daily reminder")
        else:
            await self.redis.sadd(key, user_id)
            await interaction.response.send_message("✅ You will now receive daily reminders.", ephemeral=True)
            await self.send_log(interaction.guild, f"✅ {interaction.user.mention} subscribed to daily reminder")

    # --- Slash: list subscribers (admin only) ---
    @app_commands.command(name="list-daily", description="List all users subscribed to daily reminders")
    async def list_daily(self, interaction: discord.Interaction):
        if not self.redis:
            await interaction.response.send_message("❌ Redis is not available.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You don’t have permission to use this command.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        key = self.get_subscriber_key(guild_id)
        subscribers = await self.redis.smembers(key)
        if not subscribers:
            await interaction.response.send_message("📭 No one is currently subscribed.", ephemeral=True)
            return

        mentions = []
        for uid in subscribers:
            member = interaction.guild.get_member(int(uid))
            mentions.append(member.mention if member else f"<@{uid}>")

        await interaction.response.send_message(
            f"👥 Subscribers ({len(subscribers)}):\n" + ", ".join(mentions),
            ephemeral=True
        )

    # --- Slash: debug status ---
    @app_commands.command(name="daily-debug", description="Check if you are subscribed to daily reminders")
    async def daily_debug(self, interaction: discord.Interaction):
        if not self.redis:
            await interaction.response.send_message("❌ Redis is not available.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        key = self.get_subscriber_key(interaction.guild.id)
        subscribed = await self.redis.sismember(key, user_id)
        status = "✅ You are subscribed." if subscribed else "❌ You are not subscribed."
        await interaction.response.send_message(status, ephemeral=True)

    # --- Slash: set log channel (admin only) ---
    @app_commands.command(name="set-daily-log-channel", description="Set the log channel for daily reminders")
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self.redis:
            await interaction.response.send_message("❌ Redis is not available.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You don’t have permission to use this command.", ephemeral=True)
            return

        key = self.get_log_channel_key(interaction.guild.id)
        await self.redis.set(key, str(channel.id))
        await interaction.response.send_message(f"✅ Log channel set to {channel.mention}", ephemeral=True)

    # --- Daily task ---
    @tasks.loop(hours=24)
    async def daily_task(self):
        await self.bot.wait_until_ready()
        if not self.redis:
            log.warning("❌ Redis not available, skipping daily task")
            return

        for guild in self.bot.guilds:
            key = self.get_subscriber_key(guild.id)
            subscribers = await self.redis.smembers(key)
            if not subscribers:
                continue

            success = 0
            failed = 0

            for uid in subscribers:
                member = guild.get_member(int(uid))
                if member:
                    try:
                        await member.send(DAILY_MESSAGE)
                        success += 1
                        await self.send_log(guild, f"📨 Daily sent to {member.mention}")
                    except discord.Forbidden:
                        failed += 1
                        await self.send_log(guild, f"❌ Failed to DM {member.mention}")

            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            await self.send_log(
                guild,
                f"📊 Daily summary at {now}:\n"
                f"✅ Sent: {success}\n"
                f"❌ Failed: {failed}\n"
                f"👥 Total: {len(subscribers)}"
            )

    @daily_task.before_loop
    async def before_daily_task(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        target = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await discord.utils.sleep_until(target)

async def setup(bot: commands.Bot):
    await bot.add_cog(DailyReminder(bot))
    log.info("⚙️ DailyReminder cog loaded (multi-server)")

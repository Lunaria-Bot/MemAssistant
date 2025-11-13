import logging
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta, timezone
import asyncpg

log = logging.getLogger("cog-dailyreminder")

DAILY_MESSAGE = "Hello! Just a reminder that your Mazoku Daily is ready!"

class DailyReminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None
        self.daily_task.start()

    async def cog_load(self):
        # Utilise la pool globale créée dans main.py
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour DailyReminder")

    async def cog_unload(self):
        # Ne ferme pas la pool ici, main.py s'en occupe
        self.daily_task.cancel()

    async def is_subscription_active(self, guild_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1", guild_id
            )
            if not row:
                return False
            expire_at = row["expire_at"]
            return expire_at > datetime.now(timezone.utc)

    async def send_log(self, guild: discord.Guild, message: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT channel_id FROM daily_log_channels WHERE guild_id=$1", guild.id
            )
        if not row:
            return
        channel = guild.get_channel(int(row["channel_id"]))
        if channel:
            try:
                await channel.send(message)
            except discord.Forbidden:
                log.warning("❌ Impossible d’envoyer le log dans %s", channel.name)

    # --- Slash commands identiques ---
    @app_commands.command(name="toggle-daily", description="Toggle daily Mazoku reminder on/off")
    async def toggle_daily(self, interaction: discord.Interaction):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM daily_subscribers WHERE guild_id=$1 AND user_id=$2",
                interaction.guild.id, interaction.user.id
            )
            if row:
                await conn.execute(
                    "DELETE FROM daily_subscribers WHERE guild_id=$1 AND user_id=$2",
                    interaction.guild.id, interaction.user.id
                )
                await interaction.response.send_message("❌ You will no longer receive daily reminders.", ephemeral=True)
                await self.send_log(interaction.guild, f"🚫 {interaction.user.mention} unsubscribed from daily reminder")
            else:
                await conn.execute(
                    "INSERT INTO daily_subscribers (guild_id, user_id) VALUES ($1, $2)",
                    interaction.guild.id, interaction.user.id
                )
                await interaction.response.send_message("✅ You will now receive daily reminders.", ephemeral=True)
                await self.send_log(interaction.guild, f"✅ {interaction.user.mention} subscribed to daily reminder")

    @app_commands.command(name="list-daily", description="List all users subscribed to daily reminders")
    async def list_daily(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You don’t have permission to use this command.", ephemeral=True)
            return

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM daily_subscribers WHERE guild_id=$1",
                interaction.guild.id
            )
        if not rows:
            await interaction.response.send_message("📭 No one is currently subscribed.", ephemeral=True)
            return

        mentions = []
        for row in rows:
            member = interaction.guild.get_member(int(row["user_id"]))
            mentions.append(member.mention if member else f"<@{row['user_id']}>")

        await interaction.response.send_message(
            f"👥 Subscribers ({len(rows)}):\n" + ", ".join(mentions),
            ephemeral=True
        )

    @app_commands.command(name="daily-debug", description="Check if you are subscribed to daily reminders")
    async def daily_debug(self, interaction: discord.Interaction):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM daily_subscribers WHERE guild_id=$1 AND user_id=$2",
                interaction.guild.id, interaction.user.id
            )
        status = "✅ You are subscribed." if row else "❌ You are not subscribed."
        await interaction.response.send_message(status, ephemeral=True)

    @app_commands.command(name="set-daily-log-channel", description="Set the log channel for daily reminders")
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You don’t have permission to use this command.", ephemeral=True)
            return

        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO daily_log_channels (guild_id, channel_id) VALUES ($1, $2) "
                "ON CONFLICT (guild_id) DO UPDATE SET channel_id=$2",
                interaction.guild.id, channel.id
            )
        await interaction.response.send_message(f"✅ Log channel set to {channel.mention}", ephemeral=True)

    @tasks.loop(hours=24)
    async def daily_task(self):
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            if not await self.is_subscription_active(guild.id):
                await self.send_log(guild, "⚠️ Subscription not active — Daily reminders disabled.")
                continue

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT user_id FROM daily_subscribers WHERE guild_id=$1",
                    guild.id
                )
            if not rows:
                continue

            success = 0
            failed = 0

            for row in rows:
                member = guild.get_member(int(row["user_id"]))
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
                f"👥 Total: {len(rows)}"
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
    log.info("⚙️ DailyReminder cog loaded (Postgres + subscription check)")

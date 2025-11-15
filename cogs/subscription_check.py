import logging
import discord
from discord.ext import commands
from discord import app_commands
import asyncpg
from datetime import datetime, timezone

log = logging.getLogger("cog-subscription")

GLOBAL_LOG_CHANNEL_ID = 1438563704751915018  # Salon global pour logs

class Subscription(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None

    async def cog_load(self):
        # Utilise la pool globale créée dans main.py
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour Subscription (MemAssistant)")

        async def global_check(interaction: discord.Interaction) -> bool:
            # Autorise toujours les commandes liées à la souscription
            if interaction.command.name in [
                "active-subscription",
                "subscription-status"
            ]:
                return True

            if not interaction.guild:
                return True

            if not await self.is_active(interaction.guild.id):
                await interaction.response.send_message(
                    "❌ This server does not have an active subscription.",
                    ephemeral=True
                )
                await self.send_global_log(
                    f"⛔ Blocked command `{interaction.command.name}` in **{interaction.guild.name}** "
                    f"(subscription inactive)"
                )
                return False
            return True

        self.bot.tree.interaction_check = global_check

    async def cog_unload(self):
        pass

    async def is_active(self, guild_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1", guild_id
            )
            if not row:
                return False
            expire_at = row["expire_at"]
            return expire_at > datetime.now(timezone.utc)

    async def send_global_log(self, message: str):
        channel = self.bot.get_channel(GLOBAL_LOG_CHANNEL_ID)
        if channel:
            try:
                await channel.send(message)
            except discord.Forbidden:
                log.warning("❌ Impossible d’envoyer le log global")

    # --- Commandes slash côté enfant ---
    @app_commands.command(name="active-subscription", description="Activate subscription for this server")
    async def active_subscription(self, interaction: discord.Interaction, code: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
            return

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT server_id, expire_at FROM subscription_codes WHERE code=$1", code
            )
            if not row:
                await interaction.response.send_message("❌ Invalid code.", ephemeral=True)
                return

            server_id, expire_at = row["server_id"], row["expire_at"]

            if server_id != interaction.guild.id:
                await interaction.response.send_message("❌ This code is not for this server.", ephemeral=True)
                return

            if expire_at <= datetime.now(timezone.utc):
                await interaction.response.send_message("❌ Code expired.", ephemeral=True)
                return

            await conn.execute(
                "INSERT INTO subscriptions (server_id, expire_at) VALUES ($1, $2) "
                "ON CONFLICT (server_id) DO UPDATE SET expire_at=$2",
                interaction.guild.id, expire_at
            )

        await interaction.response.send_message(
            f"✅ Subscription activated until {expire_at.strftime('%Y-%m-%d %H:%M UTC')}",
            ephemeral=True
        )
        log.info("✅ Subscription activated for guild %s until %s", interaction.guild.id, expire_at)

    @app_commands.command(name="subscription-status", description="Check subscription status for this server")
    async def subscription_status(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
            return

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1", interaction.guild.id
            )
            if not row:
                await interaction.response.send_message("❌ No active subscription for this server.", ephemeral=True)
                return

            expire_at = row["expire_at"]
            if expire_at <= datetime.now(timezone.utc):
                await interaction.response.send_message("❌ Subscription expired.", ephemeral=True)
                return

        await interaction.response.send_message(
            f"✅ Subscription active until **{expire_at.strftime('%Y-%m-%d %H:%M UTC')}**",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Subscription(bot))
    log.info("⚙️ Subscription cog loaded (MemAssistant + Postgres)")

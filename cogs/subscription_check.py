import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
import logging
from datetime import datetime, timezone

log = logging.getLogger("cog-subscription-check")

class SubscriptionCheck(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None

    async def cog_load(self):
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour SubscriptionCheck")

    async def get_subscription(self, guild_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1", guild_id
            )

    @app_commands.command(name="subscription-check", description="Check if this server has an active subscription")
    @app_commands.checks.has_permissions(administrator=True)
    async def subscription_check(self, interaction: discord.Interaction):
        row = await self.get_subscription(interaction.guild.id)
        if not row:
            await interaction.response.send_message("⛔ This server does not have a subscription.", ephemeral=True)
            return

        expire_at = row["expire_at"]
        now = datetime.now(timezone.utc)

        if expire_at > now:
            # Format lisible (jour/mois/année heure:minute)
            formatted = expire_at.strftime("%d/%m/%Y %H:%M UTC")
            await interaction.response.send_message(
                f"✅ This server has an active subscription until **{formatted}**.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⛔ This server's subscription expired on {expire_at.strftime('%d/%m/%Y %H:%M UTC')}.",
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(SubscriptionCheck(bot))
    log.info("⚙️ SubscriptionCheck cog loaded (Postgres + slash command)")

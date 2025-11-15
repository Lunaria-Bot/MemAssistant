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

    async def is_subscription_active(self, guild_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1", guild_id
            )
            if not row:
                return False
            return row["expire_at"] > datetime.now(timezone.utc)

    @app_commands.command(name="subscription-check", description="Check if this server has an active subscription")
    @app_commands.checks.has_permissions(administrator=True)
    async def subscription_check(self, interaction: discord.Interaction):
        active = await self.is_subscription_active(interaction.guild.id)
        if active:
            await interaction.response.send_message("✅ This server has an active subscription.", ephemeral=True)
        else:
            await interaction.response.send_message("⛔ This server does not have an active subscription.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SubscriptionCheck(bot))
    log.info("⚙️ SubscriptionCheck cog loaded (Postgres + slash command)")

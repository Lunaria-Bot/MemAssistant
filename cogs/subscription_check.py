import discord
from discord.ext import commands
import logging
from datetime import datetime, timezone

log = logging.getLogger("cog-child-subscription")

class ChildSubscription(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(
        name="check_subscription",
        description="Check the subscription status of this server"
    )
    async def check_subscription(self, interaction: discord.Interaction):
        """Slash command to check the subscription expiration date for the current server."""
        server_id = interaction.guild.id
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1",
                server_id
            )

        if not row:
            await interaction.response.send_message(
                f"⚠️ This server (`{server_id}`) does not have an active subscription.",
                ephemeral=True
            )
        else:
            expire_at = row["expire_at"]
            expire_str = expire_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            await interaction.response.send_message(
                f"✅ This server is subscribed until **{expire_str}**",
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(ChildSubscription(bot))
    log.info("⚙️ ChildSubscription cog loaded (slash command)")

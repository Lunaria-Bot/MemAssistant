import discord
from discord.ext import commands
import logging
from datetime import datetime

log = logging.getLogger("cog-memassistant-subscription")

class MemAssistantSubscription(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(name="debug_dsn", description="Affiche le DSN réel utilisé par MemAssistant")
async def debug_dsn(self, interaction: discord.Interaction):
    async with self.bot.db_pool.acquire() as conn:
        row = await conn.fetchval("SELECT inet_server_addr() || ':' || inet_server_port()")
        await interaction.response.send_message(
            f"📡 Connexion active vers `{row}`",
            ephemeral=True
        )

    @discord.app_commands.command(name="check_subscription", description="Check the subscription status of this server")
    async def check_subscription(self, interaction: discord.Interaction):
        server_id = int(interaction.guild.id)
        log.info("🔍 Vérification de la souscription pour server_id = %s", server_id)

        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM public.subscriptions WHERE server_id = $1",
                server_id
            )

        if not row:
            await interaction.response.send_message(
                f"⚠️ This server (`{server_id}`) does not have an active subscription.",
                ephemeral=True
            )
        else:
            expire_at = row["expire_at"]
            expire_str = expire_at.strftime("%Y-%m-%d")
            await interaction.response.send_message(
                f"✅ Server `{server_id}` is subscribed until {expire_str}",
                ephemeral=True
            )

    @discord.app_commands.command(name="raw_subs", description="List all subscriptions visible to this bot")
    async def raw_subs(self, interaction: discord.Interaction):
        async with self.bot.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT server_id, expire_at FROM public.subscriptions ORDER BY expire_at DESC")

        if not rows:
            await interaction.response.send_message("❌ No subscriptions found.", ephemeral=True)
        else:
            msg = "\n".join([f"`{r['server_id']}` → {r['expire_at']:%Y-%m-%d}" for r in rows])
            await interaction.response.send_message(f"📋 Visible subscriptions:\n{msg}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(MemAssistantSubscription(bot))
    log.info("⚙️ MemAssistant Subscription cog loaded")

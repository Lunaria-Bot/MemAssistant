import logging
import discord
from discord.ext import commands
from discord import app_commands
import asyncpg
import secrets
from datetime import datetime, timezone, timedelta

log = logging.getLogger("cog-subscription")

OWNER_ID = 912376040142307419
GLOBAL_LOG_CHANNEL_ID = 1438563704751915018

def parse_duration(duration_str: str) -> int:
    unit = duration_str[-1].lower()
    value = int(duration_str[:-1])
    if unit == "d":
        return value * 86400
    elif unit == "h":
        return value * 3600
    elif unit == "m":
        return value * 60
    elif unit == "s":
        return value
    else:
        return int(duration_str)

class Subscription(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None

    async def cog_load(self):
        # Utilise la pool globale créée dans main.py
        self.pool = self.bot.db_pool
        log.info("✅ Pool Postgres attachée pour Subscription")

        async def global_check(interaction: discord.Interaction) -> bool:
            if interaction.command.name in [
                "generate-subscription",
                "active-subscription",
                "subscription-status",
                "force-expire"
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
        # Pas de fermeture de pool ici, main.py s'en occupe
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

    @app_commands.command(name="generate-subscription", description="Generate a subscription code for a server")
    async def generate_subscription(self, interaction: discord.Interaction, duration: str, serverid: str):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("⛔ You are not allowed to use this command.", ephemeral=True)
            return

        try:
            duration_seconds = parse_duration(duration)
        except Exception:
            await interaction.response.send_message("❌ Invalid duration format (use 1d, 12h, 30m, 60s).", ephemeral=True)
            return

        code = secrets.token_hex(8)
        expire_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO subscription_codes (code, server_id, expire_at) VALUES ($1, $2, $3)",
                code, int(serverid), expire_at
            )

        await interaction.response.send_message(
            f"✅ Subscription code generated for server `{serverid}` valid until {expire_at.strftime('%Y-%m-%d %H:%M UTC')}:\n`{code}`",
            ephemeral=True
        )
        log.info("🔑 Subscription code generated for server %s (expires %s)", serverid, expire_at)

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

    @app_commands.command(name="force-expire", description="Force expire a subscription for a server")
    async def force_expire(self, interaction: discord.Interaction, serverid: str):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("⛔ You are not allowed to use this command.", ephemeral=True)
            return

        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM subscriptions WHERE server_id=$1", int(serverid))

        await interaction.response.send_message(
            f"✅ Subscription forcibly expired for server `{serverid}`",
            ephemeral=True
        )
        log.info("⛔ Subscription forcibly expired for guild %s", serverid)
        await self.send_global_log(f"⛔ Subscription forcibly expired for guild `{serverid}` by owner")

async def setup(bot: commands.Bot):
    await bot.add_cog(Subscription(bot))
    log.info("⚙️ Subscription cog loaded (Postgres)")

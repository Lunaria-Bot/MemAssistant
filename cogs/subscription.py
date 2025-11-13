import os
import logging
import discord
from discord.ext import commands
from discord import app_commands
import redis.asyncio as redis
import secrets
import time
from datetime import datetime, timezone

log = logging.getLogger("cog-subscription")

REDIS_URL = os.getenv("REDIS_URL")
OWNER_ID = 912376040142307419  # toi uniquement
GLOBAL_LOG_CHANNEL_ID = 1438563704751915018  # salon global #subscription-logs

class Subscription(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.redis = None

    async def cog_load(self):
        try:
            self.redis = redis.from_url(REDIS_URL, decode_responses=True)
            await self.redis.ping()
            log.info("✅ Redis connecté pour Subscription")
        except Exception as e:
            log.error("❌ Échec connexion Redis : %s", e)
            self.redis = None

        # Ajout du check global
        @self.bot.tree.before_invoke
        async def check_subscription(interaction: discord.Interaction):
            # Autoriser les commandes de subscription même si inactive
            if interaction.command.name in ["generate-subscription", "active-subscription", "subscription-status"]:
                return

            if not interaction.guild:
                return  # DM → pas de check

            if not await self.is_active(interaction.guild.id):
                await interaction.response.send_message(
                    "❌ This server does not have an active subscription.",
                    ephemeral=True
                )
                await self.send_global_log(
                    f"⛔ Blocked command `{interaction.command.name}` in **{interaction.guild.name}** "
                    f"(subscription inactive)"
                )
                raise app_commands.CheckFailure("Subscription inactive")

    async def cog_unload(self):
        if self.redis:
            await self.redis.close()

    def get_subscription_key(self, guild_id: int) -> str:
        return f"subscription:{guild_id}"

    async def is_active(self, guild_id: int) -> bool:
        if not self.redis:
            return False
        key = self.get_subscription_key(guild_id)
        data = await self.redis.hgetall(key)
        if not data:
            return False
        expire_at = int(data.get("expire_at", 0))
        return expire_at > int(time.time())

    async def send_global_log(self, message: str):
        channel = self.bot.get_channel(GLOBAL_LOG_CHANNEL_ID)
        if channel:
            try:
                await channel.send(message)
            except discord.Forbidden:
                log.warning("❌ Impossible d’envoyer le log global")

    # --- Commande owner only ---
    @app_commands.command(name="generate-subscription", description="Generate a subscription code for a server")
    async def generate_subscription(self, interaction: discord.Interaction, duration: int, serverid: str):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("⛔ You are not allowed to use this command.", ephemeral=True)
            return

        if not self.redis:
            await interaction.response.send_message("❌ Redis not available.", ephemeral=True)
            return

        code = secrets.token_hex(8)
        expire_at = int(time.time()) + duration

        key = f"subscription-code:{code}"
        await self.redis.hset(key, mapping={"server_id": serverid, "expire_at": expire_at})

        await interaction.response.send_message(
            f"✅ Subscription code generated for server `{serverid}` valid {duration}s:\n`{code}`",
            ephemeral=True
        )
        log.info("🔑 Subscription code generated for server %s (expires in %ss)", serverid, duration)

    # --- Commande admin only ---
    @app_commands.command(name="active-subscription", description="Activate subscription for this server")
    async def active_subscription(self, interaction: discord.Interaction, code: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
            return

        if not self.redis:
            await interaction.response.send_message("❌ Redis not available.", ephemeral=True)
            return

        key = f"subscription-code:{code}"
        data = await self.redis.hgetall(key)
        if not data:
            await interaction.response.send_message("❌ Invalid code.", ephemeral=True)
            return

        server_id = int(data.get("server_id", 0))
        expire_at = int(data.get("expire_at", 0))

        if server_id != interaction.guild.id:
            await interaction.response.send_message("❌ This code is not for this server.", ephemeral=True)
            return

        if expire_at <= int(time.time()):
            await interaction.response.send_message("❌ Code expired.", ephemeral=True)
            return

        sub_key = self.get_subscription_key(interaction.guild.id)
        await self.redis.hset(sub_key, mapping={"expire_at": expire_at})

        await interaction.response.send_message("✅ Subscription activated for this server.", ephemeral=True)
        log.info("✅ Subscription activated for guild %s until %s", interaction.guild.id, expire_at)

    # --- Commande admin only: status ---
    @app_commands.command(name="subscription-status", description="Check subscription status for this server")
    async def subscription_status(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
            return

        if not self.redis:
            await interaction.response.send_message("❌ Redis not available.", ephemeral=True)
            return

        key = self.get_subscription_key(interaction.guild.id)
        data = await self.redis.hgetall(key)
        if not data:
            await interaction.response.send_message("❌ No active subscription for this server.", ephemeral=True)
            return

        expire_at = int(data.get("expire_at", 0))
        if expire_at <= int(time.time()):
            await interaction.response.send_message("❌ Subscription expired.", ephemeral=True)
            return

        expire_dt = datetime.fromtimestamp(expire_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await interaction.response.send_message(
            f"✅ Subscription active until **{expire_dt}**",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Subscription(bot))
    log.info("⚙️ Subscription cog loaded")

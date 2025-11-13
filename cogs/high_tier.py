import time
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import asyncpg

log = logging.getLogger("cog-high-tier")

RARITY_EMOJIS = {
    "1342202597389373530": "SR",
    "1342202212948115510": "SSR",
    "1342202203515125801": "UR",
}

RARITY_CUSTOM_EMOJIS = {
    "SR": "<a:13422080344824259361ezgifcomopti:1438537746863095858>",
    "SSR": "<a:emoji_1763043426681:1438533139512430633>",
    "UR": "<a:emoji_1763043453782:1438533253903679618>",
}

RARITY_MESSAGES = {
    "SR":  "{emoji} has summoned, claim it!",
    "SSR": "{emoji} has summoned, claim it!",
    "UR":  "{emoji} has summoned, claim it!!",
}

RARITY_PRIORITY = {"SR": 1, "SSR": 2, "UR": 3}
DEFAULT_COOLDOWN = 300  # secondes

DATABASE_URL = os.getenv("DATABASE_URL")

class HighTier(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.triggered_messages = {}
        self.pool: asyncpg.Pool | None = None
        self.cleanup_triggered.start()

    async def cog_load(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        log.info("✅ Postgres connecté pour HighTier")

    def cog_unload(self):
        self.cleanup_triggered.cancel()

    async def is_subscription_active(self, guild_id: int) -> bool:
        """Vérifie si la souscription est active via Postgres."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expire_at FROM subscriptions WHERE server_id=$1", guild_id
            )
            if not row:
                return False
            expire_at = row["expire_at"]
            return expire_at > datetime.now(timezone.utc)

    async def check_cooldown(self, user_id: int) -> int:
        # Ici tu peux garder Redis si tu veux, ou migrer vers Postgres
        return 0  # simplifié pour l’exemple

    async def get_config(self, guild: discord.Guild):
        config_cog = self.bot.get_cog("GuildConfig")
        if config_cog:
            return await config_cog.get_config(guild.id)
        return None

    @app_commands.command(name="high-tier", description="Get the High Tier role to be notified of rare spawn")
    async def high_tier(self, interaction: discord.Interaction):
        await self._give_high_tier(interaction)

    @app_commands.command(name="hightier", description="Alias of /high-tier")
    async def hightier_alias(self, interaction: discord.Interaction):
        await self._give_high_tier(interaction)

    async def _give_high_tier(self, interaction: discord.Interaction):
        config = await self.get_config(interaction.guild)
        if not config or not config.get("high_tier_role_id"):
            await interaction.response.send_message("❌ High Tier role not configured.", ephemeral=True)
            return

        role = interaction.guild.get_role(config["high_tier_role_id"])
        if not role:
            await interaction.response.send_message("❌ High Tier role not found.", ephemeral=True)
            return

        member = interaction.user
        if role in member.roles:
            await interaction.response.send_message("✅ You already have the High Tier role.", ephemeral=True)
            return

        try:
            await member.add_roles(role, reason="User opted in for High Tier notifications")
            await interaction.response.send_message(
                f"You just got the {role.mention}. You will be notified now.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions to assign the role.", ephemeral=True)

    @app_commands.command(name="high-tier-remove", description="Remove the High Tier role and stop notifications")
    async def high_tier_remove(self, interaction: discord.Interaction):
        config = await self.get_config(interaction.guild)
        if not config or not config.get("high_tier_role_id"):
            await interaction.response.send_message("❌ High Tier role not configured.", ephemeral=True)
            return

        role = interaction.guild.get_role(config["high_tier_role_id"])
        if not role:
            await interaction.response.send_message("❌ High Tier role not found.", ephemeral=True)
            return

        member = interaction.user
        if role not in member.roles:
            await interaction.response.send_message("ℹ️ You don’t have the High Tier role.", ephemeral=True)
            return

        try:
            await member.remove_roles(role, reason="User opted out of High Tier notifications")
            await interaction.response.send_message(
                f"✅ The {role.mention} has been removed. You will no longer be notified.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions to remove the role.", ephemeral=True)

    @tasks.loop(minutes=30)
    async def cleanup_triggered(self):
        now = time.time()
        self.triggered_messages = {
            mid: ts for mid, ts in self.triggered_messages.items()
            if now - ts < 6 * 3600
        }

    @cleanup_triggered.before_loop
    async def before_cleanup_triggered(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or not after.embeds:
            return
        if after.id in self.triggered_messages:
            return

        embed = after.embeds[0]
        title = (embed.title or "").lower()
        desc = (embed.description or "")

        if "auto summon" not in title:
            return

        found_rarity = None
        highest_priority = 0
        for emoji_id, rarity in RARITY_EMOJIS.items():
            if str(emoji_id) in desc:
                if RARITY_PRIORITY[rarity] > highest_priority:
                    found_rarity = rarity
                    highest_priority = RARITY_PRIORITY[rarity]

        if found_rarity:
            # Vérifie la souscription
            if not await self.is_subscription_active(after.guild.id):
                await after.channel.send("⚠️ Subscription not active — High Tier spawn detected but notifications disabled.")
                return

            config = await self.get_config(after.guild)
            role_id = config["high_tier_role_id"] if config else None
            role = after.guild.get_role(role_id) if role_id else None

            required_id = config.get("required_role_id") if config else None
            required_role = after.guild.get_role(required_id) if required_id else None

            if role:
                self.triggered_messages[after.id] = time.time()
                emoji = RARITY_CUSTOM_EMOJIS.get(found_rarity, "🌸")
                msg = RARITY_MESSAGES[found_rarity].format(emoji=emoji)

                if required_role:
                    await after.channel.send(f"{msg}\n🔥 {role.mention} (only for {required_role.mention})")
                else:
                    await after.channel.send(f"{msg}\n🔥 {role.mention}")

async def setup(bot: commands.Bot):
    await bot.add_cog(HighTier(bot))
    log.info("⚙️ HighTier cog loaded (subscription check)")

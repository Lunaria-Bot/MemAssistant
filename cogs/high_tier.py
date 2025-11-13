import time
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("cog-high-tier")

RARITY_EMOJIS = {
    "1342202597389373530": "SR",
    "1342202212948115510": "SSR",
    "1342202203515125801": "UR",
}

RARITY_CUSTOM_EMOJIS = {
    "SR": "<a:SuperRare:1342208034482425936>",
    "SSR": "<a:SuperSuperRare:1342208039918370857>",
    "UR": "<a:UltraRare:1342208044351623199>",
}

RARITY_MESSAGES = {
    "SR":  "{emoji} has summoned, claim it!",
    "SSR": "{emoji} has summoned, claim it!",
    "UR":  "{emoji} has summoned, claim it!!",
}

RARITY_PRIORITY = {"SR": 1, "SSR": 2, "UR": 3}
DEFAULT_COOLDOWN = 300  # secondes

class HighTier(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.triggered_messages = {}
        self.cleanup_triggered.start()

    def cog_unload(self):
        self.cleanup_triggered.cancel()

    async def check_cooldown(self, user_id: int) -> int:
        if not getattr(self.bot, "redis", None):
            return 0
        key = f"cooldown:high-tier:{user_id}"
        last_ts = await self.bot.redis.get(key)
        now = int(time.time())
        if last_ts:
            elapsed = now - int(last_ts)
            if elapsed < DEFAULT_COOLDOWN:
                return DEFAULT_COOLDOWN - elapsed
        await self.bot.redis.set(key, str(now))
        return 0

    async def get_config(self, guild: discord.Guild):
        config_cog = self.bot.get_cog("GuildConfig")
        if config_cog:
            return await config_cog.get_config(guild.id)
        return None

    @app_commands.command(name="high-tier", description="Get the High Tier role to be notified of rare spawn")
    async def high_tier(self, interaction: discord.Interaction):
        remaining = await self.check_cooldown(interaction.user.id)
        if remaining > 0:
            await interaction.response.send_message(
                f"⏳ You must wait {remaining}s before using this command again.",
                ephemeral=True
            )
            return

        config = await self.get_config(interaction.guild)
        if not config or not config.get("high_tier_role_id"):
            await interaction.response.send_message("❌ High Tier role not configured for this server.", ephemeral=True)
            return

        required_id = config.get("required_role_id")
        required_role = interaction.guild.get_role(required_id) if required_id else None
        if required_role and required_role not in interaction.user.roles:
            await interaction.response.send_message(
                f"oops, only {required_role.mention} have access to this feature <:lilac_pensivebread:1415672792522952725>.",
                ephemeral=True
            )
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
            log.info("🎖️ %s received High Tier role", member.display_name)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions to assign the role.", ephemeral=True)

    @app_commands.command(name="high-tier-remove", description="Remove the High Tier role and stop notifications")
    async def high_tier_remove(self, interaction: discord.Interaction):
        remaining = await self.check_cooldown(interaction.user.id)
        if remaining > 0:
            await interaction.response.send_message(
                f"⏳ You must wait {remaining}s before using this command again.",
                ephemeral=True
            )
            return

        config = await self.get_config(interaction.guild)
        if not config or not config.get("high_tier_role_id"):
            await interaction.response.send_message("❌ High Tier role not configured for this server.", ephemeral=True)
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
            log.info("🚫 %s removed High Tier role", member.display_name)
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
            config = await self.get_config(after.guild)
            role_id = config["high_tier_role_id"] if config else None
            role = after.guild.get_role(role_id) if role_id else None
            if role:
                self.triggered_messages[after.id] = time.time()
                emoji = RARITY_CUSTOM_EMOJIS.get(found_rarity, "🌸")
                msg = RARITY_MESSAGES[found_rarity].format(emoji=emoji)
                await after.channel.send(f"{msg}\n🔥 {role.mention}")

async def setup(bot: commands.Bot):
    await bot.add_cog(HighTier(bot))
    log.info("⚙️ HighTier cog loaded")

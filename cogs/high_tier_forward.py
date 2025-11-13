import discord
from discord.ext import commands
import os

FORWARD_GUILD_ID = int(os.getenv("FORWARD_GUILD_ID", "0"))
FORWARD_CHANNEL_ID = int(os.getenv("FORWARD_CHANNEL_ID", "0"))

class HighTierForward(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def forward_embed(self, source_message: discord.Message, rarity: str, emoji: str):
        # Récupérer le serveur et le salon cible
        guild = self.bot.get_guild(FORWARD_GUILD_ID)
        if not guild:
            return
        channel = guild.get_channel(FORWARD_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(
            title="🌸 High Tier Claim Detected",
            description=f"**Rarity:** {rarity}\n**Source Server:** {source_message.guild.name}\n**Channel:** {source_message.channel.mention}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Message Link", value=f"[Jump to message]({source_message.jump_url})", inline=False)
        embed.set_footer(text=f"Forwarded by {self.bot.user.name}")

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or not after.embeds:
            return

        embed = after.embeds[0]
        title = (embed.title or "").lower()
        desc = (embed.description or "")

        if "auto summon" not in title:
            return

        # Détection simple (tu peux réutiliser ton mapping RARITY_EMOJIS)
        rarity = None
        if "UR" in desc:
            rarity = "UR"
        elif "SSR" in desc:
            rarity = "SSR"
        elif "SR" in desc:
            rarity = "SR"

        if rarity:
            emoji = {"SR":"<a:SuperRare:1342208034482425936>",
                     "SSR":"<a:SuperSuperRare:1342208039918370857>",
                     "UR":"<a:UltraRare:1342208044351623199>"}.get(rarity, "🌸")
            await self.forward_embed(after, rarity, emoji)

async def setup(bot: commands.Bot):
    await bot.add_cog(HighTierForward(bot))

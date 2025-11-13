import discord
from discord.ext import commands
import logging

log = logging.getLogger("cog-high-tier-forward")

RARITY_EMOJIS = {
    "1342202597389373530": "SR",
    "1342202212948115510": "SSR",
    "1342202203515125801": "UR",
}

RARITY_PRIORITY = {"SR": 1, "SSR": 2, "UR": 3}
HIGH_TIER_RARITIES = {"SR", "SSR", "UR"}

FORWARD_CHANNEL_ID = 1438519407751069778 

def clone_embed(source: discord.Embed) -> discord.Embed:
    new = discord.Embed(
        title=source.title,
        description=source.description,
        color=source.color
    )
    if source.author:
        new.set_author(name=source.author.name, icon_url=source.author.icon_url)
    if source.footer:
        new.set_footer(text=source.footer.text, icon_url=source.footer.icon_url)
    if source.thumbnail:
        new.set_thumbnail(url=source.thumbnail.url)
    if source.image:
        new.set_image(url=source.image.url)
    new.url = source.url
    for field in source.fields:
        new.add_field(name=field.name, value=field.value, inline=field.inline)
    return new

class HighTierForward(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or not after.embeds:
            return

        embed = after.embeds[0]
        title = (embed.title or "").lower()
        desc = embed.description or ""

        if "auto summon" not in title and "summon claimed" not in title:
            return

        found_rarity = None
        highest_priority = 0
        for emoji_id, rarity in RARITY_EMOJIS.items():
            if str(emoji_id) in desc:
                if RARITY_PRIORITY[rarity] > highest_priority:
                    found_rarity = rarity
                    highest_priority = RARITY_PRIORITY[rarity]

        if not found_rarity:
            log.info("🔎 Aucun emoji de rareté détecté dans le message %s", after.id)
            return

        if found_rarity not in HIGH_TIER_RARITIES:
            log.info("🔎 Rareté ignorée : %s (non high-tier)", found_rarity)
            return

        target_channel = self.bot.get_channel(FORWARD_CHANNEL_ID)
        if not target_channel:
            log.warning("❌ Salon de forwarding introuvable (%s)", FORWARD_CHANNEL_ID)
            return

        cloned = clone_embed(embed)
        source_name = after.guild.name
        source_channel = after.channel.name

        header = (
            f"🌸 High Tier Claim Detected\n"
            f"Rarity: {found_rarity}\n"
            f"Source Server: {source_name}\n"
            f"Channel: 🌐 {source_name} › # {source_channel}"
        )

        await target_channel.send(header, embed=cloned)
        log.info("📤 Forwarded High Tier (%s) from %s › #%s", found_rarity, source_name, source_channel)

async def setup(bot: commands.Bot):
    await bot.add_cog(HighTierForward(bot))
    log.info("⚙️ HighTierForward cog loaded")

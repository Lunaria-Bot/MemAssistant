import discord
from discord.ext import commands
import logging
import re

log = logging.getLogger("cog-high-tier-forward")

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

RARITY_PRIORITY = {"SR": 1, "SSR": 2, "UR": 3}
HIGH_TIER_RARITIES = {"SR", "SSR", "UR"}

FORWARD_CHANNEL_ID = 1438519407751069778

def replace_rarity_tokens(text: str | None) -> str | None:
    if not text:
        return text
    def repl(match):
        token = match.group(0).upper()
        return RARITY_CUSTOM_EMOJIS.get(token, token)
    return re.sub(r"\b(SR|SSR|UR)\b", repl, text, flags=re.IGNORECASE)

def clone_embed_with_emojis(source: discord.Embed) -> discord.Embed:
    new = discord.Embed(
        title=replace_rarity_tokens(source.title),
        description=replace_rarity_tokens(source.description),
        color=source.color
    )
    if source.author:
        new.set_author(
            name=replace_rarity_tokens(source.author.name),
            icon_url=source.author.icon_url
        )
    if source.footer:
        new.set_footer(
            text=replace_rarity_tokens(source.footer.text),
            icon_url=source.footer.icon_url
        )
    if source.thumbnail:
        new.set_thumbnail(url=source.thumbnail.url)
    if source.image:
        new.set_image(url=source.image.url)
    new.url = source.url
    for field in source.fields:
        new.add_field(
            name=replace_rarity_tokens(field.name),
            value=replace_rarity_tokens(field.value),
            inline=field.inline
        )
    return new

class HighTierForward(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.forwarded_ids = set()

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or not after.embeds:
            return
        if after.id in self.forwarded_ids:
            return

        embed = after.embeds[0]
        title = (embed.title or "").lower()
        desc = embed.description or ""

        if "summon claimed" not in title:
            return

        found_rarity = None
        highest_priority = 0
        for emoji_id, rarity in RARITY_EMOJIS.items():
            if emoji_id in desc:
                if RARITY_PRIORITY[rarity] > highest_priority:
                    found_rarity = rarity
                    highest_priority = RARITY_PRIORITY[rarity]

        if not found_rarity or found_rarity not in HIGH_TIER_RARITIES:
            return

        target_channel = self.bot.get_channel(FORWARD_CHANNEL_ID)
        if not target_channel:
            log.warning("❌ Salon de forwarding introuvable (%s)", FORWARD_CHANNEL_ID)
            return

        emoji = RARITY_CUSTOM_EMOJIS.get(found_rarity, "🌸")
        cloned = clone_embed_with_emojis(embed)
        source_name = after.guild.name
        source_channel = after.channel.name

        header = (
            f"🌸 High Tier Claim Detected\n"
            f"Rarity: {emoji}\n"
            f"Source Server: {source_name}\n"
            f"Channel: 🌐 {source_name} › #{source_channel}"
        )

        await target_channel.send(header, embed=cloned)
        self.forwarded_ids.add(after.id)
        log.info("📤 Forwarded High Tier (%s) from %s › #%s", found_rarity, source_name, source_channel)

async def setup(bot: commands.Bot):
    await bot.add_cog(HighTierForward(bot))
    log.info("⚙️ HighTierForward cog loaded")

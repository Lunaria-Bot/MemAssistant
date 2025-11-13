import time
import re
import discord
from discord.ext import commands
import logging

log = logging.getLogger("cog-high-tier-forward")

# Emoji ID markers used by the source embeds to infer rarity
RARITY_EMOJIS = {
    "1342202597389373530": "SR",
    "1342202212948115510": "SSR",
    "1342202203515125801": "UR",
}

# Custom animated emoji replacements
RARITY_CUSTOM_EMOJIS = {
    "SR": "<a:13422080344824259361ezgifcomopti:1438537746863095858>",
    "SSR": "<a:emoji_1763043426681:1438533139512430633>",
    "UR": "<a:emoji_1763043453782:1438533253903679618>",
}

RARITY_PRIORITY = {"SR": 1, "SSR": 2, "UR": 3}
HIGH_TIER_RARITIES = {"SR", "SSR", "UR"}

FORWARD_CHANNEL_ID = 1438519407751069778
DEDUP_TTL_SECONDS = 600  # 10 minutes debounce per message ID

def replace_rarity_text(text: str) -> str:
    """Replace plain SR/SSR/UR tokens with custom emojis, safely."""
    if not text:
        return text
    # Replace standalone tokens (word boundaries), case-insensitive
    def repl(match):
        token = match.group(0).upper()
        return RARITY_CUSTOM_EMOJIS.get(token, token)
    return re.sub(r"\b(SSR|SR|UR)\b", repl, text, flags=re.IGNORECASE)

def clone_and_transform_embed(source: discord.Embed) -> discord.Embed:
    """Clone the embed and replace SR/SSR/UR in title, description, fields, footer."""
    new = discord.Embed(
        title=replace_rarity_text(source.title or None),
        description=replace_rarity_text(source.description or None),
        color=source.color
    )
    if source.author:
        new.set_author(name=replace_rarity_text(source.author.name or ""), icon_url=source.author.icon_url)
    if source.footer:
        new.set_footer(text=replace_rarity_text(source.footer.text or ""), icon_url=source.footer.icon_url)
    if source.thumbnail:
        new.set_thumbnail(url=source.thumbnail.url)
    if source.image:
        new.set_image(url=source.image.url)
    new.url = source.url
    for field in source.fields:
        new.add_field(
            name=replace_rarity_text(field.name or ""),
            value=replace_rarity_text(field.value or ""),
            inline=field.inline
        )
    return new

class HighTierForward(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Track message_id -> timestamp to prevent duplicates
        self.forwarded_messages: dict[int, float] = {}

    def _is_deduped(self, message_id: int) -> bool:
        now = time.time()
        ts = self.forwarded_messages.get(message_id)
        # Purge old entries
        for mid in list(self.forwarded_messages.keys()):
            if now - self.forwarded_messages[mid] > DEDUP_TTL_SECONDS:
                self.forwarded_messages.pop(mid, None)
        return ts is not None and (now - ts) <= DEDUP_TTL_SECONDS

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or not after.embeds:
            return
        if self._is_deduped(after.id):
            return

        embed = after.embeds[0]
        title = (embed.title or "").lower()
        desc = embed.description or ""

        # Only forward on actual claim (ignore auto summon-only edits)
        if "summon claimed" not in title:
            return

        # Detect highest rarity from emoji markers in description
        found_rarity = None
        highest_priority = 0
        for emoji_id, rarity in RARITY_EMOJIS.items():
            if emoji_id in desc:  # keep as raw substring check
                pr = RARITY_PRIORITY[rarity]
                if pr > highest_priority:
                    found_rarity = rarity
                    highest_priority = pr

        if not found_rarity or found_rarity not in HIGH_TIER_RARITIES:
            return

        target_channel = self.bot.get_channel(FORWARD_CHANNEL_ID)
        if not target_channel:
            log.warning("❌ Salon de forwarding introuvable (%s)", FORWARD_CHANNEL_ID)
            return

        # Transform embed content to replace SR/SSR/UR with custom emojis
        cloned = clone_and_transform_embed(embed)
        source_name = after.guild.name
        source_channel = after.channel.name
        rarity_emoji = RARITY_CUSTOM_EMOJIS.get(found_rarity, "🌸")

        header = (
            f"🌸 High Tier Claim Detected\n"
            f"Rarity: {rarity_emoji}\n"
            f"Source Server: {source_name}\n"
            f"Channel: 🌐 {source_name} › #{source_channel}"
        )

        await target_channel.send(header, embed=cloned)
        self.forwarded_messages[after.id] = time.time()
        log.info("📤 Forwarded High Tier (%s) from %s › #%s", found_rarity, source_name, source_channel)

async def setup(bot: commands.Bot):
    await bot.add_cog(HighTierForward(bot))
    log.info("⚙️ HighTierForward cog loaded")

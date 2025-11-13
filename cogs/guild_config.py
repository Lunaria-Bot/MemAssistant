import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
import os

class GuildConfig(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_pool(self):
        if not hasattr(self.bot, "db_pool"):
            self.bot.db_pool = await asyncpg.create_pool(dsn=os.getenv("DATABASE_URL"))
        return self.bot.db_pool

    @app_commands.command(name="set-high-tier-role", description="Configure le rôle High Tier pour ce serveur")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_high_tier_role(self, interaction: discord.Interaction, role: discord.Role):
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO guild_config (guild_id, high_tier_role_id)
                VALUES ($1, $2)
                ON CONFLICT (guild_id) DO UPDATE
                SET high_tier_role_id = EXCLUDED.high_tier_role_id,
                    updated_at = CURRENT_TIMESTAMP
            """, interaction.guild.id, role.id)

        await interaction.response.send_message(f"✅ Rôle High Tier configuré : {role.mention}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(GuildConfig(bot))

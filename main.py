import discord
from discord.ext import commands
import os
import asyncio
import asyncpg

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user} (ID: {bot.user.id})")

async def setup_db():
    bot.db_pool = await asyncpg.create_pool(dsn=os.getenv("DATABASE_URL"))
    print("✅ Connexion Postgres établie")

async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("❌ DISCORD_TOKEN non défini dans les variables d'environnement")

    async with bot:
        await setup_db()

        # Charger automatiquement tous les cogs
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                cog_name = f"cogs.{filename[:-3]}"
                try:
                    await bot.load_extension(cog_name)
                    print(f"[COG] {cog_name} chargé avec succès.")
                except Exception as e:
                    print(f"[ERROR] Échec du chargement du cog {cog_name} : {e}")

        await bot.start(token)

asyncio.run(main())

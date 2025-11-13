import discord
from discord.ext import commands
import os
import asyncio
import asyncpg
import redis.asyncio as redis   # ✅ remplacement de aioredis par redis-py officiel

intents = discord.Intents.default()
intents.members = True          # nécessite "Server Members Intent" activé dans le Developer Portal
intents.message_content = True  # nécessite "Message Content Intent" activé dans le Developer Portal

bot = commands.Bot(command_prefix="?", intents=intents)

# --- Setup Postgres ---
async def setup_db(bot):
    bot.db_pool = await asyncpg.create_pool(dsn=os.getenv("DATABASE_URL"))
    print("✅ Connexion Postgres établie")

# --- Setup Redis ---
async def setup_redis(bot):
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        print("⚠️ REDIS_URL non défini, Redis désactivé")
        bot.redis = None
        return
    bot.redis = redis.from_url(redis_url, decode_responses=True)
    print("✅ Connexion Redis établie")

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user} (ID: {bot.user.id})")
    try:
        # 🔥 Synchronisation globale des commandes slash
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} commandes slash synchronisées.")
    except Exception as e:
        print(f"❌ Erreur de sync des commandes : {e}")

async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("❌ DISCORD_TOKEN non défini dans les variables d'environnement")

    async with bot:
        await setup_db(bot)
        await setup_redis(bot)

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

if __name__ == "__main__":
    asyncio.run(main())

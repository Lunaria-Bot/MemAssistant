import discord
from discord.ext import commands
import os
import asyncio
import asyncpg
import redis.asyncio as redis
import logging

# --- Logging global ---
logging.basicConfig(
    level=logging.INFO,  # mets DEBUG si tu veux encore plus de détails
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("main")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

# --- Setup Postgres ---
async def setup_db(bot):
    bot.db_pool = await asyncpg.create_pool(dsn=os.getenv("DATABASE_URL"))
    log.info("✅ Connexion Postgres établie")

# --- Setup Redis ---
async def setup_redis(bot):
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        log.warning("⚠️ REDIS_URL non défini, Redis désactivé")
        bot.redis = None
        return
    bot.redis = redis.from_url(redis_url, decode_responses=True)
    log.info("✅ Connexion Redis établie")

@bot.event
async def on_ready():
    log.info(f"✅ Bot connecté : {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        log.info(f"✅ {len(synced)} commandes slash synchronisées.")
    except Exception as e:
        log.error(f"❌ Erreur de sync des commandes : {e}")

# --- Handler global des erreurs slash ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    import traceback
    log.error("❌ Erreur dans une commande slash : %s", traceback.format_exc())
    try:
        await interaction.response.send_message(
            "❌ Une erreur est survenue lors de l'exécution de la commande.",
            ephemeral=True
        )
    except discord.InteractionResponded:
        await interaction.followup.send(
            "❌ Une erreur est survenue après la réponse initiale.",
            ephemeral=True
        )

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
                    log.info(f"[COG] {cog_name} chargé avec succès.")
                except Exception as e:
                    log.error(f"[ERROR] Échec du chargement du cog {cog_name} : {e}")

        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())

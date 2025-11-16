import discord
from discord.ext import commands
import logging
from datetime import datetime

log = logging.getLogger("cog-memassistant-subscription")

class MemAssistantSubscription(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(name="debug_postgres", description="Audit complet Postgres côté MemAssistant")
    async def debug_postgres(self, interaction: discord.Interaction):
        async with self.bot.db_pool.acquire() as conn:
            # Connexion / schéma / rôle
            ip = await conn.fetchval("SELECT inet_server_addr()")
            port = await conn.fetchval("SELECT inet_server_port()")
            schema = await conn.fetchval("SELECT current_schema")
            user = await conn.fetchval("SELECT current_user")

            # Privilèges
            grants = await conn.fetch("""
                SELECT privilege_type
                FROM information_schema.role_table_grants
                WHERE table_name = 'subscriptions' AND grantee = $1
            """, user)

            # Nombre de lignes
            count = await conn.fetchval("SELECT COUNT(*) FROM public.subscriptions")

            # Structure de table
            cols = await conn.fetch("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'subscriptions'
                ORDER BY ordinal_position
            """)

            # Index
            idx = await conn.fetch("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'subscriptions'
            """)

            # Contraintes
            constraints = await conn.fetch("""
                SELECT conname, contype, pg_get_constraintdef(c.oid) AS def
                FROM pg_constraint c
                JOIN pg_class t ON c.conrelid = t.oid
                WHERE t.relname = 'subscriptions'
            """)

        perms = ", ".join(sorted(set(r["privilege_type"] for r in grants))) if grants else "❌ Aucun"
        cols_str = "\n".join([f"- {r['column_name']} ({r['data_type']})" for r in cols]) if cols else "❌ Table introuvable"
        idx_str = "\n".join([f"- {r['indexname']} : {r['indexdef']}" for r in idx]) if idx else "❌ Aucun index"
        cons_str = "\n".join([f"- {r['conname']} ({r['contype']}) → {r['def']}" for r in constraints]) if constraints else "❌ Aucune contrainte"

        await interaction.response.send_message(
            f"📡 Connexion : `{ip}:{port}`\n"
            f"📦 Schéma actif : `{schema}`\n"
            f"👤 Rôle SQL : `{user}`\n"
            f"🔐 Privilèges sur `subscriptions` : {perms}\n"
            f"📋 Lignes visibles : `{count}`\n"
            f"🗂️ Structure de table :\n{cols_str}\n"
            f"🗝️ Index :\n{idx_str}\n"
            f"🧩 Contraintes :\n{cons_str}",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(MemAssistantSubscription(bot))
    log.info("⚙️ MemAssistant Subscription cog loaded")

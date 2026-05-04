"""
Aethris Auth — Discord Bot
Required env vars (set in Render dashboard):
  DISCORD_TOKEN   – bot token
  GUILD_ID        – your server guild ID
  BUYER_ROLE_ID   – role assigned on /create
  BACKEND_URL     – your Render web service URL
  ADMIN_SECRET    – must match backend ADMIN_SECRET
  ADMIN_ROLE_ID   – (optional) role that can use admin commands
"""

import os
import aiohttp
import asyncio
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import tasks

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID      = int(os.environ["GUILD_ID"])
BUYER_ROLE_ID = int(os.environ.get("BUYER_ROLE_ID", "0"))
BACKEND_URL   = os.environ["BACKEND_URL"].rstrip("/")
ADMIN_SECRET  = os.environ["ADMIN_SECRET"]
ADMIN_ROLE_ID = os.environ.get("ADMIN_ROLE_ID")

intents = discord.Intents.default()
intents.members = True

class AethrisBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print(f"[Aethris] Slash commands synced to guild {GUILD_ID}")
        poll_actions.start()

    def is_admin(self, interaction: discord.Interaction) -> bool:
        if ADMIN_ROLE_ID:
            role = interaction.guild.get_role(int(ADMIN_ROLE_ID))
            return role in interaction.user.roles if role else False
        return interaction.user.guild_permissions.manage_guild

    async def on_ready(self):
        print(f"[Aethris] Logged in as {self.user} ({self.user.id})")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="Aethris Auth")
        )

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

bot = AethrisBot()

# ── HTTP helpers ───────────────────────────────────────────────────────────────

async def api_post(path: str, data: dict):
    async with bot.session.post(f"{BACKEND_URL}{path}", json=data) as r:
        return await r.json(), r.status

async def api_get(path: str, params: dict = {}):
    async with bot.session.get(f"{BACKEND_URL}{path}", params=params) as r:
        return await r.json(), r.status

def expiry_display(expiry: str | None, is_lifetime: bool) -> str:
    if is_lifetime or not expiry or expiry == "Lifetime":
        return "♾️ Lifetime"
    try:
        dt = datetime.fromisoformat(expiry)
        days = (dt - datetime.utcnow()).days
        label = "expired" if days < 0 else f"{days}d left"
        return f"📅 {expiry[:10]} ({label})"
    except Exception:
        return expiry

# ── /create ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="create", description="Redeem a license key and create your account")
@app_commands.describe(
    key="Your license key (AETHRIS-XXXXX-XXXXX-XXXXX-XXXXX)",
    username="Username for the loader (3–20 chars)",
    password="Password for the loader (min 6 chars)"
)
async def cmd_create(interaction: discord.Interaction, key: str, username: str, password: str):
    await interaction.response.defer(ephemeral=True)

    data, status = await api_post("/api/redeem_key", {
        "key":          key.strip().upper(),
        "username":     username.strip(),
        "password":     password,
        "discord_id":   str(interaction.user.id),
        "discord_name": str(interaction.user),
    })

    if status != 200:
        embed = discord.Embed(title="❌ Failed", description=data.get("detail", "Unknown error"), color=0xef4444)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # Assign buyer role
    if BUYER_ROLE_ID:
        try:
            guild  = interaction.guild
            member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)
            role   = guild.get_role(BUYER_ROLE_ID)
            if role and member:
                await member.add_roles(role, reason="Aethris license redeemed")
        except Exception as e:
            print(f"[Aethris] Role assign failed: {e}")

    exp = data.get("expiry", "Lifetime")
    embed = discord.Embed(title="✅ Account Created", description="Your account is ready.", color=0x7c3aed)
    embed.add_field(name="Username", value=f"`{data['username']}`", inline=True)
    embed.add_field(name="Password", value=f"||`{password}`||", inline=True)
    embed.add_field(name="Expiry",   value=expiry_display(exp, exp == "Lifetime"), inline=True)
    embed.add_field(name="HWID",     value="🔓 Unbound — locks on first login", inline=False)
    embed.set_footer(text="Aethris Auth • Keep your credentials safe")
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /panel ─────────────────────────────────────────────────────────────────────

@bot.tree.command(name="panel", description="View your account info")
async def cmd_panel(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    data, status = await api_get("/api/user_info", {"discord_id": str(interaction.user.id)})

    if status == 404:
        embed = discord.Embed(
            title="❌ No Account",
            description="You don't have an account.\nUse `/create <key> <username> <password>` to get started.",
            color=0xef4444
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if status != 200:
        await interaction.followup.send(embed=discord.Embed(title="❌ Error", description="Could not fetch account.", color=0xef4444), ephemeral=True)
        return

    banned  = data.get("is_banned", False)
    exp     = expiry_display(data.get("expiry_date"), data.get("is_lifetime", False))
    login   = (data.get("last_login") or "Never")[:10]

    embed = discord.Embed(title="👤 Your Account", color=0xef4444 if banned else 0x7c3aed)
    embed.add_field(name="Status",       value="🔴 **BANNED**" if banned else "🟢 Active", inline=True)
    embed.add_field(name="Username",     value=f"`{data.get('username','—')}`", inline=True)
    embed.add_field(name="Expiry",       value=exp, inline=True)
    embed.add_field(name="HWID",         value="🔒 Device locked" if data.get("hwid_bound") else "🔓 Not bound", inline=True)
    embed.add_field(name="Last Login",   value=login, inline=True)
    embed.add_field(name="Member Since", value=(data.get("created_at") or "—")[:10], inline=True)
    embed.set_footer(text="Aethris Auth • Use /resethwid or /resetpassword for self-service")
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /resethwid ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="resethwid", description="Unlink your current device (HWID reset)")
async def cmd_resethwid(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    data, status = await api_post("/api/user/reset_hwid", {"discord_id": str(interaction.user.id)})

    if status != 200:
        await interaction.followup.send(embed=discord.Embed(title="❌ Failed", description=data.get("detail", "Error"), color=0xef4444), ephemeral=True)
        return

    embed = discord.Embed(title="✅ HWID Reset", description="Your device has been unlinked.\nYou can now log in from a new device.", color=0x7c3aed)
    embed.set_footer(text="Your HWID will lock again on your next login")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /resetpassword ─────────────────────────────────────────────────────────────

@bot.tree.command(name="resetpassword", description="Reset your account password")
@app_commands.describe(new_password="Your new password (min 6 characters)")
async def cmd_resetpassword(interaction: discord.Interaction, new_password: str):
    await interaction.response.defer(ephemeral=True)

    if len(new_password) < 6:
        await interaction.followup.send(embed=discord.Embed(title="❌ Too Short", description="Password must be at least 6 characters.", color=0xef4444), ephemeral=True)
        return

    data, status = await api_post("/api/user/reset_password", {
        "discord_id":   str(interaction.user.id),
        "new_password": new_password,
    })

    if status != 200:
        await interaction.followup.send(embed=discord.Embed(title="❌ Failed", description=data.get("detail", "Error"), color=0xef4444), ephemeral=True)
        return

    embed = discord.Embed(title="✅ Password Reset", color=0x7c3aed)
    embed.add_field(name="Username",     value=f"`{data.get('username','—')}`", inline=True)
    embed.add_field(name="New Password", value=f"||`{new_password}`||", inline=True)
    embed.set_footer(text="Aethris Auth • Do not share your password")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /userinfo ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="userinfo", description="Get information about a user")
@app_commands.describe(user="User to look up (defaults to yourself)")
async def cmd_userinfo(interaction: discord.Interaction, user: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    user = user or interaction.user
    data, status = await api_get("/api/user_info", {"discord_id": str(user.id)})

    embed = discord.Embed(title=f"👤 {user.display_name}", description=f"ID: `{user.id}`", color=0x7c3aed)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="Joined Server",    value=(user.joined_at.strftime("%Y-%m-%d") if user.joined_at else "—"), inline=True)
    embed.add_field(name="Account Created",  value=user.created_at.strftime("%Y-%m-%d"), inline=True)
    roles = [r.mention for r in user.roles[1:]]
    embed.add_field(name="Roles", value=", ".join(roles) if roles else "None", inline=False)

    if status == 200:
        embed.add_field(name="Cheat Account", value=f"`{data.get('username','—')}`", inline=True)
        embed.add_field(name="Status",         value="🔴 **BANNED**" if data.get("is_banned") else "🟢 Active", inline=True)
        embed.add_field(name="Expiry",         value=expiry_display(data.get("expiry_date"), data.get("is_lifetime", False)), inline=True)
        embed.add_field(name="HWID",           value="🔒 Locked" if data.get("hwid_bound") else "🔓 Not bound", inline=True)
        embed.add_field(name="Last Login",     value=(data.get("last_login") or "Never")[:10], inline=True)
    elif status == 404:
        embed.add_field(name="Cheat Account", value="❌ No account found", inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /checkkey ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="checkkey", description="Check if a license key is valid")
@app_commands.describe(key="License key to check")
async def cmd_checkkey(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)
    data, status = await api_get("/admin/keys", {"admin_secret": ADMIN_SECRET})
    if status != 200:
        await interaction.followup.send("Could not check key.", ephemeral=True)
        return

    match = next((k for k in data if k["key"] == key.strip().upper()), None)
    if not match:
        embed = discord.Embed(title="❌ Not Found", description="That key does not exist.", color=0xef4444)
    elif match["is_used"]:
        embed = discord.Embed(title="🔑 Key Status", color=0xef4444)
        embed.add_field(name="Key",     value=f"`{match['key']}`", inline=False)
        embed.add_field(name="Status",  value="❌ Already redeemed", inline=True)
        if match.get("username"):
            embed.add_field(name="Used by", value=match["username"], inline=True)
    else:
        embed = discord.Embed(title="🔑 Key Status", color=0x10b981)
        embed.add_field(name="Key",    value=f"`{match['key']}`", inline=False)
        embed.add_field(name="Status", value="✅ Available", inline=True)
        embed.add_field(name="Type",   value="♾️ Lifetime" if match["is_lifetime"] else f"📅 {match['expiry_days']} days", inline=True)

    await interaction.followup.send(embed=embed, ephemeral=True)

# ── Admin: /message ────────────────────────────────────────────────────────────

@bot.tree.command(name="message", description="[Admin] Send a DM to a user")
@app_commands.describe(user="User to DM", message="Message to send")
async def cmd_message(interaction: discord.Interaction, user: discord.User, message: str):
    if not bot.is_admin(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    try:
        await user.send(message)
        embed = discord.Embed(title="✅ DM Sent", description=f"Sent to {user.mention}", color=0x7c3aed)
        embed.add_field(name="Message", value=message, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ User has DMs disabled.", ephemeral=True)

# ── Admin: /announce ───────────────────────────────────────────────────────────

@bot.tree.command(name="announce", description="[Admin] Send an announcement to a channel")
@app_commands.describe(channel="Target channel", message="Announcement text", ping="Ping @everyone?")
async def cmd_announce(interaction: discord.Interaction, channel: discord.TextChannel, message: str, ping: bool = False):
    if not bot.is_admin(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    try:
        content = f"@everyone\n{message}" if ping else message
        await channel.send(content)
        embed = discord.Embed(title="✅ Announcement Sent", description=f"Posted in {channel.mention}", color=0x7c3aed)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ No permission to post in that channel.", ephemeral=True)

# ── Admin: /role ───────────────────────────────────────────────────────────────

@bot.tree.command(name="role", description="[Admin] Add or remove a role from a user")
@app_commands.describe(action="add or remove", user="Target member", role="Role to manage")
@app_commands.choices(action=[
    app_commands.Choice(name="add",    value="add"),
    app_commands.Choice(name="remove", value="remove"),
])
async def cmd_role(interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member, role: discord.Role):
    if not bot.is_admin(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return
    try:
        if action.value == "add":
            await user.add_roles(role)
            desc = f"Added {role.mention} to {user.mention}"
        else:
            await user.remove_roles(role)
            desc = f"Removed {role.mention} from {user.mention}"
        await interaction.response.send_message(embed=discord.Embed(title="✅ Done", description=desc, color=0x7c3aed), ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ No permission to manage that role.", ephemeral=True)

# ── /help ──────────────────────────────────────────────────────────────────────

@bot.tree.command(name="help", description="Show available commands")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(title="🔐 Aethris Auth — Commands", color=0x7c3aed)
    embed.add_field(name="User", value=(
        "`/create` — Redeem a key & create account\n"
        "`/panel` — View your account info\n"
        "`/resethwid` — Unlink your device\n"
        "`/resetpassword` — Change your password\n"
        "`/checkkey` — Check a license key\n"
        "`/userinfo` — Look up a user\n"
        "`/help` — This message"
    ), inline=False)
    if bot.is_admin(interaction):
        embed.add_field(name="Admin", value=(
            "`/message` — DM a user\n"
            "`/announce` — Post to a channel\n"
            "`/role` — Add/remove roles"
        ), inline=False)
    embed.set_footer(text="Aethris Auth v2.0")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── Bot action poll ────────────────────────────────────────────────────────────

@tasks.loop(seconds=5)
async def poll_actions():
    try:
        data, status = await api_get("/admin/bot/poll", {"admin_secret": ADMIN_SECRET})
        if status != 200 or not isinstance(data, list) or not data:
            return
        for action in data:
            try:
                if action["type"] == "send_message":
                    ch = bot.get_channel(int(action["channel_id"])) or await bot.fetch_channel(int(action["channel_id"]))
                    await ch.send(action["message"])
                elif action["type"] == "manage_role":
                    guild  = bot.get_guild(GUILD_ID)
                    member = guild.get_member(int(action["discord_id"])) or await guild.fetch_member(int(action["discord_id"]))
                    role   = guild.get_role(int(action["role_id"]))
                    if member and role:
                        if action["action"] == "add":
                            await member.add_roles(role)
                        else:
                            await member.remove_roles(role)
            except Exception as e:
                print(f"[Aethris] Action error: {e}")
    except Exception as e:
        print(f"[Aethris] Poll error: {e}")

@poll_actions.before_loop
async def before_poll():
    await bot.wait_until_ready()

if __name__ == "__main__":
    print("[Aethris] Starting bot...")
    bot.run(DISCORD_TOKEN)

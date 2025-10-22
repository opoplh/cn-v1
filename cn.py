import discord
from discord.ext import commands
import asyncio
import subprocess
import json
from datetime import datetime
import shlex
import logging
import shutil
import os
from typing import Optional, List, Dict, Any
import threading
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('vps_bot')

# Check if lxc command is available
if not shutil.which("lxc"):
    logger.error("LXC command not found. Please ensure LXC is installed.")
    raise SystemExit("LXC command not found. Please ensure LXC is installed.")

# Bot setup - Changed prefix to .
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.members = True

# Disable the default help command
bot = commands.Bot(command_prefix='.', intents=intents, help_command=None)

# Main admin user ID
MAIN_ADMIN_ID = 1295737579840340032

# VPS User Role ID
VPS_USER_ROLE_ID = None

# CPU monitoring settings
CPU_THRESHOLD = 90  # Percentage at which to stop all VPS
CHECK_INTERVAL = 60  # Seconds between CPU checks
cpu_monitor_active = True

# Data storage functions
def load_data():
    try:
        with open('user_data.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("user_data.json not found or corrupted, initializing empty data")
        return {}

def load_vps_data():
    try:
        with open('vps_data.json', 'r') as f:
            loaded = json.load(f)
            vps_data = {}
            for uid, v in loaded.items():
                if isinstance(v, dict):
                    if "container_name" in v:
                        vps_data[uid] = [v]
                    else:
                        vps_data[uid] = list(v.values())
                elif isinstance(v, list):
                    vps_data[uid] = v
                else:
                    logger.warning(f"Unknown VPS data format for user {uid}, skipping")
                    continue
            return vps_data
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("vps_data.json not found or corrupted, initializing empty data")
        return {}

def load_admin_data():
    try:
        with open('admin_data.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("admin_data.json not found or corrupted, initializing with main admin")
        return {"admins": [str(MAIN_ADMIN_ID)]}

# Load all data at startup
user_data = load_data()
vps_data = load_vps_data()
admin_data = load_admin_data()

# Save data function
def save_data():
    try:
        with open('user_data.json', 'w') as f:
            json.dump(user_data, f, indent=4)
        with open('vps_data.json', 'w') as f:
            json.dump(vps_data, f, indent=4)
        with open('admin_data.json', 'w') as f:
            json.dump(admin_data, f, indent=4)
        logger.info("Data saved successfully")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

# Admin checks
def is_admin():
    async def predicate(ctx):
        user_id = str(ctx.author.id)
        if user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", []):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "You don't have permission to use this command."))
        return False
    return commands.check(predicate)

def is_main_admin():
    async def predicate(ctx):
        if str(ctx.author.id) == str(MAIN_ADMIN_ID):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "Only the main admin can use this command."))
        return False
    return commands.check(predicate)

# Embed creation functions with black theme
def create_embed(title, description="", color=0x1a1a1a, fields=None):
    """Create a dark-themed embed"""
    embed = discord.Embed(
        title=f"▌ {title}",
        description=description,
        color=color
    )

    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1424364952730472528/1430445158809469009/standard_1.gif?ex=68f9cd5e&is=68f87bde&hm=1638a802c6a54865119402f784240a319dce77d694f8f5679308a43b1526c726&")

    if fields:
        for field in fields:
            embed.add_field(
                name=f"▸ {field['name']}",
                value=field["value"],
                inline=field.get("inline", False)
            )

    embed.set_footer(text=f"CurlNode VPS Manager • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    icon_url="https://cdn.discordapp.com/attachments/1424364952730472528/1430445158809469009/standard_1.gif?ex=68f9cd5e&is=68f87bde&hm=1638a802c6a54865119402f784240a319dce77d694f8f5679308a43b1526c726&")

    return embed

def create_success_embed(title, description=""):
    return create_embed(title, description, color=0x00ff88)

def create_error_embed(title, description=""):
    return create_embed(title, description, color=0xff3366)

def create_info_embed(title, description=""):
    return create_embed(title, description, color=0x00ccff)

def create_warning_embed(title, description=""):
    return create_embed(title, description, color=0xffaa00)

# Clean LXC command execution
async def execute_lxc(command, timeout=120):
    """Execute LXC command with timeout and error handling"""
    try:
        cmd = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        if proc.returncode != 0:
            error = stderr.decode().strip() if stderr else "Command failed with no error output"
            raise Exception(error)

        return stdout.decode().strip() if stdout else True
    except asyncio.TimeoutError:
        logger.error(f"LXC command timed out: {command}")
        raise Exception(f"Command timed out after {timeout} seconds")
    except Exception as e:
        logger.error(f"LXC Error: {command} - {str(e)}")
        raise

# Get or create VPS user role
async def get_or_create_vps_role(guild):
    """Get or create the VPS User role"""
    global VPS_USER_ROLE_ID

    if VPS_USER_ROLE_ID:
        role = guild.get_role(VPS_USER_ROLE_ID)
        if role:
            return role

    role = discord.utils.get(guild.roles, name="VPS User")
    if role:
        VPS_USER_ROLE_ID = role.id
        return role

    try:
        role = await guild.create_role(
            name="VPS User",
            color=discord.Color.dark_purple(),
            reason="VPS User role for bot management",
            permissions=discord.Permissions.none()
        )
        VPS_USER_ROLE_ID = role.id
        logger.info(f"Created VPS User role: {role.name} (ID: {role.id})")
        return role
    except Exception as e:
        logger.error(f"Failed to create VPS User role: {e}")
        return None

# CPU monitoring function
def get_cpu_usage():
    """Get current CPU usage percentage"""
    try:
        # Get CPU usage using top command
        result = subprocess.run(['top', '-bn1'], capture_output=True, text=True)
        output = result.stdout

        # Parse the output to get CPU usage
        for line in output.split('\n'):
            if '%Cpu(s):' in line:
                # Extract the idle percentage
                parts = line.split(',')
                for part in parts:
                    if 'id,' in part:
                        idle = float(part.split('%')[0].split()[-1])
                        usage = 100.0 - idle
                        return usage
        return 0.0
    except Exception as e:
        logger.error(f"Error getting CPU usage: {e}")
        return 0.0

def cpu_monitor():
    """Monitor CPU usage and stop all VPS if threshold is exceeded"""
    global cpu_monitor_active

    while cpu_monitor_active:
        try:
            cpu_usage = get_cpu_usage()
            logger.info(f"Current CPU usage: {cpu_usage}%")

            if cpu_usage > CPU_THRESHOLD:
                logger.warning(f"CPU usage ({cpu_usage}%) exceeded threshold ({CPU_THRESHOLD}%). Stopping all VPS.")

                # Execute lxc stop --all --force
                try:
                    subprocess.run(['lxc', 'stop', '--all', '--force'], check=True)
                    logger.info("All VPS stopped due to high CPU usage")

                    # Update all VPS status in database
                    for user_id, vps_list in vps_data.items():
                        for vps in vps_list:
                            if vps.get('status') == 'running':
                                vps['status'] = 'stopped'
                    save_data()
                except Exception as e:
                    logger.error(f"Error stopping all VPS: {e}")

            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Error in CPU monitor: {e}")
            time.sleep(CHECK_INTERVAL)

# Start CPU monitoring in a separate thread
cpu_thread = threading.Thread(target=cpu_monitor, daemon=True)
cpu_thread.start()

# Bot events
@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=" CurlNode  VPS Manager"))
    logger.info("Bot is ready! No loops running - clean start.")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=create_error_embed("Missing Argument", "Please use `.help` for command usage."))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=create_error_embed("Invalid Argument", "Please check your input and try again."))
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(embed=create_error_embed("System Error", "An error occurred. Please try again."))

# Bot commands
@bot.command(name='create')
@is_admin()
async def create_vps(ctx, user: discord.Member, ram: int, cpu: int):
    """Create a custom VPS for a user (Admin only)"""
    if ram <= 0 or cpu <= 0:
        await ctx.send(embed=create_error_embed("Invalid Specs", "RAM and CPU must be positive integers."))
        return

    user_id = str(user.id)
    if user_id not in vps_data:
        vps_data[user_id] = []

    vps_count = len(vps_data[user_id]) + 1
    container_name = f"vps-{user_id}-{vps_count}"
    ram_mb = ram * 1024

    await ctx.send(embed=create_info_embed("Creating VPS", f"Deploying VPS for {user.mention}..."))

    try:
        await execute_lxc(f"lxc launch ubuntu:22.04 {container_name} --config limits.memory={ram_mb}MB --config limits.cpu={cpu} -s btrpool")

        vps_info = {
            "container_name": container_name,
            "ram": f"{ram}GB",
            "cpu": str(cpu),
            "storage": "10GB",
            "status": "running",
            "created_at": datetime.now().isoformat(),
            "shared_with": []
        }
        vps_data[user_id].append(vps_info)
        save_data()

        # Get or create VPS role and assign to user
        if ctx.guild:
            vps_role = await get_or_create_vps_role(ctx.guild)
            if vps_role:
                try:
                    await user.add_roles(vps_role, reason="VPS ownership granted")
                except discord.Forbidden:
                    logger.warning(f"Failed to assign VPS role to {user.name}")

        # Create success embed for channel
        embed = create_success_embed("VPS Created Successfully")
        embed.add_field(name="Owner", value=user.mention, inline=True)
        embed.add_field(name="VPS ID", value=f"#{vps_count}", inline=True)
        embed.add_field(name="Container", value=f"`{container_name}`", inline=True)
        embed.add_field(name="Resources", value=f"**RAM:** {ram}GB\n**CPU:** {cpu} Cores\n**Storage:** 10GB", inline=False)
        await ctx.send(embed=embed)

        # Send DM to user
        try:
            dm_embed = create_success_embed("VPS Created!", f"Your VPS has been successfully deployed!")
            dm_embed.add_field(name="VPS Details", value=f"**VPS ID:** #{vps_count}\n**Container:** `{container_name}`\n**RAM:** {ram}GB\n**CPU:** {cpu} Cores\n**Storage:** 10GB", inline=False)
            dm_embed.add_field(name="Next Steps", value="Use `.manage` to control your VPS\nUse `.manage` → SSH to get access credentials", inline=False)
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            await ctx.send(embed=create_info_embed("Notification Failed", f"Couldn't send DM to {user.mention}. Please ensure DMs are enabled."))

    except Exception as e:
        await ctx.send(embed=create_error_embed("Creation Failed", f"Error: {str(e)}"))

class ManageView(discord.ui.View):
    def __init__(self, user_id, vps_list, is_shared=False, owner_id=None, is_admin=False):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.vps_list = vps_list
        self.selected_index = None
        self.is_shared = is_shared
        self.owner_id = owner_id or user_id
        self.is_admin = is_admin

        if len(vps_list) > 1:
            options = [
                discord.SelectOption(
                    label=f"VPS {i+1} ({v.get('plan', 'Custom')})",
                    description=f"Status: {v.get('status', 'unknown')}",
                    value=str(i)
                ) for i, v in enumerate(vps_list)
            ]
            self.select = discord.ui.Select(placeholder="Select a VPS to manage", options=options)
            self.select.callback = self.select_vps
            self.add_item(self.select)
            self.initial_embed = create_embed("VPS Management", "Select a VPS from the dropdown menu below.", 0x1a1a1a)
            self.initial_embed.add_field(name="Available VPS", value="\n".join([f"**VPS {i+1}:** `{v['container_name']}` - Status: `{v.get('status', 'unknown').upper()}`" for i, v in enumerate(vps_list)]), inline=False)
        else:
            self.selected_index = 0
            self.initial_embed = self.create_vps_embed(0)
            self.add_action_buttons()

    def create_vps_embed(self, index):
        vps = self.vps_list[index]
        status_color = 0x00ff88 if vps.get('status') == 'running' else 0xff3366

        owner_text = ""
        if self.is_admin and self.owner_id != self.user_id:
            try:
                owner_user = bot.get_user(int(self.owner_id))
                owner_text = f"\n**Owner:** {owner_user.mention}"
            except:
                owner_text = f"\n**Owner ID:** {self.owner_id}"

        embed = create_embed(
            f"VPS Management - VPS {index + 1}",
            f"Managing container: `{vps['container_name']}`{owner_text}",
            status_color
        )

        resource_info = f"**Plan:** {vps.get('plan', 'Custom')}\n"
        resource_info += f"**Status:** `{vps.get('status', 'unknown').upper()}`\n"
        resource_info += f"**RAM:** {vps['ram']}\n"
        resource_info += f"**CPU:** {vps['cpu']} Cores\n"
        resource_info += f"**Storage:** {vps['storage']}"

        if "processor" in vps:
            resource_info += f"\n**Processor:** {vps['processor']}"

        embed.add_field(name="📊 Resources", value=resource_info, inline=False)
        embed.add_field(name="🎮 Controls", value="Use the buttons below to manage your VPS", inline=False)

        return embed

    def add_action_buttons(self):
        if not self.is_shared and not self.is_admin:
            reinstall_button = discord.ui.Button(label="🔄 Reinstall", style=discord.ButtonStyle.danger)
            reinstall_button.callback = lambda inter: self.action_callback(inter, 'reinstall')
            self.add_item(reinstall_button)

        start_button = discord.ui.Button(label="▶ Start", style=discord.ButtonStyle.success)
        start_button.callback = lambda inter: self.action_callback(inter, 'start')
        stop_button = discord.ui.Button(label="⏸ Stop", style=discord.ButtonStyle.secondary)
        stop_button.callback = lambda inter: self.action_callback(inter, 'stop')
        ssh_button = discord.ui.Button(label="🔑 SSH", style=discord.ButtonStyle.primary)
        ssh_button.callback = lambda inter: self.action_callback(inter, 'tmate')

        self.add_item(start_button)
        self.add_item(stop_button)
        self.add_item(ssh_button)

    async def select_vps(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(embed=create_error_embed("Access Denied", "This is not your VPS!"), ephemeral=True)
            return
        self.selected_index = int(self.select.values[0])
        new_embed = self.create_vps_embed(self.selected_index)
        self.clear_items()
        self.add_action_buttons()
        await interaction.response.edit_message(embed=new_embed, view=self)

    async def action_callback(self, interaction: discord.Interaction, action: str):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(embed=create_error_embed("Access Denied", "This is not your VPS!"), ephemeral=True)
            return

        if self.is_shared:
            vps = vps_data[self.owner_id][self.selected_index]
        else:
            vps = self.vps_list[self.selected_index]

        container_name = vps["container_name"]

        if action == 'reinstall':
            if self.is_shared or self.is_admin:
                await interaction.response.send_message(embed=create_error_embed("Access Denied", "Only the VPS owner can reinstall!"), ephemeral=True)
                return

            confirm_embed = create_warning_embed("Reinstall Warning",
                f"⚠️ **WARNING:** This will erase all data on VPS `{container_name}` and reinstall Ubuntu 22.04.\n\n"
                f"This action cannot be undone. Continue?")

            class ConfirmView(discord.ui.View):
                def __init__(self, parent_view, container_name, vps, owner_id, selected_index):
                    super().__init__(timeout=60)
                    self.parent_view = parent_view
                    self.container_name = container_name
                    self.vps = vps
                    self.owner_id = owner_id
                    self.selected_index = selected_index

                @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
                async def confirm(self, interaction: discord.Interaction, item: discord.ui.Button):
                    await interaction.response.defer(ephemeral=True)
                    try:
                        # Force delete the container first
                        await interaction.followup.send(embed=create_info_embed("Deleting Container", f"Forcefully removing container `{self.container_name}`..."), ephemeral=True)
                        await execute_lxc(f"lxc delete {self.container_name} --force")

                        # Recreate with original specifications
                        await interaction.followup.send(embed=create_info_embed("Recreating Container", f"Creating new container `{self.container_name}`..."), ephemeral=True)
                        original_ram = self.vps["ram"]
                        original_cpu = self.vps["cpu"]
                        ram_mb = int(original_ram.replace("GB", "")) * 1024

                        await execute_lxc(f"lxc launch ubuntu:22.04 {self.container_name} --config limits.memory={ram_mb}MB --config limits.cpu={original_cpu} -s btrpool")

                        self.vps["status"] = "running"
                        self.vps["created_at"] = datetime.now().isoformat()
                        save_data()
                        await interaction.followup.send(embed=create_success_embed("Reinstall Complete", f"VPS `{self.container_name}` has been successfully reinstalled!"), ephemeral=True)

                        if not self.parent_view.is_shared:
                            await interaction.message.edit(embed=self.parent_view.create_vps_embed(self.parent_view.selected_index), view=self.parent_view)

                    except Exception as e:
                        await interaction.followup.send(embed=create_error_embed("Reinstall Failed", f"Error: {str(e)}"), ephemeral=True)

                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def cancel(self, interaction: discord.Interaction, item: discord.ui.Button):
                    await interaction.response.edit_message(embed=self.parent_view.create_vps_embed(self.parent_view.selected_index), view=self.parent_view)

            await interaction.response.send_message(embed=confirm_embed, view=ConfirmView(self, container_name, vps, self.owner_id, self.selected_index), ephemeral=True)

        elif action == 'start':
            await interaction.response.defer(ephemeral=True)
            try:
                await execute_lxc(f"lxc start {container_name}")
                vps["status"] = "running"
                save_data()
                await interaction.followup.send(embed=create_success_embed("VPS Started", f"VPS `{container_name}` is now running!"), ephemeral=True)
                await interaction.message.edit(embed=self.create_vps_embed(self.selected_index), view=self)
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("Start Failed", str(e)), ephemeral=True)

        elif action == 'stop':
            await interaction.response.defer(ephemeral=True)
            try:
                await execute_lxc(f"lxc stop {container_name}", timeout=120)
                vps["status"] = "stopped"
                save_data()
                await interaction.followup.send(embed=create_success_embed("VPS Stopped", f"VPS `{container_name}` has been stopped!"), ephemeral=True)
                await interaction.message.edit(embed=self.create_vps_embed(self.selected_index), view=self)
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("Stop Failed", str(e)), ephemeral=True)

        elif action == 'tmate':
            await interaction.response.send_message(embed=create_info_embed("SSH Access", "Generating SSH connection..."), ephemeral=True)

            try:
                # Check if tmate exists
                check_proc = await asyncio.create_subprocess_exec(
                    "lxc", "exec", container_name, "--", "which", "tmate",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await check_proc.communicate()

                if check_proc.returncode != 0:
                    await interaction.followup.send(embed=create_info_embed("Installing SSH", "Installing tmate..."), ephemeral=True)
                    await execute_lxc(f"lxc exec {container_name} -- sudo apt-get update -y")
                    await execute_lxc(f"lxc exec {container_name} -- sudo apt-get install tmate -y")
                    await interaction.followup.send(embed=create_success_embed("Installed", "SSH service installed!"), ephemeral=True)

                # REMOVED: Kill existing tmate sessions - now allowing unlimited sessions
                # Users can launch unlimited tmate sessions

                # Start tmate with unique session name using timestamp
                session_name = f"session-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                await execute_lxc(f"lxc exec {container_name} -- tmate -S /tmp/{session_name}.sock new-session -d")
                await asyncio.sleep(3)

                # Get SSH link
                ssh_proc = await asyncio.create_subprocess_exec(
                    "lxc", "exec", container_name, "--", "tmate", "-S", f"/tmp/{session_name}.sock", "display", "-p", "#{tmate_ssh}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await ssh_proc.communicate()
                ssh_url = stdout.decode().strip() if stdout else None

                if ssh_url:
                    try:
                        ssh_embed = create_embed("🔑 SSH Access", f"SSH connection for VPS `{container_name}`:", 0x00ff88)
                        ssh_embed.add_field(name="Command", value=f"```{ssh_url}```", inline=False)
                        ssh_embed.add_field(name="⚠️ Security", value="This link is temporary. Do not share it.", inline=False)
                        ssh_embed.add_field(name="📝 Session", value=f"Session ID: {session_name}", inline=False)
                        await interaction.user.send(embed=ssh_embed)
                        await interaction.followup.send(embed=create_success_embed("SSH Sent", f"Check your DMs for SSH link! Session: {session_name}"), ephemeral=True)
                    except discord.Forbidden:
                        await interaction.followup.send(embed=create_error_embed("DM Failed", "Enable DMs to receive SSH link!"), ephemeral=True)
                else:
                    error_msg = stderr.decode().strip() if stderr else "Unknown error"
                    await interaction.followup.send(embed=create_error_embed("SSH Failed", error_msg), ephemeral=True)
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("SSH Error", str(e)), ephemeral=True)

@bot.command(name='manage')
async def manage_vps(ctx, user: discord.Member = None):
    """Manage your VPS or another user's VPS (Admin only)"""
    # Check if user is trying to manage someone else's VPS
    if user:
        # Only admins can manage other users' VPS
        if not (str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", [])):
            await ctx.send(embed=create_error_embed("Access Denied", "Only admins can manage other users' VPS."))
            return

        user_id = str(user.id)
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            await ctx.send(embed=create_error_embed("No VPS Found", f"{user.mention} doesn't have any VPS."))
            return

        # Create admin view for managing another user's VPS
        view = ManageView(str(ctx.author.id), vps_list, is_admin=True, owner_id=user_id)
        await ctx.send(embed=create_info_embed(f"Managing {user.name}'s VPS", f"Managing VPS for {user.mention}"), view=view)
    else:
        # User managing their own VPS
        user_id = str(ctx.author.id)
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            embed = create_embed("No VPS Found", "You don't have any VPS. Use `.buywc` to purchase one.", 0xff3366)
            embed.add_field(name="Quick Actions", value="• `.plans` - View plans\n• `.buywc <plan> <processor>` - Purchase VPS", inline=False)
            await ctx.send(embed=embed)
            return
        view = ManageView(user_id, vps_list)
        await ctx.send(embed=view.initial_embed, view=view)

@bot.command(name='list-all')
@is_admin()
async def list_all_vps(ctx):
    """List all VPS and user information (Admin only)"""
    embed = create_embed("All VPS Information", "Complete overview of all VPS deployments and user statistics", 0x1a1a1a)

    total_vps = 0
    total_users = len(vps_data)
    running_vps = 0
    stopped_vps = 0

    vps_info = []
    user_summary = []

    for user_id, vps_list in vps_data.items():
        try:
            user = await bot.fetch_user(int(user_id))
            user_vps_count = len(vps_list)
            user_running = sum(1 for vps in vps_list if vps.get('status') == 'running')
            user_stopped = user_vps_count - user_running

            total_vps += user_vps_count
            running_vps += user_running
            stopped_vps += user_stopped

            # User summary
            user_summary.append(f"**{user.name}** ({user.mention}) - {user_vps_count} VPS ({user_running} running)")

            # Individual VPS details
            for i, vps in enumerate(vps_list):
                status_emoji = "🟢" if vps.get('status') == 'running' else "🔴"
                vps_info.append(f"{status_emoji} **{user.name}** - VPS {i+1}: `{vps['container_name']}` - {vps.get('plan', 'Custom')} - {vps.get('status', 'unknown').upper()}")

        except discord.NotFound:
            vps_info.append(f"❓ Unknown User ({user_id}) - {len(vps_list)} VPS")

    # Add overview statistics
    embed.add_field(name="System Overview", value=f"**Total Users:** {total_users}\n**Total VPS:** {total_vps}\n**Running:** {running_vps}\n**Stopped:** {stopped_vps}", inline=False)

    # Add user summary
    if user_summary:
        embed.add_field(name="User Summary", value="\n".join(user_summary[:10]), inline=False)
        if len(user_summary) > 10:
            embed.add_field(name="Additional Users", value=f"... and {len(user_summary) - 10} more users", inline=False)

    # Add detailed VPS list (split if too many)
    if vps_info:
        chunk_size = 15
        for i in range(0, min(len(vps_info), 30), chunk_size):
            chunk = vps_info[i:i+chunk_size]
            embed.add_field(name=f"VPS Deployments ({i+1}-{min(i+chunk_size, len(vps_info))})", value="\n".join(chunk), inline=False)

    if len(vps_info) > 30:
        embed.add_field(name="Additional VPS", value=f"... and {len(vps_info) - 30} more VPS deployments", inline=False)

    await ctx.send(embed=embed)

@bot.command(name='manage-shared')
async def manage_shared_vps(ctx, owner: discord.Member, vps_number: int):
    """Manage a shared VPS"""
    owner_id = str(owner.id)
    user_id = str(ctx.author.id)
    if owner_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[owner_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number or owner doesn't have a VPS."))
        return
    vps = vps_data[owner_id][vps_number - 1]
    if user_id not in vps.get("shared_with", []):
        await ctx.send(embed=create_error_embed("Access Denied", "You do not have access to this VPS."))
        return
    view = ManageView(user_id, [vps], is_shared=True, owner_id=owner_id)
    await ctx.send(embed=view.initial_embed, view=view)

@bot.command(name='share-user')
async def share_user(ctx, shared_user: discord.Member, vps_number: int):
    """Share VPS access with another user"""
    user_id = str(ctx.author.id)
    shared_user_id = str(shared_user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number or you don't have a VPS."))
        return
    vps = vps_data[user_id][vps_number - 1]

    if "shared_with" not in vps:
        vps["shared_with"] = []

    if shared_user_id in vps["shared_with"]:
        await ctx.send(embed=create_error_embed("Already Shared", f"{shared_user.mention} already has access!"))
        return
    vps["shared_with"].append(shared_user_id)
    save_data()
    await ctx.send(embed=create_success_embed("VPS Shared", f"VPS #{vps_number} shared with {shared_user.mention}!"))
    try:
        await shared_user.send(embed=create_embed("VPS Access Granted", f"You have access to VPS #{vps_number} from {ctx.author.mention}. Use `.manage-shared {ctx.author.mention} {vps_number}`", 0x00ff88))
    except discord.Forbidden:
        await ctx.send(embed=create_info_embed("Notification Failed", f"Could not DM {shared_user.mention}"))

@bot.command(name='share-ruser')
async def revoke_share(ctx, shared_user: discord.Member, vps_number: int):
    """Revoke shared VPS access"""
    user_id = str(ctx.author.id)
    shared_user_id = str(shared_user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number or you don't have a VPS."))
        return
    vps = vps_data[user_id][vps_number - 1]

    if "shared_with" not in vps:
        vps["shared_with"] = []

    if shared_user_id not in vps["shared_with"]:
        await ctx.send(embed=create_error_embed("Not Shared", f"{shared_user.mention} doesn't have access!"))
        return
    vps["shared_with"].remove(shared_user_id)
    save_data()
    await ctx.send(embed=create_success_embed("Access Revoked", f"Access to VPS #{vps_number} revoked from {shared_user.mention}!"))
    try:
        await shared_user.send(embed=create_embed("VPS Access Revoked", f"Your access to VPS #{vps_number} by {ctx.author.mention} has been revoked.", 0xff3366))
    except discord.Forbidden:
        await ctx.send(embed=create_info_embed("Notification Failed", f"Could not DM {shared_user.mention}"))

@bot.command(name='buywc')
async def buy_with_credits(ctx, plan: str, processor: str = "Intel"):
    """Buy a VPS with credits"""
    user_id = str(ctx.author.id)
    prices = {
        "Starter": {"Intel": 42, "AMD": 83},
        "Basic": {"Intel": 96, "AMD": 164},
        "Standard": {"Intel": 192, "AMD": 320},
        "Pro": {"Intel": 220, "AMD": 340}
    }
    plans = {
        "Starter": {"ram": "4GB", "cpu": "1", "storage": "10GB"},
        "Basic": {"ram": "8GB", "cpu": "1", "storage": "10GB"},
        "Standard": {"ram": "12GB", "cpu": "2", "storage": "10GB"},
        "Pro": {"ram": "16GB", "cpu": "2", "storage": "10GB"}
    }

    if plan not in prices:
        await ctx.send(embed=create_error_embed("Invalid Plan", "Available: Starter, Basic, Standard, Pro"))
        return
    if processor not in ["Intel", "AMD"]:
        await ctx.send(embed=create_error_embed("Invalid Processor", "Choose: Intel or AMD"))
        return

    cost = prices[plan][processor]
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}

    if user_data[user_id]["credits"] < cost:
        await ctx.send(embed=create_error_embed("Insufficient Credits", f"You need {cost} credits but have {user_data[user_id]['credits']}"))
        return

    user_data[user_id]["credits"] -= cost
    if user_id not in vps_data:
        vps_data[user_id] = []
    vps_count = len(vps_data[user_id]) + 1
    container_name = f"vps-{user_id}-{vps_count}"
    ram_str = plans[plan]["ram"]
    cpu_str = plans[plan]["cpu"]
    ram_mb = int(ram_str.replace("GB", "")) * 1024

    await ctx.send(embed=create_info_embed("Processing Purchase", f"Deploying {plan} VPS..."))

    try:
        await execute_lxc(f"lxc launch ubuntu:22.04 {container_name} --config limits.memory={ram_mb}MB --config limits.cpu={cpu_str} -s btrpool")
        vps_info = {
            "plan": plan,
            "container_name": container_name,
            "ram": ram_str,
            "cpu": cpu_str,
            "storage": plans[plan]["storage"],
            "status": "running",
            "created_at": datetime.now().isoformat(),
            "processor": processor,
            "shared_with": []
        }
        vps_data[user_id].append(vps_info)
        save_data()

        # Get or create VPS role and assign to user
        if ctx.guild:
            vps_role = await get_or_create_vps_role(ctx.guild)
            if vps_role:
                try:
                    await ctx.author.add_roles(vps_role, reason="VPS purchase completed")
                except discord.Forbidden:
                    logger.warning(f"Failed to assign VPS role to {ctx.author.name}")

        embed = create_success_embed("VPS Purchased Successfully")
        embed.add_field(name="Plan", value=f"**{plan}** ({processor})", inline=True)
        embed.add_field(name="VPS ID", value=f"#{vps_count}", inline=True)
        embed.add_field(name="Container", value=f"`{container_name}`", inline=True)
        embed.add_field(name="Cost", value=f"{cost} credits", inline=True)
        embed.add_field(name="Resources", value=f"**RAM:** {ram_str}\n**CPU:** {cpu_str} Cores\n**Storage:** 10GB", inline=False)
        await ctx.send(embed=embed)

        # Send DM to user
        try:
            dm_embed = create_success_embed("VPS Purchased!", f"Your {plan} VPS has been successfully deployed!")
            dm_embed.add_field(name="VPS Details", value=f"**VPS ID:** #{vps_count}\n**Container:** `{container_name}`\n**Plan:** {plan} ({processor})\n**RAM:** {ram_str}\n**CPU:** {cpu_str} Cores\n**Storage:** 10GB", inline=False)
            dm_embed.add_field(name="Next Steps", value="Use `.manage` to control your VPS\nUse `.manage` → SSH to get access credentials", inline=False)
            await ctx.author.send(embed=dm_embed)
        except discord.Forbidden:
            await ctx.send(embed=create_info_embed("DM Failed", "Enable private messages to receive your VPS details."))

    except Exception as e:
        await ctx.send(embed=create_error_embed("Purchase Failed", f"Error: {str(e)}"))

@bot.command(name='buyc')
async def buy_credits(ctx):
    """Get payment information"""
    user = ctx.author
    embed = create_embed("💳 Purchase Credits", "Choose your payment method below:", 0x1a1a1a)

    payment_fields = [
        {"name": "🇮🇳 UPI", "value": "```\n make ticket\n```", "inline": False},
        {"name": "💰 PayPal", "value": "```\nexample@paypal.com\n```", "inline": False},
        {"name": "₿ Crypto", "value": "BTC, ETH, USDT accepted", "inline": False},
        {"name": "📋 Next Steps", "value": "1. Pay\n2. Contact admin with transaction ID\n3. Receive credits", "inline": False}
    ]

    for field in payment_fields:
        embed.add_field(**field)

    try:
        await user.send(embed=embed)
        await ctx.send(embed=create_success_embed("Information Sent", "Payment details sent to your DMs!"))
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("DM Failed", "Enable DMs to receive payment info!"))

@bot.command(name='delete-vps')
@is_admin()
async def delete_vps(ctx, user: discord.Member, vps_number: int, *, reason: str = "No reason"):
    """Delete a user's VPS (Admin only)"""
    user_id = str(user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number or user doesn't have a VPS."))
        return
    vps = vps_data[user_id][vps_number - 1]
    container_name = vps["container_name"]

    await ctx.send(embed=create_info_embed("Deleting VPS", f"Removing VPS #{vps_number}..."))

    try:
        await execute_lxc(f"lxc delete {container_name} --force")
        del vps_data[user_id][vps_number - 1]
        if not vps_data[user_id]:
            del vps_data[user_id]
            # Remove VPS role if user has no more VPS
            if ctx.guild:
                vps_role = await get_or_create_vps_role(ctx.guild)
                if vps_role and vps_role in user.roles:
                    try:
                        await user.remove_roles(vps_role, reason="No VPS ownership")
                    except discord.Forbidden:
                        logger.warning(f"Failed to remove VPS role from {user.name}")
        save_data()

        embed = create_success_embed("VPS Deleted Successfully")
        embed.add_field(name="Owner", value=user.mention, inline=True)
        embed.add_field(name="VPS ID", value=f"#{vps_number}", inline=True)
        embed.add_field(name="Container", value=f"`{container_name}`", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(embed=create_error_embed("Deletion Failed", f"Error: {str(e)}"))

@bot.command(name='plans')
async def show_plans(ctx):
    """Show available VPS plans"""
    embed = create_embed("💎 VPS Plans - CurlNode ", "Choose your perfect VPS plan:", 0x1a1a1a)

    plan_fields = [
        {"name": "🚀 Starter", "value": "**RAM:** 4 GB\n**CPU:** 1 Core\n**Storage:** 10 GB\n━━━━━━━━━━━━━━\n**Intel:** ₹42 | **AMD:** ₹83", "inline": False},
        {"name": "⚡ Basic", "value": "**RAM:** 8 GB\n**CPU:** 1 Core\n**Storage:** 10 GB\n━━━━━━━━━━━━━━\n**Intel:** ₹96 | **AMD:** ₹164", "inline": False},
        {"name": "🔥 Standard", "value": "**RAM:** 12 GB\n**CPU:** 2 Cores\n**Storage:** 10 GB\n━━━━━━━━━━━━━━\n**Intel:** ₹192 | **AMD:** ₹320", "inline": False},
        {"name": "💎 Pro", "value": "**RAM:** 16 GB\n**CPU:** 2 Cores\n**Storage:** 10 GB\n━━━━━━━━━━━━━━\n**Intel:** ₹220 | **AMD:** ₹340", "inline": False}
    ]

    for field in plan_fields:
        embed.add_field(**field)

    embed.add_field(name="🛒 Purchase", value="Use `.buywc <plan> <processor>`\nExample: `.buywc Starter Intel`", inline=False)
    embed.set_footer(text="All plans include Ubuntu 22.04 • Full root access")
    await ctx.send(embed=embed)

@bot.command(name='adminc')
@is_admin()
async def admin_credits(ctx, user: discord.Member, amount: int):
    """Add credits to user (Admin only)"""
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Invalid Amount", "Amount must be a positive integer."))
        return
    user_id = str(user.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    user_data[user_id]["credits"] += amount
    save_data()
    await ctx.send(embed=create_success_embed("Credits Added", f"Added {amount} credits to {user.mention}\nNew balance: {user_data[user_id]['credits']}"))

@bot.command(name='adminrc')
@is_admin()
async def admin_remove_credits(ctx, user: discord.Member, amount_or_all: str):
    """Remove credits from user (Admin only)"""
    user_id = str(user.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    current_credits = user_data[user_id]["credits"]

    if amount_or_all.lower() == "all":
        removed = current_credits
        user_data[user_id]["credits"] = 0
        action = "All credits removed"
    else:
        try:
            amount = int(amount_or_all)
            if amount <= 0:
                await ctx.send(embed=create_error_embed("Invalid Amount", "Use positive number or 'all'"))
                return
            if amount > current_credits:
                amount = current_credits
            user_data[user_id]["credits"] -= amount
            removed = amount
            action = f"{amount} credits removed"
        except ValueError:
            await ctx.send(embed=create_error_embed("Invalid Amount", "Enter number or 'all'"))
            return

    save_data()
    await ctx.send(embed=create_success_embed("Credits Removed", f"{action} from {user.mention}\nRemaining: {user_data[user_id]['credits']}"))

@bot.command(name='admin-add')
@is_main_admin()
async def admin_add(ctx, user: discord.Member):
    """Add admin (Main admin only)"""
    user_id = str(user.id)
    if user_id == str(MAIN_ADMIN_ID):
        await ctx.send(embed=create_error_embed("Already Admin", "This user is already the main admin!"))
        return

    if user_id in admin_data.get("admins", []):
        await ctx.send(embed=create_error_embed("Already Admin", f"{user.mention} is already an admin!"))
        return

    if "admins" not in admin_data:
        admin_data["admins"] = []

    admin_data["admins"].append(user_id)
    save_data()
    await ctx.send(embed=create_success_embed("Admin Added", f"{user.mention} is now an admin!"))
    try:
        await user.send(embed=create_embed("🎉 Admin Role Granted", f"You are now an admin by {ctx.author.mention}", 0x00ff88))
    except discord.Forbidden:
        await ctx.send(embed=create_info_embed("Notification Failed", f"Could not DM {user.mention}"))

@bot.command(name='admin-remove')
@is_main_admin()
async def admin_remove(ctx, user: discord.Member):
    """Remove admin (Main admin only)"""
    user_id = str(user.id)
    if user_id == str(MAIN_ADMIN_ID):
        await ctx.send(embed=create_error_embed("Cannot Remove", "You cannot remove the main admin!"))
        return

    if user_id not in admin_data.get("admins", []):
        await ctx.send(embed=create_error_embed("Not Admin", f"{user.mention} is not an admin!"))
        return

    admin_data["admins"].remove(user_id)
    save_data()
    await ctx.send(embed=create_success_embed("Admin Removed", f"{user.mention} is no longer an admin!"))
    try:
        await user.send(embed=create_embed("⚠️ Admin Role Revoked", f"Your admin role was removed by {ctx.author.mention}", 0xff3366))
    except discord.Forbidden:
        await ctx.send(embed=create_info_embed("Notification Failed", f"Could not DM {user.mention}"))

@bot.command(name='admin-list')
@is_main_admin()
async def admin_list(ctx):
    """List all admins (Main admin only)"""
    admins = admin_data.get("admins", [])
    main_admin = await bot.fetch_user(MAIN_ADMIN_ID)

    embed = create_embed("👑 Admin Team", "Current administrators:", 0x1a1a1a)
    embed.add_field(name="🔰 Main Admin", value=f"{main_admin.mention} (ID: {MAIN_ADMIN_ID})", inline=False)

    if admins:
        admin_list = []
        for admin_id in admins:
            try:
                admin_user = await bot.fetch_user(int(admin_id))
                admin_list.append(f"• {admin_user.mention} (ID: {admin_id})")
            except:
                admin_list.append(f"• Unknown User (ID: {admin_id})")

        embed.add_field(name="🛡️ Admins", value="\n".join(admin_list), inline=False)
    else:
        embed.add_field(name="🛡️ Admins", value="No additional admins", inline=False)

    await ctx.send(embed=embed)

@bot.command(name='credits')
async def show_credits(ctx):
    """Show credit balance"""
    user_id = str(ctx.author.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
        save_data()

    embed = create_embed("💰 Credit Balance", f"Your account balance:", 0x1a1a1a)
    embed.add_field(name="Available Credits", value=f"**{user_data[user_id]['credits']}** credits", inline=False)
    embed.add_field(name="Need More?", value="Use `.buyc` to view payment methods", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='userinfo')
@is_admin()
async def user_info(ctx, user: discord.Member):
    """Get detailed information about a user (Admin only)"""
    user_id = str(user.id)

    # Get user's VPS
    vps_list = vps_data.get(user_id, [])

    # Get user's credits
    credits = user_data.get(user_id, {}).get("credits", 0)

    embed = create_embed(f"User Information - {user.name}", f"Detailed information for {user.mention}", 0x1a1a1a)

    # User details
    embed.add_field(name="👤 User Details", value=f"**Name:** {user.name}\n**ID:** {user.id}\n**Joined:** {user.joined_at.strftime('%Y-%m-%d %H:%M:%S')}", inline=False)

    # Credits info
    embed.add_field(name="💰 Credits", value=f"**Balance:** {credits} credits", inline=False)

    # VPS info
    if vps_list:
        vps_info = []
        total_ram = 0
        total_cpu = 0
        running_count = 0

        for i, vps in enumerate(vps_list):
            status_emoji = "🟢" if vps.get('status') == 'running' else "🔴"
            vps_info.append(f"{status_emoji} VPS {i+1}: `{vps['container_name']}` - {vps.get('status', 'unknown').upper()}")

            # Calculate totals
            ram_gb = int(vps['ram'].replace('GB', ''))
            total_ram += ram_gb
            total_cpu += int(vps['cpu'])
            if vps.get('status') == 'running':
                running_count += 1

        embed.add_field(name="🖥️ VPS Information", value=f"**Total VPS:** {len(vps_list)}\n**Running:** {running_count}\n**Total RAM:** {total_ram}GB\n**Total CPU:** {total_cpu} cores", inline=False)
        embed.add_field(name="📋 VPS List", value="\n".join(vps_info), inline=False)
    else:
        embed.add_field(name="🖥️ VPS Information", value="**No VPS owned**", inline=False)

    # Check if user is admin
    is_admin_user = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", [])
    embed.add_field(name="🛡️ Admin Status", value=f"**Admin:** {'Yes' if is_admin_user else 'No'}", inline=False)

    await ctx.send(embed=embed)

@bot.command(name='serverstats')
@is_admin()
async def server_stats(ctx):
    """Show server statistics (Admin only)"""
    total_users = len(user_data)
    total_vps = sum(len(vps_list) for vps_list in vps_data.values())
    total_credits = sum(user.get('credits', 0) for user in user_data.values())

    # Calculate resources
    total_ram = 0
    total_cpu = 0
    running_vps = 0

    for vps_list in vps_data.values():
        for vps in vps_list:
            ram_gb = int(vps['ram'].replace('GB', ''))
            total_ram += ram_gb
            total_cpu += int(vps['cpu'])
            if vps.get('status') == 'running':
                running_vps += 1

    embed = create_embed("📊 Server Statistics", "Current server overview", 0x1a1a1a)
    embed.add_field(name="👥 Users", value=f"**Total Users:** {total_users}\n**Total Admins:** {len(admin_data.get('admins', [])) + 1}", inline=False)
    embed.add_field(name="🖥️ VPS", value=f"**Total VPS:** {total_vps}\n**Running:** {running_vps}\n**Stopped:** {total_vps - running_vps}", inline=False)
    embed.add_field(name="💰 Economy", value=f"**Total Credits:** {total_credits}", inline=False)
    embed.add_field(name="📈 Resources", value=f"**Total RAM:** {total_ram}GB\n**Total CPU:** {total_cpu} cores", inline=False)

    await ctx.send(embed=embed)

@bot.command(name='vpsinfo')
@is_admin()
async def vps_info(ctx, container_name: str = None):
    """Get detailed VPS information (Admin only)"""
    if not container_name:
        # Show all VPS
        all_vps = []
        for user_id, vps_list in vps_data.items():
            try:
                user = await bot.fetch_user(int(user_id))
                for i, vps in enumerate(vps_list):
                    all_vps.append(f"**{user.name}** - VPS {i+1}: `{vps['container_name']}` - {vps.get('status', 'unknown').upper()}")
            except:
                pass

        embed = create_embed("🖥️ All VPS", f"Total VPS: {len(all_vps)}", 0x1a1a1a)

        # Split into chunks if too many
        chunk_size = 20
        for i in range(0, len(all_vps), chunk_size):
            chunk = all_vps[i:i+chunk_size]
            embed.add_field(name=f"VPS List ({i+1}-{i+chunk_size})", value="\n".join(chunk), inline=False)

        await ctx.send(embed=embed)
    else:
        # Show specific VPS info
        found_vps = None
        found_user = None

        for user_id, vps_list in vps_data.items():
            for vps in vps_list:
                if vps['container_name'] == container_name:
                    found_vps = vps
                    found_user = await bot.fetch_user(int(user_id))
                    break
            if found_vps:
                break

        if not found_vps:
            await ctx.send(embed=create_error_embed("VPS Not Found", f"No VPS found with container name: `{container_name}`"))
            return

        embed = create_embed(f"🖥️ VPS Information - {container_name}", f"Details for VPS owned by {found_user.mention}", 0x1a1a1a)
        embed.add_field(name="👤 Owner", value=f"**Name:** {found_user.name}\n**ID:** {found_user.id}", inline=False)
        embed.add_field(name="📊 Specifications", value=f"**RAM:** {found_vps['ram']}\n**CPU:** {found_vps['cpu']} Cores\n**Storage:** {found_vps['storage']}", inline=False)
        embed.add_field(name="📈 Status", value=f"**Current:** {found_vps.get('status', 'unknown').upper()}\n**Created:** {found_vps.get('created_at', 'Unknown')}", inline=False)

        if 'plan' in found_vps:
            embed.add_field(name="💎 Plan", value=f"**Plan:** {found_vps['plan']}\n**Processor:** {found_vps.get('processor', 'Unknown')}", inline=False)

        if found_vps.get('shared_with'):
            shared_users = []
            for shared_id in found_vps['shared_with']:
                try:
                    shared_user = await bot.fetch_user(int(shared_id))
                    shared_users.append(f"• {shared_user.mention}")
                except:
                    shared_users.append(f"• Unknown User ({shared_id})")
            embed.add_field(name="🔗 Shared With", value="\n".join(shared_users), inline=False)

        await ctx.send(embed=embed)

@bot.command(name='restart-vps')
@is_admin()
async def restart_vps(ctx, container_name: str):
    """Restart a VPS (Admin only)"""
    await ctx.send(embed=create_info_embed("Restarting VPS", f"Restarting VPS `{container_name}`..."))

    try:
        await execute_lxc(f"lxc restart {container_name}")

        # Update status in database
        for user_id, vps_list in vps_data.items():
            for vps in vps_list:
                if vps['container_name'] == container_name:
                    vps['status'] = 'running'
                    save_data()
                    break

        await ctx.send(embed=create_success_embed("VPS Restarted", f"VPS `{container_name}` has been restarted successfully!"))

    except Exception as e:
        await ctx.send(embed=create_error_embed("Restart Failed", f"Error: {str(e)}"))

@bot.command(name='backup-vps')
@is_admin()
async def backup_vps(ctx, container_name: str):
    """Create a snapshot of a VPS (Admin only)"""
    snapshot_name = f"{container_name}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    await ctx.send(embed=create_info_embed("Creating Backup", f"Creating snapshot of `{container_name}`..."))

    try:
        await execute_lxc(f"lxc snapshot {container_name} {snapshot_name}")
        await ctx.send(embed=create_success_embed("Backup Created", f"Snapshot `{snapshot_name}` created successfully!"))

    except Exception as e:
        await ctx.send(embed=create_error_embed("Backup Failed", f"Error: {str(e)}"))

@bot.command(name='restore-vps')
@is_admin()
async def restore_vps(ctx, container_name: str, snapshot_name: str):
    """Restore a VPS from snapshot (Admin only)"""
    await ctx.send(embed=create_info_embed("Restoring VPS", f"Restoring `{container_name}` from snapshot `{snapshot_name}`..."))

    try:
        await execute_lxc(f"lxc restore {container_name} {snapshot_name}")
        await ctx.send(embed=create_success_embed("VPS Restored", f"VPS `{container_name}` has been restored from snapshot!"))

    except Exception as e:
        await ctx.send(embed=create_error_embed("Restore Failed", f"Error: {str(e)}"))

@bot.command(name='list-snapshots')
@is_admin()
async def list_snapshots(ctx, container_name: str):
    """List all snapshots for a VPS (Admin only)"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lxc", "info", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            await ctx.send(embed=create_error_embed("Error", f"Failed to get VPS info: {stderr.decode()}"))
            return

        info = stdout.decode()
        snapshots = []

        for line in info.split('\n'):
            if 'snapshot:' in line:
                snapshot_name = line.split(':')[1].strip()
                snapshots.append(snapshot_name)

        if snapshots:
            embed = create_embed(f"📸 Snapshots for {container_name}", f"Found {len(snapshots)} snapshots", 0x1a1a1a)
            embed.add_field(name="Snapshots", value="\n".join([f"• {snap}" for snap in snapshots]), inline=False)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=create_info_embed("No Snapshots", f"No snapshots found for `{container_name}`"))

    except Exception as e:
        await ctx.send(embed=create_error_embed("Error", f"Error: {str(e)}"))

@bot.command(name='exec')
@is_admin()
async def execute_command(ctx, container_name: str, *, command: str):
    """Execute a command inside a VPS (Admin only)"""
    await ctx.send(embed=create_info_embed("Executing Command", f"Running command in `{container_name}`..."))

    try:
        proc = await asyncio.create_subprocess_exec(
            "lxc", "exec", container_name, "--", "bash", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        output = stdout.decode() if stdout else "No output"
        error = stderr.decode() if stderr else ""

        embed = create_embed(f"Command Output - {container_name}", f"Command: `{command}`", 0x1a1a1a)

        if output.strip():
            # Split output if too long
            if len(output) > 1000:
                output = output[:1000] + "\n... (truncated)"
            embed.add_field(name="📤 Output", value=f"```\n{output}\n```", inline=False)

        if error.strip():
            if len(error) > 1000:
                error = error[:1000] + "\n... (truncated)"
            embed.add_field(name="⚠️ Error", value=f"```\n{error}\n```", inline=False)

        embed.add_field(name="🔄 Exit Code", value=f"**{proc.returncode}**", inline=False)

        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(embed=create_error_embed("Execution Failed", f"Error: {str(e)}"))

@bot.command(name='stop-vps-all')
@is_admin()
async def stop_all_vps(ctx):
    """Stop all VPS using lxc stop --all --force (Admin only)"""
    await ctx.send(embed=create_warning_embed("Stopping All VPS", "⚠️ **WARNING:** This will stop ALL running VPS on the server.\n\nThis action cannot be undone. Continue?"))

    class ConfirmView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="Stop All VPS", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, item: discord.ui.Button):
            await interaction.response.defer()

            try:
                # Execute the lxc stop --all --force command
                proc = await asyncio.create_subprocess_exec(
                    "lxc", "stop", "--all", "--force",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode == 0:
                    # Update all VPS status in database to stopped
                    stopped_count = 0
                    for user_id, vps_list in vps_data.items():
                        for vps in vps_list:
                            if vps.get('status') == 'running':
                                vps['status'] = 'stopped'
                                stopped_count += 1

                    save_data()

                    embed = create_success_embed("All VPS Stopped", f"Successfully stopped {stopped_count} VPS using `lxc stop --all --force`")
                    embed.add_field(name="Command Output", value=f"```\n{stdout.decode() if stdout else 'No output'}\n```", inline=False)
                    await interaction.followup.send(embed=embed)
                else:
                    error_msg = stderr.decode() if stderr else "Unknown error"
                    embed = create_error_embed("Stop Failed", f"Failed to stop VPS: {error_msg}")
                    await interaction.followup.send(embed=embed)

            except Exception as e:
                embed = create_error_embed("Error", f"Error stopping VPS: {str(e)}")
                await interaction.followup.send(embed=embed)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, item: discord.ui.Button):
            await interaction.response.edit_message(embed=create_info_embed("Operation Cancelled", "The stop all VPS operation has been cancelled."))

    await ctx.send(view=ConfirmView())

@bot.command(name='cpu-monitor')
@is_admin()
async def cpu_monitor_control(ctx, action: str = "status"):
    """Control CPU monitoring system (Admin only)"""
    global cpu_monitor_active

    if action.lower() == "status":
        status = "Active" if cpu_monitor_active else "Inactive"
        embed = create_embed("CPU Monitor Status", f"CPU monitoring is currently **{status}**", 0x00ccff if cpu_monitor_active else 0xffaa00)
        embed.add_field(name="Threshold", value=f"{CPU_THRESHOLD}% CPU usage", inline=True)
        embed.add_field(name="Check Interval", value=f"{CHECK_INTERVAL} seconds", inline=True)
        await ctx.send(embed=embed)
    elif action.lower() == "enable":
        cpu_monitor_active = True
        await ctx.send(embed=create_success_embed("CPU Monitor Enabled", "CPU monitoring has been enabled."))
    elif action.lower() == "disable":
        cpu_monitor_active = False
        await ctx.send(embed=create_warning_embed("CPU Monitor Disabled", "CPU monitoring has been disabled."))
    else:
        await ctx.send(embed=create_error_embed("Invalid Action", "Use: `.cpu-monitor <status|enable|disable>`"))

@bot.command(name='help')
async def show_help(ctx):
    """Show help information"""
    user_id = str(ctx.author.id)
    is_user_admin = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", [])
    is_user_main_admin = user_id == str(MAIN_ADMIN_ID)

    embed = create_embed("📚 Command Help", "CurlNode  VPS Manager Commands:", 0x1a1a1a)

    user_commands = [
        (".plans", "View available VPS plans"),
        (".buyc", "Get payment information"),
        (".buywc <plan> <processor>", "Purchase VPS with credits"),
        (".credits", "Check your credit balance"),
        (".manage [@user]", "Manage your VPS or another user's VPS (Admin only)"),
        (".share-user @user <vps_number>", "Share VPS access"),
        (".share-ruser @user <vps_number>", "Revoke VPS access"),
        (".manage-shared @owner <vps_number>", "Manage shared VPS")
    ]

    user_commands_text = "\n".join([f"**{cmd}** - {desc}" for cmd, desc in user_commands])
    embed.add_field(name="👤 User Commands", value=user_commands_text, inline=False)

    if is_user_admin:
        admin_commands = [
            (".create @user <ram> <cpu>", "Create custom VPS"),
            (".delete-vps @user <vps_number> <reason>", "Delete user's VPS"),
            (".adminc @user <amount>", "Add credits"),
            (".adminrc @user <amount/all>", "Remove credits"),
            (".userinfo @user", "Get detailed user information"),
            (".serverstats", "Show server statistics"),
            (".vpsinfo [container]", "Get VPS information"),
            (".list-all", "View all VPS and user information"),
            (".restart-vps <container>", "Restart a VPS"),
            (".backup-vps <container>", "Create VPS snapshot"),
            (".restore-vps <container> <snapshot>", "Restore from snapshot"),
            (".list-snapshots <container>", "List VPS snapshots"),
            (".exec <container> <command>", "Execute command in VPS"),
            (".stop-vps-all", "Stop all VPS with lxc stop --all --force"),
            (".cpu-monitor <status|enable|disable>", "Control CPU monitoring system")
        ]

        admin_commands_text = "\n".join([f"**{cmd}** - {desc}" for cmd, desc in admin_commands])
        embed.add_field(name="🛡️ Admin Commands", value=admin_commands_text, inline=False)

    if is_user_main_admin:
        main_admin_commands = [
            (".admin-add @user", "Promote to admin"),
            (".admin-remove @user", "Remove admin"),
            (".admin-list", "View all admins")
        ]

        main_admin_commands_text = "\n".join([f"**{cmd}** - {desc}" for cmd, desc in main_admin_commands])
        embed.add_field(name="👑 Main Admin Commands", value=main_admin_commands_text, inline=False)

    embed.set_footer(text="CurlNode VPS Manager • No auto-shutdown • Clean performance")
    await ctx.send(embed=embed)

# Command aliases for typos
@bot.command(name='mangage')
async def manage_typo(ctx):
    """Handle typo in manage command"""
    await ctx.send(embed=create_info_embed("Command Correction", "Did you mean `.manage`? Use the correct command."))

@bot.command(name='stats')
async def stats_alias(ctx):
    """Alias for serverstats command"""
    if str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", []):
        await server_stats(ctx)
    else:
        await ctx.send(embed=create_error_embed("Access Denied", "This command requires admin privileges."))

@bot.command(name='info')
async def info_alias(ctx):
    """Alias for userinfo command"""
    if str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", []):
        await ctx.send(embed=create_error_embed("Usage", "Please specify a user: `.info @user`"))
    else:
        await ctx.send(embed=create_error_embed("Access Denied", "This command requires admin privileges."))

# Run the bot with your token
if __name__ == "__main__":
    token = "duscord-bit-tien"
    bot.run('to')

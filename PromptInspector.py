"""Prompt Inspector (PI-Chan)"""
import io
from collections import OrderedDict
from pathlib import Path
import asyncio
import gzip
import json
import toml
import gradio_client
from discord import Intents, Embed, ButtonStyle, Message, Attachment, File, RawReactionActionEvent, ApplicationContext
from discord.ext import commands
from discord.ui import View, button
from PIL import Image

if not Path('config.toml').exists():
    #create a clone of the base
    base_cfg = Path('config.base.toml').read_text()
    Path('config.toml').write_text(base_cfg)

CONFIG = toml.load('config.toml')
monitored: list = CONFIG.get('MONITORED_CHANNEL_IDS', [])
SCAN_LIMIT_BYTES = CONFIG.get('SCAN_LIMIT_BYTES', 40 * 1024**2)  # Default 40 MB
GRADCL = gradio_client.Client(CONFIG.get('GRADIO_BACKEND', "https://yoinked-da-nsfw-checker.hf.space/"))
intents = Intents.default() | Intents.message_content | Intents.members
client = commands.Bot(intents=intents)

def comfyui_get_data(dat):
    """try and extract the prompt/loras/checkpoints in comfy metadata"""
    try:
        aa = []
        dat = json.loads(dat)
        for _, value in dat.items():
            if value['class_type'] == "CLIPTextEncode":
                aa.append({"val": value['inputs']['text'],
                        "type": "prompt"})
            elif value['class_type'] == "CheckpointLoaderSimple":
                aa.append({"val": value['inputs']['ckpt_name'],
                        "type": "model"})
            elif value['class_type'] == "LoraLoader":
                aa.append({"val": value['inputs']['lora_name'],
                        "type": "lora"})
        return aa
    except ValueError as e:
        print(e)
        return []

def get_params_from_string(param_str):
    """Get parameters from an old a1111 metadata post"""
    output_dict = {}
    parts = param_str.split('Steps: ')
    prompts = parts[0]
    params = 'Steps: ' + parts[1]
    if 'Negative prompt: ' in prompts:
        output_dict['Prompt'] = prompts.split('Negative prompt: ')[0]
        output_dict['Negative Prompt'] = prompts.split('Negative prompt: ')[1]
        if len(output_dict['Negative Prompt']) > 1024:
            output_dict['Negative Prompt'] = output_dict['Negative Prompt'][:1020] + '...'
    else:
        output_dict['Prompt'] = prompts
    if len(output_dict['Prompt']) > 1024:
        output_dict['Prompt'] = output_dict['Prompt'][:1020] + '...'
    params = params.split(', ')
    for param in params:
        try:
            key, value = param.split(': ')
            output_dict[key] = value
        except ValueError:
            pass
    return output_dict


def get_embed(embed_dict, context: Message):
    """Create embed from a dictionary"""
    embed = Embed(color=context.author.color)
    i = 0
    for key, value in embed_dict.items():
        if key.strip() == "" or value.strip() == "":
            continue
        i += 1
        if i >= 25:
            continue
        embed.add_field(name=key[:255], value=value[:1023], inline='Prompt' not in key)
    embed.set_footer(text=f'Posted by {context.author} - nya~', icon_url=context.author.display_avatar)
    return embed


def read_info_from_image_stealth(image: Image.Image):
    """Try and read stealth PNGInfo"""
    width, height = image.size
    pixels = image.load()

    has_alpha = True if image.mode == "RGBA" else False
    mode = None
    compressed = False
    binary_data = ""
    buffer_a = ""
    buffer_rgb = ""
    index_a = 0
    index_rgb = 0
    sig_confirmed = False
    confirming_signature = True
    reading_param_len = False
    reading_param = False
    read_end = False
    for x in range(width):
        for y in range(height):
            if has_alpha:
                r, g, b, a = pixels[x, y]
                buffer_a += str(a & 1)
                index_a += 1
            else:
                r, g, b = pixels[x, y]
            buffer_rgb += str(r & 1)
            buffer_rgb += str(g & 1)
            buffer_rgb += str(b & 1)
            index_rgb += 3
            if confirming_signature:
                if index_a == len("stealth_pnginfo") * 8:
                    decoded_sig = bytearray(
                        int(buffer_a[i : i + 8], 2) for i in range(0, len(buffer_a), 8)
                    ).decode("utf-8", errors="ignore")
                    if decoded_sig in {"stealth_pnginfo", "stealth_pngcomp"}:
                        confirming_signature = False
                        sig_confirmed = True
                        reading_param_len = True
                        mode = "alpha"
                        if decoded_sig == "stealth_pngcomp":
                            compressed = True
                        buffer_a = ""
                        index_a = 0
                    else:
                        read_end = True
                        break
                elif index_rgb == len("stealth_pnginfo") * 8:
                    decoded_sig = bytearray(
                        int(buffer_rgb[i : i + 8], 2) for i in range(0, len(buffer_rgb), 8)
                    ).decode("utf-8", errors="ignore")
                    if decoded_sig in {"stealth_rgbinfo", "stealth_rgbcomp"}:
                        confirming_signature = False
                        sig_confirmed = True
                        reading_param_len = True
                        mode = "rgb"
                        if decoded_sig == "stealth_rgbcomp":
                            compressed = True
                        buffer_rgb = ""
                        index_rgb = 0
            elif reading_param_len:
                if mode == "alpha":
                    if index_a == 32:
                        param_len = int(buffer_a, 2)
                        reading_param_len = False
                        reading_param = True
                        buffer_a = ""
                        index_a = 0
                else:
                    if index_rgb == 33:
                        pop = buffer_rgb[-1]
                        buffer_rgb = buffer_rgb[:-1]
                        param_len = int(buffer_rgb, 2)
                        reading_param_len = False
                        reading_param = True
                        buffer_rgb = pop
                        index_rgb = 1
            elif reading_param:
                if mode == "alpha":
                    if index_a == param_len:
                        binary_data = buffer_a
                        read_end = True
                        break
                else:
                    if index_rgb >= param_len:
                        diff = param_len - index_rgb
                        if diff < 0:
                            buffer_rgb = buffer_rgb[:diff]
                        binary_data = buffer_rgb
                        read_end = True
                        break
            else:
                # impossible
                read_end = True
                break
        if read_end:
            break
    if sig_confirmed and binary_data != "":
        # Convert binary string to UTF-8 encoded text
        byte_data = bytearray(int(binary_data[i : i + 8], 2) for i in range(0, len(binary_data), 8))
        try:
            if compressed:
                decoded_data = gzip.decompress(bytes(byte_data)).decode("utf-8")
            else:
                decoded_data = byte_data.decode("utf-8", errors="ignore")
            return decoded_data
        except Exception as e:
            print(e)
            pass
    return None


@client.slash_command()
async def privacy(ctx):
    """
    Returns our privacy policy.
    """
    base = Embed(title="Privacy Policy", color=ctx.author.color)
    base.add_field(name="What we collect", value="Other than simple data from your user (mainly username, role color) not much else other than when an image is sent in a **monitored channel**, the bot downloads it to its RAM and processes it.\n***We do not store any of your data/images.***")
    base.add_field(name="What we use/store", value="Whenever the bot has an error decoding an image, it will print out the error and data to the console. The data consists of the raw bytes in the image metadata. Whenever a mod/admin toggles a channel on/off, the bot will save the ID to storage in case of it crashing. Other than that, that is all we use/store.")
    base.add_field(name="What we share", value="***We do not share any of your data/images.*** There's no use for them lol.")
    base.add_field(name="Open Source? Where?!", value="Yes, its [here](https://github.com/yoinked-h/PI-Chan). We are licensed under the [MIT License](https://github.com/yoinked-h/PI-Chan/blob/main/LICENSE). \nThe code is based off salt's base and NoCrypt's fork. ")
    base.set_footer(text=f"Maintained by <@444257402007846942>, this channel is {'not' if not ctx.channel_id in monitored else ''} monitored", icon_url=ctx.author.display_avatar)
    base.set_image(url="https://cdn.discordapp.com/avatars/1159983729591210004/8666dba0c893163fcf0e01629e85f6e8?size=1024")
    await ctx.respond(embed=base, ephemeral=True)
@client.slash_command()
@commands.has_permissions(manage_messages=True)
async def toggle_channel(ctx: ApplicationContext, channel_id):
    """
    Adds/Removes a channel to the list of monitored channels for this bot.
    channel_id: The ID of the channel to add. (defaults to current channel)
    
    Permissions:
    - Manage Messages
    """
    #perms
    if not ctx.author.guild_permissions.manage_messages:
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return
    try:
        if channel_id:
            channel_id = int(channel_id)
        else:
            channel_id = ctx.channel_id
        if channel_id in monitored:
            monitored.remove(channel_id)
            await ctx.respond(f"Removed {channel_id} from the list of monitored channels.", ephemeral=True)
        else:
            monitored.append(channel_id)
            await ctx.respond(f"Added {channel_id} to the list of monitored channels.", ephemeral=True)
        #update the config
        cfg = toml.load('config.toml')
        cfg['MONITORED_CHANNEL_IDS'] = monitored
        toml.dump(cfg, open('config.toml', 'w', encoding='utf-8'))
    except ValueError:
        await ctx.respond("Invalid channel ID.", ephemeral=True)
        return
    except Exception as e:
        print(f"{type(e).__name__}: {e}")
        await ctx.respond("Internal bot error, please DM yoinked.", ephemeral=True)
        
    

@client.event
async def on_ready():
    """Prints how many channels are monitored when ready"""
    print(f"Logged in as {client.user} and ready to monitor {len(monitored)} channels!")


@client.event
async def on_message(message: Message):
    """Add a magnifying glass if a post has metadata"""
    if message.channel.id in monitored and message.attachments:
        attachments = [a for a in message.attachments if a.filename.lower().endswith(".png") and a.size < SCAN_LIMIT_BYTES]
        for i, attachment in enumerate(attachments): # download one at a time as usually the first image is already ai-generated
            metadata = OrderedDict()
            await read_attachment_metadata(i, attachment, metadata)
            if metadata:
                await message.add_reaction(CONFIG.get("METADATA", "🔎"))
                return


class MyView(View):
    def __init__(self):
        super().__init__(timeout=3600, disable_on_timeout=True)
        self.metadata = None

    @button(label='Full Parameters', style=ButtonStyle.green)
    async def details(self, fullmta_button, interaction):
        fullmta_button.disabled = True
        await interaction.response.edit_message(view=self)
        if len(self.metadata) > 1980:
            with io.StringIO() as f:
                indented = json.dumps(json.loads(self.metadata), sort_keys=True, indent=2)
                f.write(indented)
                f.seek(0)
                await interaction.followup.send(file=File(f, "parameters.json"))
        else:
            await interaction.followup.send(f"```json\n{self.metadata}```")


async def read_attachment_metadata(i: int, attachment: Attachment, metadata: OrderedDict):
    """Allows downloading in bulk"""
    try:
        image_data = await attachment.read()
        with Image.open(io.BytesIO(image_data)) as img:
            if img.info:
                if 'parameters' in img.info:
                    info = img.info['parameters']
                elif 'prompt' in img.info:
                    info = img.info['prompt']
                elif 'Comment' in img.info:
                    info = img.info["Comment"]
                else: #comfy?
                    info = comfyui_get_data(img.info)
            else:
                info = read_info_from_image_stealth(img)
                
            if info:
                metadata[i] = info
    except Exception as error:
        print(f"{type(error).__name__}: {error}")


@client.event
async def on_raw_reaction_add(ctx: RawReactionActionEvent):
    """Send image metadata in reacted post to user DMs"""
    if ctx.emoji.name not in [CONFIG.get('METADATA', '🔎'), CONFIG.get('GUESS', '❔')] or ctx.channel_id not in monitored or ctx.member.bot:
        return
    channel = client.get_channel(ctx.channel_id)
    message = await channel.fetch_message(ctx.message_id)
    if not message:
        return
    attachments = [a for a in message.attachments if a.filename.lower().endswith(".png")]
    if not attachments:
        return
    if ctx.emoji.name == CONFIG.get('GUESS', '❔'):
        # todo: make this cleaner
        user_dm = await client.get_user(ctx.user_id).create_dm()
        embed = Embed(title="Predicted Prompt", color=message.author.color)
        embed = embed.set_image(url=attachments[0].url)
        predicted = GRADCL.predict(gradio_client.file(attachments[0].url),
                                   "chen-convnext3",
                                   0.45, True, True, api_name="/classify")[1]
        embed.add_field(name="DashSpace", value=predicted)
        predicted = predicted.replace(" ", ",")
        predicted = predicted.replace("-", " ")
        predicted = predicted.replace(",", ", ")
        embed.add_field(name="CommaSpace", value=predicted)
        await user_dm.send(embed=embed)
        return
    metadata = OrderedDict()
    tasks = [read_attachment_metadata(i, attachment, metadata) for i, attachment in enumerate(attachments)]
    await asyncio.gather(*tasks) #this code is amazing. -yoinked
    if not metadata:
        return
    user_dm = await client.get_user(ctx.user_id).create_dm()
    for attachment, data in [(attachments[i], data) for i, data in metadata.items()]:
        try:
            if 'Steps:' in data:
                try:
                    params = get_params_from_string(data)
                    embed = get_embed(params, message)
                    embed.set_image(url=attachment.url)
                    custom_view = MyView()
                    custom_view.metadata = data
                    await user_dm.send(view=custom_view, embed=embed)
                except Exception as e:
                    print(e)
                    txt = "## >w<\nuh oh! pi-chan did a fucky wucky and cant parse it into a neat view, so heres the raw content\n## >w<\n" + data
                    await user_dm.send(txt)
            else:
                img_type = "ComfyUI" if "\"inputs\"" in data else "NovelAI"
                
                i = 0
                if img_type=="NovelAI":
                    x = json.loads(data)
                    if "sui_image_params" in x.keys():
                        t = x['sui_image_params'].copy()
                        del x['sui_image_params']
                        for key in t:
                            t[key] = str(t[key])
                        x = x|t
                        embed = Embed(title="Swarm Parameters", color=message.author.color)
                    else:
                        embed = Embed(title="Nai Parameters", color=message.author.color)
                    if "Comment" in x.keys():
                        t = x['Comment'].replace(r'\"', '"')
                        t = json.loads(t)
                        for key in t:
                            t[key] = str(t[key])
                        x = x | t
                        del x['Comment']
                        del x['Description']
                    for k in x.keys():
                        i += 1
                        if i >= 25:
                            continue
                        embed.add_field(name=k, value=str(x[k])[:1023], inline=True)
                else:
                    embed = Embed(title="ComfyUI Parameters", color=message.author.color)
                    for enum, dax in enumerate(comfyui_get_data(data)):
                        i += 1
                        if i >= 25:
                            continue
                        embed.add_field(name=f"{dax['type']} {enum+1} (beta)", value=dax['val'], inline=True)
                embed.set_footer(text=f'Posted by {message.author}', icon_url=message.author.display_avatar)
                embed.set_image(url=attachment.url)
                await user_dm.send(embed=embed)
                with io.StringIO() as f:
                    indented = json.dumps(json.loads(data), sort_keys=True, indent=2)
                    f.write(indented)
                    f.seek(0)
                    await user_dm.send(file=File(f, "parameters.json"))
        
        except Exception as e:
            print(data)
            print(e)
            pass


@client.message_command(name="View Raw Prompt")
async def raw_prompt(ctx: ApplicationContext, message: Message):
    """Get raw list of parameters for every image in this post."""
    attachments = [a for a in message.attachments if a.filename.lower().endswith(".png")]
    if not attachments:
        await ctx.respond("This post contains no matching images.", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    metadata = OrderedDict()
    tasks = [read_attachment_metadata(i, attachment, metadata) for i, attachment in enumerate(attachments)]
    await asyncio.gather(*tasks)
    if not metadata:
        await ctx.respond(f"This post contains no image generation data.\n{message.author.mention} needs to install [this extension](<https://github.com/ashen-sensored/sd_webui_stealth_pnginfo>).", ephemeral=True)
        return
    response = json.dumps(metadata, sort_keys=True, indent=2)
    if len(response) < 1980:
        await ctx.respond(f"```yaml\n{response}```", ephemeral=True)
    else:
        with io.StringIO() as f:
            try:
                indented = json.dumps(json.loads(response), sort_keys=True, indent=2)
            except:
                pass
                #not a json
            f.write(indented)
            f.seek(0)
            await ctx.respond(embed=embed, file=File(f, "parameters.json"))
@client.message_command(name="View Parameters/Prompt")
async def formatted(ctx: ApplicationContext, message: Message):
    """Get a formatted list of parameters for every image in this post."""
    attachments = [a for a in message.attachments if a.filename.lower().endswith(".png")]
    if not attachments:
        await ctx.respond("This post contains no matching images.", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    metadata = OrderedDict()
    tasks = [read_attachment_metadata(i, attachment, metadata) for i, attachment in enumerate(attachments)]
    await asyncio.gather(*tasks)
    print(metadata.items()) #debugging
    if not metadata:
        await ctx.respond(f"This post contains no image generation data.\n{message.author.mention} needs to install [this extension](<https://github.com/ashen-sensored/sd_webui_stealth_pnginfo>).", ephemeral=True)
        return
    try:
        if 'Steps:' in data:
            try:
                params = get_params_from_string(data)
                embed = get_embed(params, message)
                embed.set_image(url=attachment.url)
                custom_view = MyView()
                custom_view.metadata = data
                await ctx.respond(view=custom_view, embed=embed)
            except Exception as e:
                print(e)
                txt = "## >w<\nuh oh! pi-chan did a fucky wucky and cant parse it into a neat view, so heres the raw content\n## >w<\n" + data
                await ctx.respond(txt)
        else:
            img_type = "ComfyUI" if "\"inputs\"" in data else "NovelAI"
            
            i = 0
            if img_type=="NovelAI":
                x = json.loads(data)
                if "sui_image_params" in x.keys():
                    t = x['sui_image_params'].copy()
                    del x['sui_image_params']
                    for key in t:
                        t[key] = str(t[key])
                    x = x|t
                    embed = Embed(title="Swarm Parameters", color=message.author.color)
                else:
                    embed = Embed(title="Nai Parameters", color=message.author.color)
                if "Comment" in x.keys():
                    t = x['Comment'].replace(r'\"', '"')
                    t = json.loads(t)
                    for key in t:
                        t[key] = str(t[key])
                    x = x | t
                    del x['Comment']
                    del x['Description']
                for k in x.keys():
                    i += 1
                    if i >= 25:
                        continue
                    embed.add_field(name=k, value=str(x[k])[:1023], inline=True)
            else:
                embed = Embed(title="ComfyUI Parameters", color=message.author.color)
                for enum, dax in enumerate(comfyui_get_data(data)):
                    i += 1
                    if i >= 25:
                        continue
                    embed.add_field(name=f"{dax['type']} {enum+1} (beta)", value=dax['val'], inline=True)
            embed.set_footer(text=f'Posted by {message.author}', icon_url=message.author.display_avatar)
            embed.set_image(url=attachment.url)
            with io.StringIO() as f:
                try:
                    indented = json.dumps(json.loads(data), sort_keys=True, indent=2)
                except:
                    indented = data
                f.write(indented)
                f.seek(0)
                await ctx.respond(embed=embed, file=File(f, "parameters.json"))
        
    except Exception as e:
        print(f"{type(e).__name__}: {e}")
        pass

try:
    import psutil
    @client.slash_command()
    async def status(ctx: ApplicationContext):
        """Get the status of the VM/bot."""
        embed = Embed(title="Status", color=0x00ff00)
        embed.add_field(name="CPU Usage", value=f"{psutil.cpu_percent()}%")
        embed.add_field(name="RAM Usage", value=f"{psutil.virtual_memory().percent}%")
        embed.add_field(name="Disk Usage", value=f"{psutil.disk_usage('/').percent}%")
        embed.set_footer(text="migus? plapped.", icon_url=ctx.author.display_avatar)
        await ctx.respond(embed=embed, ephemeral=True)
except ImportError:
    pass

client.run(CONFIG.get('TOKEN'))

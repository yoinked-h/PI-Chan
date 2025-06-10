"""Prompt Inspector (PI-Chan)"""
import io
from collections import OrderedDict
from pathlib import Path
import asyncio
import gzip
import json
import toml
import gradio_client
import discord
from discord import (
    Intents, Embed, ButtonStyle, Message, Attachment, File,
    RawReactionActionEvent, ApplicationContext, IntegrationType
)
from discord.ext import commands
from discord.ui import View, button
from PIL import Image
import comfy_parser 
import chat_module 

# --- Configuration Loading ---
CONFIG_PATH = Path('config.toml')
BASE_CONFIG_PATH = Path('config.base.toml')

if not CONFIG_PATH.exists() and BASE_CONFIG_PATH.exists():
    try:
        base_cfg = BASE_CONFIG_PATH.read_text(encoding='utf-8')
        CONFIG_PATH.write_text(base_cfg, encoding='utf-8')
        print(f"Created default config file at: {CONFIG_PATH}")
    except Exception as e:
        print(f"Error creating default config: {e}")
        exit(1) # Exit if config cannot be created
elif not CONFIG_PATH.exists():
    print(f"Error: Config file not found at {CONFIG_PATH} and base config {BASE_CONFIG_PATH} missing.")
    exit(1)


try:
    CONFIG = toml.load(CONFIG_PATH)
except toml.TomlDecodeError as e:
    print(f"Error loading config file {CONFIG_PATH}: {e}")
    exit(1)
except Exception as e:
    print(f"Unexpected error loading config: {e}")
    exit(1)

# --- Bot Setup ---
monitored: list = CONFIG.get('MONITORED_CHANNEL_IDS', [])
chatmonitored: list = CONFIG.get('GEMINIAPI_RESPONSIVE', [])
SCAN_LIMIT_BYTES = CONFIG.get('SCAN_LIMIT_BYTES', 40 * 1024**2)  # Default 40 MB
GRADIO_BACKEND = CONFIG.get('GRADIO_BACKEND')
TOKEN = CONFIG.get('TOKEN')
METADATA_EMOJI = CONFIG.get('METADATA', '🔎')
TRUSTED_UIDS = CONFIG.get('TRUSTED_UIDS', [0])
GUESS_EMOJI = CONFIG.get('GUESS', '❔')
DELETE_DM_EMOJI = CONFIG.get('DELETE_DM', '❌')

# Validate essential config
if not TOKEN:
    print("Error: DISCORD_TOKEN is not set in config.toml")
    exit(1)
if not GRADIO_BACKEND:
    print("Warning: GRADIO_BACKEND is not set in config.toml. Prompt guessing will not work.")
    GRADCL = None
else:
    try:
        GRADCL = gradio_client.Client(GRADIO_BACKEND)
        print(f"Connected to Gradio backend: {GRADIO_BACKEND}")
    except Exception as e:
        print(f"Error connecting to Gradio backend {GRADIO_BACKEND}: {e}")
        GRADCL = None


intents = Intents.default() | Intents.message_content | Intents.members
# Consider adding privileged intents gateway check if needed later
client = commands.Bot(intents=intents)

chatbotmodule = None

if chat_module.working:
    try:
        chatbotmodule = chat_module.ChatModule(
            CONFIG.get('MODEL_NAME', 'gemini-2.0-flash'),
            api_key=CONFIG.get('API_KEY'),
            personality=CONFIG.get('PERSONALITY', None),
            uid=client.user.id
        )
    except ImportError as e:
        print(f"Error initializing ChatModule: {e}")

# --- Helper Functions ---

def get_params_from_string(param_str: str) -> OrderedDict:
    """Get parameters from an old A1111 metadata string."""
    output_dict = OrderedDict() # Use OrderedDict to keep order somewhat
    try:
        parts = param_str.split('Steps: ', 1)
        if len(parts) != 2:
            # Basic fallback if 'Steps: ' isn't present
            output_dict['Parameters'] = param_str[:1024]
            return output_dict

        prompts_part = parts[0]
        params_part = 'Steps: ' + parts[1]

        # Extract prompts
        if 'Negative prompt: ' in prompts_part:
            prompt_split = prompts_part.split('Negative prompt: ', 1)
            output_dict['Prompt'] = prompt_split[0].strip()
            output_dict['Negative Prompt'] = prompt_split[1].strip()
        else:
            output_dict['Prompt'] = prompts_part.strip()
            output_dict['Negative Prompt'] = "" # Explicitly empty

        # Truncate prompts if needed
        if len(output_dict.get('Prompt', '')) > 1020:
            output_dict['Prompt'] = output_dict['Prompt'][:1020] + '...'
        if len(output_dict.get('Negative Prompt', '')) > 1020:
            output_dict['Negative Prompt'] = output_dict['Negative Prompt'][:1020] + '...'

        # Extract other parameters
        params = params_part.split(', ')
        for param in params:
            try:
                key, value = param.split(': ', 1)
                output_dict[key.strip()] = value.strip()[:1023] # Limit value length
            except ValueError:
                # Handle cases like "Fooocus V2 Expansion" which might not have ': '
                if param.strip(): # Avoid adding empty keys
                    output_dict[f"Info {len(output_dict)}"] = param.strip()[:1023] # Generic key
    except Exception as e:
        print(f"Error parsing A1111 string: {e}\nString: {param_str[:200]}...")
        output_dict['Parse Error'] = "Could not fully parse parameters."
        output_dict['Raw'] = param_str[:1000] + ('...' if len(param_str) > 1000 else '')

    return output_dict


def create_param_embed(embed_dict: dict, message_author: discord.User, title: str = "Parameters") -> Embed:
    """Creates a Discord Embed from a dictionary of parameters."""
    embed = Embed(title=title, color=message_author.color if hasattr(message_author, 'color') else discord.Color.blue())
    i = 0
    skipped = 0
    for key, value in embed_dict.items():
        key_str = str(key).strip()
        value_str = str(value).strip()

        if not key_str or not value_str:
            skipped += 1
            continue

        if i >= 24: # Keep under embed field limit (25), reserve one for footer etc.
            skipped += 1
            continue

        # Format value as code block, ensure length limits
        formatted_value = f"```\n{value_str[:1000]}\n```"
        # Determine if inline
        is_prompt_field = 'prompt' in key_str.lower() or 'negative' in key_str.lower()
        is_long_value = len(value_str) > 100 # Arbitrary threshold for inline

        embed.add_field(
            name=key_str[:255],
            value=formatted_value[:1024],
            inline=not is_prompt_field and not is_long_value
        )
        i += 1

    if skipped > 0:
        embed.add_field(name="Note", value=f"Skipped {skipped} empty or additional fields due to limits.", inline=False)

    embed.set_footer(text=f'Posted by {message_author}', icon_url=message_author.display_avatar)
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


def drawthings_drain(info: dict):
    """Extracts and formats parameters from DrawThings metadata."""
    try:
        # Path through the typical DrawThings XMP structure
        xmp_data = info.get('XML:com.adobe.xmp', '')
        # Split carefully to find the relevant JSON part
        parts = xmp_data.split('<rdf:li xml:lang="x-default">', 2)
        if len(parts) > 2:
            json_part = parts[2].split('</rdf:li>', 1)[0]
            p = json.loads(json_part)
            # Remap keys to a more standard format
            remapped = {
                'Prompt': p.get('c', ''),
                'Negative Prompt': p.get('uc', ''),
                'Model': p.get('model', ''),
                'Seed': p.get('seed', ''),
                'Steps': p.get('steps', ''),
                'CFG Scale': p.get('scale'), # Might be under v2
                'Sampler': p.get('sampler'), # Might be under v2
                # Try to get size/other details from v2 if present
            }
            if 'v2' in p and isinstance(p['v2'], dict):
                remapped['Width'] = p['v2'].get('width')
                remapped['Height'] = p['v2'].get('height')
                if not remapped.get('Sampler'): remapped['Sampler'] = p['v2'].get('sampler')
                if not remapped.get('CFG Scale'): remapped['CFG Scale'] = p['v2'].get('scale')
                remapped['Guidance Mode'] = p['v2'].get('guidanceMode')
                remapped['Aesthetic Score'] = p['v2'].get('aesthetic_score') # Key indicator
                # Add more v2 fields if needed

            # Include original for debugging/completeness if needed, but maybe not directly in output
            # remapped['_original_drawthings'] = p
            # Filter out None values before returning
            filtered_remapped = {k: v for k, v in remapped.items() if v is not None}
            return json.dumps(filtered_remapped) # Return as JSON string for consistency
        else:
            print("Could not find DrawThings JSON payload in XMP.")
            return None
    except json.JSONDecodeError:
        print("Error decoding DrawThings JSON.")
        return None
    except Exception as e:
        print(f"Error processing DrawThings metadata: {e}")
        return None


async def read_attachment_metadata(attachment: Attachment):
    """
    Reads metadata from a single image attachment.
    Returns a tuple: (metadata, error_message).
    Metadata can be a string (A1111, NAI, Invoke, DrawThings JSON) or list (Comfy parsed).
    """
    metadata = None
    info_source = None # To track where the metadata came from
    try:
        if not attachment.filename.lower().endswith((".png", ".webp")): # Support webp too?
            return None, "Not a PNG or WEBP file."
        if attachment.size > SCAN_LIMIT_BYTES:
            return None, f"File size ({attachment.size / 1024**2:.1f} MB) exceeds limit ({SCAN_LIMIT_BYTES / 1024**2:.1f} MB)."

        image_data = await attachment.read()
        with Image.open(io.BytesIO(image_data)) as img:
            # 1. Check standard PNG info chunks
            if img.info:
                if 'parameters' in img.info: # A1111
                    metadata = img.info['parameters']
                    info_source = "A1111 (parameters)"
                elif 'prompt' in img.info: # NAI?
                    metadata = img.info['prompt']
                    info_source = "NAI? (prompt)"
                elif 'Comment' in img.info: # NAI JSON / Swarm / Others?
                    metadata = img.info["Comment"]
                    info_source = "JSON? (Comment)"
                elif 'invokeai_metadata' in img.info: # InvokeAI
                    metadata = img.info['invokeai_metadata']
                    info_source = "InvokeAI (invokeai_metadata)"
                elif 'XML:com.adobe.xmp' in img.info: # DrawThings
                    metadata = drawthings_drain(img.info)
                    info_source = "DrawThings (XMP)"
                elif 'generate_info' in img.info: # Illust metadata
                    metadata = img.info['generate_info']
                    info_source = "Illust (generate_info)"
                elif 'class_type' in img.info: # ComfyUI
                    metadata = img.info
                    info_source = "ComfyUI (info)"


            # 2. If no standard metadata found, try stealth PNGInfo
            if metadata is None:
                # Ensure image mode is suitable for stealth reading (needs RGB or RGBA)
                if img.mode not in ("RGB", "RGBA"):
                    try:
                        # print(f"Converting image from {img.mode} to RGBA for stealth check.")
                        img_conv = img.convert("RGBA")
                        metadata = read_info_from_image_stealth(img_conv)
                        if metadata: info_source = "Stealth PNGInfo"
                        img_conv.close() # Close converted image
                    except Exception as conv_err:
                        print(f"Error converting image for stealth read: {conv_err}")
                else:
                    metadata = read_info_from_image_stealth(img)
                    if metadata: info_source = "Stealth PNGInfo"

        # print(f"Metadata found via: {info_source}" if metadata else "No metadata found.")
        return metadata, None # Return metadata and no error

    except FileNotFoundError:
        return None, "Attachment could not be downloaded."
    except discord.HTTPException as e:
        return None, f"Network error downloading attachment: {e.status}"
    except Image.UnidentifiedImageError:
        return None, "Could not identify image format. Is it corrupted?"
    except Exception as error:
        print(f"Error reading attachment metadata for {attachment.filename}: {type(error).__name__}: {error}")
        # import traceback
        # traceback.print_exc() # More detail for debugging
        return None, f"An unexpected error occurred: {type(error).__name__}"

# --- UI Views ---
class MyView(View):
    """View with a button to show full A1111 parameters."""
    def __init__(self, metadata_string: str):
        super().__init__(timeout=3600, disable_on_timeout=True)
        self.metadata = metadata_string

    @button(label='Full Parameters', style=ButtonStyle.green)
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button): # Corrected signature
        button.disabled = True
        await interaction.response.edit_message(view=self)
        if not self.metadata:
            await interaction.followup.send("Metadata is missing.", ephemeral=True)
            return

        if len(self.metadata) > 1980:
            # Try to format as JSON if possible, otherwise send as text
            try:
                # Attempt to parse A1111 string into dict, then format nicely
                params_dict = get_params_from_string(self.metadata)
                formatted_json = json.dumps(params_dict, indent=2)
                file_content = formatted_json
                filename = "parameters.json"
            except Exception:
                # Fallback to raw text if JSON fails
                file_content = self.metadata
                filename = "parameters.txt"

            with io.StringIO(file_content) as f:
                f.seek(0)
                await interaction.followup.send(file=File(f, filename), ephemeral=True)
        else:
            # Send directly if short enough
            await interaction.followup.send(f"```\n{self.metadata[:1990]}\n```", ephemeral=True)

# --- Unified Metadata Processing and Display Function ---

async def process_and_display_metadata(
    message: Message,
    attachment: Attachment,
    metadata,
    send_func: callable, # Coroutine to send the final message (e.g., ctx.respond, user_dm.send)
    attach_original_image: bool = False,
    add_details_button: bool = False # Specific to A1111 reaction context
):
    """
    Parses metadata (string or pre-parsed list) and sends a formatted embed.

    Args:
        message: The original discord Message.
        attachment: The discord Attachment the metadata belongs to.
        metadata: The metadata string or pre-parsed ComfyUI list.
        send_func: The async function to call to send the response.
        attach_original_image: Whether to attach the original image file to the response.
        add_details_button: Whether to add the 'Full Parameters' button (for A1111 reaction).
    """
    files_to_send = []
    view_to_send = None
    embed = None

    try:        
        if isinstance(metadata, str): # String metadata (A1111, NAI, Invoke, JSON, etc.)
            if 'Steps:' in metadata and 'Negative prompt:' in metadata: # Likely A1111
                img_type = "A1111"
                params = get_params_from_string(metadata)
                embed = create_param_embed(params, message.author, title=f"{img_type} Parameters")
                if add_details_button:
                    view_to_send = MyView(metadata) # Pass raw string to button view

            else: # Try parsing as JSON, handle different known structures
                img_type = "Unknown JSON" # Default
                params_dict = None
                try:
                    params_dict = json.loads(metadata)
                    if not isinstance(params_dict, dict):
                        raise ValueError("Metadata is not a JSON object") # Ensure it's a dict

                    # Identify specific JSON types
                    if "generation_mode" in params_dict: # InvokeAI
                        img_type = "InvokeAI"
                        # Optionally remove less relevant keys for cleaner embed
                        keys_to_remove = ['generation_mode', 'seamless_y', 'positive_style_prompt',
                                        'negative_style_prompt', 'regions', 'canvas_v2_metadata',
                                        'app_version', '_invokeai_metadata_tag', '_dream_metadata_tag']
                        for key in keys_to_remove:
                            params_dict.pop(key, None)

                    elif "sui_image_params" in params_dict: # SwarmUI
                        img_type = "SwarmUI"
                        swarm_params = params_dict.pop('sui_image_params', {})
                        params_dict = {}
                        # Merge swarm params into main dict (convert values to str for safety)
                        for key, val in swarm_params.items():
                            if "sui_" not in key: 
                                params_dict[key] = str(val)

                    elif "aesthetic_score" in params_dict or 'Guidance Mode' in params_dict: # DrawThings (already parsed to JSON)
                        img_type = "DrawThings"
                        # Keys are likely already well-named from drawthings_drain

                    elif "Comment" in params_dict and isinstance(params_dict["Comment"], str): # NAI round 2 (JSON in Comment)
                        try:
                            comment_json = json.loads(params_dict["Comment"])
                            if isinstance(comment_json, dict):
                                img_type = "NovelAI"
                                params_dict.pop("Comment", None) # Remove original comment
                                params_dict.pop("Description", None) # Often redundant
                                params_dict.update(comment_json) # Merge parsed comment
                        except json.JSONDecodeError:
                            pass # Keep original comment if not valid JSON
                    elif 'sampler' in params_dict and 'seed' in params_dict and 'strength' in params_dict: # Likely NAI (primary keys)
                        img_type = "NovelAI"
                        params_dict.pop("Description", None)
                    
                    elif 'samplerName' in params_dict: # Illust 
                        img_type = "Illust"
                        params_dict.pop("type", None)
                        if params_dict['checkpoint'] == "unknown":
                            params_dict.pop("checkpoint", None)
                    
                    elif 'class_type' in metadata: # Comfy
                        img_type = "ComfyUI"
                        # Pass into comfy_parser
                        params_dict = comfy_parser.comfyui_get_data(metadata)
                    
                    # Create embed from the dictionary
                    embed = create_param_embed(params_dict, message.author, title=f"{img_type} Parameters")

                except (json.JSONDecodeError, ValueError):
                    # Not A1111, Not valid JSON -> Treat as basic text/unknown
                    img_type = "Unknown/Text"
                    embed = Embed(title="Parameters (Unknown Format)", color=message.author.color if hasattr(message.author, 'color') else discord.Color.blue())
                    embed.add_field(name="Raw Metadata", value=f"```\n{metadata[:1000]}\n```" + ('...' if len(metadata) > 1000 else ''), inline=False)
                    embed.set_footer(text=f'Posted by {message.author}', icon_url=message.author.display_avatar)

                # Add JSON file for all non-A1111 string types if possible
                json_str_for_file = metadata # Use original string
                with io.StringIO(json_str_for_file) as f:
                    f.seek(0)
                    files_to_send.append(File(f, "parameters.json" if params_dict else "parameters.txt"))

        else:
            # Should not happen if called correctly
            print(f"Error: Invalid metadata type passed: {type(metadata)}")
            await send_func(content="Error: Could not process metadata due to unexpected data type.")
            return

        # Final steps for all types
        if embed:
            if attach_original_image:
                embed.set_image(url=attachment.url) # Add image preview

            # Send the message using the provided function
            # Need to handle different signatures (send_func might not accept 'view' or 'files')
            kwargs = {'embed': embed}
            if files_to_send:
                kwargs['files'] = files_to_send
            if view_to_send:
                kwargs['view'] = view_to_send

            try:
                await send_func(**kwargs)
            except TypeError as te:
                # Fallback if the send_func doesn't accept all args
                print(f"Warning: send_func call failed, trying simpler call: {te}")
                try:
                    # Try sending without view first
                    if 'view' in kwargs: del kwargs['view']
                    await send_func(**kwargs)
                except Exception as fallback_e:
                    print(f"Error sending metadata response (fallback failed): {fallback_e}")
                    # Try sending just a simple text message as last resort
                    await send_func(content="Error displaying formatted parameters. Raw data might be available via command.")

            except discord.HTTPException as http_e:
                print(f"Discord API error sending metadata: {http_e}")
                await send_func(content=f"Error sending parameters due to Discord API error: {http_e.status}")
            except Exception as send_e:
                print(f"Unexpected error sending metadata response: {send_e}")
                await send_func(content="An unexpected error occurred while sending the parameters.")

        else: # Case where embed creation failed somehow
            await send_func(content="Could not generate parameter embed.")


    except Exception as e:
        print(f"Fatal error in process_and_display_metadata for {attachment.filename}: {type(e).__name__}: {e} | {img_type}")
        # import traceback
        # traceback.print_exc()
        try:
            await send_func(content=f"## >w<\nuh oh! pi-chan did a fucky wucky and couldn't parse the parameters\nError: {type(e).__name__}\n## >w<")
        except Exception as final_e:
            print(f"Error sending error message: {final_e}")


# --- Gradio Prediction ---
async def predict_prompt_task(user_id: int, member_color: discord.Color, attachment: Attachment):
    """Task to predict prompt using Gradio and send to user DMs."""
    if not GRADCL:
        print("Gradio client not configured, skipping prediction.")
        # Optionally notify user DM?
        return

    user = client.get_user(user_id)
    if not user:
        print(f"Cannot find user {user_id} for prediction DM.")
        return

    try:
        user_dm = await user.create_dm()
        embed = Embed(title="Predicted Prompt (Experimental)", color=member_color)
        embed.set_image(url=attachment.url)
        embed.set_footer(text="Prediction via yoinked-da-nsfw-checker HF Space")

        # Show a "predicting" message
        predict_msg = await user_dm.send(embed=embed, content="✨ Predicting tags...")

        # Make the Gradio prediction
        job = GRADCL.submit(
                gradio_client.file(attachment.url), # filepath in 'parameter_9' Textbox component
                "chen-evangelion",                  # value in 'Select Classifier' Dropdown component
                0.45,		                        # value in 'Threshold' Slider component
                True,		                        # value in 'Use character interrogation?' Checkbox component
                True,		                        # value in 'Use general interrogation?' Checkbox component
                api_name="/classify"
        )
        # Wait for result - consider adding a timeout
        try:
            # result is typically a tuple, we need the second element [1]
            result_data = await asyncio.wait_for(asyncio.to_thread(job.result), timeout=120) # 2 min timeout
            predicted_tags = result_data[1] if isinstance(result_data, tuple) and len(result_data) > 1 else "Error: Unexpected result format"
        except asyncio.TimeoutError:
            predicted_tags = "Error: Prediction timed out."
        except Exception as pred_err:
            predicted_tags = f"Error during prediction: {pred_err}"


        # Format and add fields
        if predicted_tags.startswith("Error:"):
            embed.add_field(name="Prediction Failed", value=f"```{predicted_tags}```", inline=False)
        else:
            # Original formatting (DashSpace)
            tags_dash = predicted_tags[:1000] # Limit length
            embed.add_field(name="Tags (Dash Space)", value=f"```\n{tags_dash}\n```", inline=False)

            # Comma Space formatting
            tags_comma = tags_dash.replace(" ", ",").replace("-", " ").replace(",", ", ")[:1000]
            embed.add_field(name="Tags (Comma Space)", value=f"```\n{tags_comma}\n```", inline=False)

        # Edit the original message with results
        await predict_msg.edit(content="✨ Prediction Complete!", embed=embed)

    except discord.Forbidden:
        print(f"Cannot send DM to user {user_id} (DMs likely disabled).")
    except discord.HTTPException as http_e:
        print(f"Discord API error during prediction DM: {http_e}")
        # Maybe try sending to original channel if DM fails? Risky.
    except Exception as e:
        print(f"Error in predict_prompt_task for user {user_id}: {type(e).__name__}: {e}")
        try:
            await user_dm.send("Sorry, an error occurred while predicting the prompt.")
        except Exception: pass # Ignore if sending error message fails

# --- Discord Events ---

@client.event
async def on_ready():
    """Prints bot status when ready."""
    print(f"Logged in as {client.user} ({client.user.id})")
    print(f"Monitoring {len(monitored)} channels: {monitored}")
    print(f"Using metadata emoji: {METADATA_EMOJI}")
    print(f"Using guess emoji: {GUESS_EMOJI}" if GRADCL else "Prompt Guessing Disabled (No Gradio Client)")
    print(f"Scan limit: {SCAN_LIMIT_BYTES / 1024**2:.1f} MB")
    print("------")

@client.event
async def on_message(message: Message):
    """Checks messages in monitored channels for images with metadata."""
    # Ignore bots, DMs, and non-monitored channels
    if message.author.bot or not message.guild or message.channel.id not in monitored:
        return

    if chatbotmodule is not None:
        # Check if the message contains any chatbot triggers
        triggers = chatbotmodule.triggers if hasattr(chatbotmodule, "triggers") else []
        if any(trigger in message.content.lower() for trigger in triggers):
            # Fetch last 15 messages or until 10 minutes before this message
            history = []
            async for msg in message.channel.history(limit=50, before=message.created_at, oldest_first=False):
                if (message.created_at - msg.created_at).total_seconds() > 600:
                    break
                history.append(msg)
                if len(history) >= 14:
                    break
                # Get chatbot response
            try:
                response = await asyncio.to_thread(chatbotmodule.chat_with_messages, history)
                if response:
                    await message.channel.send(response, reference=message)
            except Exception as e:
                print(f"Chatbot error: {e}")

    if message.attachments:
        # Check only the first valid attachment for performance
        for attachment in message.attachments:
            metadata, error = await read_attachment_metadata(attachment)
            if error:
                # print(f"Skipping attachment {attachment.filename}: {error}")
                continue # Try next attachment if first one fails or is invalid
            if metadata:
                try:
                    await message.add_reaction(METADATA_EMOJI)
                    # Found metadata in one attachment, no need to check others in this message
                    return
                except discord.HTTPException as e:
                    print(f"Failed to add reaction: {e}")
                    return # Stop if reaction fails
            # else: # No metadata found in this attachment, try next
                # print(f"No metadata found in {attachment.filename}")


@client.event
async def on_raw_reaction_add(payload: RawReactionActionEvent):
    """Handles reactions to potentially trigger metadata display or prompt guessing."""
    # Ignore bots, DMs, and non-monitored channels
    if payload.member.bot or not payload.guild_id or payload.channel_id not in monitored:
        if payload.emoji == DELETE_DM_EMOJI and payload.member.bot and payload.member.id == client.user.id:
            # Handle delete DM emoji reaction
            try:
                channel = client.get_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                if message and message.author.id == client.user.id:
                    await message.delete() # Delete the bot's own message
            except Exception: pass # Ignore if DM fails
        return

    emoji_name = str(payload.emoji) # Get emoji representation

    # Check if the reaction is one we care about
    is_metadata_request = emoji_name == METADATA_EMOJI
    is_guess_request = emoji_name == GUESS_EMOJI and GRADCL is not None

    if not is_metadata_request and not is_guess_request:
        return

    try:
        channel = client.get_channel(payload.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel): return # Ensure channel exists and is text
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        print(f"Message {payload.message_id} not found for reaction.")
        return
    except discord.Forbidden:
        print(f"Missing permissions to fetch message {payload.message_id}.")
        return
    except Exception as e:
        print(f"Error fetching message for reaction: {e}")
        return

    # Ensure the message has attachments
    valid_attachments = [
        a for a in message.attachments
        if a.filename.lower().endswith((".png", ".webp")) and a.size <= SCAN_LIMIT_BYTES
    ]
    if not valid_attachments:
        return # No valid attachments to process

    # Handle Prompt Guessing
    if is_guess_request:
        # Create a task for each valid attachment to predict in parallel
        tasks = [
            asyncio.create_task(
                predict_prompt_task(payload.user_id, payload.member.color, attachment)
            ) for attachment in valid_attachments
        ]
        # Notify user maybe?
        try:
            user_dm = await client.get_user(payload.user_id).create_dm()
            await user_dm.send(f"✨ Attempting to predict prompts for {len(tasks)} image(s) from the message...")
        except Exception: pass # Ignore if DM fails
        # Tasks run in background, no need to await here usually
        return # Guessing handled, exit


    # Handle Metadata Request
    if is_metadata_request:
        user = client.get_user(payload.user_id)
        if not user: return # Should not happen

        try:
            user_dm = await user.create_dm()
        except discord.Forbidden:
            print(f"Cannot send DM to user {payload.user_id}, metadata request ignored.")
            # Optionally notify in channel? Might be noisy.
            # await channel.send(f"{payload.member.mention}, I can't DM you the parameters!", delete_after=15)
            return
        except Exception as e:
            print(f"Error creating DM for metadata request: {e}")
            return

        processed_count = 0
        # Process each valid attachment for metadata
        for attachment in valid_attachments:
            metadata, error = await read_attachment_metadata(attachment)
            if error:
                # print(f"Skipping attachment {attachment.filename} for reaction: {error}")
                continue # Skip attachments with errors

            if metadata:
                processed_count += 1
                # Use the unified function to process and send
                await process_and_display_metadata(
                    message=message,
                    attachment=attachment,
                    metadata=metadata,
                    send_func=user_dm.send, # Send to user's DMs
                    attach_original_image=True, # Don't attach image in reaction DM
                    add_details_button=('Steps:' in metadata if isinstance(metadata, str) else False) # Add button only for A1111 strings
                )
            # else: No metadata found for this specific attachment

        if processed_count == 0:
            try:
                await user_dm.send("I couldn't find any generation parameters in the attachments of that message.")
            except Exception: pass # Ignore if DM fails


# --- Discord Commands ---

@client.slash_command(name="privacy", description="Shows the bot's privacy policy.")
async def privacy(ctx: ApplicationContext):
    """Returns our privacy policy."""
    base = Embed(title="Privacy Policy", color=ctx.author.color if hasattr(ctx.author, 'color') else discord.Color.blue())
    base.add_field(name="What we collect", value="When an image is sent in a **monitored channel**, the bot temporarily downloads it to memory (RAM) for processing.\nIt extracts metadata (like generation parameters) if present.\nBasic user info (username, color) is used for display purposes (e.g., embed footers).\n***We do not store your images or extracted metadata permanently.*** Data is processed in memory and discarded.", inline=False)
    base.add_field(name="What we store", value="The only persistent storage used is for:\n- The list of monitored channel IDs.\n- The bot's configuration file (`config.toml`).\nError logs may contain snippets of metadata temporarily if parsing fails, primarily for debugging by the bot operator.", inline=False)
    base.add_field(name="What we share", value="***We do not share any of your data or images with third parties.***", inline=False)
    base.add_field(name="Open Source", value="This bot is open source! Find the code [here](https://github.com/yoinked-h/PI-Chan).\nLicensed under the [MIT License](https://github.com/yoinked-h/PI-Chan/blob/main/LICENSE).\nBased on prior work by salt and NoCrypt.", inline=False)

    is_monitored = ctx.channel_id in monitored
    footer_text = f"Maintained by <@444257402007846942> | This channel is {'[monitored](<https://www.youtube.com/watch?v=kbNdx0yqbZE>)' if is_monitored else '[not monitored](https://www.youtube.com/watch?v=bnA9dt7Ul7c>)'}"
    icon = ctx.author.display_avatar if ctx.author else client.user.display_avatar
    base.set_footer(text=footer_text, icon_url=icon)
    # Bot avatar as image
    if client.user.display_avatar:
        base.set_thumbnail(url=client.user.display_avatar.url)
    await ctx.respond(embed=base, ephemeral=True)


@client.slash_command(name="toggle_channel", description="Adds/Removes a channel from monitoring.")
@commands.has_permissions(manage_messages=True)
@commands.guild_only() # Ensure this command is not used in DMs
async def toggle_channel(
    ctx: ApplicationContext,
    channel: discord.TextChannel = None # Optional argument, defaults to current channel
):
    """
    Adds/Removes a channel from the list of monitored channels.
    Requires 'Manage Messages' permission.
    """
    target_channel = channel or ctx.channel
    if not isinstance(target_channel, discord.TextChannel):
        await ctx.respond("Invalid channel type provided.", ephemeral=True)
        return

    channel_id = target_channel.id

    global monitored # Ensure we modify the global list
    if channel_id in monitored:
        monitored.remove(channel_id)
        action = "Removed"
    else:
        monitored.append(channel_id)
        action = "Added"

    # Update the config file persistently
    try:
        # Read existing config first to avoid overwriting other settings
        current_config = {}
        if CONFIG_PATH.exists():
            current_config = toml.load(CONFIG_PATH)

        current_config['MONITORED_CHANNEL_IDS'] = monitored # Update the list

        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            toml.dump(current_config, f)

        await ctx.respond(f"{action} channel {target_channel.mention} (`{channel_id}`) {('to' if action == 'Added' else 'from')} the monitoring list.", ephemeral=True)
        print(f"Channel {action}: {channel_id} by {ctx.author} ({ctx.author.id}). Current list: {monitored}")

    except Exception as e:
        print(f"Error updating config file for toggle_channel: {e}")
        await ctx.respond(f"Failed to update config file. {action} channel {target_channel.mention} in memory, but change may be lost on restart.", ephemeral=True)

@client.slash_command(
    name="toggle_gemini_channel",
    description="Adds/Removes a channel from the Gemini prompt-guessing monitor list."
)
@commands.has_permissions(manage_messages=True)
@commands.guild_only()
async def toggle_gemini_channel(
    ctx: ApplicationContext,
    channel: discord.TextChannel = None
):
    """
    Adds or removes a channel from GEMINIAPI_RESPONSIVE in config.toml.
    Requires Manage Messages permission.
    """
    target = channel or ctx.channel
    if not isinstance(target, discord.TextChannel):
        await ctx.respond("Invalid channel.", ephemeral=True)
        return
    
    channel_id = target.id
    global chatmonitored

    if channel_id in chatmonitored:
        chatmonitored.remove(channel_id)
        action = "Removed"
        preposition = "from"
    else:
        chatmonitored.append(channel_id)
        action = "Added"
        preposition = "to"

    # Persist change
    try:
        current = toml.load(CONFIG_PATH) if CONFIG_PATH.exists() else {}
        current['GEMINIAPI_RESPONSIVE'] = chatmonitored
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            toml.dump(current, f)

        await ctx.respond(
            f"{action} channel {target.mention} (`{channel_id}`) {preposition} prompt-guessing list.",
            ephemeral=True
        )

    except Exception as e:
        print(f"Error updating GEMINIAPI_RESPONSIVE in config: {e}")
        await ctx.respond(
            f"{action} in memory, but failed to write to config.toml. Change may be lost on restart.",
            ephemeral=True
        )


@toggle_gemini_channel.error
async def toggle_gemini_channel_error(ctx: ApplicationContext, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You need Manage Messages permission to use this.", ephemeral=True)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.respond("This command can only be used in a server.", ephemeral=True)
    else:
        print(f"Error in toggle_gemini_channel: {error}")
        await ctx.respond("Unexpected error occurred.", ephemeral=True)

@toggle_channel.error
async def toggle_channel_error(ctx: ApplicationContext, error):
    """Error handler for toggle_channel command."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You need the 'Manage Messages' permission to use this command.", ephemeral=True)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.respond("This command can only be used in a server.", ephemeral=True)
    else:
        print(f"Error in toggle_channel command: {error}")
        await ctx.respond("An unexpected error occurred.", ephemeral=True)


@client.message_command(name="View Raw Prompt")
async def raw_prompt(ctx: ApplicationContext, message: Message):
    """(Message Command) Get raw metadata for the first valid image."""
    if not message.attachments:
        await ctx.respond("This message has no attachments.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    metadata_found = None
    first_attachment = None

    for attachment in message.attachments:
        metadata, error = await read_attachment_metadata(attachment)
        if error:
            # print(f"Skipping {attachment.filename} for raw view: {error}")
            continue
        if metadata:
            metadata_found = metadata
            first_attachment = attachment
            break # Stop after finding the first one with metadata

    if not metadata_found:
        await ctx.respond(f"No image generation data found in the attachments.\n{message.author.mention} might need to enable metadata embedding in their image generator.", ephemeral=True)
        return

    # Prepare the response (raw string or JSON dump)
    response_text = ""
    filename = "parameters.txt" # Default filename

    if isinstance(metadata_found, list): # ComfyUI parsed list
        try:
            # Dump the parsed list as JSON
            response_text = json.dumps(metadata_found, indent=2)
            filename = "parameters_parsed.json"
        except Exception as e:
            response_text = f"Error formatting parsed data: {e}\n\nRaw list: {metadata_found}"
    elif isinstance(metadata_found, str):
        # Try to format as pretty JSON if it is JSON, otherwise use raw string
        try:
            parsed_json = json.loads(metadata_found)
            response_text = json.dumps(parsed_json, indent=2, sort_keys=True)
            filename = "parameters.json"
        except (json.JSONDecodeError, TypeError):
            # Not valid JSON, use the raw string
            response_text = metadata_found
            filename = "parameters.txt"
    else:
        response_text = "Error: Unknown metadata format."


    # Send the response
    if len(response_text) < 1980:
        await ctx.respond(f"```{filename.split('.')[1]}\n{response_text}\n```", ephemeral=True)
    else:
        with io.StringIO(response_text) as f:
            f.seek(0)
            await ctx.respond(file=File(f, filename), ephemeral=True)


@client.message_command(
    name="View Parameters",
    integration_types={
        IntegrationType.guild_install,
        IntegrationType.user_install,
    }
)
async def formatted_params(ctx: ApplicationContext, message: Message):
    """(Message Command) Get formatted parameters for the first valid image."""
    if not message.attachments:
        await ctx.respond("This message has no attachments.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    metadata_found = None
    first_attachment = None
    error_message = "No attachments with readable metadata found." # Default error

    # Find the first attachment with metadata
    for attachment in message.attachments:
        metadata, error = await read_attachment_metadata(attachment)
        if error:
            error_message = f"Checked attachment {attachment.filename}: {error}" # Keep last error
            continue
        if metadata:
            metadata_found = metadata
            first_attachment = attachment
            break # Found metadata, stop searching

    if not metadata_found:
        await ctx.respond(f"{error_message}\n{message.author.mention} might need to enable metadata embedding in their image generator.", ephemeral=True)
        return

    # Use the unified function to display
    await process_and_display_metadata(
        message=message,
        attachment=first_attachment,
        metadata=metadata_found,
        send_func=ctx.respond, # Respond to the interaction (ephemeral due to defer)
        attach_original_image=False, # Attach the image for context in the command response
        add_details_button=False # No button needed for command context
    )

# --- Optional Status Command ---
try:
    import psutil
    @client.slash_command(name="status", description="Shows the bot's current resource usage.")
    async def status(ctx: ApplicationContext):
        """Get the status of the VM/bot."""
        try:
            cpu_usage = psutil.cpu_percent()
            ram = psutil.virtual_memory()
            ram_usage = ram.percent
            disk = psutil.disk_usage('/')
            disk_usage = disk.percent

            embed = Embed(title="Bot Status", color=discord.Color.green())
            embed.add_field(name="CPU Usage", value=f"{cpu_usage:.1f}%")
            embed.add_field(name="RAM Usage", value=f"{ram_usage:.1f}% ({ram.used / 1024**3:.1f}/{ram.total / 1024**3:.1f} GB)")
            embed.add_field(name="Disk Usage", value=f"{disk_usage:.1f}% ({disk.used / 1024**3:.1f}/{disk.total / 1024**3:.1f} GB)")
            embed.set_footer(text="Resource usage of the host system.", icon_url=ctx.author.display_avatar if ctx.author else None)
            await ctx.respond(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"Error getting system status: {e}")
            await ctx.respond("Could not retrieve system status.", ephemeral=True)

except ImportError:
    print("psutil not installed, /status command disabled.")
    pass # Silently disable if psutil is not available

# --- Run the Bot ---
if __name__ == "__main__":
    if not TOKEN:
        print("FATAL: Discord bot token not found in config.toml. Exiting.")
    else:
        print("Starting bot...")
        try:
            client.run(TOKEN)
        except discord.LoginFailure:
            print("FATAL: Improper token provided. Check your config.toml.")
        except Exception as e:
            print(f"FATAL: An error occurred during bot startup or runtime: {e}")
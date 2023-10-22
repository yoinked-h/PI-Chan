# PI-Chan
Get prompts/descriptions of images uploaded on discord.

## Functionality

This Discord bot reacts to any image with generation metadata from the most popular webuis.
If you want to get a *rough* prompt, react with ❔

Install [this](https://github.com/ashen-sensored/sd_webui_stealth_pnginfo) if it breaks!

## Setup

1. Clone the repository
2. Install the dependencies with `pip install -r requirements.txt`
3. Create a Discord bot and invite it to your server
4. Enable the `Message Content Intent` in the Discord developer portal
5. Enable the `Server Members Intent` in the Discord developer portal
6. Create a file named ".env" in the root directory of the project
7. Set `BOT_TOKEN=<your discord bot token>` in the .env file
    7.1. So like `BOT_TOKEN=HFBVSAOa876vat764bq8967fgh8d8a76`
8. Add the channel IDs you want the bot to work in into the `config.toml` file
9.  Run the bot with `python3 PromptInspector.py`

## Examples
![1](images/mag_glass.png)
![2](images/cui_md.png)
![3](images/predicted.png)
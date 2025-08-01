import pytomlpp as toml
from pathlib import Path
import re
working = False
try:
    import openai
    working = True
except ImportError:
    print("Error: OpenAI library not found.")
    openai = None    

BASE = """
name = "bot"
definition = '''[You are a helpful assistant.]'''
triggers = ["bot", "pichan"]
repl = "bot: "
"""

IMG = ('png', 'jpg', 'jpeg', 'gif', 'webp')

async def handle_pings(msg):
    pattern = re.compile(r"<@(\d+)>")
    ids = set(pattern.findall(msg.content))
    id_to_name = {}
    for user_id in ids:
        user = await msg.guild.fetch_member(int(user_id))
        if user:
            id_to_name[user_id] = f"@{user.global_name or user.display_name}"
        else:
            id_to_name[user_id] = f"@unknown"
    def repl(match):
        uid = match.group(1)
        return id_to_name.get(uid, f"@unknown")
    return pattern.sub(repl, msg.content)

class ChatModule:
    def __init__(self, model_name="gpt-3.5-turbo", api_key=None, personality=None, vision=False):
        self.vision = vision
        if api_key is None:
            raise ValueError("API key must be provided.")
        self.client = openai.Client(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        self.model_name = model_name
        if personality is None:
            self.personality = toml.loads(BASE)
        else:
            personality_path = Path(f"{personality}.toml")
            if not personality_path.exists():
                personality_path = Path(personality)
            self.personality = toml.loads(personality_path.read_text())
        self.triggers = self.personality['triggers']
    async def preprocess(self, messages, uid):
        if not messages:
            raise ValueError("Messages cannot be empty.")
        chat_messages = []
        for message in messages:
            cont = []
            role = "user" if message.author.id != uid else "assistant"
            if message.attachments and self.vision:
                mime = message.attachments[0].filename.lower().split('.')[-1]
                if mime in IMG:
                    cont.append({"type": "image_url", "image_url": {"url": message.attachments[0].url}})
            if message.content:
                name = message.author.global_name if message.author.id != uid else self.personality['name']
                msgcont = message.content.strip()
                if message.mentions:
                    msgcont = await handle_pings(message)
                cont.append({"type": "text", "text": f"{name}: {msgcont}"})
            if cont:
                chat_messages.append({"role": role, "content": cont})
        # Add system prompt at the start
        chat_messages.insert(0, {"role": "system", "content": self.personality['definition']})
        return chat_messages
    async def chat(self, chat_messages):
        if not chat_messages:
            raise ValueError("Contents cannot be empty.")
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=chat_messages,
            max_tokens=768,
        )
        txt = response.choices[0].message.content.strip()
        temp = txt.split(':')
        if len(temp) > 1 and temp[0].strip().lower() == self.personality['repl'].strip().lower():
            txt = ':'.join(temp[1:]).strip()
        txt = txt.replace('&#x20;', ' ')
        return txt
    async def chat_with_messages(self, messages, uid):
        chat_messages = await self.preprocess(messages, uid)
        return await self.chat(chat_messages)

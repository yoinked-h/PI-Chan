import pytomlpp as toml
from pathlib import Path
import re
working = False
try:
    from google import genai
    from google.genai import types
    working = True
except ImportError:
    print("Error: Google GenAI library not found.")
    genai = None

BASE = """
name = "bot"
definition = '''[You are a helpful assistant.]'''
triggers = ["bot", "pichan"]
repl = "bot: "
"""

IMG = ('png', 'jpg', 'jpeg', 'gif', 'webp')

async def handle_pings(msg):
    # Regex to find all <@id> patterns
    pattern = re.compile(r"<@(\d+)>")
    # Find all unique IDs in the message
    ids = set(pattern.findall(msg.content))
    # Map IDs to global names
    id_to_name = {}
    for user_id in ids:
        user = await msg.guild.fetch_member(int(user_id))
        if user:
            id_to_name[user_id] = f"@{user.global_name or user.display_name}"
        else:
            id_to_name[user_id] = f"@unknown"
    # Replace all <@id> with @name
    def repl(match):
        uid = match.group(1)
        return id_to_name.get(uid, f"@unknown")
    return pattern.sub(repl, msg.content)

class ChatModule:
    def __init__(self, model_name="gemini-2.0-flash", api_key=None, personality=None,):
        if not working:
            raise ImportError("Google GenAI library is not available.")
        self.client = genai.Client(api_key=api_key)
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
        
        contents = []
        for message in messages:
            tp = []
            if message.attachments:
                attch = await message.attachments[0].read()
                mime = message.attachments[0].filename.lower().split('.')[-1]
                if mime in IMG:
                    if not message.content:
                        tp.append(types.Part.from_text(
                        text=message.author.global_name + ': ',
                    ))
                    tp.append(types.Part.from_bytes(
                        data=attch,
                        mime_type=f'image/{mime}',
                    ))
            if message.content:
                name = message.author.global_name if message.author.id != uid else self.personality['name']
                msgcont = message.content.strip()
                if message.mentions:
                    msgcont = await handle_pings(message)
                tp.append(types.Part.from_text(
                    text= name + ': ' + msgcont,
                ))
            role = "user" if message.author.id != uid else "model"
            contents.append(types.Content(parts=tp, role=role))
        return contents

    async def chat(self, contents):
        if not contents:
            raise ValueError("Contents cannot be empty.")
        
        response = self.client.models.generate_content(
            model=self.model_name,
            config=types.GenerateContentConfig(system_instruction=self.personality['definition'],
                                            max_output_tokens=256,
                                            safety_settings=[
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        ]),
            contents=contents
        )
        
        txt = response.text.strip()
        temp = txt.split(':')
        # remove the first Bot: if it starts like that
        if len(temp) > 1 and temp[0].strip().lower() == self.personality['repl'].strip().lower():
            txt = ':'.join(temp[1:]).strip()
        txt = txt.replace('&#x20;', ' ')
        return txt
    async def chat_with_messages(self, messages, uid):
        contents = await self.preprocess(messages, uid)
        return await self.chat(contents)


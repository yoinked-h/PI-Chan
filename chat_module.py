import toml
from pathlib import Path
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

class ChatModule:
    def __init__(self, model_name="gemini-2.0-flash", api_key=None, personality=None, uid=None):
        if not working:
            raise ImportError("Google GenAI library is not available.")
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.personality = toml.loads(Path(personality)) or toml.loads(BASE)
        self.uid = uid or 1
    def preprocess(self, messages):
        if not messages:
            raise ValueError("Messages cannot be empty.")
        
        contents = []
        for message in messages:
            tp = []
            if message.attachments:
                attch = message.attachments[0].read()
                mime = message.attachments[0].filename.lower().split('.')[-1]
                if mime in IMG:
                    tp.append(types.Part.from_bytes(
                        data=attch,
                        mime_type=f'image/{mime}',
                    ))
            if message.text:
                tp.append(types.Part.from_text(
                    text=message.author.name + ': ' + message.text.strip(),
                ))
            role = "user" if message.author.id != self.uid else "model"
            contents.append(types.Content(parts=tp, role=role))
        
        return contents

    def chat(self, contents):
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
        return txt
    def chat_with_messages(self, messages):
        contents = self.preprocess(messages)
        return self.chat(contents)
        
        
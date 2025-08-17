"""Translation utilities for PI-Chan"""
import pytomlpp as toml
from pathlib import Path

class TranslationManager:
    def __init__(self, language: str = "en"):
        self.language = language
        self.translations = {}
        self.load_translations()
    
    def load_translations(self):
        """Load translations from the specified language file"""
        translation_file = Path(f"translations/{self.language}.toml")
        if not translation_file.exists():
            # Fallback to English if language file doesn't exist
            translation_file = Path("translations/en.toml")
            if not translation_file.exists():
                print(f"Warning: No translation file found for {self.language}, and en.toml fallback missing!")
                return
        
        try:
            self.translations = toml.loads(translation_file.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"Error loading translation file {translation_file}: {e}")
            self.translations = {}
    
    def get(self, key: str, **kwargs) -> str:
        """Get a translated message with optional formatting"""
        try:
            message = self.translations.get('messages', {}).get(key, f"[MISSING TRANSLATION: {key}]")
            if kwargs:
                return message.format(**kwargs)
            return message
        except KeyError as e:
            print(f"Translation formatting error for key '{key}': {e}")
            return f"[TRANSLATION ERROR: {key}]"
        except Exception as e:
            print(f"Unexpected translation error for key '{key}': {e}")
            return f"[TRANSLATION ERROR: {key}]"
    
    def print(self, key: str, **kwargs):
        """Print a translated message"""
        print(self.get(key, **kwargs))

# Global translation manager instance
_translator = None

def init_translator(language: str = "en"):
    """Initialize the global translator"""
    global _translator
    _translator = TranslationManager(language)

def get_translator():
    """Get the global translator instance"""
    global _translator
    if _translator is None:
        init_translator()
    return _translator

def t(key: str, **kwargs) -> str:
    """Shorthand function to get translated text"""
    return get_translator().get(key, **kwargs)

def tprint(key: str, **kwargs):
    """Shorthand function to print translated text"""
    get_translator().print(key, **kwargs)

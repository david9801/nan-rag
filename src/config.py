import os
import pathlib
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

NAN_API_KEY  = os.getenv("NAN_API_KEY")
NAN_BASE_URL = os.getenv("NAN_BASE_URL", "https://api.nan.builders/v1")
EMBED_MODEL  = os.getenv("EMBED_MODEL",  "qwen3-embedding")
LLM_MODEL    = os.getenv("LLM_MODEL",    "deepseek-v4-flash")
API_KEY      = os.getenv("API_KEY")       # clave para proteger la API HTTP

# DB_PATH: override via env var para Docker (/app/data/chroma_db)
# Por defecto apunta a <repo_root>/data/chroma_db cuando se ejecuta localmente
_repo_root = pathlib.Path(__file__).parent.parent
DB_PATH     = os.getenv("DB_PATH", str(_repo_root / "data" / "chroma_db"))

CHUNK_SIZE    = 800    # tokens aprox
CHUNK_OVERLAP = 100
TOP_K         = 5      # chunks a recuperar por query

client = OpenAI(
    api_key=NAN_API_KEY,
    base_url=NAN_BASE_URL,
)

RFCS = {
    "rfc6749": {
        "url":         "https://www.rfc-editor.org/rfc/rfc6749.txt",
        "title":       "OAuth 2.0 Authorization Framework",
        "description": "El estándar principal de OAuth 2.0",
    },
    "rfc6750": {
        "url":         "https://www.rfc-editor.org/rfc/rfc6750.txt",
        "title":       "OAuth 2.0 Bearer Token Usage",
        "description": "Cómo usar Bearer tokens en OAuth",
    },
    "rfc7636": {
        "url":         "https://www.rfc-editor.org/rfc/rfc7636.txt",
        "title":       "PKCE for OAuth Public Clients",
        "description": "Proof Key for Code Exchange",
    },
    "rfc8414": {
        "url":         "https://www.rfc-editor.org/rfc/rfc8414.txt",
        "title":       "OAuth 2.0 Authorization Server Metadata",
        "description": "Discovery endpoint de OAuth",
    },
    "rfc7519": {
        "url":         "https://www.rfc-editor.org/rfc/rfc7519.txt",
        "title":       "JSON Web Token (JWT)",
        "description": "El estándar JWT",
    },
    "rfc9110": {
        "url":         "https://www.rfc-editor.org/rfc/rfc9110.txt",
        "title":       "HTTP Semantics",
        "description": "Semántica de HTTP/1.1 y HTTP/2",
    },
    "rfc8446": {
        "url":         "https://www.rfc-editor.org/rfc/rfc8446.txt",
        "title":       "TLS 1.3",
        "description": "Transport Layer Security versión 1.3",
    },
}

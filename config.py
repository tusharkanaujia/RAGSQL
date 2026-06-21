"""Central config — values come from environment / .env (never hardcode secrets)."""
import os
from dotenv import load_dotenv

load_dotenv()

CONN_STR = os.environ.get(
    "LBS_CONN_STR",
    "DRIVER={ODBC Driver 17 for SQL Server};SERVER=YOUR_SERVER;"
    "DATABASE=SputnikCube;Trusted_Connection=yes;",
)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

# Neo4j (optional — only for multi-hop relational questions: legal-entity /
# netting / collateral chains). Leave NEO4J_PASSWORD empty to disable the graph.
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

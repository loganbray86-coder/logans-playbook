"""Private-league ESPN cookies for friends. Encrypted with Fernet; never logged, never returned.

The client only ever holds an opaque token. The token IS the Fernet ciphertext of
{lid, s2, swid}, so nothing is lost when Render restarts or redeploys (its disk is wiped),
and a token can only unlock the one league it was created for.
Key: env FERNET_KEY (urlsafe base64, 32 bytes). Without it (local dev only) a key is
generated once into cache/.fernet_key; on Render that file would vanish on restart, so
set FERNET_KEY there."""
import json, os, re, time, hashlib
from cryptography.fernet import Fernet, InvalidToken

BASE = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(BASE, "cache", ".fernet_key")
MAX_AGE = 400 * 86400          # tokens older than ~13 months stop working (new season = reconnect)
_f = None

def _fernet():
    global _f
    if _f: return _f
    k = os.environ.get("FERNET_KEY", "").strip()
    if not k:
        try: k = open(KEY_PATH).read().strip()
        except Exception:
            k = Fernet.generate_key().decode()
            os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
            fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh: fh.write(k)
    _f = Fernet(k.encode())
    return _f

def key_source():
    return "env" if os.environ.get("FERNET_KEY", "").strip() else "local-file"

def clean_s2(v):
    v = str(v or "").strip().strip('"').strip("'")
    v = re.sub(r"^espn_s2\s*[=:]\s*", "", v, flags=re.I).strip()
    return v if 40 <= len(v) <= 2000 and re.fullmatch(r"[A-Za-z0-9%+/=_.\-]+", v) else None

def clean_swid(v):
    v = str(v or "").strip().strip('"').strip("'")
    v = re.sub(r"^swid\s*[=:]\s*", "", v, flags=re.I).strip().strip("{}").upper()
    return "{%s}" % v if re.fullmatch(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", v) else None

def seal(lid, s2, swid):
    return _fernet().encrypt(json.dumps({"lid": int(lid), "s2": s2, "swid": swid, "v": 1}).encode()).decode()

def open_token(tok, lid):
    """Cookies for this league, or None if the token is missing/forged/for another league."""
    if not tok or len(tok) > 4000: return None
    try: d = json.loads(_fernet().decrypt(tok.encode(), ttl=MAX_AGE))
    except (InvalidToken, ValueError, TypeError): return None
    if int(d.get("lid", -1)) != int(lid): return None
    return {"espn_s2": d["s2"], "SWID": d["swid"]}

def tag(tok):
    """Short non-reversible id for cache keys (never the token itself)."""
    return hashlib.sha256(tok.encode()).hexdigest()[:16] if tok else "pub"

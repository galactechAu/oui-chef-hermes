"""Source classification, URL safety and recipe-import prompt construction."""
import base64
import binascii
import ipaddress
import socket
from urllib.parse import urlparse, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener


def classify_source(*, url: str = "", text: str = "", image_data: str = "") -> str:
    supplied = sum(bool(str(value).strip()) for value in (url, text, image_data))
    if supplied != 1:
        raise ValueError("provide exactly one: a URL, pasted text, or an image")
    if image_data:
        return "image"
    if text:
        return "text"
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith("instagram.com"):
        return "instagram"
    return "webpage"


def validate_public_url(value: str) -> str:
    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("use a public http or https recipe URL")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".lan", ".ts.net")):
        raise ValueError("local and private network URLs cannot be imported")
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror:
        raise ValueError("the recipe URL host could not be resolved")
    try:
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("local and private network URLs cannot be imported")
    except ValueError as error:
        if str(error).startswith("local and private"):
            raise
        raise ValueError("the recipe URL did not resolve to a public address")
    return parsed.geturl()


class _SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return super().redirect_request(request, fp, code, msg, headers, validate_public_url(urljoin(request.full_url, newurl)))


def validate_public_redirects(value: str, timeout: int = 8) -> str:
    """Follow a small public redirect chain without ever following a private hop."""
    start = validate_public_url(value)
    response = build_opener(_SafeRedirects()).open(Request(start, method="HEAD", headers={"User-Agent": "Oui-Chef/1.0"}), timeout=timeout)
    try:
        return validate_public_url(response.geturl())
    finally:
        response.close()


def fetch_public_source_text(value: str, timeout: int = 12, limit_bytes: int = 1_500_000) -> str:
    """Fetch bounded, public HTML text only after validating every redirect hop."""
    final_url = validate_public_redirects(value, timeout=min(timeout, 8))
    response = build_opener(_SafeRedirects()).open(Request(final_url, headers={"User-Agent": "Oui-Chef/1.0"}), timeout=timeout)
    try:
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "html" not in content_type and "text" not in content_type:
            raise ValueError("recipe source did not return readable public text")
        raw = response.read(limit_bytes + 1)
        if len(raw) > limit_bytes:
            raise ValueError("recipe source page is too large to verify safely")
        return raw.decode("utf-8", "replace")
    finally:
        response.close()


def recipe_import_prompt(source_type: str, content: str) -> str:
    source_note = {
        "webpage": "Inspect this public recipe webpage URL",
        "youtube": "Inspect this public YouTube URL. Use a public transcript, captions, description, or clearly available recipe details; if those do not contain enough recipe information, return an error instead of inventing it",
        "instagram": "Inspect this public Instagram URL. Use only publicly accessible caption/on-page content; if login or access restrictions prevent enough detail, return an error instead of inventing it",
        "text": "Turn this pasted recipe text into a structured cooking recipe",
        "image": "Turn OCR text from this recipe image into a structured cooking recipe",
    }.get(source_type, "Inspect this recipe source")
    return f'''{source_note}: {content}\n\nReturn ONLY a compact JSON object: {{"name":"", "summary":"", "image_url":"direct public image URL or empty", "complexity":"Easy|Moderate|Advanced", "prep_time_min":integer or null, "cook_time_min":integer or null, "health_rating":integer 1-5, "ingredients":["short item"], "steps":["imperative cooking step"]}}. Do not invent facts or quantities absent from the source. Preserve every preparation action required to cook safely: state whether ingredients are chopped, diced, sliced, minced, grated, trimmed, drained, or measured before it is used. Each step must be standalone and say the heat, timing, quantities and doneness cue when the source provides them. Reject recipes containing mushrooms or mushroom-derived ingredients; return {{"error":"..."}} instead. Health rating is Meal Planner's fat-loss fit: 5=high-protein, lower-carb, low-added-sugar and fat-conscious; 1=poor fit. Keep at most 15 ingredients and 18 steps. No markdown.'''


def decode_image_data(value: str, limit_bytes: int = 8 * 1024 * 1024) -> tuple[str, bytes]:
    try:
        header, encoded = value.split(",", 1)
        if not header.startswith("data:image/") or ";base64" not in header:
            raise ValueError
        mime = header[5:].split(";", 1)[0].lower()
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("upload a valid PNG, JPEG, WEBP, or GIF image")
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"} or not raw or len(raw) > limit_bytes:
        raise ValueError("image must be PNG, JPEG, WEBP, or GIF and no larger than 8 MB")
    return mime, raw

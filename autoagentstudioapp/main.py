import html
import ipaddress
import os
import re
import json
import hashlib
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, parse_qs
from urllib.request import Request as URLRequest, urlopen

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from agent import generate_app
from auth_utils import hash_password, verify_password
from database import ENGINE, Base, get_db
from models import AppVersion, User

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

GENERATED_APPS_ROOT = Path(os.getenv("GENERATED_APPS_ROOT", "generated_apps"))
GENERATED_APPS_ROOT.mkdir(parents=True, exist_ok=True)

IMAGE_CACHE_ROOT = Path(os.getenv("IMAGE_CACHE_ROOT", "generated_image_cache"))
IMAGE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-this-secret-in-production")
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 8)))
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)


class PromptRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    app_name: str | None = None
    source_app_id: int | None = None


@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=ENGINE)


# Ensures DB tables are available even in tooling contexts where startup events are skipped.
Base.metadata.create_all(bind=ENGINE)


def set_notification(request: Request, message: str, level: str = "success") -> None:
    request.session["notification"] = {"message": message, "level": level}


def pop_notification(request: Request):
    return request.session.pop("notification", None)


def get_current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def generate_default_app_name(db: Session, user_id: int) -> str:
    app_count = db.query(func.count(AppVersion.id)).filter(AppVersion.user_id == user_id).scalar() or 0
    return f"App #{app_count + 1}"


def save_generated_files(user_id: int, app_id: int, version_number: int, html_code: str) -> str:
    app_output_dir = GENERATED_APPS_ROOT / f"user_{user_id}" / f"app_{app_id}_v{version_number}"
    app_output_dir.mkdir(parents=True, exist_ok=True)
    (app_output_dir / "index.html").write_text(html_code, encoding="utf-8")
    return str(app_output_dir.resolve())


def sanitize_filename(value: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return candidate or "app"


DEFAULT_IMAGE_WIDTH = 1280
DEFAULT_IMAGE_HEIGHT = 720
MAX_IMAGE_DIMENSION = 2200
IMAGE_QUERY_STOPWORDS = {
    "image",
    "images",
    "photo",
    "photos",
    "picture",
    "pictures",
    "app",
    "website",
    "web",
    "page",
    "design",
    "create",
    "generate",
    "show",
    "with",
    "for",
    "the",
    "and",
    "from",
    "that",
    "this",
    "make",
    "need",
    "high",
    "quality",
}
IMAGE_PATH_STOPWORDS = {
    "random",
    "featured",
    "image",
    "images",
    "photo",
    "photos",
    "upload",
    "thumb",
    "thumbnail",
    "download",
    "seed",
    "w",
    "h",
    "width",
    "height",
    "fit",
    "crop",
    "auto",
    "format",
    "raw",
}
IMAGE_HOST_HINTS = {
    "source.unsplash.com",
    "images.unsplash.com",
    "picsum.photos",
    "loremflickr.com",
    "upload.wikimedia.org",
    "images.pexels.com",
    "cdn.pixabay.com",
    "img.icons8.com",
}
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp", ".avif")
IMAGE_FEATURE_HINTS = (
    "image",
    "images",
    "photo",
    "photos",
    "gallery",
    "wallpaper",
    "portfolio",
    "travel",
    "fashion",
    "food",
    "art",
    "nature",
    "product",
)


def sanitize_image_query(query: str | None) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]+", " ", (query or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "creative ai visual"
    return cleaned[:90]


def normalize_image_dimensions(width: int | None, height: int | None) -> tuple[int, int]:
    safe_width = width or DEFAULT_IMAGE_WIDTH
    safe_height = height or DEFAULT_IMAGE_HEIGHT
    safe_width = max(200, min(MAX_IMAGE_DIMENSION, int(safe_width)))
    safe_height = max(200, min(MAX_IMAGE_DIMENSION, int(safe_height)))
    return safe_width, safe_height


def build_image_placeholder_svg(label: str = "Image unavailable") -> str:
    safe_label = sanitize_image_query(label)[:60]
    safe_label = html.escape(safe_label)
    return f"""
    <svg xmlns='http://www.w3.org/2000/svg' width='1200' height='800' viewBox='0 0 1200 800'>
      <defs>
        <linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>
          <stop offset='0%' stop-color='#0ea5e9'/>
          <stop offset='50%' stop-color='#7c3aed'/>
          <stop offset='100%' stop-color='#f97316'/>
        </linearGradient>
      </defs>
      <rect width='1200' height='800' fill='url(#g)'/>
      <circle cx='180' cy='140' r='90' fill='rgba(255,255,255,0.18)'/>
      <circle cx='1050' cy='650' r='120' fill='rgba(255,255,255,0.14)'/>
      <text x='600' y='330' text-anchor='middle' font-size='64' font-family='Segoe UI, Arial' fill='white'>Image Preview</text>
      <text x='600' y='420' text-anchor='middle' font-size='34' font-family='Segoe UI, Arial' fill='white'>{safe_label}</text>
      <text x='600' y='500' text-anchor='middle' font-size='24' font-family='Segoe UI, Arial' fill='rgba(255,255,255,0.9)'>AutoAgent Studio fallback image</text>
    </svg>
    """.strip()


def build_image_placeholder_data_uri(label: str = "Image unavailable") -> str:
    return f"data:image/svg+xml;charset=UTF-8,{quote(build_image_placeholder_svg(label))}"


def parse_int_value(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d.]+", "", value)
    if not cleaned:
        return None
    try:
        parsed = int(float(cleaned))
        if parsed <= 0:
            return None
        return parsed
    except ValueError:
        return None


def build_media_image_url(
    query: str | None = None,
    source_url: str | None = None,
    width: int | None = None,
    height: int | None = None,
    seed: str | None = None,
) -> str:
    normalized_width, normalized_height = normalize_image_dimensions(width, height)
    params: dict[str, str] = {
        "w": str(normalized_width),
        "h": str(normalized_height),
    }
    if query:
        params["q"] = sanitize_image_query(query)
    if source_url:
        params["src"] = source_url
    if seed:
        params["seed"] = seed[:24]
    return f"/media/image?{urlencode(params)}"


def get_html_attr(tag: str, attr_name: str) -> str | None:
    pattern = re.compile(
        rf'\b{re.escape(attr_name)}\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
        re.IGNORECASE,
    )
    match = pattern.search(tag)
    if not match:
        return None
    return match.group(1) or match.group(2) or match.group(3)


def set_html_attr(tag: str, attr_name: str, attr_value: str) -> str:
    quoted_value = attr_value.replace('"', "&quot;")
    attr_pattern = re.compile(
        rf'\b{re.escape(attr_name)}\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)',
        re.IGNORECASE,
    )

    if attr_pattern.search(tag):
        return attr_pattern.sub(f'{attr_name}="{quoted_value}"', tag, count=1)

    if tag.endswith("/>"):
        return tag[:-2] + f' {attr_name}="{quoted_value}" />'
    return tag[:-1] + f' {attr_name}="{quoted_value}">'


def get_query_hint_from_text(text: str | None) -> str:
    tokens = []
    for token in re.findall(r"[a-zA-Z0-9]+", (text or "").lower()):
        if len(token) < 3:
            continue
        if token in IMAGE_QUERY_STOPWORDS:
            continue
        tokens.append(token)
    if not tokens:
        return ""
    return sanitize_image_query(" ".join(tokens[:7]))


def get_query_hint_from_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    collected: list[str] = []
    query_params = parse_qs(parsed.query)
    for key in ("q", "query", "keyword", "keywords", "search", "term", "text"):
        collected.extend(query_params.get(key, []))

    for token in re.split(r"[\/_,\-]+", parsed.path.lower()):
        if len(token) < 3 or token in IMAGE_PATH_STOPWORDS:
            continue
        if token.isdigit():
            continue
        collected.append(token)

    return get_query_hint_from_text(" ".join(collected))


def normalize_remote_source_url(url: str | None) -> str | None:
    if not url:
        return None
    candidate = url.strip()
    if not candidate:
        return None
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None

    hostname = (parsed.hostname or "").lower()
    if hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return None

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            return None
    except ValueError:
        pass

    return candidate


def is_likely_image_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if hostname in IMAGE_HOST_HINTS:
        return True
    if path.endswith(IMAGE_EXTENSIONS):
        return True
    query_text = (parsed.query or "").lower()
    if any(marker in query_text for marker in ("auto=format", "fit=crop", "img", "image", "photo")):
        return True
    if any(marker in hostname for marker in ("unsplash", "wikimedia", "picsum", "flickr", "pexels", "pixabay")):
        return True
    return False


def rewrite_problematic_image_url(
    url: str | None,
    query_hint: str | None = None,
    width: int | None = None,
    height: int | None = None,
    force_proxy: bool = False,
) -> str:
    if not url:
        return ""
    cleaned = url.strip()
    if cleaned.startswith("/media/image?"):
        return cleaned
    if cleaned.lower().startswith(("data:", "blob:")):
        return cleaned

    normalized = normalize_remote_source_url(cleaned)
    safe_query = sanitize_image_query(query_hint or get_query_hint_from_url(cleaned))
    normalized_width, normalized_height = normalize_image_dimensions(width, height)

    if not normalized:
        seed = hashlib.sha256(f"{cleaned}|{safe_query}".encode("utf-8")).hexdigest()[:12]
        return build_media_image_url(
            query=safe_query,
            width=normalized_width,
            height=normalized_height,
            seed=seed,
        )

    if not force_proxy and not is_likely_image_url(normalized):
        return normalized

    seed = hashlib.sha256(f"{normalized}|{safe_query}".encode("utf-8")).hexdigest()[:12]
    return build_media_image_url(
        query=safe_query,
        source_url=normalized,
        width=normalized_width,
        height=normalized_height,
        seed=seed,
    )


def fetch_remote_json(url: str, timeout: float = 2.5) -> dict | None:
    try:
        request = URLRequest(
            url,
            headers={
                "User-Agent": "AutoAgentStudio/1.0",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return json.loads(payload)
    except Exception:
        return None


def fetch_remote_image_bytes(url: str, timeout: float = 3.5) -> tuple[bytes, str] | None:
    normalized = normalize_remote_source_url(url)
    if not normalized:
        return None
    try:
        request = URLRequest(
            normalized,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AutoAgentStudio/1.0)",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://autoagentstudio.local/",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            raw_content_type = response.headers.get("Content-Type", "application/octet-stream")
            content_type = raw_content_type.split(";")[0].strip().lower()
            body = response.read()
            if not body:
                return None
            if not content_type.startswith("image/"):
                return None
            return body, content_type
    except Exception:
        return None


def build_image_cache_paths(cache_key: str) -> tuple[Path, Path]:
    return IMAGE_CACHE_ROOT / f"{cache_key}.bin", IMAGE_CACHE_ROOT / f"{cache_key}.json"


def load_cached_image(cache_key: str) -> tuple[bytes, str] | None:
    content_path, meta_path = build_image_cache_paths(cache_key)
    if not content_path.exists() or not meta_path.exists():
        return None
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        media_type = metadata.get("media_type", "image/svg+xml")
        if not media_type.startswith("image/"):
            media_type = "image/svg+xml"
        return content_path.read_bytes(), media_type
    except Exception:
        return None


def save_cached_image(cache_key: str, image_bytes: bytes, media_type: str) -> None:
    content_path, meta_path = build_image_cache_paths(cache_key)
    try:
        content_path.write_bytes(image_bytes)
        meta_path.write_text(json.dumps({"media_type": media_type}), encoding="utf-8")
    except Exception:
        pass


def find_wikimedia_image_url(query: str, width: int, height: int, seed: str) -> str | None:
    search_query = sanitize_image_query(query)
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrnamespace": "6",
        "gsrlimit": "8",
        "gsrsearch": f"filetype:bitmap {search_query}",
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": str(width),
        "iiurlheight": str(height),
    }
    api_url = f"https://commons.wikimedia.org/w/api.php?{urlencode(params)}"
    payload = fetch_remote_json(api_url)
    if not payload:
        return None

    pages = payload.get("query", {}).get("pages", {})
    candidates: list[str] = []
    for page in pages.values():
        image_info = page.get("imageinfo") or []
        if not image_info:
            continue
        image_url = image_info[0].get("thumburl") or image_info[0].get("url")
        if image_url:
            candidates.append(image_url)

    if not candidates:
        return None

    seed_int = int(seed, 16) if seed else 0
    return candidates[seed_int % len(candidates)]


def resolve_image_bytes(
    query: str,
    source_url: str | None,
    width: int,
    height: int,
    seed: str,
) -> tuple[bytes, str]:
    candidates: list[str] = []
    if source_url:
        candidates.append(source_url)

    # Querying Wikimedia helps with relevant matches, but skip this extra request
    # when a direct source URL is already provided.
    if not source_url:
        wikimedia_candidate = find_wikimedia_image_url(query, width, height, seed)
        if wikimedia_candidate:
            candidates.append(wikimedia_candidate)

    tag_query = ",".join(sanitize_image_query(query).split()[:4]) or "creative,ai"
    seed_int = int(seed, 16) % 100000 if seed else 1
    candidates.append(f"https://loremflickr.com/{width}/{height}/{tag_query}?lock={seed_int}")
    candidates.append(f"https://picsum.photos/seed/{seed[:12] or 'autoagent'}/{width}/{height}")

    for candidate in candidates:
        fetched = fetch_remote_image_bytes(candidate)
        if fetched:
            return fetched

    placeholder_svg = build_image_placeholder_svg(query or "Image unavailable")
    return placeholder_svg.encode("utf-8"), "image/svg+xml"


def extract_img_dimensions(tag: str) -> tuple[int, int]:
    width = parse_int_value(get_html_attr(tag, "width"))
    height = parse_int_value(get_html_attr(tag, "height"))
    return normalize_image_dimensions(width, height)


def should_add_visual_gallery(prompt_query: str, generated_html: str) -> bool:
    if re.search(r"<img\b", generated_html, flags=re.IGNORECASE):
        return False
    if re.search(r"background-image\s*:\s*url\(", generated_html, flags=re.IGNORECASE):
        return False
    prompt_text = (prompt_query or "").lower()
    return any(hint in prompt_text for hint in IMAGE_FEATURE_HINTS)


def inject_visual_gallery(generated_html: str, prompt_query: str) -> str:
    query = prompt_query or "creative ai visual"
    cards = []
    for idx, suffix in enumerate(("hero visual", "concept art", "inspiration"), start=1):
        seed = hashlib.sha256(f"{query}|{suffix}|{idx}".encode("utf-8")).hexdigest()[:12]
        image_url = build_media_image_url(query=f"{query} {suffix}", seed=seed)
        cards.append(
            f"""
            <article class="aas-gallery-card">
                <img src="{image_url}" alt="{html.escape(query)} preview {idx}" loading="lazy" decoding="async">
            </article>
            """.strip()
        )

    gallery_html = f"""
    <style id="aas-gallery-style">
      .aas-gallery {{
        margin: 28px auto 20px;
        width: min(1200px, calc(100% - 24px));
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 14px;
      }}
      .aas-gallery-card {{
        border-radius: 14px;
        overflow: hidden;
        box-shadow: 0 10px 24px rgba(0,0,0,0.22);
      }}
      .aas-gallery-card img {{
        width: 100%;
        height: 100%;
        min-height: 220px;
        object-fit: cover;
        display: block;
      }}
    </style>
    <section class="aas-gallery">
      {"".join(cards)}
    </section>
    """

    if "</body>" in generated_html.lower():
        return re.sub(r"</body>", f"{gallery_html}</body>", generated_html, count=1, flags=re.IGNORECASE)
    return f"{generated_html}\n{gallery_html}"


def enhance_generated_html(generated_html: str, prompt: str = "") -> str:
    if not generated_html:
        return generated_html

    prompt_query = get_query_hint_from_text(prompt) or sanitize_image_query(prompt)

    def replace_remote_image_literal(match: re.Match) -> str:
        candidate = match.group(0)
        if candidate.startswith("/media/image?"):
            return candidate
        if not is_likely_image_url(candidate):
            return candidate
        return rewrite_problematic_image_url(candidate, prompt_query, force_proxy=True)

    generated_html = re.sub(
        r"https?://[^\s\"'<>)]+" ,
        replace_remote_image_literal,
        generated_html,
        flags=re.IGNORECASE,
    )
    generated_html = re.sub(
        r"//source\.unsplash\.com/[^\s\"'<>)]+" ,
        lambda match: rewrite_problematic_image_url(match.group(0), prompt_query, force_proxy=True),
        generated_html,
        flags=re.IGNORECASE,
    )

    def rewrite_css_url(match: re.Match) -> str:
        quote_char = match.group(1) or ""
        raw_value = (match.group(2) or "").strip()
        lower_value = raw_value.lower()
        if not raw_value or lower_value.startswith(("data:", "blob:", "javascript:", "#")):
            return match.group(0)

        likely_image = is_likely_image_url(raw_value) or lower_value.endswith(IMAGE_EXTENSIONS)
        if not likely_image and raw_value.startswith(("http://", "https://")):
            return match.group(0)

        rewritten = rewrite_problematic_image_url(raw_value, prompt_query, force_proxy=True)
        return f"url({quote_char}{rewritten}{quote_char})"

    generated_html = re.sub(
        r"url\(\s*(['\"]?)([^)\"']+)\1\s*\)",
        rewrite_css_url,
        generated_html,
        flags=re.IGNORECASE,
    )

    def patch_img_tag(match: re.Match) -> str:
        tag = match.group(0)
        src = (get_html_attr(tag, "src") or "").strip()
        alt = (get_html_attr(tag, "alt") or "Generated image").strip()
        width, height = extract_img_dimensions(tag)
        query_hint = get_query_hint_from_text(f"{alt} {prompt_query}") or prompt_query
        fallback = build_image_placeholder_data_uri(alt or "Generated image")

        rewritten_src = rewrite_problematic_image_url(
            src,
            query_hint=query_hint,
            width=width,
            height=height,
            force_proxy=True,
        )
        if not rewritten_src:
            rewritten_src = build_media_image_url(query=query_hint, width=width, height=height)

        tag = set_html_attr(tag, "src", rewritten_src)
        tag = set_html_attr(tag, "loading", get_html_attr(tag, "loading") or "lazy")
        tag = set_html_attr(tag, "decoding", get_html_attr(tag, "decoding") or "async")
        tag = set_html_attr(tag, "referrerpolicy", get_html_attr(tag, "referrerpolicy") or "no-referrer")
        tag = set_html_attr(
            tag,
            "onerror",
            f"this.onerror=null;this.src='{fallback}';",
        )
        return tag

    generated_html = re.sub(r"<img\b[^>]*>", patch_img_tag, generated_html, flags=re.IGNORECASE)

    if 'id="aas-fullscreen-base"' not in generated_html:
        base_style = """
        <style id="aas-fullscreen-base">
          html, body { margin: 0; min-height: 100%; width: 100%; }
          body { min-height: 100vh; }
        </style>
        """.strip()
        if "</head>" in generated_html.lower():
            generated_html = re.sub(r"</head>", f"{base_style}</head>", generated_html, count=1, flags=re.IGNORECASE)
        else:
            generated_html = f"{base_style}\n{generated_html}"

    if should_add_visual_gallery(prompt_query, generated_html):
        generated_html = inject_visual_gallery(generated_html, prompt_query)

    return generated_html


@app.get("/media/image")
async def media_image(
    q: str = "",
    src: str | None = None,
    w: int = DEFAULT_IMAGE_WIDTH,
    h: int = DEFAULT_IMAGE_HEIGHT,
    seed: str | None = None,
):
    width, height = normalize_image_dimensions(w, h)
    query = sanitize_image_query(q or get_query_hint_from_url(src))
    source_url = normalize_remote_source_url(src)
    stable_seed = (seed or hashlib.sha256(f"{query}|{source_url or ''}|{width}x{height}".encode("utf-8")).hexdigest())[:24]
    cache_key = hashlib.sha256(
        f"{query}|{source_url or ''}|{width}|{height}|{stable_seed}".encode("utf-8")
    ).hexdigest()

    cached = load_cached_image(cache_key)
    if cached:
        image_bytes, media_type = cached
        return Response(
            content=image_bytes,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=43200"},
        )

    image_bytes, media_type = resolve_image_bytes(query, source_url, width, height, stable_seed)
    save_cached_image(cache_key, image_bytes, media_type)
    return Response(
        content=image_bytes,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=43200"},
    )


def cleanup_temp_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


@app.get("/")
async def root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "notification": pop_notification(request)},
    )


@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == normalize_email(email)).first()
    if not user or not verify_password(password, user.password):
        set_notification(request, "Invalid credentials", "error")
        return RedirectResponse(url="/login", status_code=303)

    request.session.clear()
    request.session["user_id"] = user.id
    request.session["user_name"] = user.name
    set_notification(request, "Login successful", "success")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "notification": pop_notification(request)},
    )


@app.post("/register")
async def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    clean_name = (name or "").strip()
    clean_email = normalize_email(email)

    if not clean_name or not clean_email or not password:
        set_notification(request, "All fields are required.", "error")
        return RedirectResponse(url="/register", status_code=303)

    if password != confirm_password:
        set_notification(request, "Passwords do not match.", "error")
        return RedirectResponse(url="/register", status_code=303)

    existing_user = db.query(User).filter(User.email == clean_email).first()
    if existing_user:
        set_notification(request, "Email is already registered.", "error")
        return RedirectResponse(url="/register", status_code=303)

    user = User(name=clean_name, email=clean_email, password=hash_password(password))
    db.add(user)
    db.commit()
    set_notification(request, "Registration successful. Please log in.", "success")
    return RedirectResponse(url="/login", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    set_notification(request, "You have been logged out.", "success")
    return RedirectResponse(url="/login", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        set_notification(request, "Please log in to continue.", "error")
        return RedirectResponse(url="/login", status_code=303)

    total_apps = db.query(func.count(AppVersion.id)).filter(AppVersion.user_id == user.id).scalar() or 0
    latest_app = (
        db.query(AppVersion)
        .filter(AppVersion.user_id == user.id)
        .order_by(AppVersion.created_at.desc(), AppVersion.id.desc())
        .first()
    )

    return templates.TemplateResponse(
        "dashboard_home.html",
        {
            "request": request,
            "user": user,
            "total_apps": total_apps,
            "latest_app": latest_app,
            "notification": pop_notification(request),
        },
    )


@app.get("/generate-studio", response_class=HTMLResponse)
async def generate_studio(
    request: Request,
    source_app_id: int | None = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        set_notification(request, "Please log in to continue.", "error")
        return RedirectResponse(url="/login", status_code=303)

    selected_app = None
    if source_app_id is not None:
        selected_app = (
            db.query(AppVersion)
            .filter(AppVersion.id == source_app_id, AppVersion.user_id == user.id)
            .first()
        )

    recent_apps = (
        db.query(AppVersion)
        .filter(AppVersion.user_id == user.id)
        .order_by(AppVersion.created_at.desc(), AppVersion.id.desc())
        .limit(8)
        .all()
    )

    return templates.TemplateResponse(
        "generate_studio.html",
        {
            "request": request,
            "user": user,
            "selected_app": selected_app,
            "recent_apps": recent_apps,
            "notification": pop_notification(request),
        },
    )


@app.get("/apps-studio", response_class=HTMLResponse)
async def apps_studio(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        set_notification(request, "Please log in to continue.", "error")
        return RedirectResponse(url="/login", status_code=303)

    search_query = (q or "").strip()
    apps_query = db.query(AppVersion).filter(AppVersion.user_id == user.id)

    if search_query:
        search_pattern = f"%{search_query}%"
        apps_query = apps_query.filter(
            or_(
                AppVersion.app_name.ilike(search_pattern),
                AppVersion.prompt.ilike(search_pattern),
            )
        )

    apps = apps_query.order_by(AppVersion.created_at.desc(), AppVersion.id.desc()).all()
    app_lookup = [{"id": app.id, "app_name": app.app_name, "prompt": app.prompt} for app in apps]

    return templates.TemplateResponse(
        "apps_studio.html",
        {
            "request": request,
            "user": user,
            "apps": apps,
            "apps_lookup": app_lookup,
            "search_query": search_query,
            "notification": pop_notification(request),
        },
    )


@app.post("/generate")
async def generate(
    data: PromptRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": "Unauthorized. Please log in first."},
        )

    prompt = (data.prompt or "").strip()
    if not prompt:
        return {"success": False, "error": "Prompt is required."}

    app_name = (data.app_name or "").strip()
    source_app = None

    if data.source_app_id is not None:
        source_app = (
            db.query(AppVersion)
            .filter(
                AppVersion.id == data.source_app_id,
                AppVersion.user_id == user.id,
            )
            .first()
        )
        if not source_app:
            return {"success": False, "error": "Selected app was not found."}
        if not app_name:
            app_name = source_app.app_name

    if not app_name:
        app_name = generate_default_app_name(db, user.id)

    version_number = 1
    if source_app:
        max_version = (
            db.query(func.max(AppVersion.version_number))
            .filter(
                AppVersion.user_id == user.id,
                AppVersion.app_name == app_name,
            )
            .scalar()
        )
        version_number = (max_version or 0) + 1

    try:
        generated_code = generate_app(prompt)
        generated_code = enhance_generated_html(generated_code, prompt)

        app_record = AppVersion(
            user_id=user.id,
            app_name=app_name,
            prompt=prompt,
            version_number=version_number,
            source_app_id=source_app.id if source_app else None,
        )
        db.add(app_record)
        db.commit()
        db.refresh(app_record)

        app_record.output_dir = save_generated_files(
            user_id=user.id,
            app_id=app_record.id,
            version_number=version_number,
            html_code=generated_code,
        )
        db.commit()

        return {
            "success": True,
            "code": generated_code,
            "notification": "App created successfully",
            "app": {
                "id": app_record.id,
                "app_name": app_record.app_name,
                "version_number": app_record.version_number,
                "created_at": app_record.created_at.isoformat() if app_record.created_at else None,
                "preview_url": f"/apps/{app_record.id}/view",
                "download_url": f"/apps/{app_record.id}/download",
            },
        }
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}


@app.get("/apps/{app_id}/view", response_class=HTMLResponse)
async def view_app(
    app_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    app_record = (
        db.query(AppVersion)
        .filter(AppVersion.id == app_id, AppVersion.user_id == user.id)
        .first()
    )
    if not app_record:
        raise HTTPException(status_code=404, detail="App not found.")

    if not app_record.output_dir:
        raise HTTPException(status_code=404, detail="No generated files found for this app.")

    app_index = Path(app_record.output_dir) / "index.html"
    if not app_index.exists() or not app_index.is_file():
        raise HTTPException(status_code=404, detail="Generated app file is missing.")

    return HTMLResponse(content=app_index.read_text(encoding="utf-8"))


@app.get("/apps/{app_id}/download")
async def download_app(
    app_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    app_record = (
        db.query(AppVersion)
        .filter(AppVersion.id == app_id, AppVersion.user_id == user.id)
        .first()
    )
    if not app_record:
        raise HTTPException(status_code=404, detail="App not found.")

    if not app_record.output_dir:
        raise HTTPException(status_code=404, detail="No generated files found for this app.")

    app_output_dir = Path(app_record.output_dir)
    if not app_output_dir.exists() or not app_output_dir.is_dir():
        raise HTTPException(status_code=404, detail="App output directory is missing.")

    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_zip.close()

    with zipfile.ZipFile(temp_zip.name, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in app_output_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(app_output_dir))

    download_name = f"{sanitize_filename(app_record.app_name)}-v{app_record.version_number}.zip"
    return FileResponse(
        path=temp_zip.name,
        media_type="application/zip",
        filename=download_name,
        background=BackgroundTask(cleanup_temp_file, temp_zip.name),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000)

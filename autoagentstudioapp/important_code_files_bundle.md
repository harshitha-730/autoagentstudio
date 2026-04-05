# Important Code Files Bundle

This file consolidates key project source files (excluding instructions.md and .env secrets).

## main.py
```py
import os
import re
import tempfile
import zipfile
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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

```

## agent.py
```py
import google.generativeai as genai
from dotenv import load_dotenv
import os
import time

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def generate_app(user_prompt, max_retries=3):
    """
    Generate an HTML app from a user prompt using Google Generative AI.
    Includes retry logic for rate limit handling.
    """
    system_instruction = """
    You are an expert web developer AI agent.
    When given a description of an app, you generate a complete, 
    working single-file HTML application with:
    - Clean HTML structure
    - CSS styling (modern, beautiful UI)
    - JavaScript functionality
    
    IMPORTANT RULES:
    - Return ONLY the raw HTML code
    - No explanations, no markdown, no code blocks
    - Everything in one single HTML file
    - Make it fully functional and good looking
    """
    
    full_prompt = f"{system_instruction}\n\nCreate this app: {user_prompt}"
    
    # Get the first available model
    try:
        models = genai.list_models()
        available_models = []
        for model in models:
            if "generateContent" in model.supported_generation_methods:
                # Remove 'models/' prefix for GenerativeModel
                model_name = model.name.replace("models/", "")
                available_models.append(model_name)
        
        if not available_models:
            return """
            <html>
            <head><title>No Models Available</title></head>
            <body style="background: #f0f0f0; font-family: Arial;">
            <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
                <h1>No Models Available</h1>
                <p>No generative models are available for your API key. Please check your Google Cloud project configuration.</p>
            </div>
            </body>
            </html>
            """
        
        # Use the first available model (usually the fastest/cheapest)
        model_name = available_models[0]
        print(f"Using available model: {model_name}")
        print(f"Other available models: {available_models[1:5]}")  # Show first 4 alternatives
        
    except Exception as e:
        print(f"Error listing models: {e}")
        # Fallback to a known model
        model_name = "gemini-2.0-flash"
        print(f"Using fallback model: {model_name}")
    
    try:
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        print(f"Error initializing model {model_name}: {e}")
        return f"""
        <html>
        <head><title>Model Initialization Error</title></head>
        <body style="background: #f0f0f0; font-family: Arial;">
        <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
            <h1>Model Initialization Failed</h1>
            <p>Could not initialize the generative model: {model_name}</p>
            <p style="color: #666; font-size: 0.9em;">{str(e)}</p>
        </div>
        </body>
        </html>
        """
    
    for attempt in range(max_retries):
        try:
            print(f"Generating content with {model_name} (attempt {attempt + 1}/{max_retries})...")
            response = model.generate_content(full_prompt)
            print("Content generated successfully!")
            return response.text
        except Exception as e:
            error_message = str(e)
            print(f"Error on attempt {attempt + 1}: {error_message}")
            
            # Check if it's a rate limit error
            if "429" in error_message or "RESOURCE_EXHAUSTED" in error_message:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    print(f"Rate limited. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue
                else:
                    return f"""
                    <html>
                    <head><title>API Quota Exceeded</title></head>
                    <body style="background: #f0f0f0; font-family: Arial;">
                    <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
                        <h1>API Quota Exceeded</h1>
                        <p>The Google Gemini API quota has been exceeded. Please:</p>
                        <ul>
                            <li>Wait a few moments and try again</li>
                            <li>Upgrade your Google Gemini API plan for higher limits</li>
                            <li>Check your billing details at <a href="https://ai.google.dev/gemini-api/docs/rate-limits" target="_blank">Google AI Documentation</a></li>
                        </ul>
                        <p style="color: #666; font-size: 0.9em;">Error: {error_message[:300]}</p>
                    </div>
                    </body>
                    </html>
                    """
            
            # For other errors on last attempt, return error message
            if attempt == max_retries - 1:
                return f"""
                <html>
                <head><title>Generation Error</title></head>
                <body style="background: #f0f0f0; font-family: Arial;">
                <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
                    <h1>Error Generating App</h1>
                    <p>An error occurred while generating your app after {max_retries} attempts:</p>
                    <pre style="background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 0.85em; max-height: 200px; overflow-y: auto;">{error_message}</pre>
                </div>
                </body>
                </html>
                """
    
    # Fallback error message
    return """
    <html>
    <head><title>Generation Failed</title></head>
    <body style="background: #f0f0f0; font-family: Arial;">
    <div style="max-width: 600px; margin: 50px auto; padding: 20px; background: white; border-radius: 8px;">
        <h1>Failed to Generate App</h1>
        <p>Unable to generate app after multiple attempts. Please try again later.</p>
    </div>
    </body>
    </html>
    """
```

## auth_utils.py
```py
import base64
import hashlib
import hmac
import os


PBKDF2_ITERATIONS = int(os.getenv("PASSWORD_HASH_ITERATIONS", "390000"))
SALT_SIZE = 16
HASH_NAME = "sha256"
DERIVED_KEY_LENGTH = 32


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password cannot be empty.")

    salt = os.urandom(SALT_SIZE)
    derived_key = hashlib.pbkdf2_hmac(
        HASH_NAME,
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=DERIVED_KEY_LENGTH,
    )
    payload = salt + derived_key
    return base64.b64encode(payload).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash:
        return False

    try:
        decoded = base64.b64decode(stored_hash.encode("utf-8"))
        salt = decoded[:SALT_SIZE]
        expected_key = decoded[SALT_SIZE:]
    except Exception:
        return False

    test_key = hashlib.pbkdf2_hmac(
        HASH_NAME,
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=len(expected_key),
    )

    return hmac.compare_digest(expected_key, test_key)

```

## database.py
```py
import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()


def _build_default_mysql_url() -> str:
    db_user = quote_plus(os.getenv("DB_USER", "root"))
    db_password = quote_plus(os.getenv("DB_PASSWORD", ""))
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "3306")
    db_name = os.getenv("DB_NAME", "autovision_studio")

    auth = db_user if not db_password else f"{db_user}:{db_password}"
    return f"mysql+mysqlconnector://{auth}@{db_host}:{db_port}/{db_name}"


def _resolve_database_url() -> str:
    return os.getenv("DATABASE_URL", _build_default_mysql_url())


def _create_database_engine():
    primary_url = _resolve_database_url()
    allow_sqlite_fallback = os.getenv("ALLOW_SQLITE_FALLBACK", "true").lower() == "true"
    sqlite_fallback_url = os.getenv("SQLITE_FALLBACK_URL", "sqlite:///./autovision_studio.db")

    try:
        mysql_engine = create_engine(primary_url, pool_pre_ping=True, future=True)
        with mysql_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return mysql_engine, primary_url, False
    except Exception as mysql_error:
        if not allow_sqlite_fallback:
            raise RuntimeError(
                "Could not connect to MySQL database. Configure DATABASE_URL/DB_* environment values."
            ) from mysql_error

        sqlite_engine = create_engine(
            sqlite_fallback_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
        with sqlite_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        print(
            "MySQL connection failed. Using SQLite fallback instead. "
            "Set ALLOW_SQLITE_FALLBACK=false to enforce MySQL-only mode."
        )
        return sqlite_engine, sqlite_fallback_url, True


ENGINE, ACTIVE_DATABASE_URL, USING_SQLITE_FALLBACK = _create_database_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=ENGINE, future=True)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

```

## models.py
```py
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    apps = relationship("AppVersion", back_populates="user", cascade="all, delete-orphan")


class AppVersion(Base):
    __tablename__ = "apps"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    app_name = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=False)
    version_number = Column(Integer, nullable=False, default=1)
    output_dir = Column(String(500), nullable=True)
    source_app_id = Column(Integer, ForeignKey("apps.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="apps")
    source_app = relationship("AppVersion", remote_side=[id], uselist=False)

```

## requirements.txt
```txt
fastapi==0.104.1
uvicorn==0.24.0
jinja2==3.1.2
pydantic==2.5.0
google-generativeai==0.4.0
python-dotenv==1.0.0
itsdangerous==2.2.0
sqlalchemy==2.0.48
mysql-connector-python==9.6.0
python-multipart==0.0.22

```

## templates/index.html
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AutoAgent Studio</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Segoe UI', sans-serif;
            background: #0f0f1a;
            color: #ffffff;
            min-height: 100vh;
        }

        header {
            padding: 20px 40px;
            background: #1a1a2e;
            border-bottom: 1px solid #333;
            text-align: center;
        }

        header h1 {
            font-size: 2rem;
            background: linear-gradient(90deg, #7c3aed, #3b82f6);
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        header p {
            color: #888;
            margin-top: 5px;
        }

        .container {
            max-width: 900px;
            margin: 40px auto;
            padding: 0 20px;
        }

        .input-section {
            background: #1a1a2e;
            border-radius: 16px;
            padding: 30px;
            border: 1px solid #333;
        }

        .input-section label {
            display: block;
            margin-bottom: 10px;
            color: #aaa;
            font-size: 0.95rem;
        }

        textarea {
            width: 100%;
            padding: 15px;
            background: #0f0f1a;
            border: 1px solid #444;
            border-radius: 10px;
            color: #fff;
            font-size: 1rem;
            resize: vertical;
            min-height: 120px;
            outline: none;
            transition: border 0.3s;
        }

        textarea:focus {
            border-color: #7c3aed;
        }

        button {
            margin-top: 15px;
            padding: 14px 30px;
            background: linear-gradient(90deg, #7c3aed, #3b82f6);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 1rem;
            cursor: pointer;
            width: 100%;
            transition: opacity 0.3s;
        }

        button:hover { opacity: 0.85; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }

        .status {
            margin-top: 15px;
            padding: 10px 15px;
            border-radius: 8px;
            font-size: 0.9rem;
            display: none;
        }

        .status.loading {
            display: block;
            background: #1e3a5f;
            color: #60a5fa;
        }

        .status.error {
            display: block;
            background: #3b1a1a;
            color: #f87171;
        }

        .output-section {
            margin-top: 30px;
            display: none;
        }

        .output-section h2 {
            margin-bottom: 15px;
            color: #aaa;
            font-size: 1rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .preview-container {
            border-radius: 16px;
            overflow: hidden;
            border: 1px solid #333;
        }

        .preview-bar {
            background: #1a1a2e;
            padding: 10px 20px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }

        .dot.red { background: #ef4444; }
        .dot.yellow { background: #f59e0b; }
        .dot.green { background: #10b981; }

        iframe {
            width: 100%;
            height: 500px;
            border: none;
            background: white;
        }

        .code-toggle {
            margin-top: 15px;
            background: #1a1a2e;
            border: 1px solid #333;
            border-radius: 10px;
            overflow: hidden;
        }

        .code-toggle summary {
            padding: 12px 20px;
            cursor: pointer;
            color: #aaa;
            font-size: 0.9rem;
        }

        .code-toggle pre {
            padding: 20px;
            overflow-x: auto;
            font-size: 0.8rem;
            color: #a78bfa;
            background: #0f0f1a;
            max-height: 300px;
            overflow-y: auto;
        }

        .preview-label {
            color: #666;
            font-size: 0.85rem;
            margin-left: 10px;
        }
    </style>
</head>
<body>

<header>
    <h1>âš¡ AutoAgent Studio</h1>
    <p>Describe any app in plain English â€” AI will build it instantly</p>
</header>

<div class="container">
    <div class="input-section">
        <label>What app do you want to create?</label>
        <textarea id="promptInput" placeholder="e.g. Create a todo app where I can add, edit and delete tasks with a clean modern UI..."></textarea>
        <button id="generateBtn" onclick="generateApp()">âš¡ Generate App</button>
        <div class="status" id="status"></div>
    </div>

    <div class="output-section" id="outputSection">
        <h2>ðŸŽ‰ Your Generated App</h2>
        <div class="preview-container">
            <div class="preview-bar">
                <div class="dot red"></div>
                <div class="dot yellow"></div>
                <div class="dot green"></div>
                <span class="preview-label">Live Preview</span>
            </div>
            <iframe id="previewFrame"></iframe>
        </div>

        <details class="code-toggle">
            <summary>ðŸ“„ View Generated Code</summary>
            <pre id="codeOutput"></pre>
        </details>
    </div>
</div>

<script>
    async function generateApp() {
        const prompt = document.getElementById('promptInput').value.trim();
        const btn = document.getElementById('generateBtn');
        const status = document.getElementById('status');
        const outputSection = document.getElementById('outputSection');

        if (!prompt) {
            alert('Please enter a prompt first!');
            return;
        }

        btn.disabled = true;
        btn.textContent = 'â³ Generating...';
        status.className = 'status loading';
        status.textContent = 'ðŸ¤– AI Agent is building your app... this may take a few seconds';
        outputSection.style.display = 'none';

        try {
            const response = await fetch('/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: prompt })
            });

            const data = await response.json();

            if (data.success) {
                status.style.display = 'none';
                
                // Show preview in iframe
                const iframe = document.getElementById('previewFrame');
                iframe.srcdoc = data.code;

                // Show code
                document.getElementById('codeOutput').textContent = data.code;

                outputSection.style.display = 'block';
                outputSection.scrollIntoView({ behavior: 'smooth' });
            } else {
                status.className = 'status error';
                status.textContent = 'âŒ Error: ' + data.error;
            }
        } catch (err) {
            status.className = 'status error';
            status.textContent = 'âŒ Connection error. Is the server running?';
        } finally {
            btn.disabled = false;
            btn.textContent = 'âš¡ Generate App';
        }
    }
</script>

</body>
</html>

```

## templates/login.html
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login | AutoAgent Studio</title>
    <style>
        :root {
            --bg: #0f172a;
            --panel: #111827;
            --border: #1f2937;
            --text: #f9fafb;
            --muted: #94a3b8;
            --accent: #0ea5e9;
            --accent-hover: #0284c7;
            --danger: #dc2626;
            --danger-bg: #450a0a;
            --success: #16a34a;
            --success-bg: #052e16;
        }

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            min-height: 100vh;
            font-family: "Segoe UI", sans-serif;
            background: radial-gradient(circle at top, #1e293b 0%, var(--bg) 50%);
            color: var(--text);
            display: grid;
            place-items: center;
            padding: 24px;
        }

        .card {
            width: min(100%, 440px);
            background: rgba(17, 24, 39, 0.92);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 28px;
            backdrop-filter: blur(4px);
        }

        h1 {
            margin: 0 0 8px;
            font-size: 1.8rem;
        }

        .subtitle {
            margin: 0 0 24px;
            color: var(--muted);
            font-size: 0.95rem;
        }

        .notification {
            padding: 10px 12px;
            border-radius: 10px;
            margin-bottom: 16px;
            font-size: 0.92rem;
        }

        .notification.success {
            background: var(--success-bg);
            color: #86efac;
            border: 1px solid #166534;
        }

        .notification.error {
            background: var(--danger-bg);
            color: #fca5a5;
            border: 1px solid #7f1d1d;
        }

        label {
            display: block;
            margin-bottom: 6px;
            color: #cbd5e1;
            font-size: 0.9rem;
        }

        input {
            width: 100%;
            padding: 11px 12px;
            border: 1px solid #334155;
            border-radius: 10px;
            background: #0b1220;
            color: var(--text);
            margin-bottom: 14px;
            outline: none;
        }

        input:focus {
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.2);
        }

        button {
            width: 100%;
            padding: 11px 14px;
            border: none;
            border-radius: 10px;
            background: var(--accent);
            color: white;
            font-size: 0.95rem;
            cursor: pointer;
        }

        button:hover {
            background: var(--accent-hover);
        }

        .meta {
            margin-top: 16px;
            color: var(--muted);
            font-size: 0.9rem;
            text-align: center;
        }

        .meta a {
            color: #38bdf8;
            text-decoration: none;
        }
    </style>
</head>
<body>
    <main class="card">
        <h1>AutoAgent Studio</h1>
        <p class="subtitle">Sign in to manage and generate your apps.</p>

        {% if notification %}
            <div class="notification {{ notification.level }}">{{ notification.message }}</div>
        {% endif %}

        <form method="post" action="/login">
            <label for="email">Email</label>
            <input id="email" name="email" type="email" autocomplete="email" required>

            <label for="password">Password</label>
            <input id="password" name="password" type="password" autocomplete="current-password" required>

            <button type="submit">Login</button>
        </form>

        <p class="meta">New user? <a href="/register">Create an account</a></p>
    </main>
</body>
</html>

```

## templates/register.html
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Register | AutoAgent Studio</title>
    <style>
        :root {
            --bg: #04121f;
            --panel: #0f172a;
            --border: #1e293b;
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #06b6d4;
            --accent-hover: #0891b2;
            --danger-bg: #431407;
            --danger: #fdba74;
            --success-bg: #052e16;
            --success: #86efac;
        }

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            min-height: 100vh;
            font-family: "Segoe UI", sans-serif;
            color: var(--text);
            background: linear-gradient(155deg, #0c4a6e, var(--bg) 45%);
            display: grid;
            place-items: center;
            padding: 24px;
        }

        .card {
            width: min(100%, 500px);
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 28px;
        }

        h1 {
            margin: 0 0 8px;
            font-size: 1.7rem;
        }

        p {
            margin: 0 0 20px;
            color: var(--muted);
            font-size: 0.95rem;
        }

        .notification {
            padding: 10px 12px;
            border-radius: 10px;
            margin-bottom: 16px;
            font-size: 0.92rem;
        }

        .notification.success {
            background: var(--success-bg);
            color: var(--success);
            border: 1px solid #166534;
        }

        .notification.error {
            background: var(--danger-bg);
            color: var(--danger);
            border: 1px solid #9a3412;
        }

        label {
            display: block;
            margin-bottom: 6px;
            color: #cbd5e1;
            font-size: 0.9rem;
        }

        input {
            width: 100%;
            padding: 11px 12px;
            border: 1px solid #334155;
            border-radius: 10px;
            background: #020617;
            color: var(--text);
            margin-bottom: 14px;
            outline: none;
        }

        input:focus {
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(6, 182, 212, 0.2);
        }

        .row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }

        button {
            width: 100%;
            padding: 11px 14px;
            border: none;
            border-radius: 10px;
            background: var(--accent);
            color: white;
            font-size: 0.95rem;
            cursor: pointer;
            margin-top: 4px;
        }

        button:hover {
            background: var(--accent-hover);
        }

        .meta {
            margin-top: 16px;
            color: var(--muted);
            font-size: 0.9rem;
            text-align: center;
        }

        .meta a {
            color: #67e8f9;
            text-decoration: none;
        }

        @media (max-width: 600px) {
            .row {
                grid-template-columns: 1fr;
                gap: 0;
            }
        }
    </style>
</head>
<body>
    <main class="card">
        <h1>Create account</h1>
        <p>Register once to track generated apps, versions, and downloads.</p>

        {% if notification %}
            <div class="notification {{ notification.level }}">{{ notification.message }}</div>
        {% endif %}

        <form method="post" action="/register">
            <label for="name">Name</label>
            <input id="name" name="name" type="text" autocomplete="name" required>

            <label for="email">Email</label>
            <input id="email" name="email" type="email" autocomplete="email" required>

            <div class="row">
                <div>
                    <label for="password">Password</label>
                    <input id="password" name="password" type="password" autocomplete="new-password" required>
                </div>
                <div>
                    <label for="confirm_password">Confirm Password</label>
                    <input id="confirm_password" name="confirm_password" type="password" autocomplete="new-password" required>
                </div>
            </div>

            <button type="submit">Register</button>
        </form>

        <p class="meta">Already have an account? <a href="/login">Login</a></p>
    </main>
</body>
</html>

```

## templates/dashboard_home.html
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard | AutoAgent Studio</title>
    <style>
        :root {
            --bg-1: #1e1b4b;
            --bg-2: #0f3d5e;
            --bg-3: #0f5132;
            --text: #f8fafc;
            --muted: #d1d5db;
            --glass: rgba(10, 16, 36, 0.78);
            --border: rgba(255, 255, 255, 0.2);
            --card-a: #f97316;
            --card-b: #22d3ee;
            --card-c: #a78bfa;
        }

        * { box-sizing: border-box; }

        body {
            margin: 0;
            min-height: 100vh;
            color: var(--text);
            font-family: "Segoe UI", sans-serif;
            background:
                radial-gradient(circle at 8% 8%, #fb7185 0%, transparent 24%),
                radial-gradient(circle at 90% 6%, #38bdf8 0%, transparent 25%),
                radial-gradient(circle at 84% 84%, #4ade80 0%, transparent 24%),
                linear-gradient(135deg, var(--bg-1), var(--bg-2) 40%, var(--bg-3));
            background-attachment: fixed;
        }

        .nav {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding: 14px clamp(14px, 4vw, 34px);
            border-bottom: 1px solid var(--border);
            background: rgba(8, 12, 28, 0.72);
            backdrop-filter: blur(10px);
            position: sticky;
            top: 0;
            z-index: 10;
        }

        .brand {
            font-weight: 700;
            letter-spacing: 0.01em;
        }

        .nav-links {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }

        .nav-links a {
            text-decoration: none;
            color: #fff7ed;
            border: 1px solid rgba(255, 255, 255, 0.28);
            border-radius: 999px;
            padding: 7px 12px;
            font-size: 0.85rem;
            background: rgba(30, 41, 59, 0.7);
        }

        .nav-links a.active {
            background: linear-gradient(90deg, #f97316, #db2777);
        }

        .container {
            width: min(1100px, 100%);
            margin: 0 auto;
            padding: 26px clamp(14px, 4vw, 34px) 32px;
        }

        h1 {
            margin: 0 0 8px;
            font-size: clamp(1.3rem, 3vw, 2rem);
        }

        .subtitle {
            color: var(--muted);
            margin: 0 0 16px;
        }

        .notification {
            margin-bottom: 16px;
            border: 1px solid rgba(255, 255, 255, 0.25);
            border-radius: 12px;
            padding: 10px 12px;
            background: rgba(37, 99, 235, 0.5);
            color: #e0e7ff;
            font-size: 0.9rem;
        }

        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }

        .stat {
            border: 1px solid var(--border);
            border-radius: 14px;
            background: var(--glass);
            padding: 14px;
        }

        .stat h3 {
            margin: 0;
            font-size: 1.55rem;
        }

        .stat p {
            margin: 3px 0 0;
            color: #bfdbfe;
            font-size: 0.86rem;
        }

        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 14px;
        }

        .card {
            border-radius: 16px;
            padding: 18px;
            border: 1px solid var(--border);
            color: #fff;
            text-decoration: none;
            display: block;
            box-shadow: 0 12px 24px rgba(0, 0, 0, 0.28);
            transition: transform 0.2s ease, filter 0.2s ease;
        }

        .card:hover {
            transform: translateY(-3px);
            filter: brightness(1.04);
        }

        .card.generate {
            background: linear-gradient(135deg, var(--card-a), #ea580c);
        }

        .card.apps {
            background: linear-gradient(135deg, var(--card-b), #0ea5e9);
        }

        .card.about {
            background: linear-gradient(135deg, var(--card-c), #7c3aed);
        }

        .card h2 {
            margin: 0 0 6px;
            font-size: 1.06rem;
        }

        .card p {
            margin: 0;
            font-size: 0.9rem;
            opacity: 0.95;
        }
    </style>
</head>
<body>
    <header class="nav">
        <div class="brand">AutoAgent Studio ðŸ¤–</div>
        <nav class="nav-links">
            <a class="active" href="/dashboard">Home</a>
            <a href="/generate-studio">Generation Studio</a>
            <a href="/apps-studio">Created Apps Studio</a>
            <a href="/logout">Logout</a>
        </nav>
    </header>

    <main class="container">
        <h1>Hello, {{ user.name }} ðŸ‘‹</h1>
        <p class="subtitle">Welcome to AutoAgent Studio. Choose where you want to work.</p>

        {% if notification %}
            <div class="notification">{{ notification.message }}</div>
        {% endif %}

        <section class="stats">
            <article class="stat">
                <h3>{{ total_apps }}</h3>
                <p>Total apps created</p>
            </article>
            <article class="stat">
                <h3>{% if latest_app %}v{{ latest_app.version_number }}{% else %}--{% endif %}</h3>
                <p>Latest app version</p>
            </article>
            <article class="stat">
                <h3>{% if latest_app %}{{ latest_app.app_name }}{% else %}No app yet{% endif %}</h3>
                <p>Most recent app</p>
            </article>
        </section>

        <section class="cards">
            <a class="card generate" href="/generate-studio">
                <h2>ðŸš€ Generation Studio</h2>
                <p>Create new apps, edit prompts, and re-run versions with AI generation.</p>
            </a>
            <a class="card apps" href="/apps-studio">
                <h2>ðŸ—‚ï¸ Created Apps Studio</h2>
                <p>Browse all your generated apps, search history, download ZIPs, and open full-screen previews.</p>
            </a>
            <a class="card about" href="/generate-studio">
                <h2>âœ¨ Start Building</h2>
                <p>Jump directly to app generation and launch your ideas into a full-screen experience.</p>
            </a>
        </section>
    </main>
</body>
</html>

```

## templates/generate_studio.html
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Generation Studio | AutoAgent Studio</title>
    <style>
        :root {
            --bg-1: #3b0764;
            --bg-2: #0f172a;
            --bg-3: #065f46;
            --text: #f8fafc;
            --muted: #d1d5db;
            --glass: rgba(8, 13, 28, 0.78);
            --border: rgba(255, 255, 255, 0.2);
        }

        * { box-sizing: border-box; }

        body {
            margin: 0;
            min-height: 100vh;
            color: var(--text);
            font-family: "Segoe UI", sans-serif;
            background:
                radial-gradient(circle at 10% 12%, #fb7185 0%, transparent 24%),
                radial-gradient(circle at 90% 8%, #38bdf8 0%, transparent 26%),
                radial-gradient(circle at 82% 88%, #34d399 0%, transparent 24%),
                linear-gradient(135deg, var(--bg-1), var(--bg-2) 40%, var(--bg-3));
            background-attachment: fixed;
        }

        .nav {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding: 14px clamp(14px, 4vw, 34px);
            border-bottom: 1px solid var(--border);
            background: rgba(8, 12, 28, 0.72);
            backdrop-filter: blur(10px);
            position: sticky;
            top: 0;
            z-index: 10;
        }

        .brand {
            font-weight: 700;
        }

        .nav-links {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .nav-links a {
            text-decoration: none;
            color: #fff7ed;
            border: 1px solid rgba(255, 255, 255, 0.28);
            border-radius: 999px;
            padding: 7px 12px;
            font-size: 0.85rem;
            background: rgba(30, 41, 59, 0.7);
        }

        .nav-links a.active {
            background: linear-gradient(90deg, #f97316, #db2777);
        }

        .container {
            width: min(1200px, 100%);
            margin: 0 auto;
            padding: 24px clamp(14px, 4vw, 34px) 34px;
            display: grid;
            gap: 14px;
            grid-template-columns: 1.15fr 0.85fr;
        }

        .panel {
            border: 1px solid var(--border);
            border-radius: 16px;
            background: var(--glass);
            padding: 16px;
            box-shadow: 0 12px 26px rgba(0, 0, 0, 0.25);
        }

        .panel h1, .panel h2 {
            margin: 0 0 8px;
        }

        .panel p {
            margin: 0 0 12px;
            color: var(--muted);
            font-size: 0.9rem;
        }

        .notification,
        .status {
            display: none;
            border-radius: 12px;
            padding: 10px 12px;
            margin-bottom: 12px;
            font-size: 0.9rem;
            border: 1px solid transparent;
        }

        .notification.visible,
        .status.visible {
            display: block;
        }

        .notification.success,
        .status.success {
            background: #14532d;
            color: #bbf7d0;
            border-color: rgba(187, 247, 208, 0.45);
        }

        .notification.error,
        .status.error {
            background: #7f1d1d;
            color: #fecaca;
            border-color: rgba(254, 202, 202, 0.45);
        }

        .status.info {
            background: #1d4ed8;
            color: #dbeafe;
            border-color: rgba(219, 234, 254, 0.45);
        }

        label {
            display: block;
            font-size: 0.88rem;
            margin-bottom: 6px;
            color: #fbcfe8;
        }

        input[type="text"],
        textarea {
            width: 100%;
            border: 1px solid rgba(255, 255, 255, 0.24);
            border-radius: 12px;
            background: rgba(10, 15, 34, 0.85);
            color: #fff;
            padding: 10px 12px;
            font-size: 0.94rem;
            outline: none;
        }

        input:focus,
        textarea:focus {
            border-color: #fef08a;
            box-shadow: 0 0 0 3px rgba(254, 240, 138, 0.22);
        }

        textarea {
            min-height: 130px;
            resize: vertical;
            margin-bottom: 10px;
        }

        .row {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 10px;
        }

        button,
        .action-link {
            border: none;
            border-radius: 12px;
            padding: 10px 12px;
            font-size: 0.9rem;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }

        .btn-primary {
            background: linear-gradient(90deg, #f59e0b, #f97316);
            color: #fff;
            font-weight: 700;
        }

        .btn-secondary {
            background: linear-gradient(90deg, #22d3ee, #6366f1);
            color: #fff;
        }

        .loading {
            display: none;
            align-items: center;
            gap: 10px;
            border: 1px solid rgba(254, 240, 138, 0.45);
            background: rgba(120, 53, 15, 0.45);
            border-radius: 12px;
            padding: 10px 12px;
            margin-bottom: 12px;
        }

        .loading.visible {
            display: flex;
        }

        .spinner {
            font-size: 1.6rem;
            animation: spin 1.2s linear infinite;
            transform-origin: center;
        }

        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        .result {
            display: none;
            border: 1px solid rgba(167, 243, 208, 0.45);
            background: rgba(5, 46, 22, 0.52);
            border-radius: 12px;
            padding: 12px;
        }

        .result.visible {
            display: block;
        }

        details {
            margin-top: 10px;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--border);
        }

        details summary {
            padding: 9px 11px;
            background: rgba(20, 26, 56, 0.92);
            color: #fef08a;
            cursor: pointer;
            font-size: 0.88rem;
        }

        details pre {
            margin: 0;
            background: #020617;
            color: #a7f3d0;
            max-height: 280px;
            overflow: auto;
            padding: 11px;
            font-size: 0.77rem;
            line-height: 1.4;
        }

        .recent-list {
            display: grid;
            gap: 8px;
            margin-top: 6px;
        }

        .recent-item {
            border: 1px solid rgba(255, 255, 255, 0.24);
            background: rgba(15, 23, 42, 0.78);
            border-radius: 11px;
            padding: 10px;
        }

        .recent-title {
            font-size: 0.9rem;
            font-weight: 600;
            margin-bottom: 4px;
        }

        .recent-meta {
            font-size: 0.78rem;
            color: #bfdbfe;
            margin-bottom: 8px;
        }

        .recent-actions a {
            text-decoration: none;
            display: inline-block;
            font-size: 0.78rem;
            color: #fff7ed;
            background: rgba(234, 88, 12, 0.65);
            border: 1px solid rgba(255, 255, 255, 0.24);
            border-radius: 8px;
            padding: 5px 8px;
            margin-right: 6px;
            margin-bottom: 4px;
        }

        @media (max-width: 960px) {
            .container { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <header class="nav">
        <div class="brand">AutoAgent Studio ðŸ¤–</div>
        <nav class="nav-links">
            <a href="/dashboard">Home</a>
            <a class="active" href="/generate-studio">Generation Studio</a>
            <a href="/apps-studio">Created Apps Studio</a>
            <a href="/logout">Logout</a>
        </nav>
    </header>

    <main class="container">
        <section class="panel">
            <h1>Generation Studio ðŸš€</h1>
            <p>Build a fresh app or continue from an older version prompt.</p>

            <div
                id="notificationBanner"
                class="notification {% if notification %}visible {{ notification.level }}{% endif %}"
            >
                {% if notification %}{{ notification.message }}{% endif %}
            </div>

            <label for="appNameInput">App Name (optional) ðŸ·ï¸</label>
            <input
                id="appNameInput"
                type="text"
                placeholder="Example: Travel Planner"
                value="{% if selected_app %}{{ selected_app.app_name }}{% endif %}"
            >

            <label for="promptInput">Prompt ðŸ’¡</label>
            <textarea id="promptInput" placeholder="Describe the app...">{% if selected_app %}{{ selected_app.prompt }}{% endif %}</textarea>

            <input id="sourceAppId" type="hidden" value="{% if selected_app %}{{ selected_app.id }}{% endif %}">

            <div class="row">
                <button id="generateBtn" class="btn-primary" type="button" onclick="generateApp()">Generate App ðŸš€</button>
                <button class="btn-secondary" type="button" onclick="resetRerunState()">Start New ðŸ§¼</button>
                <a class="action-link btn-secondary" href="/apps-studio">Go to Apps Studio ðŸ—‚ï¸</a>
            </div>

            <div id="loadingBox" class="loading" aria-live="polite">
                <div class="spinner">ðŸŒ€</div>
                <div>
                    <div style="font-weight:700; color:#fde68a;">AI agents are generating your app...</div>
                    <div id="loadingFact" style="font-size:0.84rem; color:#fecdd3;">Fun fact: agentic workflows can chain planning and execution in one loop.</div>
                </div>
            </div>

            <div id="statusBox" class="status"></div>

            <section id="outputSection" class="result">
                <div style="margin-bottom:8px; font-weight:700;">Your app is ready ðŸŽ‰</div>
                <div class="row">
                    <a id="openFullScreenLink" class="action-link btn-primary" href="#" target="_blank" rel="noopener">Open Full-Screen App ðŸš€</a>
                    <a id="latestDownloadLink" class="action-link btn-secondary" href="#">Download ZIP ðŸ“¦</a>
                    <a class="action-link btn-secondary" href="/apps-studio">View in Apps Studio</a>
                </div>
                <details>
                    <summary>View Generated Code ðŸ§¾</summary>
                    <pre id="codeOutput"></pre>
                </details>
            </section>
        </section>

        <aside class="panel">
            <h2>Quick Re-run Sources âœï¸</h2>
            <p>Jump from recent apps to prefill prompt and generate a new version.</p>

            {% if recent_apps %}
                <div class="recent-list">
                    {% for app in recent_apps %}
                        <div class="recent-item">
                            <div class="recent-title">{{ app.app_name }} (v{{ app.version_number }})</div>
                            <div class="recent-meta">{{ app.created_at.strftime("%Y-%m-%d %H:%M:%S") if app.created_at else "N/A" }}</div>
                            <div class="recent-actions">
                                <a href="/generate-studio?source_app_id={{ app.id }}">Edit &amp; Re-run</a>
                                <a href="/apps/{{ app.id }}/view" target="_blank" rel="noopener">Full Screen</a>
                            </div>
                        </div>
                    {% endfor %}
                </div>
            {% else %}
                <div style="color:#fbcfe8; font-size:0.9rem;">No apps created yet.</div>
            {% endif %}
        </aside>
    </main>

    <script>
        const promptInput = document.getElementById("promptInput");
        const appNameInput = document.getElementById("appNameInput");
        const sourceAppIdInput = document.getElementById("sourceAppId");
        const generateBtn = document.getElementById("generateBtn");
        const loadingBox = document.getElementById("loadingBox");
        const loadingFact = document.getElementById("loadingFact");
        const statusBox = document.getElementById("statusBox");
        const banner = document.getElementById("notificationBanner");
        const outputSection = document.getElementById("outputSection");
        const codeOutput = document.getElementById("codeOutput");
        const openFullScreenLink = document.getElementById("openFullScreenLink");
        const latestDownloadLink = document.getElementById("latestDownloadLink");

        const PROMPT_KEY = "autoagent_last_prompt";
        const APP_NAME_KEY = "autoagent_last_app_name";
        const facts = [
            "Fun fact: agentic systems can adapt next actions from intermediate results.",
            "Fun fact: structured prompts often reduce retries and improve output quality.",
            "Fun fact: saving prompt history makes iterative app design much faster.",
            "Fun fact: multi-step reasoning allows AI to refine output before returning.",
            "Fun fact: combining tools + memory enables richer autonomous workflows."
        ];

        let factTimer = null;
        let factIndex = 0;

        function setStatus(message, level) {
            statusBox.textContent = message;
            statusBox.className = "status visible " + level;
        }

        function clearStatus() {
            statusBox.textContent = "";
            statusBox.className = "status";
        }

        function showNotification(message, level) {
            banner.textContent = message;
            banner.className = "notification visible " + level;
        }

        function persistPromptToStorage() {
            localStorage.setItem(PROMPT_KEY, promptInput.value);
            localStorage.setItem(APP_NAME_KEY, appNameInput.value);
        }

        function restorePromptFromStorage() {
            const hasPrefill = Boolean(sourceAppIdInput.value);
            if (hasPrefill) {
                return;
            }
            const p = localStorage.getItem(PROMPT_KEY);
            const n = localStorage.getItem(APP_NAME_KEY);
            if (p) promptInput.value = p;
            if (n) appNameInput.value = n;
        }

        function startFacts() {
            factIndex = 0;
            loadingFact.textContent = facts[0];
            stopFacts();
            factTimer = setInterval(() => {
                factIndex = (factIndex + 1) % facts.length;
                loadingFact.textContent = facts[factIndex];
            }, 2300);
        }

        function stopFacts() {
            if (factTimer) {
                clearInterval(factTimer);
                factTimer = null;
            }
        }

        function setGeneratingState(isGenerating) {
            generateBtn.disabled = isGenerating;
            generateBtn.textContent = isGenerating ? "Generating Magic... âœ¨" : "Generate App ðŸš€";
            if (isGenerating) {
                loadingBox.classList.add("visible");
                startFacts();
            } else {
                loadingBox.classList.remove("visible");
                stopFacts();
            }
        }

        function resetRerunState() {
            sourceAppIdInput.value = "";
            promptInput.value = "";
            appNameInput.value = "";
            persistPromptToStorage();
            setStatus("Ready for a brand new app ðŸŒˆ", "info");
        }

        async function generateApp() {
            const prompt = promptInput.value.trim();
            const appName = appNameInput.value.trim();
            const sourceAppId = sourceAppIdInput.value.trim();

            if (!prompt) {
                setStatus("Please enter a prompt first.", "error");
                return;
            }

            clearStatus();
            persistPromptToStorage();
            setGeneratingState(true);
            setStatus("Generating your app now... ðŸ¤–", "info");

            let previewTab = null;
            try {
                previewTab = window.open("", "_blank", "noopener");
                if (previewTab) {
                    previewTab.document.write(
                        "<!doctype html><html><head><title>Generating...</title></head>" +
                        "<body style='margin:0;display:grid;place-items:center;min-height:100vh;" +
                        "background:linear-gradient(135deg,#7c3aed,#06b6d4);color:#fff;font-family:Segoe UI,sans-serif'>" +
                        "<div style='text-align:center'><div style='font-size:56px;margin-bottom:8px'>ðŸ¤–âœ¨</div>" +
                        "<div style='font-size:20px;font-weight:700'>Generating your app...</div>" +
                        "<div style='margin-top:8px;font-size:14px;opacity:.9'>AutoAgent Studio is crafting your UI</div></div>" +
                        "</body></html>"
                    );
                }
            } catch (error) {
                previewTab = null;
            }

            const payload = { prompt: prompt };
            if (appName) payload.app_name = appName;
            if (sourceAppId) payload.source_app_id = Number(sourceAppId);

            try {
                const response = await fetch("/generate", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();

                if (!data.success) {
                    if (previewTab && !previewTab.closed) previewTab.close();
                    showNotification(data.error || "Unable to generate app.", "error");
                    setStatus(data.error || "Unable to generate app.", "error");
                    return;
                }

                outputSection.classList.add("visible");
                codeOutput.textContent = data.code || "";
                sourceAppIdInput.value = data.app ? String(data.app.id) : sourceAppIdInput.value;

                const previewUrl = data.app?.preview_url || (data.app ? `/apps/${data.app.id}/view` : "#");
                const downloadUrl = data.app?.download_url || (data.app ? `/apps/${data.app.id}/download` : "#");
                openFullScreenLink.href = previewUrl;
                latestDownloadLink.href = downloadUrl;

                if (previewTab && !previewTab.closed && previewUrl !== "#") {
                    previewTab.location.href = previewUrl;
                } else if (previewUrl !== "#") {
                    window.open(previewUrl, "_blank", "noopener");
                }

                showNotification(data.notification || "App created successfully ðŸŽ‰", "success");
                setStatus("Done! Opened in full-screen page. You can switch to Apps Studio anytime.", "success");
            } catch (error) {
                if (previewTab && !previewTab.closed) previewTab.close();
                showNotification("Connection error. Please try again.", "error");
                setStatus("Connection error. Please try again.", "error");
            } finally {
                setGeneratingState(false);
            }
        }

        promptInput.addEventListener("input", persistPromptToStorage);
        appNameInput.addEventListener("input", persistPromptToStorage);
        restorePromptFromStorage();
    </script>
</body>
</html>

```

## templates/apps_studio.html
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Created Apps Studio | AutoAgent Studio</title>
    <style>
        :root {
            --bg-1: #111827;
            --bg-2: #0f766e;
            --bg-3: #4c1d95;
            --text: #f8fafc;
            --muted: #d1d5db;
            --glass: rgba(7, 14, 28, 0.78);
            --border: rgba(255, 255, 255, 0.2);
        }

        * { box-sizing: border-box; }

        body {
            margin: 0;
            min-height: 100vh;
            color: var(--text);
            font-family: "Segoe UI", sans-serif;
            background:
                radial-gradient(circle at 10% 8%, #f97316 0%, transparent 22%),
                radial-gradient(circle at 90% 10%, #06b6d4 0%, transparent 24%),
                radial-gradient(circle at 84% 84%, #a78bfa 0%, transparent 22%),
                linear-gradient(135deg, var(--bg-1), var(--bg-2) 46%, var(--bg-3));
            background-attachment: fixed;
        }

        .nav {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding: 14px clamp(14px, 4vw, 34px);
            border-bottom: 1px solid var(--border);
            background: rgba(8, 12, 28, 0.72);
            backdrop-filter: blur(10px);
            position: sticky;
            top: 0;
            z-index: 10;
        }

        .brand { font-weight: 700; }

        .nav-links {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .nav-links a {
            text-decoration: none;
            color: #fff7ed;
            border: 1px solid rgba(255, 255, 255, 0.28);
            border-radius: 999px;
            padding: 7px 12px;
            font-size: 0.85rem;
            background: rgba(30, 41, 59, 0.7);
        }

        .nav-links a.active {
            background: linear-gradient(90deg, #0ea5e9, #22c55e);
        }

        .container {
            width: min(1200px, 100%);
            margin: 0 auto;
            padding: 24px clamp(14px, 4vw, 34px) 34px;
        }

        .panel {
            border: 1px solid var(--border);
            border-radius: 16px;
            background: var(--glass);
            padding: 16px;
            box-shadow: 0 12px 26px rgba(0, 0, 0, 0.25);
        }

        h1 {
            margin: 0 0 8px;
            font-size: clamp(1.25rem, 3vw, 1.9rem);
        }

        .subtitle {
            margin: 0 0 14px;
            color: var(--muted);
            font-size: 0.92rem;
        }

        .notification {
            margin-bottom: 12px;
            border-radius: 12px;
            padding: 10px 12px;
            font-size: 0.9rem;
            border: 1px solid rgba(255, 255, 255, 0.24);
            background: rgba(37, 99, 235, 0.5);
            color: #dbeafe;
        }

        .search {
            display: flex;
            gap: 8px;
            margin-bottom: 12px;
        }

        .search input {
            flex: 1;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.24);
            background: rgba(10, 15, 34, 0.85);
            color: #fff;
            padding: 10px 12px;
            outline: none;
        }

        .search button {
            border: none;
            border-radius: 12px;
            padding: 10px 13px;
            background: linear-gradient(90deg, #22d3ee, #3b82f6);
            color: #082f49;
            font-weight: 700;
            cursor: pointer;
        }

        .toolbar {
            margin-bottom: 12px;
        }

        .toolbar a {
            text-decoration: none;
            border-radius: 10px;
            padding: 9px 12px;
            background: linear-gradient(90deg, #f59e0b, #f97316);
            color: #fff;
            display: inline-block;
            font-size: 0.88rem;
        }

        .empty-state {
            border: 1px dashed rgba(255, 255, 255, 0.32);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
            color: #fbcfe8;
            font-size: 0.92rem;
            background: rgba(88, 28, 135, 0.2);
        }

        .table-wrap {
            overflow-x: auto;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: rgba(15, 23, 42, 0.8);
        }

        table {
            width: 100%;
            min-width: 840px;
            border-collapse: collapse;
        }

        th,
        td {
            text-align: left;
            padding: 10px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            font-size: 0.88rem;
            vertical-align: top;
        }

        th {
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            background: rgba(3, 7, 18, 0.9);
            color: #fef08a;
        }

        .prompt-preview {
            max-width: 380px;
            white-space: pre-wrap;
            word-break: break-word;
            color: #e2e8f0;
        }

        .version {
            color: #93c5fd;
            font-size: 0.82rem;
            margin-left: 4px;
        }

        .actions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }

        .actions a {
            text-decoration: none;
            border-radius: 9px;
            border: 1px solid rgba(255, 255, 255, 0.28);
            padding: 7px 9px;
            font-size: 0.8rem;
            white-space: nowrap;
        }

        .rerun {
            background: rgba(234, 88, 12, 0.65);
            color: #fff7ed;
        }

        .view {
            background: rgba(34, 197, 94, 0.35);
            color: #dcfce7;
        }

        .zip {
            background: rgba(14, 165, 233, 0.4);
            color: #e0f2fe;
        }

        @media (max-width: 720px) {
            .search {
                flex-direction: column;
            }
            .search button {
                width: 100%;
            }
        }
    </style>
</head>
<body>
    <header class="nav">
        <div class="brand">AutoAgent Studio ðŸ¤–</div>
        <nav class="nav-links">
            <a href="/dashboard">Home</a>
            <a href="/generate-studio">Generation Studio</a>
            <a class="active" href="/apps-studio">Created Apps Studio</a>
            <a href="/logout">Logout</a>
        </nav>
    </header>

    <main class="container">
        <section class="panel">
            <h1>Created Apps Studio ðŸ—‚ï¸</h1>
            <p class="subtitle">Search your history and move between generation, preview, and download flows.</p>

            {% if notification %}
                <div class="notification">{{ notification.message }}</div>
            {% endif %}

            <div class="toolbar">
                <a href="/generate-studio">Create New App ðŸš€</a>
            </div>

            <form class="search" method="get" action="/apps-studio">
                <input
                    type="search"
                    name="q"
                    placeholder="Search by app name or prompt keywords"
                    value="{{ search_query }}"
                >
                <button type="submit">Search ðŸ”</button>
            </form>

            {% if not apps %}
                <div class="empty-state">
                    {% if search_query %}No matching apps found ðŸ˜…{% else %}No apps created yet{% endif %}
                </div>
            {% else %}
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>App</th>
                                <th>Prompt</th>
                                <th>Date and Time</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for app in apps %}
                                <tr>
                                    <td>
                                        <strong>{{ app.app_name }}</strong>
                                        <span class="version">(v{{ app.version_number }})</span>
                                    </td>
                                    <td class="prompt-preview">
                                        {{ app.prompt[:130] }}{% if app.prompt|length > 130 %}...{% endif %}
                                    </td>
                                    <td>{{ app.created_at.strftime("%Y-%m-%d %H:%M:%S") if app.created_at else "N/A" }}</td>
                                    <td>
                                        <div class="actions">
                                            <a class="rerun" href="/generate-studio?source_app_id={{ app.id }}">âœï¸ Edit &amp; Re-run</a>
                                            <a class="view" href="/apps/{{ app.id }}/view" target="_blank" rel="noopener">ðŸš€ Full Screen</a>
                                            <a class="zip" href="/apps/{{ app.id }}/download">ðŸ“¦ ZIP</a>
                                        </div>
                                    </td>
                                </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            {% endif %}
        </section>
    </main>
</body>
</html>

```

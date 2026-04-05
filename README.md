# AutoAgent Studio

🤖 **AI-powered web app generator** - Create fully functional web applications from simple text prompts using Google Generative AI.

## 🚀 Live Demo

**Try it now!** → https://autoagentstudio-production.up.railway.app

The app is live and ready to use. Register, generate AI-powered apps, and download them instantly!

## Features

- ✨ **Instant App Generation** - Describe your idea, get a complete HTML/CSS/JavaScript app
- 👤 **User Authentication** - Secure login and registration system
- 📊 **Dashboard** - Manage and view all your generated apps
- 🔄 **App Versioning** - Create new versions of existing apps
- 📥 **Download Apps** - Export generated apps as HTML files
- 🗄️ **Database Support** - Works with MySQL or SQLite (fallback)
- 🚀 **Production Ready** - Built with FastAPI for high performance

## Tech Stack

- **Backend**: FastAPI + Uvicorn
- **Database**: SQLAlchemy ORM (MySQL/PostgreSQL/SQLite)
- **AI**: Google Generative AI (Gemini)
- **Frontend**: Jinja2 templates
- **Session Management**: Secure session middleware
- **Authentication**: PBKDF2 password hashing

## Getting Started

### Prerequisites

- Python 3.11+
- Google Gemini API key (free tier available at [ai.google.dev](https://ai.google.dev))
- MySQL (optional - SQLite is used as fallback)

### Local Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/harshitha-730/autoagentstudio.git
   cd autoagentstudio
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   # On Windows:
   .\.venv\Scripts\activate
   # On macOS/Linux:
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r autoagentstudioapp/requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cd autoagentstudioapp
   cp .env.example .env
   # Edit .env and add your GEMINI_API_KEY
   ```

5. **Run the application**
   ```bash
   python -m uvicorn main:app --reload
   ```

   The app will be available at `http://localhost:8000`

## Environment Variables

### Required
- `GEMINI_API_KEY` - Your Google Gemini API key

### Optional Database Configuration
- `DATABASE_URL` - Full database connection string (overrides DB_* variables)
- `DB_HOST` - Database host (default: localhost)
- `DB_PORT` - Database port (default: 3306)
- `DB_USER` - Database username (default: root)
- `DB_PASSWORD` - Database password
- `DB_NAME` - Database name (default: autovision_studio)

### Optional Settings
- `SESSION_SECRET` - Secret key for session encryption (change in production!)
- `SESSION_MAX_AGE` - Session timeout in seconds (default: 28800)
- `SESSION_HTTPS_ONLY` - HTTPS-only cookies (default: false)
- `ALLOW_SQLITE_FALLBACK` - Use SQLite if MySQL fails (default: true)
- `PASSWORD_HASH_ITERATIONS` - Password hash iterations (default: 390000)

See `.env.example` for all available options.

## Deployment on Railway

Railway automatically detects and deploys this project using the provided `Dockerfile`.

### Quick Deploy

1. **Push to GitHub** (already done if you cloned this)
2. **Go to [railway.app](https://railway.app)**
3. **Click "New Project" → "Deploy from GitHub repo"**
4. **Select your `autoagentstudio` repository**
5. **Railway auto-deploys using the Dockerfile**
6. **Add environment variables** in the Railway dashboard:
   - `GEMINI_API_KEY` - Your API key
   - `SESSION_SECRET` - A random secure string
   - (Optional) `DATABASE_URL` if using external database

### Using with Railway Database

If you want to use Railway's MySQL/PostgreSQL:

1. In Railway dashboard, add a MySQL service to your project
2. Railway auto-populates `DATABASE_URL` environment variable
3. Your app automatically uses the hosted database

**Note**: The app works fine with SQLite fallback for quick testing!

## Project Structure

```
autoagentstudioapp/
├── main.py                 # FastAPI application & routes
├── agent.py               # AI prompt processing
├── database.py            # SQLAlchemy setup
├── models.py              # Database models
├── auth_utils.py          # Password hashing
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables (add to .gitignore)
├── .env.example          # Example configuration
└── templates/            # HTML templates
    ├── index.html
    ├── login.html
    ├── register.html
    ├── dashboard_home.html
    ├── generate_studio.html
    └── apps_studio.html
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Home page |
| GET | `/login` | Login page |
| POST | `/login` | Login form submission |
| GET | `/register` | Registration page |
| POST | `/register` | Register new user |
| GET | `/logout` | Logout user |
| GET | `/dashboard` | User dashboard |
| GET | `/generate-studio` | App generation page |
| POST | `/generate` | Generate new app |
| GET | `/apps-studio` | View all apps |
| GET | `/apps/{app_id}/view` | View generated app |
| GET | `/apps/{app_id}/download` | Download app as HTML |
| GET | `/media/image` | Image proxy endpoint |

## Security Notes

⚠️ **Before deploying to production:**
- Change `SESSION_SECRET` to a random string
- Enable `SESSION_HTTPS_ONLY=true` if using HTTPS
- Use a strong database password
- Rotate your Gemini API key regularly
- Consider rate limiting for API endpoints
- Use environment variables for all sensitive data

## Troubleshooting

### "MySQL connection failed"
The app falls back to SQLite automatically. To use MySQL:
- Ensure database is running and accessible
- Check `DATABASE_URL` or `DB_*` environment variables
- Set `ALLOW_SQLITE_FALLBACK=false` to enforce MySQL-only mode

### "No Models Available"
- Verify your `GEMINI_API_KEY` is valid
- Check Google AI API quota at [ai.google.dev](https://ai.google.dev)
- Ensure your API key has "Generative Language API" enabled

### Railway Deployment Fails
- Check Railway build logs for specific errors
- Verify all environment variables are set
- Ensure `Dockerfile` and requirements.txt are in correct locations

## Contributing

Feel free to fork and submit pull requests!

## License

MIT License - see LICENSE file for details

## Support

For issues and questions:
- GitHub Issues: [Create an issue](https://github.com/harshitha-730/autoagentstudio/issues)
- Google Generative AI Docs: [ai.google.dev](https://ai.google.dev)
- FastAPI Docs: [fastapi.tiangolo.com](https://fastapi.tiangolo.com)

---

**Made with ❤️ using FastAPI and Google Generative AI**

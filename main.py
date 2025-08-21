import os
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    send_from_directory,
    abort,
    jsonify,
    render_template_string,
    flash,
)
from werkzeug.utils import secure_filename

# -----------------------------
# Config
# -----------------------------
APP_NAME = "MovieHub"
DATABASE = os.environ.get("DATABASE_PATH", "movies.db")  # for local dev; ephemeral on Vercel
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "static/posters")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")  # set in Vercel Project Settings
ITEMS_PER_PAGE = int(os.environ.get("ITEMS_PER_PAGE", 24))

# Ensure folders exist (best-effort; on serverless this may be ephemeral)
Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "unsafe-dev-key")
app.config.update(UPLOAD_FOLDER=UPLOAD_FOLDER, MAX_CONTENT_LENGTH=10 * 1024 * 1024)  # 10MB

# -----------------------------
# DB helpers
# -----------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            year TEXT,
            quality TEXT,
            languages TEXT,
            description TEXT,
            tg_url TEXT,
            poster TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


init_db()

# -----------------------------
# Utils
# -----------------------------

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def require_admin(password: str) -> bool:
    return password and password == ADMIN_PASSWORD


# -----------------------------
# Templates (Tailwind via CDN)
# -----------------------------
BASE_TMPL = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ title }} — {{ app_name }}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="icon" href="data:," />
  <style>
    .card { @apply bg-white/5 rounded-2xl shadow-xl overflow-hidden backdrop-blur border border-white/10; }
    body { background: #0f1115; color: #e5e7eb; }
    a.btn { @apply inline-block px-4 py-2 rounded-xl font-medium border border-white/20 hover:bg-white/10; }
    input, textarea, select { @apply w-full bg-white/5 border border-white/10 rounded-xl px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500; }
    label { @apply text-sm text-gray-300; }
  </style>
</head>
<body class="min-h-screen">
  <header class="sticky top-0 z-30 bg-[#0f1115]/80 backdrop-blur border-b border-white/10">
    <div class="max-w-6xl mx-auto px-4 py-4 flex items-center gap-3">
      <a href="{{ url_for('home') }}" class="text-xl font-bold">{{ app_name }}</a>
      <form action="{{ url_for('home') }}" method="get" class="flex-1 flex gap-2">
        <input type="search" name="q" value="{{ q or '' }}" placeholder="Search movies..." />
        <select name="sort">
          <option value="new" {% if sort=='new' %}selected{% endif %}>Newest</option>
          <option value="az" {% if sort=='az' %}selected{% endif %}>A → Z</option>
        </select>
        <button class="a btn" type="submit">Search</button>
      </form>
      <a class="btn" href="{{ url_for('add_movie') }}">Add</a>
      <a class="btn" href="{{ url_for('api_movies') }}">API</a>
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-4 py-6">
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="mb-4 space-y-2">
          {% for m in messages %}
          <div class="bg-emerald-500/10 border border-emerald-500/30 text-emerald-200 rounded-xl px-4 py-2">{{ m }}</div>
          {% endfor %}
        </div>
      {% endif %}
    {% endwith %}

    {% block content %}{% endblock %}
  </main>

  <footer class="border-t border-white/10 text-sm text-gray-400">
    <div class="max-w-6xl mx-auto px-4 py-6 flex items-center justify-between">
      <p>© {{ now[:4] }} {{ app_name }} • Personal catalog. Use only with content you have rights to share.</p>
      <a class="hover:underline" href="{{ url_for('dmca') }}">DMCA / Terms</a>
    </div>
  </footer>
</body>
</html>
"""

HOME_TMPL = r"""
{% extends base %}
{% block content %}
  {% if movies|length == 0 %}
    <p class="text-gray-400">No movies yet. Click <a class="underline" href="{{ url_for('add_movie') }}">Add</a> to create one.</p>
  {% endif %}
  <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-5">
    {% for m in movies %}
    <article class="card group">
      <a href="{{ m.tg_url }}" target="_blank" rel="noopener" class="block">
        <img src="{{ url_for('poster_file', filename=m.poster) if m.poster else 'https://placehold.co/600x900/png?text=No+Poster' }}" alt="{{ m.title }}" class="w-full aspect-[2/3] object-cover group-hover:opacity-90"/>
      </a>
      <div class="p-3 space-y-1">
        <h3 class="font-semibold leading-tight">{{ m.title }}</h3>
        <p class="text-xs text-gray-400">{{ m.year or '' }} {{ ('• ' + m.quality) if m.quality else '' }} {{ ('• ' + m.languages) if m.languages else '' }}</p>
        <p class="text-sm text-gray-300 line-clamp-2">{{ m.description }}</p>
        <div class="pt-2 flex gap-2">
          <a class="btn" href="{{ m.tg_url }}" target="_blank" rel="noopener">Open in Telegram</a>
          <a class="btn" href="{{ url_for('delete_movie', movie_id=m.id) + '?' + urlencode({'key': admin_key}) }}" onclick="return confirm('Delete this movie?')">Delete</a>
        </div>
      </div>
    </article>
    {% endfor %}
  </div>

  {% if page_count > 1 %}
  <div class="mt-6 flex items-center gap-2">
    {% if page > 1 %}
      <a class="btn" href="?{{ urlencode({'q': q or '', 'sort': sort, 'page': page-1}) }}">Prev</a>
    {% endif %}
    <span class="px-3 py-2">Page {{ page }} / {{ page_count }}</span>
    {% if page < page_count %}
      <a class="btn" href="?{{ urlencode({'q': q or '', 'sort': sort, 'page': page+1}) }}">Next</a>
    {% endif %}
  </div>
  {% endif %}
{% endblock %}
"""

ADD_TMPL = r"""
{% extends base %}
{% block content %}
  <h1 class="text-2xl font-bold mb-4">Add movie</h1>
  <form class="grid grid-cols-1 md:grid-cols-2 gap-4" method="post" enctype="multipart/form-data">
    <div class="space-y-3">
      <div>
        <label>Admin Key</label>
        <input type="password" name="key" placeholder="Admin password" required />
      </div>
      <div>
        <label>Title *</label>
        <input name="title" required />
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div>
          <label>Year</label>
          <input name="year" />
        </div>
        <div>
          <label>Quality</label>
          <input name="quality" placeholder="1080p, 4K, etc" />
        </div>
        <div>
          <label>Languages</label>
          <input name="languages" placeholder="EN, HI, etc" />
        </div>
      </div>
      <div>
        <label>Telegram URL *</label>
        <input name="tg_url" placeholder="https://t.me/your_bot?start=..." required />
      </div>
      <div class="md:col-span-2">
        <label>Description</label>
        <textarea name="description" rows="4"></textarea>
      </div>
      <div>
        <label>Poster (PNG/JPG/WEBP, ≤10MB)</label>
        <input type="file" name="poster" accept="image/*" />
      </div>
      <div class="pt-2">
        <button class="btn" type="submit">Save</button>
        <a class="btn" href="{{ url_for('home') }}">Cancel</a>
      </div>
    </div>
  </form>
{% endblock %}
"""

DMCA_TMPL = r"""
{% extends base %}
{% block content %}
  <h1 class="text-2xl font-bold mb-4">DMCA / Terms</h1>
  <p class="text-gray-300">This is a personal catalog site. Only add content and links you have the rights to share. If you believe any content infringes your rights, contact the site owner for removal.</p>
{% endblock %}
"""

# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def home():
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "new")
    page = int(request.args.get("page", 1))

    conn = get_db()
    params = []
    where = ""
    if q:
        where = "WHERE title LIKE ? OR description LIKE ?"
        like = f"%{q}%"
        params.extend([like, like])

    order = "ORDER BY datetime(created_at) DESC" if sort == "new" else "ORDER BY lower(title) ASC"

    # count
    cur = conn.execute(f"SELECT COUNT(*) FROM movies {where}", params)
    total = cur.fetchone()[0]
    page_count = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    offset = (page - 1) * ITEMS_PER_PAGE

    cur = conn.execute(
        f"SELECT * FROM movies {where} {order} LIMIT ? OFFSET ?",
        (*params, ITEMS_PER_PAGE, offset),
    )
    movies = [dict(row) for row in cur.fetchall()]
    conn.close()

    return render_template_string(
        HOME_TMPL,
        base=BASE_TMPL,
        title="Home",
        movies=movies,
        app_name=APP_NAME,
        now=datetime.utcnow().isoformat(),
        q=q,
        sort=sort,
        page=page,
        page_count=page_count,
        urlencode=urlencode,
        admin_key=request.args.get("key", ""),
    )


@app.route("/add", methods=["GET", "POST"]) 
def add_movie():
    if request.method == "POST":
        key = request.form.get("key")
        if not require_admin(key):
            abort(401)
        title = (request.form.get("title") or "").strip()
        year = (request.form.get("year") or "").strip()
        quality = (request.form.get("quality") or "").strip()
        languages = (request.form.get("languages") or "").strip()
        description = (request.form.get("description") or "").strip()
        tg_url = (request.form.get("tg_url") or "").strip()
        if not title or not tg_url:
            abort(400)

        poster_filename = None
        file = request.files.get("poster")
        if file and file.filename:
            if not allowed_file(file.filename):
                abort(400, "Invalid file type")
            filename = secure_filename(file.filename)
            # Make unique
            filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{filename}"
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            poster_filename = filename

        conn = get_db()
        conn.execute(
            "INSERT INTO movies(title, year, quality, languages, description, tg_url, poster, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (title, year, quality, languages, description, tg_url, poster_filename, datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()
        flash("Movie added ✔")
        return redirect(url_for("home", **{"key": key}))

    return render_template_string(
        ADD_TMPL,
        base=BASE_TMPL,
        title="Add",
        app_name=APP_NAME,
        now=datetime.utcnow().isoformat(),
    )


@app.route("/delete/<int:movie_id>")
def delete_movie(movie_id: int):
    key = request.args.get("key")
    if not require_admin(key):
        abort(401)
    conn = get_db()
    cur = conn.execute("SELECT poster FROM movies WHERE id=?", (movie_id,))
    row = cur.fetchone()
    if row:
        poster = row[0]
        if poster:
            try:
                os.remove(os.path.join(app.config["UPLOAD_FOLDER"], poster))
            except FileNotFoundError:
                pass
        conn.execute("DELETE FROM movies WHERE id=?", (movie_id,))
        conn.commit()
    conn.close()
    flash("Deleted ✔")
    return redirect(url_for("home", **{"key": key}))


@app.route("/api/movies")
def api_movies():
    conn = get_db()
    cur = conn.execute("SELECT * FROM movies ORDER BY datetime(created_at) DESC")
    movies = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify(movies)


@app.route("/posters/<path:filename>")
def poster_file(filename):
    # Serve uploaded posters
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/dmca")
def dmca():
    return render_template_string(
        DMCA_TMPL,
        base=BASE_TMPL,
        title="DMCA",
        app_name=APP_NAME,
        now=datetime.utcnow().isoformat(),
    )


# Root health route for Vercel/uptime checks
@app.route("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}


# --------------
# Local run
# --------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

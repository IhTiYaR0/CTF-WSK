import uvicorn
import os
import uuid
import asyncio
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, func, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship
from passlib.context import CryptContext
from starlette.middleware.sessions import SessionMiddleware
import bcrypt

DATABASE_URL = "sqlite:///./ctf.db"
SECRET_KEY = "super-secret-ctf-key"
UPLOAD_DIR = "static/uploads"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- МОДЕЛИ ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    is_admin = Column(Boolean, default=False)
    is_root = Column(Boolean, default=False)
    progress = relationship("UserProgress", back_populates="user")

class Challenge(Base):
    __tablename__ = "challenges"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(String)
    question = Column(String)
    flag = Column(String)
    points = Column(Integer, default=100) # Новое поле: баллы
    file_path = Column(String, nullable=True) # Путь к файлу задания (опционально)
    answer_format = Column(String, nullable=True) # Формат ответа (опционально)
    module_id = Column(Integer, ForeignKey("modules.id"), nullable=True)
    max_attempts = Column(Integer, default=5)
    sort_order = Column(Integer, default=0)
    module = relationship("Module", back_populates="challenges")

class Module(Base):
    __tablename__ = "modules"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    challenges = relationship("Challenge", back_populates="module")

class UserProgress(Base):
    __tablename__ = "user_progress"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    challenge_id = Column(Integer, ForeignKey("challenges.id"))
    attempts = Column(Integer, default=0)
    is_solved = Column(Boolean, default=False)
    user = relationship("User", back_populates="progress")
    challenge = relationship("Challenge")

class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String, default="")

Base.metadata.create_all(bind=engine)

# --- ПОДГОТОВКА ФАЙЛОВ И БАЗЫ ---
os.makedirs(UPLOAD_DIR, exist_ok=True)

def ensure_challenge_file_column():
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(challenges)")).fetchall()
        col_names = {c[1] for c in cols}
        if "file_path" not in col_names:
            conn.execute(text("ALTER TABLE challenges ADD COLUMN file_path VARCHAR"))

ensure_challenge_file_column()

def ensure_challenge_answer_format_column():
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(challenges)")).fetchall()
        col_names = {c[1] for c in cols}
        if "answer_format" not in col_names:
            conn.execute(text("ALTER TABLE challenges ADD COLUMN answer_format VARCHAR"))

ensure_challenge_answer_format_column()

def ensure_challenge_module_column():
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(challenges)")).fetchall()
        col_names = {c[1] for c in cols}
        if "module_id" not in col_names:
            conn.execute(text("ALTER TABLE challenges ADD COLUMN module_id INTEGER"))

ensure_challenge_module_column()

def ensure_challenge_max_attempts_column():
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(challenges)")).fetchall()
        col_names = {c[1] for c in cols}
        if "max_attempts" not in col_names:
            conn.execute(text("ALTER TABLE challenges ADD COLUMN max_attempts INTEGER"))
        conn.execute(text("UPDATE challenges SET max_attempts = 5 WHERE max_attempts IS NULL"))

ensure_challenge_max_attempts_column()

def ensure_challenge_sort_order_column():
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(challenges)")).fetchall()
        col_names = {c[1] for c in cols}
        if "sort_order" not in col_names:
            conn.execute(text("ALTER TABLE challenges ADD COLUMN sort_order INTEGER"))
        conn.execute(text("UPDATE challenges SET sort_order = id WHERE sort_order IS NULL"))

ensure_challenge_sort_order_column()

def ensure_user_root_column():
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        col_names = {c[1] for c in cols}
        if "is_root" not in col_names:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_root BOOLEAN DEFAULT 0"))

ensure_user_root_column()

def ensure_root_policy():
    db = SessionLocal()
    try:
        # Root role belongs only to the dedicated "root" username.
        root_user = db.query(User).filter(func.lower(User.username) == "root").first()
        if root_user:
            root_user.is_root = True
            root_user.is_admin = True
            db.query(User).filter(User.id != root_user.id, User.is_root == True).update(
                {User.is_root: False},
                synchronize_session=False
            )
        else:
            db.query(User).filter(User.is_root == True).update(
                {User.is_root: False},
                synchronize_session=False
            )
        db.commit()
    finally:
        db.close()

ensure_root_policy()

def ensure_default_module_and_backfill():
    db = SessionLocal()
    try:
        if db.query(Module).count() == 0:
            db.add(Module(title="Модуль 1", is_active=True, sort_order=1))
            db.commit()
        default_module = db.query(Module).order_by(Module.id).first()
        if default_module:
            db.query(Challenge).filter(Challenge.module_id == None).update(
                {Challenge.module_id: default_module.id}
            )
            db.commit()
    finally:
        db.close()

ensure_default_module_and_backfill()

def renumber_module_titles(db: Session, module_id: int | None):
    if module_id is None:
        return
    challenges = db.query(Challenge).filter(Challenge.module_id == module_id).order_by(Challenge.sort_order, Challenge.id).all()
    for idx, ch in enumerate(challenges, start=1):
        ch.title = f"Задание #{idx}"
    db.commit()

def ensure_setting_exists(db: Session, key: str, default: str):
    if not db.query(Setting).filter(Setting.key == key).first():
        db.add(Setting(key=key, value=default))
        db.commit()

def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        return row.value
    return default

def set_setting(db: Session, key: str, value: str):
    row = db.query(Setting).filter(Setting.key == key).first()
    if not row:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()

# --- ИНИЦИАЛИЗАЦИЯ ---
templates = Jinja2Templates(directory="templates")
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

event_subscribers: set[asyncio.Queue] = set()

def notify_clients():
    for q in list(event_subscribers):
        try:
            q.put_nowait("update")
        except asyncio.QueueFull:
            pass

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def get_current_user(request: Request, db: Session):
    user_id = request.session.get("user_id")
    return db.query(User).filter(User.id == user_id).first() if user_id else None

def is_admin_or_root(user: User | None) -> bool:
    return bool(user and (user.is_admin or user.is_root))

def is_root_user(user: User | None) -> bool:
    return bool(user and user.is_root)

@app.get("/events")
async def events(request: Request):
    async def event_generator():
        q: asyncio.Queue = asyncio.Queue()
        event_subscribers.add(q)
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive without triggering UI refresh
                    yield "event: ping\ndata: 1\n\n"
        finally:
            event_subscribers.discard(q)
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )

def _sanitize_filename(name: str) -> str:
    name = os.path.basename(name).strip()
    if not name:
        return "file.txt"
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    cleaned = "".join(ch if ch in allowed else "_" for ch in name)
    return cleaned or "file.txt"

def _normalize_flag(value: str) -> str:
    # Ignore case and all whitespace
    return "".join(value.split()).lower()

def _unique_filename(filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while os.path.exists(os.path.join(UPLOAD_DIR, candidate)):
        candidate = f"{base}({counter}){ext}"
        counter += 1
    return candidate

async def save_upload_file(upload: UploadFile) -> str:
    original = upload.filename or ""
    safe_name = _sanitize_filename(original)
    safe_name = _unique_filename(safe_name)
    full_path = os.path.join(UPLOAD_DIR, safe_name)
    content = await upload.read()
    with open(full_path, "wb") as f:
        f.write(content)
    # Храним путь относительно /static
    return f"uploads/{safe_name}"

def remove_file_if_exists(path: str | None):
    if not path:
        return
    full_path = path
    if path.startswith("uploads/"):
        full_path = os.path.join("static", path)
    if path.startswith("static/"):
        full_path = path
    try:
        if os.path.exists(full_path):
            os.remove(full_path)
    except OSError:
        pass

# --- МАРШРУТЫ АВТОРИЗАЦИИ ---
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Пользователь уже существует"})
    is_first = db.query(User).count() == 0
    normalized_username = username.strip().lower()
    is_root_account = normalized_username == "root"
    # Хешируем пароль с bcrypt
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    new_user = User(
        username=username,
        password_hash=password_hash,
        is_admin=(is_first or is_root_account),
        is_root=is_root_account
    )
    db.add(new_user)
    db.commit()
    if is_root_account:
        ensure_root_policy()
    return RedirectResponse(url="/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный логин или пароль"})
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

# --- ГЛАВНАЯ СТРАНИЦА ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user: return RedirectResponse("/login")
    if is_admin_or_root(user): return RedirectResponse("/admin")

    ensure_setting_exists(db, "show_challenges", "1")
    show_challenges = get_setting(db, "show_challenges", "1") == "1"

    active_challenges = db.query(Challenge).join(Module).filter(Module.is_active == True).order_by(Module.sort_order, Challenge.sort_order, Challenge.id).all()
    challenges_data = []
    total_score = db.query(func.coalesce(func.sum(Challenge.points), 0)).join(UserProgress).filter(
        UserProgress.user_id == user.id,
        UserProgress.is_solved == True,
        UserProgress.challenge_id == Challenge.id
    ).scalar() or 0

    module_counters: dict[int, int] = {}
    for ch in active_challenges:
        prog = db.query(UserProgress).filter_by(user_id=user.id, challenge_id=ch.id).first()
        max_attempts = ch.max_attempts or 5
        status_text, css_class, can_open = "Доступно", "neutral", True
        if prog:
            if prog.is_solved:
                status_text, css_class = f"Решено (+{ch.points})", "success"
            elif prog.attempts >= max_attempts:
                status_text, css_class, can_open = "Провалено", "danger", False
            else:
                status_text = f"Попыток: {prog.attempts}/{max_attempts}"
        
        challenges_data.append({
            "id": ch.id,
            "title": ch.title,
            "points": ch.points,
            "status": status_text,
            "class": css_class,
            "can_open": can_open,
            "module_title": ch.module.title if ch.module else "",
            "module_number": module_counters.setdefault(ch.module_id or 0, 0) + 1
        })
        module_counters[ch.module_id or 0] += 1

    if not show_challenges:
        challenges_data = []
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "challenges": challenges_data,
        "total_score": total_score,
        "show_challenges": show_challenges
    })

# --- АДМИНКА ---

# --- РЕДАКТИРОВАНИЕ И УДАЛЕНИЕ ---

@app.get("/admin/edit/{ch_id}", response_class=HTMLResponse)
async def edit_challenge_page(request: Request, ch_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not is_admin_or_root(user): return RedirectResponse("/")
    
    challenge = db.query(Challenge).filter(Challenge.id == ch_id).first()
    if not challenge: return RedirectResponse("/admin")
    
    modules = db.query(Module).order_by(Module.sort_order, Module.id).all()
    return templates.TemplateResponse("edit_challenge.html", {"request": request, "ch": challenge, "modules": modules})

@app.post("/admin/edit/{ch_id}")
async def edit_challenge(
    request: Request, 
    ch_id: int,
    question: str = Form(...), 
    flag: str = Form(...), 
    points: int = Form(...), 
    answer_format: str = Form(""),
    module_id: int = Form(...),
    max_attempts: int = Form(...),
    remove_file: bool = Form(False),
    file: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not is_admin_or_root(user): raise HTTPException(403)

    challenge = db.query(Challenge).filter(Challenge.id == ch_id).first()
    if challenge:
        challenge.question = question
        challenge.flag = flag
        challenge.points = points
        challenge.answer_format = answer_format.strip() or None
        challenge.module_id = module_id
        challenge.max_attempts = max_attempts

        if remove_file and challenge.file_path:
            remove_file_if_exists(challenge.file_path)
            challenge.file_path = None

        if file and file.filename:
            if challenge.file_path:
                remove_file_if_exists(challenge.file_path)
            challenge.file_path = await save_upload_file(file)
        db.commit()
        renumber_module_titles(db, module_id)
        notify_clients()
    
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/reorder_challenges")
async def reorder_challenges(request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin):
        raise HTTPException(status_code=403)
    data = await request.json()
    ids = data.get("ids", [])
    module_id = data.get("module_id")
    for idx, ch_id in enumerate(ids, start=1):
        db.query(Challenge).filter(Challenge.id == ch_id).update({Challenge.sort_order: idx})
    db.commit()
    renumber_module_titles(db, module_id)
    notify_clients()
    return {"ok": True}

@app.post("/admin/delete/{ch_id}")
async def delete_challenge(request: Request, ch_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not is_admin_or_root(user): raise HTTPException(403)

    challenge = db.query(Challenge).filter(Challenge.id == ch_id).first()
    if challenge:
        if challenge.file_path:
            remove_file_if_exists(challenge.file_path)
        # Также удаляем прогресс пользователей по этому заданию, чтобы не было "мусора"
        db.query(UserProgress).filter_by(challenge_id=ch_id).delete()
        db.delete(challenge)
        db.commit()
        renumber_module_titles(db, challenge.module_id)
        notify_clients()
        
    return RedirectResponse(url="/admin", status_code=303)

# --- ОБНОВЛЕННАЯ АДМИНКА ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not is_admin_or_root(user): return RedirectResponse("/")

    ensure_setting_exists(db, "show_challenges", "1")
    show_challenges = get_setting(db, "show_challenges", "1") == "1"

    modules = db.query(Module).order_by(Module.sort_order, Module.id).all()
    challenges = db.query(Challenge).order_by(Challenge.module_id, Challenge.sort_order, Challenge.id).all()
    # Берем всех, кто не админ
    participants = db.query(User).filter(User.is_admin == False, User.is_root == False).all()
    
    users_stats = []
    for p in participants:
        # Ищем все прогрессы этого пользователя, которые помечены как "решено"
        solved_tasks = db.query(UserProgress).join(Challenge).filter(
            UserProgress.user_id == p.id, 
            UserProgress.is_solved == True
        ).all()
        
        total_score = 0
        solved_details = []
        total_attempts = 0
        
        for st in solved_tasks:
            total_score += st.challenge.points
            total_attempts += st.attempts
            # Сохраняем информацию: Название задания и количество попыток
            solved_details.append(f"{st.challenge.title} (с {st.attempts}-й попытки)")
        
        users_stats.append({
            "id": p.id,
            "username": p.username,
            "score": total_score,
            "attempts": total_attempts,
            "details": ", ".join(solved_details) if solved_details else "Нет решений"
        })

    # Сортируем: больше баллов — выше; при равных баллах меньше попыток — выше
    users_stats.sort(key=lambda x: (-x['score'], x['attempts']))
    
    # Определяем победителя (первый в отсортированном списке, если у него есть баллы)
    winner = None
    if users_stats and users_stats[0]['score'] > 0:
        winner = users_stats[0]['username']

    return templates.TemplateResponse("admin.html", {
        "request": request, 
        "user": user, 
        "challenges": challenges, 
        "users": users_stats,
        "winner_name": winner,
        "show_challenges": show_challenges,
        "modules": modules
    })

@app.post("/admin/toggle_challenges")
async def toggle_challenges(request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin):
        raise HTTPException(status_code=403)

    ensure_setting_exists(db, "show_challenges", "1")
    current = get_setting(db, "show_challenges", "1")
    new_value = "0" if current == "1" else "1"
    set_setting(db, "show_challenges", new_value)
    notify_clients()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/create_module")
async def create_module(request: Request, title: str = Form(...), db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin):
        raise HTTPException(status_code=403)
    max_order = db.query(func.max(Module.sort_order)).scalar() or 0
    mod = Module(title=title.strip(), is_active=True, sort_order=max_order + 1)
    db.add(mod)
    db.commit()
    notify_clients()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/module/{module_id}/toggle")
async def toggle_module(request: Request, module_id: int, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin):
        raise HTTPException(status_code=403)
    mod = db.query(Module).filter(Module.id == module_id).first()
    if mod:
        mod.is_active = not mod.is_active
        db.commit()
        notify_clients()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/module/{module_id}/delete")
async def delete_module(request: Request, module_id: int, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin):
        raise HTTPException(status_code=403)
    mod = db.query(Module).filter(Module.id == module_id).first()
    if mod:
        used = db.query(Challenge).filter(Challenge.module_id == module_id).count()
        if used == 0:
            db.delete(mod)
            db.commit()
            notify_clients()
    return RedirectResponse(url="/admin", status_code=303)

# Обновляем создание: убираем description из аргументов
@app.post("/admin/create")
async def create_challenge(
    request: Request,
    question: str = Form(...),
    flag: str = Form(...),
    points: int = Form(...),
    answer_format: str = Form(""),
    module_id: int = Form(...),
    max_attempts: int = Form(...),
    file: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if is_admin_or_root(user):
        # Передаем пустую строку в описание, так как поле в БД осталось, но нам оно не нужно
        max_order = db.query(func.max(Challenge.sort_order)).scalar() or 0
        file_path = None
        if file and file.filename:
            file_path = await save_upload_file(file)
        ch = Challenge(
            title="",
            description="",
            question=question,
            flag=flag,
            points=points,
            file_path=file_path,
            answer_format=answer_format.strip() or None,
            module_id=module_id,
            max_attempts=max_attempts,
            sort_order=max_order + 1
        )
        db.add(ch)
        db.commit()
        # Автоматическое название по порядку внутри модуля
        renumber_module_titles(db, module_id)
        notify_clients()
    return RedirectResponse(url="/admin", status_code=303)

    # --- СТРАНИЦА УЧАСТНИКА (ДЕТАЛИ) ---
@app.get("/admin/user/{user_id}", response_class=HTMLResponse)
async def view_user_details(request: Request, user_id: int, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin): return RedirectResponse("/")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user: return RedirectResponse("/admin")
    
    # Готовим отображение модуля и номер задания внутри модуля
    module_number_map: dict[int, int] = {}
    module_title_map: dict[int, str] = {}
    module_counters: dict[int, int] = {}
    all_challenges = db.query(Challenge).order_by(Challenge.module_id, Challenge.sort_order, Challenge.id).all()
    for ch in all_challenges:
        mid = ch.module_id or 0
        module_counters[mid] = module_counters.get(mid, 0) + 1
        module_number_map[ch.id] = module_counters[mid]
        module_title_map[ch.id] = ch.module.title if ch.module else ""

    # Получаем весь прогресс пользователя
    all_progress = db.query(UserProgress).filter(UserProgress.user_id == user_id).all()
    
    # Для каждого прогресса получаем информацию о задании
    user_challenges = []
    total_score = 0
    
    for prog in all_progress:
        challenge = db.query(Challenge).filter(Challenge.id == prog.challenge_id).first()
        if challenge:
            max_attempts = challenge.max_attempts or 5
            status = "Решено" if prog.is_solved else ("Провалено" if prog.attempts >= max_attempts else f"Попыток: {prog.attempts}/{max_attempts}")
            if prog.is_solved:
                total_score += challenge.points
            
            user_challenges.append({
                "id": challenge.id,
                "title": challenge.title,
                "points": challenge.points,
                "status": status,
                "is_solved": prog.is_solved,
                "attempts": prog.attempts,
                "max_attempts": max_attempts,
                "module_title": module_title_map.get(challenge.id, ""),
                "module_number": module_number_map.get(challenge.id, 1)
            })
    
    # Также получаем все задания, которые пользователь НЕ начинал
    started_challenge_ids = [prog.challenge_id for prog in all_progress]
    not_started = db.query(Challenge).filter(~Challenge.id.in_(started_challenge_ids)).all()
    
    for ch in not_started:
        max_attempts = ch.max_attempts or 5
        user_challenges.append({
            "id": ch.id,
            "title": ch.title,
            "points": ch.points,
            "status": "Не начато",
            "is_solved": False,
            "attempts": 0,
            "max_attempts": max_attempts,
            "module_title": module_title_map.get(ch.id, ""),
            "module_number": module_number_map.get(ch.id, 1)
        })
    
    # Сортируем по модулю и порядку внутри модуля
    user_challenges.sort(key=lambda x: (x['module_title'], x['module_number']))
    
    return templates.TemplateResponse("user_details.html", {
        "request": request,
        "admin": admin,
        "can_force_solve": is_root_user(admin),
        "target_user": user,
        "challenges": user_challenges,
        "total_score": total_score
    })

# --- СБРОС ПРОГРЕССА УЧАСТНИКА ---
@app.post("/admin/user/{user_id}/reset")
async def reset_user_progress(request: Request, user_id: int, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin): raise HTTPException(status_code=403)
    
    # Сбрасываем весь прогресс пользователя
    db.query(UserProgress).filter(UserProgress.user_id == user_id).delete()
    db.commit()
    notify_clients()
    
    return RedirectResponse(url=f"/admin/user/{user_id}", status_code=303)

# --- СБРОС ПРОГРЕССА ПО КОНКРЕТНОМУ ЗАДАНИЮ ---
@app.post("/admin/user/{user_id}/reset_challenge/{ch_id}")
async def reset_user_challenge(request: Request, user_id: int, ch_id: int, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin): raise HTTPException(status_code=403)
    
    db.query(UserProgress).filter(
        UserProgress.user_id == user_id,
        UserProgress.challenge_id == ch_id
    ).delete()
    db.commit()
    notify_clients()
    
    return RedirectResponse(url=f"/admin/user/{user_id}", status_code=303)

# --- ROOT: ПРИНУДИТЕЛЬНО ПОМЕТИТЬ ЗАДАНИЕ КАК РЕШЕННОЕ ---
@app.post("/admin/user/{user_id}/solve_challenge/{ch_id}")
async def force_solve_user_challenge(request: Request, user_id: int, ch_id: int, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_root_user(admin):
        raise HTTPException(status_code=403)

    target_user = db.query(User).filter(User.id == user_id).first()
    challenge = db.query(Challenge).filter(Challenge.id == ch_id).first()
    if not target_user or not challenge:
        return RedirectResponse(url=f"/admin/user/{user_id}", status_code=303)
    if is_admin_or_root(target_user):
        return RedirectResponse(url=f"/admin/user/{user_id}", status_code=303)

    prog = db.query(UserProgress).filter(
        UserProgress.user_id == user_id,
        UserProgress.challenge_id == ch_id
    ).first()
    if not prog:
        prog = UserProgress(user_id=user_id, challenge_id=ch_id, attempts=1, is_solved=True)
        db.add(prog)
    else:
        prog.is_solved = True
        if prog.attempts < 1:
            prog.attempts = 1

    db.commit()
    notify_clients()
    return RedirectResponse(url=f"/admin/user/{user_id}", status_code=303)

# --- УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ---
@app.post("/admin/user/{user_id}/delete")
async def delete_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin):
        raise HTTPException(status_code=403)

    # Запрещаем удалять самого себя
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Нельзя удалить текущего администратора")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(url="/admin", status_code=303)

    # Запрещаем удалять админов и root
    if is_admin_or_root(user):
        raise HTTPException(status_code=400, detail="Нельзя удалить администратора")

    db.query(UserProgress).filter(UserProgress.user_id == user_id).delete()
    db.delete(user)
    db.commit()
    notify_clients()

    return RedirectResponse(url="/admin", status_code=303)

# --- СМЕНА ПАРОЛЯ АДМИНА ---
@app.get("/admin/change_password", response_class=HTMLResponse)
async def change_password_page(request: Request, db: Session = Depends(get_db)):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin): return RedirectResponse("/")
    return templates.TemplateResponse("change_password.html", {"request": request})

@app.post("/admin/change_password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    admin = get_current_user(request, db)
    if not is_admin_or_root(admin):
        return RedirectResponse("/")

    if not bcrypt.checkpw(current_password.encode('utf-8'), admin.password_hash.encode('utf-8')):
        return templates.TemplateResponse("change_password.html", {"request": request, "error": "Текущий пароль неверен"})

    if new_password != confirm_password:
        return templates.TemplateResponse("change_password.html", {"request": request, "error": "Пароли не совпадают"})

    if len(new_password) < 6:
        return templates.TemplateResponse("change_password.html", {"request": request, "error": "Пароль должен быть не короче 6 символов"})

    admin.password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    db.commit()
    notify_clients()
    return templates.TemplateResponse("change_password.html", {"request": request, "success": "Пароль успешно изменён"})

# --- СТРАНИЦА ЗАДАНИЯ (ИСПРАВЛЕНА ЛОГИКА ОБНОВЛЕНИЯ) ---
@app.get("/challenge/{ch_id}", response_class=HTMLResponse)
async def view_challenge(request: Request, ch_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user: return RedirectResponse("/login")
    if not is_admin_or_root(user):
        ensure_setting_exists(db, "show_challenges", "1")
        show_challenges = get_setting(db, "show_challenges", "1") == "1"
        if not show_challenges:
            return RedirectResponse("/", status_code=303)
    
    challenge = db.query(Challenge).filter(Challenge.id == ch_id).first()
    if not challenge:
        return RedirectResponse("/", status_code=303)
    if not is_admin_or_root(user):
        if not challenge.module or not challenge.module.is_active:
            return RedirectResponse("/", status_code=303)
    prog = db.query(UserProgress).filter_by(user_id=user.id, challenge_id=ch_id).first()
    
    # Сообщения об успехе/ошибке берем из сессии и тут же удаляем
    msg = request.session.pop("msg", None)
    msg_type = request.session.pop("msg_type", "error")

    attempts = prog.attempts if prog else 0
    solved = prog.is_solved if prog else False
    max_attempts = challenge.max_attempts or 5
    blocked = (attempts >= max_attempts and not solved)
    module_number = 1
    if challenge.module_id:
        module_challenges = db.query(Challenge).filter(Challenge.module_id == challenge.module_id).order_by(Challenge.sort_order, Challenge.id).all()
        for idx, ch in enumerate(module_challenges, start=1):
            if ch.id == challenge.id:
                module_number = idx
                break

    return templates.TemplateResponse("challenge.html", {
        "request": request,
        "challenge": challenge,
        "attempts_left": max_attempts - attempts,
        "max_attempts": max_attempts,
        "module_number": module_number,
        "solved": solved, "blocked": blocked, "msg": msg, "msg_type": msg_type
    })

@app.post("/challenge/{ch_id}")
async def submit_flag(request: Request, ch_id: int, flag: str = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user: return RedirectResponse("/login")

    challenge = db.query(Challenge).filter(Challenge.id == ch_id).first()
    prog = db.query(UserProgress).filter_by(user_id=user.id, challenge_id=ch_id).first()

    if not prog:
        prog = UserProgress(user_id=user.id, challenge_id=ch_id, attempts=0)
        db.add(prog)

    # Если уже решено или заблокировано — просто уходим обратно
    max_attempts = challenge.max_attempts or 5
    if prog.is_solved or prog.attempts >= max_attempts:
        return RedirectResponse(url=f"/challenge/{ch_id}", status_code=303)

    # Проверка флага
    if _normalize_flag(flag) == _normalize_flag(challenge.flag):
        prog.is_solved = True
        prog.attempts += 1
        request.session["msg"] = "Правильно! Баллы начислены."
        request.session["msg_type"] = "success"
    else:
        prog.attempts += 1
        request.session["msg"] = "Неверный флаг!"
        request.session["msg_type"] = "error"
    
    db.commit()
    notify_clients()
    # Важно: делаем Redirect, чтобы обновление страницы (GET) не вызывало повторный POST
    return RedirectResponse(url=f"/challenge/{ch_id}", status_code=303)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


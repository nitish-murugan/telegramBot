"""
database.py — SQLAlchemy models and DB helpers
"""
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Text,
    DateTime, Boolean, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot_data.db")

# Fix for SQLAlchemy + render postgres URLs
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
Session = scoped_session(SessionFactory)

Base = declarative_base()


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(128))
    first_name = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow)

    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    reminders = relationship("Reminder", back_populates="user", cascade="all, delete-orphan")
    images = relationship("UserImage", back_populates="user", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="user", cascade="all, delete-orphan")
    chat_history = relationship("ChatHistory", back_populates="user", cascade="all, delete-orphan")


class Memory(Base):
    """Stores key facts the bot has learned about the user."""
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    key = Column(String(256), nullable=False)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="memories")


class ChatHistory(Base):
    """Rolling conversation history per user."""
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(16), nullable=False)   # "user" or "model"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="chat_history")


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    telegram_id = Column(BigInteger, nullable=False)
    message = Column(Text, nullable=False)
    remind_at = Column(DateTime, nullable=False)
    is_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="reminders")


class UserImage(Base):
    __tablename__ = "user_images"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(256), nullable=False)
    file_path = Column(String(512), nullable=False)
    description = Column(Text)           # AI-generated description for semantic search
    tags = Column(Text)                  # JSON list of tags
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="images")


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=False)
    is_protected = Column(Boolean, default=False)
    password_hash = Column(String(256))   # SHA-256 hex
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="notes")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def init_db():
    Base.metadata.create_all(engine)


def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None) -> User:
    session = Session()
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name)
        session.add(user)
        session.commit()
    else:
        # Update display name if changed
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            changed = True
        if changed:
            session.commit()
    return user


def get_chat_history(user_id: int, limit: int = 20):
    session = Session()
    rows = (
        session.query(ChatHistory)
        .filter_by(user_id=user_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def save_chat_message(user_id: int, role: str, content: str):
    session = Session()
    msg = ChatHistory(user_id=user_id, role=role, content=content)
    session.add(msg)
    session.commit()
    # Keep last 40 messages
    total = session.query(ChatHistory).filter_by(user_id=user_id).count()
    if total > 40:
        oldest = (
            session.query(ChatHistory)
            .filter_by(user_id=user_id)
            .order_by(ChatHistory.created_at.asc())
            .limit(total - 40)
            .all()
        )
        for old in oldest:
            session.delete(old)
        session.commit()


def get_user_memories(user_id: int) -> dict:
    session = Session()
    rows = session.query(Memory).filter_by(user_id=user_id).all()
    return {r.key: r.value for r in rows}


def upsert_memory(user_id: int, key: str, value: str):
    session = Session()
    mem = session.query(Memory).filter_by(user_id=user_id, key=key).first()
    if mem:
        mem.value = value
        mem.updated_at = datetime.utcnow()
    else:
        mem = Memory(user_id=user_id, key=key, value=value)
        session.add(mem)
    session.commit()


def get_pending_reminders():
    session = Session()
    return session.query(Reminder).filter_by(is_sent=False).all()


def mark_reminder_sent(reminder_id: int):
    session = Session()
    r = session.query(Reminder).filter_by(id=reminder_id).first()
    if r:
        r.is_sent = True
        session.commit()


def add_reminder(user_id: int, telegram_id: int, message: str, remind_at: datetime) -> Reminder:
    session = Session()
    r = Reminder(user_id=user_id, telegram_id=telegram_id, message=message, remind_at=remind_at)
    session.add(r)
    session.commit()
    return r


def save_image_record(user_id: int, name: str, file_path: str, description: str, tags: str) -> UserImage:
    session = Session()
    img = UserImage(
        user_id=user_id, name=name, file_path=file_path,
        description=description, tags=tags
    )
    session.add(img)
    session.commit()
    return img


def search_images(user_id: int, query: str):
    session = Session()
    q = query.lower()
    images = session.query(UserImage).filter_by(user_id=user_id).all()
    results = []
    for img in images:
        searchable = f"{img.name} {img.description or ''} {img.tags or ''}".lower()
        if q in searchable:
            results.append(img)
    return results


def add_note(user_id: int, title: str, content: str, is_protected: bool = False, password_hash: str = None) -> Note:
    session = Session()
    note = Note(
        user_id=user_id, title=title, content=content,
        is_protected=is_protected, password_hash=password_hash
    )
    session.add(note)
    session.commit()
    return note


def get_notes(user_id: int):
    session = Session()
    return session.query(Note).filter_by(user_id=user_id).order_by(Note.created_at.desc()).all()


def get_note_by_id(note_id: int, user_id: int):
    session = Session()
    return session.query(Note).filter_by(id=note_id, user_id=user_id).first()


def delete_note(note_id: int, user_id: int) -> bool:
    session = Session()
    note = session.query(Note).filter_by(id=note_id, user_id=user_id).first()
    if note:
        session.delete(note)
        session.commit()
        return True
    return False

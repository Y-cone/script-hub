from datetime import datetime
from sqlalchemy import Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    relative_path: Mapped[str] = mapped_column(String(1024))
    extension: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(50))  # python/shell/bat/powershell
    description: Mapped[str] = mapped_column(Text, default="")
    parameters: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    working_dir: Mapped[str] = mapped_column(String(1024), nullable=True)
    env_vars: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    dangerous: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="scan")  # scan | upload
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
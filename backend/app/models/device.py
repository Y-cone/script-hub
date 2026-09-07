from datetime import datetime
from sqlalchemy import Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base


class Device(Base):
    """远程设备（SSH 凭据存 keyring/文件，DB 仅存引用）"""
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(20))  # linux | mac
    host: Mapped[str] = mapped_column(String(512))
    port: Mapped[int] = mapped_column(Integer, default=22)
    auth_type: Mapped[str] = mapped_column(String(20))  # password | key
    username: Mapped[str] = mapped_column(String(128))
    auth_ref: Mapped[str] = mapped_column(String(128))  # keyring 引用键（含密码/密钥路径）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
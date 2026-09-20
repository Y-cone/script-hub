from datetime import datetime
from sqlalchemy import Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base


class Schedule(Base):
    """定时调度任务"""
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[int] = mapped_column(Integer, ForeignKey("scripts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    cron_expr: Mapped[str | None] = mapped_column(String(50), nullable=True)  # cron 表达式
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 简单间隔
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    parameters: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    env_vars: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    working_dir: Mapped[str] = mapped_column(String(1024), nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, default=0)
    device_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 目标设备（NULL=本机）
    # V5-E 调度下放：local=工具内 APScheduler（默认）/ device=目标设备系统调度器
    exec_location: Mapped[str] = mapped_column(String(10), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
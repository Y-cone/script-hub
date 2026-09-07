from datetime import datetime
from sqlalchemy import Integer, String, Text, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base


class RunHistory(Base):
    __tablename__ = "run_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True)
    parameters: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    command: Mapped[str] = mapped_column(String(2048), default="")
    output: Mapped[str] = mapped_column(Text, default="")
    output_file: Mapped[str] = mapped_column(String(1024), nullable=True)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # running/success/failed/killed/timeout
    duration: Mapped[float] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    is_scheduled: Mapped[int] = mapped_column(Integer, default=0)
    schedule_id: Mapped[int] = mapped_column(Integer, nullable=True)
    device_id: Mapped[int] = mapped_column(Integer, nullable=True)

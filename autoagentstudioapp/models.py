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

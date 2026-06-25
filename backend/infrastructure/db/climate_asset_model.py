from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Enum, Integer, String
from sqlalchemy.orm import declarative_base, Mapped, mapped_column

from domain.entities.climate_asset import ClimateAssetStatus

Base = declarative_base()


class ClimateAssetModel(Base):
    __tablename__ = "climate_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    variable: Mapped[str] = mapped_column(String, nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    month: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ClimateAssetStatus] = mapped_column(
        Enum(ClimateAssetStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ClimateAssetModel(id={self.id}, provider={self.provider}, variable={self.variable})>"

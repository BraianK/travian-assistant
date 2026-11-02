"""SQLAlchemy ORM models for Travian Kingdoms Assistant."""

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Float, BigInteger, DateTime, Text, CheckConstraint, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

Base = declarative_base()


class Account(Base):
    """Game account table."""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False)
    world = Column(String, nullable=False)
    server = Column(String, nullable=False)
    alliance = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    last_login = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "username": self.username,
            "world": self.world,
            "server": self.server,
            "alliance": self.alliance,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        return cls(
            account_id=data.get("account_id", ""),
            username=data.get("username", ""),
            world=data.get("world", ""),
            server=data.get("server", ""),
            alliance=data.get("alliance"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            last_login=datetime.fromisoformat(data["last_login"]) if data.get("last_login") else None,
        )


class Village(Base):
    """Village information table."""
    __tablename__ = "villages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False)
    village_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)
    coordinates = Column(Text, nullable=False)  # JSON: {"row": int, "col": int, "field": int}
    population = Column(Integer, default=0)
    farm_level = Column(Integer, default=0)
    chief_level = Column(Integer, default=0)
    temple_level = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "village_id": self.village_id,
            "name": self.name,
            "coordinates": self.coordinates,
            "population": self.population,
            "farm_level": self.farm_level,
            "chief_level": self.chief_level,
            "temple_level": self.temple_level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Village":
        return cls(
            account_id=data.get("account_id", ""),
            village_id=data.get("village_id", ""),
            name=data.get("name"),
            coordinates=data.get("coordinates", "{}"),
            population=data.get("population", 0),
            farm_level=data.get("farm_level", 0),
            chief_level=data.get("chief_level", 0),
            temple_level=data.get("temple_level", 0),
        )


class ResourceRecord(Base):
    """Time-series resource data table."""
    __tablename__ = "resources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    village_id = Column(Integer, nullable=False)
    resource_type = Column(String, nullable=False)
    current_amount = Column(Integer, nullable=False)
    max_capacity = Column(Integer, nullable=False)
    production_rate = Column(Float, nullable=False)
    recorded_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        CheckConstraint("resource_type IN ('wood', 'clay', 'iron', 'grain')", name="resource_type_check"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "village_id": self.village_id,
            "resource_type": self.resource_type,
            "current_amount": self.current_amount,
            "max_capacity": self.max_capacity,
            "production_rate": self.production_rate,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }


class TroopRecord(Base):
    """Troop deployment and training table."""
    __tablename__ = "troops"

    id = Column(Integer, primary_key=True, autoincrement=True)
    village_id = Column(Integer, nullable=False)
    unit_type = Column(String, nullable=False)
    count = Column(Integer, default=0)
    training_start = Column(DateTime, nullable=True)
    training_end = Column(DateTime, nullable=True)
    recorded_at = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "village_id": self.village_id,
            "unit_type": self.unit_type,
            "count": self.count,
            "training_start": self.training_start.isoformat() if self.training_start else None,
            "training_end": self.training_end.isoformat() if self.training_end else None,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }


class BuildingQueue(Base):
    """Building construction queue table."""
    __tablename__ = "building_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    village_id = Column(Integer, nullable=False)
    building_type = Column(String, nullable=False)
    target_level = Column(Integer, nullable=True)
    start_time = Column(DateTime, nullable=True)
    completion_time = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    is_active = Column(Integer, default=1)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "village_id": self.village_id,
            "building_type": self.building_type,
            "target_level": self.target_level,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "completion_time": self.completion_time.isoformat() if self.completion_time else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "is_active": bool(self.is_active),
        }


class DashboardSnapshot(Base):
    """Latest persisted dashboard details for a village."""
    __tablename__ = "dashboard_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    village_id = Column(String, nullable=False)
    snapshot_type = Column(String, nullable=False)
    payload = Column(Text, nullable=False)
    recorded_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("village_id", "snapshot_type", name="dashboard_snapshot_village_type"),
    )


class AttackLog(Base):
    """Attack results logging table."""
    __tablename__ = "attack_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attacker_village_id = Column(String, nullable=True)
    target_village_id = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.now)
    outcome = Column(String, nullable=False)
    resources_looted = Column(Integer, default=0)
    notes = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "attacker_village_id": self.attacker_village_id,
            "target_village_id": self.target_village_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "outcome": self.outcome,
            "resources_looted": self.resources_looted,
            "notes": self.notes,
        }


class ScoutReport(Base):
    """Scout intelligence data table."""
    __tablename__ = "scout_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_village_id = Column(String, nullable=False)
    target_coordinates = Column(Text, nullable=False)
    target_alliance = Column(String, nullable=True)
    village_name = Column(String, nullable=True)
    reported_troops = Column(Text, nullable=False)  # JSON
    total_military = Column(Integer, default=0)
    barracks_level = Column(Integer, default=0)
    training_camp_level = Column(Integer, default=0)
    wall_level = Column(Integer, default=0)
    warehouse_level = Column(Integer, default=0)
    collected_at = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_village_id": self.target_village_id,
            "target_coordinates": self.target_coordinates,
            "target_alliance": self.target_alliance,
            "village_name": self.village_name,
            "reported_troops": self.reported_troops,
            "total_military": self.total_military,
            "barracks_level": self.barracks_level,
            "training_camp_level": self.training_camp_level,
            "wall_level": self.wall_level,
            "warehouse_level": self.warehouse_level,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
        }


def init_db(db_path: str = "./data/travian.db") -> Session:
    """Initialize database and create all tables."""
    from pathlib import Path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{db_path}", echo=False, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()


def get_session(db_path: str = "./data/travian.db") -> Session:
    """Get a database session."""
    try:
        engine = create_engine(f"sqlite:///{db_path}", echo=False, connect_args={"check_same_thread": False})
        # Check if tables exist
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        if not tables or "accounts" not in tables:
            Base.metadata.create_all(engine)
        
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()
    except Exception as e:
        print(f"Database init warning: {e}")
        engine = create_engine(f"sqlite:///{db_path}", echo=False, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()

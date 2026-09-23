from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text,
    DateTime, ForeignKey, JSON, Enum as SAEnum, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import uuid

from database import Base


def gen_uuid():
    return str(uuid.uuid4())


# ---------- Enums ----------

class SourceType(str, enum.Enum):
    reddit = "reddit"
    hackernews = "hackernews"
    google_trends = "google_trends"
    twitter = "twitter"
    bluesky = "bluesky"
    rss = "rss"


class ItemType(str, enum.Enum):
    pain_point = "pain_point"
    trend = "trend"
    opportunity = "opportunity"
    complaint = "complaint"
    wish = "wish"
    unknown = "unknown"


class SolutionStatus(str, enum.Enum):
    draft = "draft"
    saved = "saved"
    dismissed = "dismissed"


class JobStatus(str, enum.Enum):
    running = "running"
    paused = "paused"
    failed = "failed"
    idle = "idle"


class AlertStatus(str, enum.Enum):
    active = "active"
    triggered = "triggered"
    paused = "paused"


# ---------- Models ----------

class Source(Base):
    __tablename__ = "sources"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    type = Column(SAEnum(SourceType, native_enum=False), nullable=False)
    config = Column(JSON, default=dict)          # subreddits, keywords, etc.
    is_active = Column(Boolean, default=True)
    fetch_interval_minutes = Column(Integer, default=30)
    last_fetched_at = Column(DateTime, nullable=True)
    total_items_fetched = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    feed_items = relationship("FeedItem", back_populates="source", cascade="all, delete")


class FeedItem(Base):
    __tablename__ = "feed_items"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_feed_items_source_external"),
        Index("ix_feed_items_source_fetched", "source_id", "fetched_at"),
        Index("ix_feed_items_external_id", "external_id"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    source_id = Column(String, ForeignKey("sources.id"), nullable=False)
    external_id = Column(String, nullable=True)     # original post ID
    title = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    author = Column(String, nullable=True)
    score = Column(Integer, default=0)              # upvotes/likes
    comment_count = Column(Integer, default=0)
    raw_data = Column(JSON, default=dict)
    is_duplicate = Column(Boolean, default=False)
    fetched_at = Column(DateTime, server_default=func.now())

    source = relationship("Source", back_populates="feed_items")
    classification = relationship("Classification", back_populates="feed_item", uselist=False)


class Classification(Base):
    __tablename__ = "classifications"

    id = Column(String, primary_key=True, default=gen_uuid)
    feed_item_id = Column(String, ForeignKey("feed_items.id"), nullable=False)
    item_type = Column(SAEnum(ItemType, native_enum=False), default=ItemType.unknown)
    topic = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    audience = Column(String, nullable=True)
    severity = Column(Float, default=0.0)       # 0-10
    keywords = Column(JSON, default=list)
    sentiment = Column(String, nullable=True)   # positive/negative/neutral
    cluster_id = Column(String, ForeignKey("clusters.id"), nullable=True)
    classified_at = Column(DateTime, server_default=func.now())
    failed = Column(Boolean, default=False)
    error = Column(Text, nullable=True)

    feed_item = relationship("FeedItem", back_populates="classification")
    cluster = relationship("Cluster", back_populates="classifications")


class Trend(Base):
    __tablename__ = "trends"

    id = Column(String, primary_key=True, default=gen_uuid)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(SAEnum(ItemType, native_enum=False), default=ItemType.trend)
    volume = Column(Integer, default=1)         # how many posts
    score = Column(Float, default=0.0)
    keywords = Column(JSON, default=list)
    audience = Column(String, nullable=True)
    is_rising = Column(Boolean, default=False)
    first_seen_at = Column(DateTime, server_default=func.now())
    last_seen_at = Column(DateTime, server_default=func.now())

    pain_points = relationship("PainPoint", back_populates="trend")
    solutions = relationship("Solution", back_populates="trend")


class PainPoint(Base):
    __tablename__ = "pain_points"

    id = Column(String, primary_key=True, default=gen_uuid)
    trend_id = Column(String, ForeignKey("trends.id"), nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    audience = Column(String, nullable=True)
    severity = Column(Float, default=0.0)
    frequency = Column(Integer, default=1)
    keywords = Column(JSON, default=list)
    is_dismissed = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    trend = relationship("Trend", back_populates="pain_points")
    solutions = relationship("Solution", back_populates="pain_point")


class Solution(Base):
    __tablename__ = "solutions"

    id = Column(String, primary_key=True, default=gen_uuid)
    pain_point_id = Column(String, ForeignKey("pain_points.id"), nullable=True)
    trend_id = Column(String, ForeignKey("trends.id"), nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    business_model = Column(Text, nullable=True)
    target_audience = Column(String, nullable=True)
    risks = Column(Text, nullable=True)
    validation_notes = Column(Text, nullable=True)
    status = Column(SAEnum(SolutionStatus, native_enum=False), default=SolutionStatus.draft)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    pain_point = relationship("PainPoint", back_populates="solutions")
    trend = relationship("Trend", back_populates="solutions")


class Cluster(Base):
    __tablename__ = "clusters"

    id = Column(String, primary_key=True, default=gen_uuid)
    label = Column(String, nullable=False)
    keywords = Column(JSON, default=list)
    item_count = Column(Integer, default=0)
    centroid = Column(JSON, nullable=True)      # embedding vector
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    classifications = relationship("Classification", back_populates="cluster")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    condition = Column(JSON, default=dict)        # {type: "keyword", value: "AI", threshold: 10}
    status = Column(SAEnum(AlertStatus, native_enum=False), default=AlertStatus.active)
    notification_channel = Column(String, default="log")  # log, email, webhook
    notification_config = Column(JSON, default=dict)
    last_triggered_at = Column(DateTime, nullable=True)
    trigger_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

    history = relationship("AlertHistory", back_populates="alert", cascade="all, delete")


class AlertHistory(Base):
    __tablename__ = "alert_history"

    id = Column(String, primary_key=True, default=gen_uuid)
    alert_id = Column(String, ForeignKey("alerts.id"), nullable=False)
    triggered_at = Column(DateTime, server_default=func.now())
    payload = Column(JSON, default=dict)

    alert = relationship("Alert", back_populates="history")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    status = Column(SAEnum(JobStatus, native_enum=False), default=JobStatus.idle)
    interval_minutes = Column(Integer, default=30)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    run_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

    logs = relationship("JobLog", back_populates="job", cascade="all, delete")


class JobLog(Base):
    __tablename__ = "job_logs"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    started_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    success = Column(Boolean, default=True)
    items_processed = Column(Integer, default=0)
    message = Column(Text, nullable=True)

    job = relationship("Job", back_populates="logs")


class AppSettings(Base):
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(JSON, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

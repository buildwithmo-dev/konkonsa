from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any
from datetime import datetime
from models import SourceType, ItemType, SolutionStatus, JobStatus, AlertStatus


# ---------- Source ----------

class SourceCreate(BaseModel):
    name: str
    type: SourceType
    config: dict = {}
    fetch_interval_minutes: int = 30

class SourceUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[SourceType] = None  # <--- Added this line!
    config: Optional[dict] = None
    is_active: Optional[bool] = None
    fetch_interval_minutes: Optional[int] = None

class SourceOut(BaseModel):
    id: str
    name: str
    type: SourceType
    config: dict
    is_active: bool
    fetch_interval_minutes: int
    last_fetched_at: Optional[datetime]
    total_items_fetched: int
    error_message: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}


# ---------- Feed ----------

class FeedItemOut(BaseModel):
    id: str
    source_id: str
    external_id: Optional[str]
    title: Optional[str]
    body: Optional[str]
    url: Optional[str]
    author: Optional[str]
    score: int
    comment_count: int
    is_duplicate: bool
    fetched_at: datetime
    model_config = {"from_attributes": True}


# ---------- Classification ----------

class ClassifyRequest(BaseModel):
    text: str
    context: Optional[str] = None

class ClassifyBatchRequest(BaseModel):
    feed_item_ids: list[str]

class _KeywordsListMixin:
    @field_validator("keywords", mode="before", check_fields=False)
    @classmethod
    def normalize_keywords(cls, value):
        # Existing database rows may contain NULL/None.
        # The public API contract always exposes keywords as a list.
        return [] if value is None else value

class ClassificationOut(_KeywordsListMixin, BaseModel):
    id: str
    feed_item_id: str
    item_type: ItemType
    topic: Optional[str]
    summary: Optional[str]
    audience: Optional[str]
    severity: float
    keywords: list[str]
    sentiment: Optional[str]
    cluster_id: Optional[str]
    classified_at: datetime
    failed: bool
    error: Optional[str]
    model_config = {"from_attributes": True}


# ---------- Trends ----------

class TrendOut(_KeywordsListMixin, BaseModel):
    id: str
    title: str
    description: Optional[str]
    category: ItemType
    volume: int
    score: float
    keywords: list[str]
    audience: Optional[str]
    is_rising: bool
    first_seen_at: datetime
    last_seen_at: datetime
    model_config = {"from_attributes": True}


# ---------- Pain Points ----------

class PainPointUpdate(BaseModel):
    severity: Optional[float] = Field(None, ge=0, le=10)

class PainPointOut(_KeywordsListMixin, BaseModel):
    id: str
    trend_id: Optional[str]
    title: str
    description: Optional[str]
    audience: Optional[str]
    severity: float
    frequency: int | None = None
    keywords: list[str] = Field(default_factory=list)
    is_dismissed: bool
    created_at: datetime
    model_config = {"from_attributes": True}


# ---------- Solutions ----------

class SolutionGenerateRequest(BaseModel):
    pain_point_id: Optional[str] = None
    trend_id: Optional[str] = None
    count: int = Field(default=3, ge=1, le=10)

class SolutionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    business_model: Optional[str] = None
    target_audience: Optional[str] = None
    risks: Optional[str] = None
    status: Optional[SolutionStatus] = None   # new — lets the Solutions page Save/Dismiss actions work

class SolutionOut(BaseModel):
    id: str
    pain_point_id: Optional[str]
    trend_id: Optional[str]
    title: str
    description: Optional[str]
    business_model: Optional[str]
    target_audience: Optional[str]
    risks: Optional[str]
    validation_notes: Optional[str]
    status: SolutionStatus
    created_at: datetime
    model_config = {"from_attributes": True}


# ---------- Search ----------

class SearchResult(BaseModel):
    type: str           # trend | pain_point | solution | feed_item
    id: str
    title: str
    snippet: Optional[str]
    score: Optional[float]


# ---------- Clusters ----------

class ClusterOut(_KeywordsListMixin, BaseModel):
    id: str
    label: str
    keywords: list[str]
    item_count: int
    created_at: datetime
    model_config = {"from_attributes": True}


# ---------- Alerts ----------

class AlertCreate(BaseModel):
    name: str
    condition: dict
    notification_channel: str = "log"
    notification_config: dict = {}

class AlertUpdate(BaseModel):
    name: Optional[str] = None
    condition: Optional[dict] = None
    notification_channel: Optional[str] = None
    notification_config: Optional[dict] = None

class AlertOut(BaseModel):
    id: str
    name: str
    condition: dict
    status: AlertStatus
    notification_channel: str
    last_triggered_at: Optional[datetime]
    trigger_count: int
    created_at: datetime
    model_config = {"from_attributes": True}

class AlertHistoryOut(BaseModel):
    id: str
    alert_id: str
    triggered_at: datetime
    payload: dict
    model_config = {"from_attributes": True}


# ---------- Jobs ----------

class JobOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: JobStatus
    interval_minutes: int
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    last_error: Optional[str]
    run_count: int
    model_config = {"from_attributes": True}

class JobLogOut(BaseModel):
    id: str
    job_id: str
    started_at: datetime
    finished_at: Optional[datetime]
    success: bool
    items_processed: int
    message: Optional[str]
    model_config = {"from_attributes": True}


# ---------- Analytics ----------

class SummaryStats(BaseModel):
    total_feed_items: int
    total_trends: int
    total_pain_points: int
    total_solutions: int
    total_sources: int
    items_last_24h: int

class SourceStats(BaseModel):
    source_id: str
    source_name: str
    total_items: int
    items_last_24h: int
    error_rate: float

class CategoryBreakdown(BaseModel):
    category: str
    count: int
    percentage: float

class SentimentBreakdown(BaseModel):
    positive: int
    negative: int
    neutral: int
    date: Optional[str]


# ---------- Settings ----------

class SettingUpdate(BaseModel):
    value: Any


# ---------- Common ----------

class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int

class MessageResponse(BaseModel):
    message: str

class HealthResponse(BaseModel):
    status: str
    version: str
    db: str
    uptime_seconds: float

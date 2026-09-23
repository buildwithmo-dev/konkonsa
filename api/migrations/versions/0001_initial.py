"""Initial Konkonsa schema.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _enum(name, values):
    return sa.Enum(*values, name=name, native_enum=False)


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", _enum("sourcetype", ["reddit", "hackernews", "google_trends", "twitter", "bluesky", "rss"]), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("fetch_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("total_items_fetched", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "trends",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("category", _enum("itemtype", ["pain_point", "trend", "opportunity", "complaint", "wish", "unknown"])),
        sa.Column("volume", sa.Integer()), sa.Column("score", sa.Float()), sa.Column("keywords", sa.JSON()),
        sa.Column("audience", sa.String()), sa.Column("is_rising", sa.Boolean()),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "clusters",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("label", sa.String(), nullable=False),
        sa.Column("keywords", sa.JSON()), sa.Column("item_count", sa.Integer()), sa.Column("centroid", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "feed_items",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("source_id", sa.String(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.String()), sa.Column("title", sa.Text()), sa.Column("body", sa.Text()), sa.Column("url", sa.Text()),
        sa.Column("author", sa.String()), sa.Column("score", sa.Integer()), sa.Column("comment_count", sa.Integer()),
        sa.Column("raw_data", sa.JSON()), sa.Column("is_duplicate", sa.Boolean()), sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "external_id", name="uq_feed_items_source_external"),
    )
    op.create_index("ix_feed_items_source_fetched", "feed_items", ["source_id", "fetched_at"])
    op.create_index("ix_feed_items_external_id", "feed_items", ["external_id"])
    op.create_table(
        "classifications",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("feed_item_id", sa.String(), sa.ForeignKey("feed_items.id"), nullable=False),
        sa.Column("item_type", _enum("itemtype", ["pain_point", "trend", "opportunity", "complaint", "wish", "unknown"])),
        sa.Column("topic", sa.String()), sa.Column("summary", sa.Text()), sa.Column("audience", sa.String()), sa.Column("severity", sa.Float()),
        sa.Column("keywords", sa.JSON()), sa.Column("sentiment", sa.String()), sa.Column("cluster_id", sa.String(), sa.ForeignKey("clusters.id")),
        sa.Column("classified_at", sa.DateTime(), server_default=sa.func.now()), sa.Column("failed", sa.Boolean()), sa.Column("error", sa.Text()),
    )
    op.create_table(
        "pain_points",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("trend_id", sa.String(), sa.ForeignKey("trends.id")),
        sa.Column("title", sa.String(), nullable=False), sa.Column("description", sa.Text()), sa.Column("audience", sa.String()),
        sa.Column("severity", sa.Float()), sa.Column("frequency", sa.Integer()), sa.Column("keywords", sa.JSON()), sa.Column("is_dismissed", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "solutions",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("pain_point_id", sa.String(), sa.ForeignKey("pain_points.id")),
        sa.Column("trend_id", sa.String(), sa.ForeignKey("trends.id")), sa.Column("title", sa.String(), nullable=False), sa.Column("description", sa.Text()),
        sa.Column("business_model", sa.Text()), sa.Column("target_audience", sa.String()), sa.Column("risks", sa.Text()), sa.Column("validation_notes", sa.Text()),
        sa.Column("status", _enum("solutionstatus", ["draft", "saved", "dismissed"])),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("name", sa.String(), nullable=False), sa.Column("condition", sa.JSON()),
        sa.Column("status", _enum("alertstatus", ["active", "triggered", "paused"])), sa.Column("notification_channel", sa.String()),
        sa.Column("notification_config", sa.JSON()), sa.Column("last_triggered_at", sa.DateTime()), sa.Column("trigger_count", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "alert_history",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("alert_id", sa.String(), sa.ForeignKey("alerts.id"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(), server_default=sa.func.now()), sa.Column("payload", sa.JSON()),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("name", sa.String(), nullable=False, unique=True), sa.Column("description", sa.Text()),
        sa.Column("status", _enum("jobstatus", ["running", "paused", "failed", "idle"])), sa.Column("interval_minutes", sa.Integer()),
        sa.Column("last_run_at", sa.DateTime()), sa.Column("next_run_at", sa.DateTime()), sa.Column("last_error", sa.Text()),
        sa.Column("run_count", sa.Integer()), sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "job_logs",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now()), sa.Column("finished_at", sa.DateTime()), sa.Column("success", sa.Boolean()),
        sa.Column("items_processed", sa.Integer()), sa.Column("message", sa.Text()),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), primary_key=True), sa.Column("value", sa.JSON()), sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in ["app_settings", "job_logs", "jobs", "alert_history", "alerts", "solutions", "pain_points", "classifications", "feed_items", "clusters", "trends", "sources"]:
        op.drop_table(table)

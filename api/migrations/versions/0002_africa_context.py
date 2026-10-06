"""Africa-context columns, plus columns models.py had that 0001 never created."""
from alembic import op
import sqlalchemy as sa

revision = "0002_africa_context"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _idx(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _cols(table):
        op.add_column(table, column)


def upgrade() -> None:
    _add("feed_items", sa.Column("relevance_score", sa.Float(), nullable=True))
    _add("feed_items", sa.Column("primary_country", sa.String(2), nullable=True))
    _add("feed_items", sa.Column("countries", sa.JSON(), nullable=True))
    _add("classifications", sa.Column("promoted_at", sa.DateTime(), nullable=True))
    _add("classifications", sa.Column("pain_point_id", sa.String(), sa.ForeignKey("pain_points.id", ondelete="SET NULL"), nullable=True))
    _add("classifications", sa.Column("trend_id", sa.String(), sa.ForeignKey("trends.id", ondelete="SET NULL"), nullable=True))
    _add("pain_points", sa.Column("embedding", sa.JSON(), nullable=True))
    _add("trends", sa.Column("embedding", sa.JSON(), nullable=True))

    for name, table, cols in [
        ("ix_feed_items_country_fetched", "feed_items", ["primary_country", "fetched_at"]),
        ("ix_classifications_promoted_at", "classifications", ["promoted_at"]),
    ]:
        if name not in _idx(table):
            op.create_index(name, table, cols)


def downgrade() -> None:
    op.drop_index("ix_feed_items_country_fetched", table_name="feed_items")
    for col in ("countries", "primary_country", "relevance_score"):
        op.drop_column("feed_items", col)
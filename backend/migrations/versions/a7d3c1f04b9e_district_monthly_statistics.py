"""district_monthly_statistics — precomputed per-district × per-month stats.

Adds a single denormalised table that the precompute command fills once
per ``(provider, variable, year, month)`` so the existing range-statistics
endpoints can read the answer straight from PostgreSQL. The on-demand
``RasterComputation`` path stays intact; the routers do not consult this
table yet (Phase 3 of the audit plan).

The unique constraint covers every range query path: a district range
query selects by ``(provider, variable, gid_2, year, month)``; a state
range query selects by ``(provider, variable, gid_1, year, month)``. The
``gid_1``/``gid_2`` columns are kept as VARCHAR (no FK to a districts
table) because GADM is loaded from a static GeoPackage and is not part
of the application schema.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7d3c1f04b9e"
down_revision: Union[str, Sequence[str], None] = "11588229830c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "district_monthly_statistics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("variable", sa.String(length=64), nullable=False),
        sa.Column("gid_2", sa.String(length=64), nullable=False),
        sa.Column("gid_1", sa.String(length=64), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("pixel_count", sa.Integer(), nullable=False),
        sa.Column("valid_pixel_count", sa.Integer(), nullable=False),
        sa.Column("valid_pixel_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("mean", sa.Float(), nullable=False),
        sa.Column("minimum", sa.Float(), nullable=False),
        sa.Column("maximum", sa.Float(), nullable=False),
        sa.Column("source_asset_id", sa.String(length=64), nullable=False),
        sa.Column("bbox", sa.JSON(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("year BETWEEN 1900 AND 2100", name="ck_dms_year"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_dms_month"),
        sa.UniqueConstraint(
            "provider",
            "variable",
            "gid_2",
            "year",
            "month",
            name="uq_dms_provider_variable_gid_year_month",
        ),
    )
    op.create_index(
        "ix_dms_state_variable_period",
        "district_monthly_statistics",
        ["provider", "variable", "gid_1", "year", "month"],
    )
    op.create_index(
        "ix_dms_asset_lookup",
        "district_monthly_statistics",
        ["source_asset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dms_asset_lookup", table_name="district_monthly_statistics")
    op.drop_index(
        "ix_dms_state_variable_period", table_name="district_monthly_statistics"
    )
    op.drop_table("district_monthly_statistics")

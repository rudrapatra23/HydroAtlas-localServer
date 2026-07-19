"""Deduplicate climate_assets and enforce one row per period."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f9b6d1e2a4"
down_revision: Union[str, Sequence[str], None] = "a7d3c1f04b9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    if dialect_name != "sqlite":
        op.execute(
            sa.text(
                """
                WITH ranked AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY provider, variable, year, month
                            ORDER BY created_at DESC NULLS LAST, id DESC
                        ) AS row_num
                    FROM climate_assets
                )
                DELETE FROM climate_assets AS asset
                USING ranked
                WHERE asset.id = ranked.id
                  AND ranked.row_num > 1
                """
            )
        )
    with op.batch_alter_table("climate_assets") as batch_op:
        batch_op.create_unique_constraint(
            "uq_climate_assets_provider_variable_year_month",
            ["provider", "variable", "year", "month"],
        )


def downgrade() -> None:
    with op.batch_alter_table("climate_assets") as batch_op:
        batch_op.drop_constraint(
            "uq_climate_assets_provider_variable_year_month",
            type_="unique",
        )

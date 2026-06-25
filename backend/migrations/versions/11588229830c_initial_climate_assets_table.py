from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '11588229830c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'climate_assets',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('variable', sa.String(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('storage_key', sa.String(), nullable=False),
        sa.Column('checksum', sa.String(), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('pending', 'downloading', 'processing', 'uploading', 'completed', 'failed', name='climateassetstatus'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_climate_assets_provider', 'climate_assets', ['provider'], unique=False)
    op.create_index('ix_climate_assets_variable', 'climate_assets', ['variable'], unique=False)
    op.create_index('ix_climate_assets_year', 'climate_assets', ['year'], unique=False)
    op.create_index('ix_climate_assets_month', 'climate_assets', ['month'], unique=False)
    op.create_index('ix_climate_assets_status', 'climate_assets', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_climate_assets_status', table_name='climate_assets')
    op.drop_index('ix_climate_assets_month', table_name='climate_assets')
    op.drop_index('ix_climate_assets_year', table_name='climate_assets')
    op.drop_index('ix_climate_assets_variable', table_name='climate_assets')
    op.drop_index('ix_climate_assets_provider', table_name='climate_assets')
    op.drop_table('climate_assets')
    sa.Enum(name='climateassetstatus').drop(op.get_bind())

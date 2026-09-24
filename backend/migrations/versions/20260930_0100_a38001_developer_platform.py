"""Developer platform: optional expiry on Public API keys."""

from alembic import op
import sqlalchemy as sa
from app.db.types import TZDateTime

revision = "a38001"
down_revision = "a37001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("public_api_keys") as batch:
        batch.add_column(sa.Column("expires_at", TZDateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("public_api_keys") as batch:
        batch.drop_column("expires_at")

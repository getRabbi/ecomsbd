"""Business tables from V2.1/V2.2 are accessible only through FastAPI.

`tenant_invitations`, `import_mappings` and `product_variants` were created
without row-level security. Client roles hold no privileges on them, so nothing
was exposed, but every other business table enforces both and so do these now.
"""

from alembic import op
import sqlalchemy as sa

revision = "a23007"
down_revision = "a23006"
branch_labels = None
depends_on = None

_TABLES = ["tenant_invitations", "import_mappings", "product_variants"]


def upgrade():
    db = op.get_bind()
    if db.dialect.name != "postgresql":
        return
    roles = (
        db.execute(
            sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
        )
        .scalars()
        .all()
    )
    grantees = ", ".join(["PUBLIC", *['"' + role + '"' for role in roles]])
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    # The backend role bypasses RLS, so older revisions run unchanged with it on.
    pass

"""V4 phase 1: device ingest keys, pairing codes (WP1.1); intake rules (WP1.2)."""

import unicodedata
import uuid
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, JSONColumn, TZDateTime

revision = "a41001"
down_revision = "a40001"
branch_labels = None
depends_on = None

_TABLES = ["device_ingest_keys", "device_pairing_codes", "intake_rule_sets", "shop_intake_settings"]

# Digit classes spelled out (ASCII + Bangla U+09E6..U+09EF): ``\d`` means
# different things in Java, JavaScript and Python.
_DIGIT = r"[0-9\u09E6-\u09EF]"
_SEP = r"[ \u00A0.\-]"


def _nfc(words):
    return [unicodedata.normalize("NFC", word) for word in words]


def _rules_v1():
    """Intake rules version 1, frozen here; later versions are published by OPS."""
    no_selectors = {
        "selectors": {"conversation": None, "message": None, "message_text": None, "sender": None},
        "verified_at": None,
    }
    return {
        "schema_version": 1,
        "packages": [
            {
                "package": "com.facebook.pages.app",
                "label": "Meta Business Suite",
                "default_enabled": True,
            },
            {"package": "com.whatsapp.w4b", "label": "WhatsApp Business", "default_enabled": True},
            {"package": "com.whatsapp", "label": "WhatsApp", "default_enabled": False},
            {"package": "com.facebook.orca", "label": "Messenger", "default_enabled": False},
        ],
        "phone": {
            "patterns": [
                rf"(?<!{_DIGIT})(?:\+?[8\u09EE][8\u09EE]{_SEP}?)?[0\u09E6]{_SEP}?[1\u09E7]"
                rf"[3-9\u09E9-\u09EF](?:{_SEP}?{_DIGIT}){{8}}(?!{_DIGIT})"
            ],
            "national_pattern": r"^01[3-9][0-9]{8}$",
            "country_prefix": "88",
            "required": True,
        },
        "intent": {
            "bn": _nfc(
                [
                    "অর্ডার",
                    "অডার",
                    "নিব",
                    "নেব",
                    "নিতে চাই",
                    "কিনব",
                    "কিনতে চাই",
                    "লাগবে",
                    "দাম",
                    "কত টাকা",
                    "পাঠিয়ে দিন",
                    "পাঠাবেন",
                    "পাঠান",
                    "ডেলিভারি",
                    "কনফার্ম",
                    "পিস",
                    "সাইজ",
                    "কালার",
                    "ক্যাশ অন ডেলিভারি",
                ]
            ),
            "banglish": [
                "nibo",
                "nebo",
                "nite chai",
                "kinbo",
                "kinte chai",
                "lagbe",
                "dam koto",
                "koto taka",
                "pathan",
                "pathiye din",
                "pathiye den",
                "pathaben",
                "order korbo",
                "order korte chai",
                "order dilam",
                "confirm korlam",
                "confirm korchi",
                "delivery charge",
                "ekta nibo",
                "duita nibo",
                "pis",
                "pc",
            ],
            "en": [
                "order",
                "confirm",
                "buy",
                "price",
                "delivery",
                "home delivery",
                "cash on delivery",
                "cod",
                "size",
                "colour",
                "color",
                "qty",
                "quantity",
                "pcs",
                "piece",
                "parcel",
                "available",
                "stock",
            ],
        },
        "address": {
            "bn": _nfc(
                [
                    "ঠিকানা",
                    "রোড",
                    "বাসা",
                    "বাড়ি",
                    "বাড়ী",
                    "ফ্ল্যাট",
                    "সেক্টর",
                    "ব্লক",
                    "থানা",
                    "উপজেলা",
                    "ইউনিয়ন",
                    "গ্রাম",
                    "বাজার",
                    "পাড়া",
                    "জেলা",
                    "মোড়",
                ]
            ),
            "latin": [
                "address",
                "addr",
                "thikana",
                "road",
                "rd",
                "house",
                "bari",
                "basha",
                "basa",
                "flat",
                "floor",
                "lane",
                "sector",
                "block",
                "thana",
                "upazila",
                "upozila",
                "union",
                "village",
                "bazar",
                "para",
                "zilla",
                "zila",
                "district",
            ],
            "district_list": "bd_districts_64",
        },
        "matching": {
            "normalization": "NFC",
            "case": "insensitive",
            "latin": "whole_word",
            "bangla": "substring",
        },
        "score": {
            "weights": {"phone": 40, "intent": 20, "address": 10, "district": 20},
            "max_hits": {"intent": 2, "address": 2},
            "threshold": 60,
        },
        "buffer_window_minutes": 25,
        "refresh_hours": 24,
        "extension": {
            "hosts": {
                "business.facebook.com": no_selectors,
                "www.messenger.com": no_selectors,
                "web.whatsapp.com": no_selectors,
            }
        },
    }


def _base_columns():
    return [
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "device_ingest_keys",
        *_base_columns(),
        sa.Column(
            "connection_id",
            GUID,
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_last4", sa.String(4), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("created_by", GUID, nullable=False),
        sa.Column("revoked_at", TZDateTime(), nullable=True),
        sa.Column("last_used_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_device_ingest_keys_hash"),
    )
    op.create_index("ix_device_ingest_keys_tenant_id", "device_ingest_keys", ["tenant_id"])
    op.create_index("ix_device_ingest_keys_created_at", "device_ingest_keys", ["created_at"])
    op.create_index("ix_device_ingest_keys_connection", "device_ingest_keys", ["connection_id"])

    op.create_table(
        "device_pairing_codes",
        *_base_columns(),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_by", GUID, nullable=False),
        sa.Column("expires_at", TZDateTime(), nullable=False),
        sa.Column("claimed_at", TZDateTime(), nullable=True),
        sa.Column("connection_id", GUID, nullable=True),
    )
    op.create_index("ix_device_pairing_codes_tenant_id", "device_pairing_codes", ["tenant_id"])
    op.create_index("ix_device_pairing_codes_created_at", "device_pairing_codes", ["created_at"])
    op.create_index("ix_device_pairing_codes_hash", "device_pairing_codes", ["code_hash"])

    # WP1.2: global (not tenant-owned), admin-managed.
    rule_sets = op.create_table(
        "intake_rule_sets",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", JSONColumn, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("activated_at", TZDateTime(), nullable=True),
        sa.Column("activated_by", sa.String(120), nullable=True),
        sa.UniqueConstraint("version", name="uq_intake_rule_sets_version"),
        sa.CheckConstraint("version >= 1", name="ck_intake_rule_sets_version"),
    )
    op.create_index("ix_intake_rule_sets_created_at", "intake_rule_sets", ["created_at"])
    now = datetime.now(UTC)
    op.bulk_insert(
        rule_sets,
        [
            {
                "id": uuid.uuid4(),
                "created_at": now,
                "updated_at": now,
                "version": 1,
                "schema_version": 1,
                "payload": _rules_v1(),
                "active": True,
                "created_by": "migration",
                "note": "V4 WP1.2 seed",
                "activated_at": now,
                "activated_by": "migration",
            }
        ],
    )

    op.create_table(
        "shop_intake_settings",
        *_base_columns(),
        sa.Column("covered_packages", JSONColumn, nullable=False),
        sa.Column("package_toggles", JSONColumn, nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_shop_intake_settings_tenant"),
    )
    op.create_index("ix_shop_intake_settings_tenant_id", "shop_intake_settings", ["tenant_id"])
    op.create_index("ix_shop_intake_settings_created_at", "shop_intake_settings", ["created_at"])

    db = op.get_bind()
    if db.dialect.name == "postgresql":
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
    for table in reversed(_TABLES):
        op.drop_table(table)

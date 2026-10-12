"""grid region context and durable community discovery

Revision ID: 587d3f371ea3
Revises: 03acecf962d2
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


@dataclass(frozen=True)
class RegionContext:
    recognized_region: bool
    region_known: bool
    region_codes: tuple[str, ...]
    region_key: str | None = None
    display_name: str | None = None
    vehicle_codes: tuple[str, ...] = ()
    search_places: tuple[str, ...] = ()
    confidence: str = "unknown"
    source: str = "grid_category"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


REGION_ROWS = (
    (("03", "103"), "buryatia", "Республика Бурятия", ("Улан-Удэ", "Бурятия", "Ulan Ude")),
    (("11", "111"), "komi", "Республика Коми", ("Сыктывкар", "Коми", "Syktyvkar")),
    (("12", "112"), "mari_el", "Республика Марий Эл", ("Йошкар-Ола", "Марий Эл", "Yoshkar Ola")),
    (("17", "117"), "tuva", "Республика Тыва", ("Кызыл", "Тыва", "Kyzyl")),
    (
        ("23", "93", "123", "193", "323"),
        "krasnodar_krai",
        "Краснодарский край",
        ("Краснодар", "Краснодарский край", "Krasnodar"),
    ),
    (
        ("24", "124"),
        "krasnoyarsk_krai",
        "Красноярский край",
        ("Красноярск", "Красноярский край", "Krasnoyarsk"),
    ),
    (
        ("28", "128"),
        "amur_oblast",
        "Амурская область",
        ("Благовещенск", "Амурская область", "Blagoveshchensk"),
    ),
    (
        ("30", "130"),
        "astrakhan_oblast",
        "Астраханская область",
        ("Астрахань", "Астраханская область", "Astrakhan"),
    ),
    (
        ("31", "131"),
        "belgorod_oblast",
        "Белгородская область",
        ("Белгород", "Белгородская область", "Belgorod"),
    ),
    (
        ("34", "134"),
        "volgograd_oblast",
        "Волгоградская область",
        ("Волгоград", "Волгоградская область", "Volgograd"),
    ),
    (
        ("35", "135"),
        "vologda_oblast",
        "Вологодская область",
        ("Вологда", "Вологодская область", "Vologda"),
    ),
    (
        ("37", "137"),
        "ivanovo_oblast",
        "Ивановская область",
        ("Иваново", "Ивановская область", "Ivanovo"),
    ),
    (
        ("42", "142"),
        "kemerovo_oblast",
        "Кемеровская область — Кузбасс",
        ("Кемерово", "Кузбасс", "Kemerovo"),
    ),
    (
        ("44", "144"),
        "kostroma_oblast",
        "Костромская область",
        ("Кострома", "Костромская область", "Kostroma"),
    ),
    (("46", "146"), "kursk_oblast", "Курская область", ("Курск", "Курская область", "Kursk")),
    (
        ("48", "148"),
        "lipetsk_oblast",
        "Липецкая область",
        ("Липецк", "Липецкая область", "Lipetsk"),
    ),
    (
        ("54", "154"),
        "novosibirsk_oblast",
        "Новосибирская область",
        ("Новосибирск", "Новосибирская область", "Novosibirsk"),
    ),
    (
        ("56", "156"),
        "orenburg_oblast",
        "Оренбургская область",
        ("Оренбург", "Оренбургская область", "Orenburg"),
    ),
    (("58", "158"), "penza_oblast", "Пензенская область", ("Пенза", "Пензенская область", "Penza")),
    (
        ("61", "161"),
        "rostov_oblast",
        "Ростовская область",
        ("Ростов-на-Дону", "Ростовская область", "Rostov"),
    ),
    (
        ("62", "162"),
        "ryazan_oblast",
        "Рязанская область",
        ("Рязань", "Рязанская область", "Ryazan"),
    ),
    (
        ("64", "164"),
        "saratov_oblast",
        "Саратовская область",
        ("Саратов", "Саратовская область", "Saratov"),
    ),
    (
        ("68", "168"),
        "tambov_oblast",
        "Тамбовская область",
        ("Тамбов", "Тамбовская область", "Tambov"),
    ),
    (
        ("73", "173"),
        "ulyanovsk_oblast",
        "Ульяновская область",
        ("Ульяновск", "Ульяновская область", "Ulyanovsk"),
    ),
)
BY_CODE = {code: row for row in REGION_ROWS for code in row[0]}
BY_KEY = {row[1]: row for row in REGION_ROWS}
_CODES = r"\d{1,3}(?:\s*(?:/|,|\s)\s*\d{1,3})*"
_PATTERN = re.compile(rf"^(?:({_CODES})\s*(?:регион|region)|(?:регион|region)\s*({_CODES}))$", re.I)


def region_for_key(key: str | None, *, source: str = "grid_region") -> RegionContext | None:
    row = BY_KEY.get(key or "")
    if not row:
        return None
    codes, canonical, name, places = row
    return RegionContext(True, True, (codes[0],), canonical, name, codes, places, "high", source)


def parse_region(category: str | None) -> RegionContext | None:
    value = " ".join(unicodedata.normalize("NFKC", category or "").strip().split())
    match = _PATTERN.fullmatch(value)
    if not match:
        return None
    codes = tuple(
        dict.fromkeys(c.zfill(2) for c in re.findall(r"\d+", match.group(1) or match.group(2)))
    )
    rows = [BY_CODE.get(code) for code in codes]
    keys = {row[1] for row in rows if row}
    if any(row is None for row in rows) or len(keys) != 1:
        return RegionContext(True, False, codes)
    row = rows[0]
    assert row
    aliases, key, name, places = row
    return RegionContext(True, True, codes, key, name, aliases, places, "high")


revision: str = "587d3f371ea3"
down_revision: str | None = "03acecf962d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "community_discovery_candidates",
        sa.Column("grid_id", sa.Uuid(), nullable=False),
        sa.Column("vk_group_id", sa.BigInteger(), nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("suggested_category", sa.String(length=200), nullable=True),
        sa.Column("suggested_region_key", sa.String(length=64), nullable=True),
        sa.Column(
            "found_by_queries",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "seed_community_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("topic_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("visual_score", sa.Float(), nullable=True),
        sa.Column("region_score", sa.Float(), nullable=True),
        sa.Column("activity_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("graph_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("final_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="new", nullable=False),
        sa.Column("classification", sa.String(length=24), server_default="new", nullable=False),
        sa.Column("analysis_state", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column(
            "reason", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "previews", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("visual_centroid", sa.LargeBinary(), nullable=True),
        sa.Column("visual_model", sa.String(length=200), nullable=True),
        sa.Column("visual_dimensions", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_community_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["photo_reviewers.id"],
            name=op.f("fk_community_discovery_candidates_approved_by_photo_reviewers"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_community_id"],
            ["communities.id"],
            name=op.f("fk_community_discovery_candidates_approved_community_id_communities"),
        ),
        sa.ForeignKeyConstraint(
            ["grid_id"], ["grids.id"], name=op.f("fk_community_discovery_candidates_grid_id_grids")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_community_discovery_candidates")),
        sa.UniqueConstraint("grid_id", "domain", name="uq_discovery_grid_domain"),
        sa.UniqueConstraint("grid_id", "vk_group_id", name="uq_discovery_grid_vk_id"),
    )
    op.create_table(
        "community_discovery_runs",
        sa.Column("grid_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("scope_value", sa.String(length=200), nullable=True),
        sa.Column("state", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(length=32), server_default="searching", nullable=False),
        sa.Column(
            "queries", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("query_cursor", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "counters", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_community_discovery_runs_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["grid_id"], ["grids.id"], name=op.f("fk_community_discovery_runs_grid_id_grids")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_community_discovery_runs")),
    )
    op.create_table(
        "community_discovery_evidence",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column(
            "queries", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "seed_community_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["community_discovery_candidates.id"],
            name=op.f(
                "fk_community_discovery_evidence_candidate_id_community_discovery_candidates"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["community_discovery_runs.id"],
            name=op.f("fk_community_discovery_evidence_run_id_community_discovery_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "candidate_id", name=op.f("pk_community_discovery_evidence")
        ),
    )
    op.add_column("grid_communities", sa.Column("region_key", sa.String(length=64), nullable=True))
    op.add_column(
        "grid_communities",
        sa.Column("region_codes", postgresql.ARRAY(sa.String(length=3)), nullable=True),
    )
    op.add_column(
        "grid_communities",
        sa.Column("discovery_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    connection = op.get_bind()
    for row in connection.execute(
        sa.text("SELECT grid_id, community_id, category FROM grid_communities")
    ).mappings():
        region = parse_region(row["category"])
        if region:
            connection.execute(
                sa.text(
                    "UPDATE grid_communities SET region_key=:key, region_codes=:codes "
                    "WHERE grid_id=:grid AND community_id=:community"
                ),
                {
                    "key": region.region_key,
                    "codes": list(region.region_codes),
                    "grid": row["grid_id"],
                    "community": row["community_id"],
                },
            )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("grid_communities", "discovery_provenance")
    op.drop_column("grid_communities", "region_codes")
    op.drop_column("grid_communities", "region_key")
    op.drop_table("community_discovery_evidence")
    op.drop_table("community_discovery_runs")
    op.drop_table("community_discovery_candidates")
    # ### end Alembic commands ###

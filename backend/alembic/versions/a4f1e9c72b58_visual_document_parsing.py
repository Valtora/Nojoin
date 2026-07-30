"""Visual document parsing: pages, parse state, and the 512-dim embedding cutover

Revision ID: a4f1e9c72b58
Revises: e1a7c93b8d24
Create Date: 2026-07-30 00:00:00.000000

Three related changes that have to land together:

1. ``document_pages`` -- parsed content stored per page/slide/sheet, written
   incrementally so an interrupted parse resumes instead of repeating vision
   calls. Also ends the practice of reconstructing a document's text by
   concatenating its overlapping chunks, which re-emitted every overlap.

2. Parse state on ``documents`` -- requested mode, non-fatal warning, and
   progress counters.

3. The embedding cutover. ``context_chunks.embedding`` moves from 384 to 512
   dimensions because the RAG model changes from all-MiniLM-L6-v2 (roughly a
   256-token window, so the tail of any real page was never searchable) to
   jina-embeddings-v2-small-en (8192 tokens, so a whole page embeds intact).

   THIS IS A DESTRUCTIVE CUTOVER. Vectors from two different models are not
   comparable at any width, so every existing row is deleted rather than
   migrated -- there is no arithmetic that converts one to the other. Search
   and meeting chat return nothing until the post-upgrade rebuild sweep
   re-indexes each recording. The sweep is dispatched by
   ``backend.worker.tasks.rebuild_text_embeddings_task``.
"""

from typing import Sequence, Union

import pgvector
import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f1e9c72b58"
down_revision: Union[str, Sequence[str], None] = "e1a7c93b8d24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "document_pages",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "parse_mode",
            sa.Enum("STRUCTURAL", "VISUAL", name="pageparsemode"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "page_number", name="uq_document_page_number"
        ),
    )
    op.create_index(
        op.f("ix_document_pages_document_id"),
        "document_pages",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_pages_page_number"),
        "document_pages",
        ["page_number"],
        unique=False,
    )

    # --- parse state on documents ---
    parse_mode = sa.Enum("VISUAL", "STRUCTURAL", name="documentparsemode")
    parse_mode.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "documents",
        sa.Column(
            "parse_mode",
            parse_mode,
            nullable=False,
            server_default="VISUAL",
        ),
    )
    op.add_column("documents", sa.Column("parse_warning", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("pages_parsed", sa.Integer(), nullable=False, server_default="0"),
    )

    # --- embedding cutover ---
    # Purge before altering: a 384-dimension value cannot be cast to a
    # 512-dimension one, so the ALTER only succeeds on an empty column, and
    # keeping the rows would be worse than losing them (they would score as
    # noise against every query).
    op.execute("DELETE FROM context_chunks")

    op.add_column(
        "context_chunks",
        sa.Column("document_page_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_context_chunks_document_page_id",
        "context_chunks",
        "document_pages",
        ["document_page_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_context_chunks_document_page_id"),
        "context_chunks",
        ["document_page_id"],
        unique=False,
    )

    op.add_column(
        "context_chunks",
        sa.Column(
            "embedding_version", sa.Integer(), nullable=False, server_default="2"
        ),
    )
    op.create_index(
        op.f("ix_context_chunks_embedding_version"),
        "context_chunks",
        ["embedding_version"],
        unique=False,
    )

    # Drop and re-add rather than ALTER TYPE: there is no cast between vector
    # widths, and with the table emptied above there is nothing to preserve.
    # Safe here only because no ivfflat/hnsw index exists on this column.
    op.drop_column("context_chunks", "embedding")
    op.add_column(
        "context_chunks",
        sa.Column(
            "embedding", pgvector.sqlalchemy.vector.VECTOR(dim=512), nullable=True
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    Symmetrically destructive: going back to a 384-dimension column discards
    every vector the new model produced, for the same incomparability reason.
    """
    op.execute("DELETE FROM context_chunks")
    op.drop_column("context_chunks", "embedding")
    op.add_column(
        "context_chunks",
        sa.Column(
            "embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True
        ),
    )
    op.drop_index(
        op.f("ix_context_chunks_embedding_version"), table_name="context_chunks"
    )
    op.drop_column("context_chunks", "embedding_version")
    op.drop_index(
        op.f("ix_context_chunks_document_page_id"), table_name="context_chunks"
    )
    op.drop_constraint(
        "fk_context_chunks_document_page_id", "context_chunks", type_="foreignkey"
    )
    op.drop_column("context_chunks", "document_page_id")

    op.drop_column("documents", "pages_parsed")
    op.drop_column("documents", "page_count")
    op.drop_column("documents", "parse_warning")
    op.drop_column("documents", "parse_mode")
    sa.Enum(name="documentparsemode").drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f("ix_document_pages_page_number"), table_name="document_pages")
    op.drop_index(op.f("ix_document_pages_document_id"), table_name="document_pages")
    op.drop_table("document_pages")
    sa.Enum(name="pageparsemode").drop(op.get_bind(), checkfirst=True)

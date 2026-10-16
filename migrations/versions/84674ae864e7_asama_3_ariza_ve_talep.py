"""Asama 3: ariza ve talep

Revision ID: 84674ae864e7
Revises: 0a6f12ac96e2
Create Date: 2026-10-16 11:31:26.943707

"""
from alembic import op
import sqlalchemy as sa

# Talep geçmişi eklenir ama değiştirilemez / silinemez. Uygulama katmanındaki korumaya ek olarak
# SQLite veritabanı tetikleyicileri de doğrudan SQL ile yapılan UPDATE/DELETE'i reddeder.
IMMUTABLE_TRIGGERS = {
    "trg_ticket_events_no_update": ("UPDATE", "Talep geçmişi kayıtları değiştirilemez."),
    "trg_ticket_events_no_delete": ("DELETE", "Talep geçmişi kayıtları silinemez."),
}


# revision identifiers, used by Alembic.
revision = '84674ae864e7'
down_revision = '0a6f12ac96e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('tickets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('description', sa.String(length=2000), nullable=False),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('location', sa.String(length=120), nullable=False),
    sa.Column('priority', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('resolution_note', sa.String(length=500), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('assigned_to_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("(status IN ('resolved', 'rejected')) = (closed_at IS NOT NULL)", name='ck_tickets_closed_at'),
    sa.CheckConstraint("category IN ('computer', 'printer', 'network', 'software', 'electrical', 'other')", name='ck_tickets_category'),
    sa.CheckConstraint("priority IN ('low', 'normal', 'high', 'urgent')", name='ck_tickets_priority'),
    sa.CheckConstraint("status IN ('new', 'reviewing', 'in_progress', 'resolved', 'rejected')", name='ck_tickets_status'),
    sa.ForeignKeyConstraint(['assigned_to_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tickets_assigned_to_id'), ['assigned_to_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_created_by_id'), ['created_by_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_priority'), ['priority'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_status'), ['status'], unique=False)

    op.create_table('ticket_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ticket_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=20), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=True),
    sa.Column('from_assignee_id', sa.Integer(), nullable=True),
    sa.Column('to_assignee_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("event_type IN ('created', 'status_changed', 'assigned', 'reassigned')", name='ck_ticket_events_type'),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['from_assignee_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
    sa.ForeignKeyConstraint(['to_assignee_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ticket_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ticket_events_ticket_id'), ['ticket_id'], unique=False)

    if op.get_bind().dialect.name == "sqlite":
        for name, (action, message) in IMMUTABLE_TRIGGERS.items():
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {action} ON ticket_events "
                f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
            )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        for name in IMMUTABLE_TRIGGERS:
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    with op.batch_alter_table('ticket_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ticket_events_ticket_id'))

    op.drop_table('ticket_events')
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tickets_status'))
        batch_op.drop_index(batch_op.f('ix_tickets_priority'))
        batch_op.drop_index(batch_op.f('ix_tickets_created_by_id'))
        batch_op.drop_index(batch_op.f('ix_tickets_created_at'))
        batch_op.drop_index(batch_op.f('ix_tickets_category'))
        batch_op.drop_index(batch_op.f('ix_tickets_assigned_to_id'))

    op.drop_table('tickets')

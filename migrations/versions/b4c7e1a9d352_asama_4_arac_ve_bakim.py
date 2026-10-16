"""Asama 4: arac ve is makinesi bakim takibi

Revision ID: b4c7e1a9d352
Revises: 84674ae864e7
Create Date: 2026-10-16 12:11:40.808929

"""
from alembic import op
import sqlalchemy as sa

# Bakım kayıtları ve iptalleri eklenir ama değiştirilemez / silinemez; bir araçta bakım kaydı varsa sayaç türü
# (km <-> saat) değiştirilemez. Uygulama katmanındaki korumaya ek olarak SQLite tetikleyicileri doğrudan SQL'i de reddeder.
TRIGGERS = {
    "trg_maint_records_no_update": (
        "BEFORE UPDATE ON maintenance_records", "BEGIN SELECT RAISE(ABORT, 'Bakım kayıtları değiştirilemez.'); END"),
    "trg_maint_records_no_delete": (
        "BEFORE DELETE ON maintenance_records", "BEGIN SELECT RAISE(ABORT, 'Bakım kayıtları silinemez.'); END"),
    "trg_maint_voids_no_update": (
        "BEFORE UPDATE ON maintenance_voids", "BEGIN SELECT RAISE(ABORT, 'Bakım iptal kayıtları değiştirilemez.'); END"),
    "trg_maint_voids_no_delete": (
        "BEFORE DELETE ON maintenance_voids", "BEGIN SELECT RAISE(ABORT, 'Bakım iptal kayıtları silinemez.'); END"),
    "trg_vehicles_meter_type_locked": (
        "BEFORE UPDATE OF meter_type ON vehicles FOR EACH ROW WHEN NEW.meter_type <> OLD.meter_type "
        "AND EXISTS (SELECT 1 FROM maintenance_records WHERE vehicle_id = OLD.id)",
        "BEGIN SELECT RAISE(ABORT, 'Bakım kaydı olan aracın sayaç türü değiştirilemez.'); END"),
}


# revision identifiers, used by Alembic.
revision = 'b4c7e1a9d352'
down_revision = '84674ae864e7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vehicles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vehicle_type', sa.String(length=60), nullable=False),
    sa.Column('identifier', sa.String(length=30), nullable=False),
    sa.Column('brand', sa.String(length=60), nullable=True),
    sa.Column('model', sa.String(length=60), nullable=True),
    sa.Column('model_year', sa.Integer(), nullable=True),
    sa.Column('meter_type', sa.String(length=10), nullable=False),
    sa.Column('current_meter', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("archived = 0 OR status = 'out_of_service'", name='ck_vehicles_archive_status'),
    sa.CheckConstraint("length(identifier) > 0 AND instr(identifier, ' ') = 0", name='ck_vehicles_identifier'),
    sa.CheckConstraint("meter_type IN ('km', 'hours')", name='ck_vehicles_meter_type'),
    sa.CheckConstraint("status IN ('active', 'in_maintenance', 'out_of_service')", name='ck_vehicles_status'),
    sa.CheckConstraint('current_meter >= 0', name='ck_vehicles_current_meter'),
    sa.CheckConstraint('model_year IS NULL OR (model_year >= 1950 AND model_year <= 2100)', name='ck_vehicles_model_year'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('identifier')
    )
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vehicles_archived'), ['archived'], unique=False)
        batch_op.create_index(batch_op.f('ix_vehicles_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_vehicles_vehicle_type'), ['vehicle_type'], unique=False)

    op.create_table('maintenance_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('maintenance_type', sa.String(length=20), nullable=False),
    sa.Column('maintenance_date', sa.Date(), nullable=False),
    sa.Column('meter_reading', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=False),
    sa.Column('provider', sa.String(length=120), nullable=True),
    sa.Column('cost_kurus', sa.Integer(), nullable=True),
    sa.Column('next_date', sa.Date(), nullable=True),
    sa.Column('next_meter', sa.Integer(), nullable=True),
    sa.Column('notes', sa.String(length=1000), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("maintenance_type IN ('periodic', 'oil_filter', 'tire', 'brake', 'repair', 'inspection', 'other')", name='ck_maint_records_type'),
    sa.CheckConstraint('cost_kurus IS NULL OR cost_kurus >= 0', name='ck_maint_records_cost'),
    sa.CheckConstraint('meter_reading >= 0', name='ck_maint_records_meter'),
    sa.CheckConstraint('next_date IS NULL OR next_date >= maintenance_date', name='ck_maint_records_next_date'),
    sa.CheckConstraint('next_meter IS NULL OR next_meter > meter_reading', name='ck_maint_records_next_meter'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('maintenance_records', schema=None) as batch_op:
        batch_op.create_index('ix_maint_records_plan', ['vehicle_id', 'maintenance_type', 'maintenance_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_maintenance_records_vehicle_id'), ['vehicle_id'], unique=False)

    op.create_table('maintenance_voids',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('voided_by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('length(trim(reason)) >= 5', name='ck_maint_voids_reason'),
    sa.ForeignKeyConstraint(['record_id'], ['maintenance_records.id'], ),
    sa.ForeignKeyConstraint(['voided_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('record_id')
    )

    if op.get_bind().dialect.name == "sqlite":
        for name, (where, body) in TRIGGERS.items():
            op.execute(f"CREATE TRIGGER {name} {where} {body}")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        for name in TRIGGERS:
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table('maintenance_voids')
    with op.batch_alter_table('maintenance_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_maintenance_records_vehicle_id'))
        batch_op.drop_index('ix_maint_records_plan')

    op.drop_table('maintenance_records')
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vehicles_vehicle_type'))
        batch_op.drop_index(batch_op.f('ix_vehicles_status'))
        batch_op.drop_index(batch_op.f('ix_vehicles_archived'))

    op.drop_table('vehicles')

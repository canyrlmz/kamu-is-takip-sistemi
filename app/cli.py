"""Komut satırı komutları: güvenli ilk yönetici kurulumu."""
import click
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models.audit_log import USER_CREATED
from app.models.user import User
from app.permissions import (
    LEGACY_STAFF_PERMISSIONS,
    ROLE_ARIZA_KONTROL,
    ROLE_BIRIM_SORUMLUSU,
    ROLE_LABELS,
    ROLE_SUPERADMIN,
)
from app.services.audit import log_event
from app.services.passwords import normalize_username, validate_password_strength, validate_username
from app.services.users import ASSIGNABLE_ROLES, UserRuleError, transfer_superadmin

DB_HINT = "Veritabanı tabloları bulunamadı. Önce şunu çalıştırın: flask --app run db upgrade"


def _create_user(username: str, full_name: str, password: str, role: str) -> User:
    username = normalize_username(username)
    full_name = (full_name or "").strip()
    errors = validate_username(username)
    if not full_name or len(full_name) > 120:
        errors.append("Ad soyad boş olamaz ve en fazla 120 karakter olabilir.")
    errors += validate_password_strength(password, username)
    if errors:
        raise click.ClickException("\n".join(errors))

    try:
        if db.session.scalar(db.select(User).where(User.username == username)):
            raise click.ClickException("Bu kullanıcı adı zaten kullanılıyor.")
        user = User(username=username, full_name=full_name, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        if role == ROLE_ARIZA_KONTROL:  # geliştirme personeli: eski personel yetkilerinin karşılığıyla oluşturulur
            user.set_permissions(LEGACY_STAFF_PERMISSIONS)
        log_event(USER_CREATED, entity_type="user", entity_id=user.id, details=f"Rol: {role} (komut satırı)", commit=False)
        db.session.commit()
    except OperationalError as exc:
        db.session.rollback()
        raise click.ClickException(DB_HINT) from exc
    except IntegrityError as exc:  # örn. eş zamanlı kurulumda ikinci aktif süper admin (veritabanı kuralı)
        db.session.rollback()
        raise click.ClickException("Hesap oluşturulamadı: kullanıcı adı kullanılıyor veya zaten bir aktif süper admin var.") from exc
    return user


def register_commands(app):
    password_option = click.option(
        "--password",
        envvar="KARABUK_ADMIN_PASSWORD",
        prompt="Parola",
        hide_input=True,
        confirmation_prompt=True,
        help="Boş bırakılırsa güvenli biçimde sorulur. Komut satırına yazmak kabuk geçmişinde iz bırakır.",
    )

    @app.cli.command("create-admin")
    @click.option("--username", prompt="Kullanıcı adı")
    @click.option("--full-name", prompt="Ad soyad")
    @password_option
    def create_admin(username, full_name, password):
        """İlk süper admin hesabını oluşturur (zaten aktif bir süper admin varsa reddeder)."""
        try:
            existing = db.session.scalar(
                db.select(User).where(User.role == ROLE_SUPERADMIN, User.active.is_(True)).limit(1))
        except OperationalError as exc:
            raise click.ClickException(DB_HINT) from exc
        if existing is not None:
            raise click.ClickException(
                "Zaten bir yönetici (aktif süper admin) hesabı var. İşlem iptal edildi. "
                "Yetkiyi başka birine vermek için 'transfer-superadmin' komutunu kullanın.")
        user = _create_user(username, full_name, password, ROLE_SUPERADMIN)
        click.echo(f"Süper admin hesabı oluşturuldu: {user.username}")

    @app.cli.command("create-staff")
    @click.option("--username", prompt="Kullanıcı adı")
    @click.option("--full-name", prompt="Ad soyad")
    @click.option("--password", prompt="Parola", hide_input=True, confirmation_prompt=True)
    def create_staff(username, full_name, password):
        """Geliştirme için örnek personel hesabı oluşturur."""
        user = _create_user(username, full_name, password, ROLE_ARIZA_KONTROL)
        click.echo(f"Personel hesabı oluşturuldu: {user.username}")

    @app.cli.command("transfer-superadmin")
    @click.option("--to", "target", required=True, help="Süper admin yetkisini alacak aktif kullanıcının kullanıcı adı.")
    @click.option("--former-role", type=click.Choice(ASSIGNABLE_ROLES), default=ROLE_BIRIM_SORUMLUSU, show_default=True,
                  help="Mevcut süper adminin devirden sonraki rolü (o rolün tavanındaki tüm izinleri alır).")
    @click.option("--yes", is_flag=True, help="Onay sorusunu atla (betikler için).")
    def transfer_superadmin_command(target, former_role, yes):
        """Süper admin yetkisini tek işlemde başka bir aktif kullanıcıya devreder.

        Yalnızca sunucu konsolundan çalıştırılır. İşlem atomiktir: herhangi bir hata olursa hiçbir hesap değişmez.
        Devir, denetim kaydına yazılır. Çalıştırmadan önce veritabanı yedeği alın.
        """
        try:
            current = db.session.scalars(
                db.select(User).where(User.role == ROLE_SUPERADMIN, User.active.is_(True))).all()
        except OperationalError as exc:
            raise click.ClickException(DB_HINT) from exc
        names = ", ".join(u.username for u in current) or "yok"
        click.echo(f"Mevcut aktif süper admin: {names}")
        click.echo(f"Yeni süper admin: {normalize_username(target)}; eski hesabın yeni rolü: {ROLE_LABELS[former_role]}")
        if not yes and not click.confirm("Devir uygulansın mı?", default=False):
            raise click.ClickException("İşlem iptal edildi; hiçbir değişiklik yapılmadı.")
        try:
            old, new = transfer_superadmin(target, former_role=former_role)
        except UserRuleError as exc:
            raise click.ClickException(str(exc)) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise click.ClickException(DB_HINT) from exc
        click.echo(f"Süper admin yetkisi devredildi: {old.username} -> {new.username}")

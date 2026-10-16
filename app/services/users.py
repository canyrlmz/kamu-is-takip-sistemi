"""Kullanıcı, rol, kıdem ve izin yönetimi iş kuralları.

Bu modüldeki her işlem (CLI devri hariç) `users.manage` iznini yeniden doğrular; bu izin yalnızca süper adminde
bulunur ve hiçbir kullanıcıya atanamaz. Kuralların özeti:

- Tek aktif süper admin vardır. Süper admin rolü bu servisle verilemez/alınamaz; yalnızca `transfer_superadmin`
  (komut satırı) tek işlemde devreder. Veritabanı kısmi benzersiz indeksi de ikinci aktif süper admini reddeder.
- Süper admin kendi rolünü düşüremez, hesabını devre dışı bırakamaz; başka hiçbir akış da sistemi süper adminsiz bırakamaz.
- Kıdem (1-5) yalnızca bilgidir; hiçbir yetki kontrolünde kullanılmaz.
- İzinler rol tavanını aşamaz; `users.manage` hiçbir role atanamaz.
- Her değişiklik denetim kaydına yazılır (parola veya parola özeti asla yazılmaz).
"""
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.audit_log import (
    SUPERADMIN_TRANSFERRED,
    USER_ACTIVATED,
    USER_CREATED,
    USER_DEACTIVATED,
    USER_PERMISSIONS_CHANGED,
    USER_ROLE_CHANGED,
    USER_SENIORITY_CHANGED,
    USER_UPDATED,
)
from app.models.user import User
from app.permissions import (
    MAX_SENIORITY,
    MIN_SENIORITY,
    PERMISSIONS,
    ROLE_BIRIM_SORUMLUSU,
    ROLE_LABELS,
    ROLE_SUPERADMIN,
    USERS_MANAGE,
    role_ceiling,
    split_grants,
)
from app.services.audit import log_event
from app.services.authz import ensure_permission
from app.services.passwords import normalize_username, validate_password_strength, validate_username

ASSIGNABLE_ROLES = tuple(r for r in ROLE_LABELS if r != ROLE_SUPERADMIN)
MAX_NAME_LENGTH = 120
_AUDIT_KEYS_PER_ENTRY = 200  # `details` alanı 255 karakterdir; uzun izin listeleri birden çok kayda bölünür


class UserRuleError(Exception):
    """İş kuralı ihlali; mesaj kullanıcıya gösterilir."""


# --- Doğrulama yardımcıları -------------------------------------------------------------------------
def _check_role(role: str) -> str:
    if role == ROLE_SUPERADMIN:
        raise UserRuleError(
            "Süper admin rolü bu ekrandan verilemez. Yetki devri için sunucuda 'transfer-superadmin' komutu kullanılır.")
    if role not in ASSIGNABLE_ROLES:
        raise UserRuleError("Geçersiz rol.")
    return role


def _check_seniority(value) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        raise UserRuleError("Kıdem seviyesi geçersiz.") from None
    if not MIN_SENIORITY <= level <= MAX_SENIORITY:
        raise UserRuleError(f"Kıdem seviyesi {MIN_SENIORITY} ile {MAX_SENIORITY} arasında olmalıdır.")
    return level


def _check_permissions(role: str, requested) -> frozenset:
    """İstenen izinlerin tamamı rol tavanı içinde olmalıdır; aksi hâlde sessizce kırpmak yerine reddedilir."""
    accepted, rejected = split_grants(role, set(requested or ()))
    if rejected:
        if USERS_MANAGE in rejected:
            raise UserRuleError("Kullanıcı yönetimi izni hiçbir role atanamaz; yalnızca süper adminde bulunur.")
        names = ", ".join(PERMISSIONS[p][1] if p in PERMISSIONS else str(p) for p in sorted(rejected, key=str))
        raise UserRuleError(f"'{ROLE_LABELS[role]}' rolü için verilemeyen izinler: {names}")
    return accepted


def _check_name(full_name: str) -> str:
    full_name = (full_name or "").strip()
    if not full_name or len(full_name) > MAX_NAME_LENGTH:
        raise UserRuleError(f"Ad soyad boş olamaz ve en fazla {MAX_NAME_LENGTH} karakter olabilir.")
    return full_name


def _active_superadmin_count() -> int:
    return db.session.scalar(
        db.select(db.func.count()).select_from(User).where(User.role == ROLE_SUPERADMIN, User.active.is_(True)))


def _log_permission_change(actor_id, user: User, added, removed) -> None:
    """İzin farkını denetim kaydına yazar; uzun listeler 255 karakter sınırına takılmasın diye bölünür."""
    for label, keys in (("eklenen", sorted(added)), ("çıkarılan", sorted(removed))):
        chunk: list[str] = []
        size = 0
        for key in keys + [None]:
            if key is None or size + len(key) + 2 > _AUDIT_KEYS_PER_ENTRY:
                if chunk:
                    log_event(USER_PERMISSIONS_CHANGED, user_id=actor_id, entity_type="user", entity_id=user.id,
                              details=f"Hesap: {user.username}; {label}: {', '.join(chunk)}", commit=False)
                chunk, size = [], 0
            if key is not None:
                chunk.append(key)
                size += len(key) + 2


# --- İşlemler ---------------------------------------------------------------------------------------
def create_user(actor: User, *, username: str, full_name: str, password: str, role: str,
                seniority: int = MIN_SENIORITY, permissions=()) -> User:
    ensure_permission(actor, USERS_MANAGE)
    username = normalize_username(username)
    errors = validate_username(username) + validate_password_strength(password, username)
    if errors:
        raise UserRuleError(" ".join(errors))
    full_name = _check_name(full_name)
    role = _check_role(role)
    level = _check_seniority(seniority)
    granted = _check_permissions(role, permissions)

    user = User(username=username, full_name=full_name, role=role, seniority_level=level, active=True)
    user.set_password(password)
    try:
        db.session.add(user)
        db.session.flush()
        user.set_permissions(granted, granted_by_id=actor.id)
        log_event(USER_CREATED, user_id=actor.id, entity_type="user", entity_id=user.id,
                  details=f"Hesap: {user.username}, rol: {role}, kıdem: {level}", commit=False)
        if granted:
            _log_permission_change(actor.id, user, granted, ())
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise UserRuleError("Bu kullanıcı adı zaten kullanılıyor.") from None
    return user


def update_user(actor: User, user: User, *, full_name: str, role: str, seniority, permissions,
                active: bool | None = None) -> list[str]:
    """Kullanıcının ad, rol, kıdem, izin ve (isteğe bağlı) aktiflik bilgisini tek işlemde günceller.
    Değişen alanların adlarını döndürür; hata olursa hiçbir şey değişmez."""
    ensure_permission(actor, USERS_MANAGE)
    full_name = _check_name(full_name)
    level = _check_seniority(seniority)

    if user.role == ROLE_SUPERADMIN:
        # Süper adminin rolü/aktifliği değiştirilemez; izinleri örtüktür (tabloda satır tutulmaz).
        if role not in (None, ROLE_SUPERADMIN):
            raise UserRuleError("Süper adminin rolü düşürülemez. Yetki devri için 'transfer-superadmin' komutu kullanılır.")
        if active is False:
            raise UserRuleError("Son aktif yönetici (süper admin) hesabı devre dışı bırakılamaz.")
        new_role, wanted = ROLE_SUPERADMIN, frozenset()
    else:
        if user.id == actor.id and role != user.role:
            raise UserRuleError("Kendi rolünüzü değiştiremezsiniz.")
        new_role = _check_role(role)
        wanted = _check_permissions(new_role, permissions)

    changes: list[str] = []
    old_role, old_level, old_name = user.role, user.seniority_level, user.full_name
    old_perms = user.granted_permissions
    old_active = bool(user.active)
    try:
        if full_name != old_name:
            user.full_name = full_name
            changes.append("full_name")
            log_event(USER_UPDATED, user_id=actor.id, entity_type="user", entity_id=user.id,
                      details=f"Hesap: {user.username}; ad soyad güncellendi", commit=False)
        if new_role != old_role:
            user.role = new_role
            changes.append("role")
            log_event(USER_ROLE_CHANGED, user_id=actor.id, entity_type="user", entity_id=user.id,
                      details=f"Hesap: {user.username}; {old_role} -> {new_role}", commit=False)
        if level != old_level:
            user.seniority_level = level
            changes.append("seniority_level")
            log_event(USER_SENIORITY_CHANGED, user_id=actor.id, entity_type="user", entity_id=user.id,
                      details=f"Hesap: {user.username}; {old_level} -> {level}", commit=False)
        if wanted != old_perms:
            user.set_permissions(wanted, granted_by_id=actor.id)
            changes.append("permissions")
            _log_permission_change(actor.id, user, wanted - old_perms, old_perms - wanted)
        if active is not None and bool(active) != old_active:
            _apply_active(actor, user, bool(active))
            changes.append("active")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise UserRuleError("Sistemde yalnızca bir aktif süper admin olabilir.") from None
    except Exception:
        db.session.rollback()
        raise
    return changes


def _apply_active(actor: User, user: User, active: bool) -> None:
    """Aktiflik değişikliğini uygular ve denetim kaydını (commit etmeden) ekler."""
    if not active:
        if user.role == ROLE_SUPERADMIN:
            raise UserRuleError(
                "Son aktif yönetici (süper admin) hesabı devre dışı bırakılamaz. "
                "Yetkiyi devretmek için sunucuda 'transfer-superadmin' komutu kullanılır.")
        if user.id == actor.id:
            raise UserRuleError("Kendi hesabınızı devre dışı bırakamazsınız.")
    user.active = active
    log_event(USER_ACTIVATED if active else USER_DEACTIVATED, user_id=actor.id, entity_type="user",
              entity_id=user.id, details=f"Hesap: {user.username}", commit=False)


def set_user_active(actor: User, user: User, active: bool) -> bool:
    """Hesabı etkinleştirir/devre dışı bırakır; değişiklik olduysa True döner."""
    ensure_permission(actor, USERS_MANAGE)
    if bool(user.active) == active:
        return False
    try:
        _apply_active(actor, user, active)
        db.session.commit()
    except IntegrityError:
        # Başka bir aktif süper admin varken ikinci aktif süper admin oluşamaz (kısmi benzersiz indeks).
        db.session.rollback()
        raise UserRuleError("Sistemde yalnızca bir aktif süper admin olabilir; bu hesap etkinleştirilemedi.") from None
    except Exception:
        db.session.rollback()
        raise
    return True


def transfer_superadmin(target_username: str, *, former_role: str = ROLE_BIRIM_SORUMLUSU) -> tuple[User, User]:
    """Süper admin yetkisini tek veritabanı işleminde başka bir aktif kullanıcıya devreder.

    Yalnızca sunucu konsolundan (CLI) çağrılır; oturum açmış kullanıcı yoktur. Önceki süper admin `former_role`
    rolüne iner ve o rolün tavanındaki tüm izinleri alır (iş kesintisi olmasın; sonra arayüzden daraltılabilir).
    Herhangi bir adımda hata olursa hiçbir şey değişmez. Dönüş: (eski süper admin, yeni süper admin).
    """
    former_role = _check_role(former_role)
    current = db.session.scalars(
        db.select(User).where(User.role == ROLE_SUPERADMIN, User.active.is_(True))).all()
    if len(current) != 1:
        raise UserRuleError(f"Devir için tam olarak bir aktif süper admin bulunmalıdır (bulunan: {len(current)}).")
    old = current[0]
    target = db.session.scalar(db.select(User).where(User.username == normalize_username(target_username)))
    if target is None:
        raise UserRuleError("Hedef kullanıcı bulunamadı.")
    if target.id == old.id:
        raise UserRuleError("Hedef hesap zaten süper admin.")
    if not target.active:
        raise UserRuleError("Hedef hesap devre dışı; önce etkinleştirilmelidir.")
    if target.role == ROLE_SUPERADMIN:
        raise UserRuleError("Hedef hesap zaten süper admin.")

    target_old_role = target.role
    try:
        # Sıra önemlidir: kısmi benzersiz indeks her an en fazla bir aktif süper admine izin verir.
        old.role = former_role
        old.set_permissions(role_ceiling(former_role), granted_by_id=old.id)
        db.session.flush()
        target.role = ROLE_SUPERADMIN
        target.set_permissions((), granted_by_id=old.id)  # süper adminin tabloda izin satırı tutulmaz
        db.session.flush()
        if _active_superadmin_count() != 1:
            raise UserRuleError("Devir sonrası tek aktif süper admin kuralı sağlanamadı; işlem iptal edildi.")
        log_event(USER_ROLE_CHANGED, user_id=old.id, entity_type="user", entity_id=old.id,
                  details=f"Hesap: {old.username}; {ROLE_SUPERADMIN} -> {former_role} (süper admin devri)", commit=False)
        log_event(USER_ROLE_CHANGED, user_id=old.id, entity_type="user", entity_id=target.id,
                  details=f"Hesap: {target.username}; {target_old_role} -> {ROLE_SUPERADMIN} (süper admin devri)",
                  commit=False)
        log_event(SUPERADMIN_TRANSFERRED, user_id=old.id, entity_type="user", entity_id=target.id,
                  details=f"{old.username} -> {target.username} (komut satırı); eski hesap rolü: {former_role}",
                  commit=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return old, target

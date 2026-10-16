"""İzin kayıt defteri: roller, modül izinleri, rol tavanları ve kıdem seviyeleri.

Bu modül saf veridir (Flask veya veritabanı bağımlılığı yok). Yetki kararlarının tek kaynağıdır:

* Kıdem seviyesi bilgi amaçlıdır; HİÇBİR yetki vermez.
* Etkin izinler = kullanıcıya atanan izinler ∩ rol tavanı. Tavan dışı bir izin satırı (örn. elle SQL ile eklenmiş)
  etkisizdir. Süper admin tüm izinlere örtük olarak sahiptir; izinleri tabloda tutulmaz.
* `users.manage` hiçbir role atanamaz (SYSTEM_ONLY); yalnızca süper admin kullanabilir.
"""

# --- Roller -------------------------------------------------------------------------------------
ROLE_SUPERADMIN = "superadmin"
ROLE_BIRIM_SORUMLUSU = "birim_sorumlusu"
ROLE_BAKIM_PERSONELI = "bakim_personeli"
ROLE_ARIZA_KONTROL = "ariza_kontrol"
ROLE_GORUNTULEYICI = "goruntuleyici"

ROLE_LABELS = {
    ROLE_SUPERADMIN: "Süper admin",
    ROLE_BIRIM_SORUMLUSU: "Birim sorumlusu",
    ROLE_BAKIM_PERSONELI: "Bakım personeli",
    ROLE_ARIZA_KONTROL: "Arıza kontrol",
    ROLE_GORUNTULEYICI: "Görüntüleyici",
}

# --- Kıdem (yetki vermez) -------------------------------------------------------------------------
MIN_SENIORITY, MAX_SENIORITY = 1, 5
# Kurum unvanları belirlendiğinde yalnızca bu sözlüğün etiketleri değiştirilir.
SENIORITY_LABELS = {level: f"Seviye {level}" for level in range(MIN_SENIORITY, MAX_SENIORITY + 1)}

# --- İzinler --------------------------------------------------------------------------------------
USERS_MANAGE = "users.manage"
AUDIT_VIEW = "audit.view"

ASSETS_VIEW, ASSETS_CREATE, ASSETS_UPDATE, ASSETS_ASSIGN = (
    "assets.view", "assets.create", "assets.update", "assets.assign")
TICKETS_VIEW, TICKETS_VIEW_ALL, TICKETS_CREATE, TICKETS_UPDATE, TICKETS_MANAGE, TICKETS_ASSIGN = (
    "tickets.view", "tickets.view_all", "tickets.create", "tickets.update", "tickets.manage", "tickets.assign")
VEHICLES_VIEW, VEHICLES_CREATE, VEHICLES_UPDATE = "vehicles.view", "vehicles.create", "vehicles.update"
MAINTENANCE_VIEW, MAINTENANCE_CREATE, MAINTENANCE_VOID = "maintenance.view", "maintenance.create", "maintenance.void"

# anahtar -> (modül, açıklama). Sıra arayüzdeki görüntüleme sırasıdır.
PERMISSIONS = {
    USERS_MANAGE: ("users", "Kullanıcı yönetimi (yalnızca süper admin)"),
    AUDIT_VIEW: ("audit", "Denetim kayıtlarını görüntüle"),
    ASSETS_VIEW: ("assets", "Demirbaşları görüntüle"),
    ASSETS_CREATE: ("assets", "Demirbaş oluştur"),
    ASSETS_UPDATE: ("assets", "Demirbaş düzenle, arşivle ve geri al"),
    ASSETS_ASSIGN: ("assets", "Zimmet ver ve iade al"),
    TICKETS_VIEW: ("tickets", "Kendi açtığı ve kendisine atanan talepleri görüntüle"),
    TICKETS_VIEW_ALL: ("tickets", "Tüm talepleri görüntüle"),
    TICKETS_CREATE: ("tickets", "Talep aç"),
    TICKETS_UPDATE: ("tickets", "Kendisine atanan talebin durumunu değiştir"),
    TICKETS_MANAGE: ("tickets", "Her talepte her durum geçişini yap (reddet, yeniden aç)"),
    TICKETS_ASSIGN: ("tickets", "Talebe sorumlu ata"),
    VEHICLES_VIEW: ("vehicles", "Araç ve iş makinelerini görüntüle"),
    VEHICLES_CREATE: ("vehicles", "Araç / iş makinesi oluştur"),
    VEHICLES_UPDATE: ("vehicles", "Araç / iş makinesi düzenle, arşivle ve geri al"),
    MAINTENANCE_VIEW: ("maintenance", "Bakım geçmişini ve uyarıları görüntüle"),
    MAINTENANCE_CREATE: ("maintenance", "Bakım kaydı ekle"),
    MAINTENANCE_VOID: ("maintenance", "Bakım kaydını gerekçeyle iptal et"),
}
MODULE_LABELS = {
    "users": "Kullanıcılar", "audit": "Denetim", "assets": "Demirbaş ve zimmet", "tickets": "Arıza ve talepler",
    "vehicles": "Araç ve iş makineleri", "maintenance": "Bakım",
}

# Hiçbir role (süper admin dahil tabloda) atanamayan izinler.
SYSTEM_ONLY = frozenset({USERS_MANAGE})
GRANTABLE = frozenset(PERMISSIONS) - SYSTEM_ONLY

_ALL_VIEW = frozenset({ASSETS_VIEW, TICKETS_VIEW, TICKETS_VIEW_ALL, VEHICLES_VIEW, MAINTENANCE_VIEW})
_TICKETS_ALL = frozenset({TICKETS_VIEW, TICKETS_VIEW_ALL, TICKETS_CREATE, TICKETS_UPDATE, TICKETS_MANAGE, TICKETS_ASSIGN})

# Rol tavanı: bir role verilebilecek izinlerin üst sınırı. Süper admin burada yoktur (örtük tüm izinler).
ROLE_CEILINGS = {
    ROLE_BIRIM_SORUMLUSU: GRANTABLE,
    ROLE_BAKIM_PERSONELI: frozenset({
        VEHICLES_VIEW, VEHICLES_CREATE, VEHICLES_UPDATE, MAINTENANCE_VIEW, MAINTENANCE_CREATE, MAINTENANCE_VOID,
        ASSETS_VIEW, TICKETS_VIEW, TICKETS_CREATE}),
    ROLE_ARIZA_KONTROL: _TICKETS_ALL | frozenset({ASSETS_VIEW, VEHICLES_VIEW, MAINTENANCE_VIEW}),
    # Görüntüleyici tavanı yalnızca görüntüleme izinlerinden oluşur (yazma/oluşturma yok). `tickets.view_all` tavandadır
    # ama önerilen sette DEĞİLDİR: tüm talepleri görme yetkisi süper admin tarafından ayrıca verilir.
    ROLE_GORUNTULEYICI: _ALL_VIEW,
}

# Eski `staff` hesaplarının bugünkü yetkilerinin birebir karşılığı (geçişte ariza_kontrol'e bu izinlerle taşınır).
LEGACY_STAFF_PERMISSIONS = frozenset({
    ASSETS_VIEW, VEHICLES_VIEW, MAINTENANCE_VIEW, TICKETS_VIEW, TICKETS_CREATE, TICKETS_UPDATE})

# Rol seçilince formda önceden işaretlenen öneriler (düzenlenebilir; her zaman tavanın alt kümesidir).
ROLE_PRESETS = {
    ROLE_BIRIM_SORUMLUSU: frozenset(),
    ROLE_BAKIM_PERSONELI: frozenset({
        VEHICLES_VIEW, VEHICLES_CREATE, VEHICLES_UPDATE, MAINTENANCE_VIEW, MAINTENANCE_CREATE,
        ASSETS_VIEW, TICKETS_VIEW, TICKETS_CREATE}),
    ROLE_ARIZA_KONTROL: LEGACY_STAFF_PERMISSIONS,
    ROLE_GORUNTULEYICI: _ALL_VIEW - {TICKETS_VIEW_ALL},
}


def role_ceiling(role: str) -> frozenset:
    """Rolün izin tavanı. Süper admin için tüm izinler (users.manage dahil); bilinmeyen rol için boş küme."""
    if role == ROLE_SUPERADMIN:
        return frozenset(PERMISSIONS)
    return ROLE_CEILINGS.get(role, frozenset())


def effective_permissions(role: str, granted) -> frozenset:
    """Etkin izinler: süper admin için hepsi; diğerleri için atananlar ∩ rol tavanı (users.manage hiçbir zaman)."""
    if role == ROLE_SUPERADMIN:
        return frozenset(PERMISSIONS)
    return frozenset(granted) & role_ceiling(role) & GRANTABLE


def has_permission(role: str, granted, permission: str) -> bool:
    return permission in effective_permissions(role, granted)


def split_grants(role: str, requested):
    """İstenen izinleri (kabul edilenler, reddedilenler) olarak ayırır. Reddedilen: bilinmeyen, users.manage
    veya rol tavanı dışı olanlar. Süper admin için hiçbir izin tabloda tutulmaz."""
    requested = set(requested)
    allowed = requested & role_ceiling(role) & GRANTABLE if role != ROLE_SUPERADMIN else set()
    return frozenset(allowed), frozenset(requested - allowed)

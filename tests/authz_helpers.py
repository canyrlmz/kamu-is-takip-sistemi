"""Yetkilendirme testleri için yardımcılar: tam olarak istenen izinlerle kullanıcı oluşturur."""
from app.extensions import db
from app.models.permission import UserPermission
from app.models.user import User
from app.permissions import ROLE_BIRIM_SORUMLUSU
from tests.conftest import TEST_PASSWORD


def make_profile(app, username, role=ROLE_BIRIM_SORUMLUSU, permissions=(), active=True, seniority=1,
                 full_name=None):
    """Rol + izin kümesiyle kullanıcı oluşturur. İzinler `set_permissions` ile (tavan kontrolü olmadan) yazılır;
    tavan dışı satırlar etkisiz olmalıdır (testler bunu ayrıca doğrular)."""
    with app.app_context():
        user = User(username=username, full_name=full_name or username.title(), role=role, active=active,
                    seniority_level=seniority)
        user.set_password(TEST_PASSWORD)
        db.session.add(user)
        db.session.flush()
        user.set_permissions(permissions)
        db.session.commit()
        return user.id


def insert_raw_grant(app, user_id, permission):
    """Rol tavanını aşan izin satırını doğrudan ekler (elle SQL / eski veri senaryosu)."""
    with app.app_context():
        db.session.add(UserPermission(user_id=user_id, permission=permission))
        db.session.commit()


def reload_user(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        db.session.expunge(user)
        return user

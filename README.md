# Can Yorulmaz — Kamu İş Takip Sistemi

**Bağımsız yazılım prototipi · Python · Flask · SQLAlchemy · SQLite**

Demirbaş ve zimmet, arıza ve talep, araç ve iş makinesi bakım süreçleri ile kullanıcı rol ve izin yönetimini tek uygulamada bir araya getirmeyi amaçlayan kişisel bir yazılım projesi.

> **Proje durumu:** Aşama 1–5 tamamlandı. Mevcut test çalıştırmasında 621 test başarılı oldu. Testler marka düzenlemesinden sonra yeniden çalıştırıldı; sonuç `621 passed, 264 warnings`.

Bu proje herhangi bir kamu kurumunun resmî sistemi değildir. Kurum onayı veya kurumsal bağlantı iddiası taşımaz ve gerçek bir kurumsal dağıtımın yerine geçtiğini iddia etmez.

## Özellikler

### Demirbaş ve zimmet yönetimi
- Demirbaş kaydı oluşturma, görüntüleme, düzenleme ve arşivleme
- Zimmet verme ve iade alma
- Demirbaş ve zimmet geçmişinin izlenmesi
- Benzersizlik ve aktif zimmet kurallarının veritabanında korunması

### Arıza ve talep yönetimi
- Talep oluşturma, görüntüleme ve durum takibi
- Sorumlu atama ve yeniden atama
- İşlem geçmişi ve durum geçişi kuralları
- Kullanıcı yetkisine göre kayıt erişimi

### Araç ve bakım takibi
- Araç ve iş makinesi kayıtlarının yönetimi
- Bakım kayıtları ve bakım iptali için gerekçeli kayıt
- Tarih ve sayaç değerlerine dayalı bakım uyarıları
- Arşivleme ve denetim kaydı

### Kullanıcı, rol ve izin yönetimi
- Kullanıcı hesabı oluşturma ve etkinleştirme/devre dışı bırakma
- Rol, kıdem seviyesi ve modül izinlerini ayrı yönetme
- Sunucu tarafında yetkilendirme kontrolleri
- Tek aktif süper yönetici kuralı
- Denetim kayıtları

## Teknolojiler

| Alan | Teknoloji |
|---|---|
| Programlama dili | Python |
| Web uygulaması | Flask |
| ORM ve veri erişimi | Flask-SQLAlchemy / SQLAlchemy |
| Veritabanı | SQLite |
| Şema sürümleme | Alembic / Flask-Migrate |
| Arayüz | HTML, CSS, Bootstrap |
| Test | pytest |

## Mimari ve güvenlik yaklaşımı

- İş kuralları mümkün olduğunca servis katmanında doğrulanır.
- Yetki kontrolleri yalnızca arayüzdeki butonları gizlemeye dayanmaz; sunucu tarafında da uygulanır.
- Kritik veri kuralları uygun yerlerde veritabanı kısıtları ve SQLite tetikleyicileriyle desteklenir.
- Migration işlemleri ve mevcut verilerin korunması testlerle kontrol edilir.
- Parola özetleri denetim kayıtlarına yazılmaz.
- `.env` ve yerel veritabanı dosyaları Git'e eklenmez.

Bu maddeler projenin kod ve test yapısını açıklar; bağımsız bir güvenlik denetimi veya üretim ortamı sertifikasyonu anlamına gelmez.

## Kurulum

Gereksinimler: Windows, Python 3.12 ve PowerShell.

```powershell
git clone https://github.com/canyrlmz/kamu-is-takip-sistemi.git
cd kamu-is-takip-sistemi

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
$key = python -c "import secrets; print(secrets.token_hex(32))"
(Get-Content .env) -replace '^SECRET_KEY=.*', "SECRET_KEY=$key" |
    Set-Content .env -Encoding ascii

flask --app run db upgrade
flask --app run create-admin
python run.py

```


## Proje aşamaları

| Aşama | Kapsam |
|---|---|
| 1 | Temel uygulama altyapısı |
| 2 | Demirbaş ve zimmet yönetimi |
| 3 | Arıza ve talep yönetimi |
| 4 | Araç ve bakım takibi |
| 5 | Kullanıcı rolleri, kıdem ve izin yönetimi |

Ayrıntılı kurulum, migration, yedekleme ve geri alma yönergeleri için [Aşamalar 1–5 teknik dokümantasyonuna](docs/ASAMALAR_1_5_KURULUM_MIGRATION.md) bakın.

## Testler

Son doğrulama çalıştırmasında 621 test başarılı oldu (621 passed, 264 warnings). Bu sonuç ilgili test çalıştırmasına aittir.

Testleri çalıştırmak için:

```powershell
python -m pytest -q
```

## Bilinen sınırlamalar

- Bu proje bağımsız bir yazılım prototipidir; resmî bir kamu kurumu sistemi değildir.
- Gerçek kurumsal kullanım öncesinde güvenlik ve dağıtım değerlendirmesi yapılmalıdır.
- E-posta/SMS bildirimleri ve parola sıfırlama mevcut kapsamın dışında olabilir.
- Lisans dosyası yoksa projeye açık kaynak lisansı verildiği varsayılmamalıdır.

## Geliştirici

**Can Yorulmaz** — Bilgisayar Mühendisliği mezunu.

- GitHub: https://github.com/canyrlmz
- Proje deposu: https://github.com/canyrlmz/kamu-is-takip-sistemi

---

Bu çalışma kişisel portföy ve öğrenme amacıyla geliştirilmiştir. Herhangi bir kamu kurumunun resmî ürünü veya onaylanmış sistemi değildir.

# Can Yorulmaz - Kamu İş Takip Sistemi (bağımsız prototip)
Demirbaş/zimmet, arıza/talep ve araç bakım süreçleri için **bağımsız bir kişisel yazılım prototipi**.
Herhangi bir kamu kurumunun resmî sistemi değildir; kurum onayı veya kurumsal bağlantı iddiası taşımaz.
Mevcut kurum yazılımlarının yerine geçtiğini iddia etmez; yalnızca örnek verilerle yerel olarak çalışır.

**Durum: Aşama 1 + kullanıcı yönetimi + Aşama 2 (demirbaş ve zimmet) + Aşama 3 (arıza ve talep) + Aşama 4 (araç ve iş makinesi bakım takibi, 4.1 + 4.2) + Aşama 5 (personel kıdem ve yetki yönetimi)** (güvenli giriş/çıkış, roller, denetim kaydı, kontrol paneli,
yönetici için kullanıcı yönetimi: personel hesabı oluşturma, etkinleştirme/devre dışı bırakma).
Tüm planlanan modüller eklenmiştir.

## Kurulum (Windows PowerShell)

```powershell
cd karabuk_takip
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
$key = python -c "import secrets; print(secrets.token_hex(32))"
(Get-Content .env) -replace '^SECRET_KEY=.*', "SECRET_KEY=$key" | Set-Content .env -Encoding ascii

flask --app run db upgrade
flask --app run create-admin
python run.py
```

Tarayıcıda http://127.0.0.1:5000 adresini açın. `Activate.ps1` çalışmazsa:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Demirbaş ve Zimmet modülü (Aşama 2)

Menüde **Demirbaş ve Zimmet**. Ekranlar: liste (arama/filtre/sayfalama), detay, yeni kayıt, düzenleme,
zimmet verme, iade alma, arşivleme (pasife alma) ve geri alma. Zimmet geçmişi demirbaş detayında görünür.

**Yetkiler (Aşama 5'ten itibaren izin tabanlı, bkz. aşağıdaki bölüm):** görüntüleme `assets.view`, oluşturma
`assets.create`, düzenleme/arşiv `assets.update`, zimmet verme/iade `assets.assign` izinleriyle yapılır.

**İş kuralları:**
- Demirbaş kodu benzersizdir (büyük/küçük harf fark etmez, büyük harfle saklanır). Dolu seri numarası da benzersizdir; boş bırakılabilir.
- Bir demirbaşın aynı anda yalnızca bir aktif zimmeti olabilir. Bu kural hem uygulamada hem veritabanında (kısmi benzersiz indeks) uygulanır.
- Yalnızca "Depoda" durumundaki, arşivde olmayan demirbaş zimmetlenebilir. Zimmetlenince durum "Kullanımda" olur; iadede durum "Depoda", "Bakımda" veya "Hurda adayı" seçilir. Zimmetli demirbaşın durumu elle değiştirilemez.
- İade edilen zimmet silinmez; iade tarihi doldurulur. Tarihler gelecekte olamaz, iade tarihi başlangıçtan önce olamaz, yeni zimmet önceki iadeden önce başlayamaz.
- Demirbaş silinmez. Zimmetli olmayan kayıt arşivlenebilir; arşivdeki kayıt listede varsayılan olarak gizlenir, geri alınabilir.
- Oluşturma, güncelleme, arşivleme/geri alma, zimmet verme ve iade alma denetim kaydına yazılır.

### Mevcut kuruluma uygulama (Windows PowerShell)

```powershell
cd $HOME\Documents\karabuk_takip
.\.venv\Scripts\Activate.ps1

# 1) Veritabanı yedeği (migration öncesi)
Copy-Item instance\karabuk_takip_dev.db instance\karabuk_takip_dev.db.yedek-asama2

# 2) Yeni tabloları oluştur (mevcut kullanıcılar ve denetim kayıtları korunur)
flask --app run db upgrade
flask --app run db current      # 0a6f12ac96e2 (head) görünmeli

# 3) Testler ve çalıştırma
python -m pytest -q
python run.py
```

Sorun olursa son migration geri alınabilir: `flask --app run db downgrade` (yeni iki tablo silinir,
önceki veriler kalır). Yedekten dönmek için uygulamayı durdurup yedek dosyayı eski adına kopyalayın.

## Arıza ve Talep modülü (Aşama 3)

Menüde **Arıza ve Talepler**. Ekranlar: liste (talep no, durum, kategori, öncelik, atama filtreleri), detay ve
işlem geçmişi, yeni talep, durum değiştirme, sorumlu atama. Panelde açık / işlemdeki / bana atanan (yönetici için
ayrıca atanmamış) talep sayıları görünür; sayılar kullanıcının görebildiği taleplerle sınırlıdır.

**Talep:** başlık, açıklama, kategori (Bilgisayar, Yazıcı, Ağ / İnternet, Yazılım, Elektrik, Diğer), yer / konum,
öncelik (Düşük, Normal, Yüksek, Acil), durum, oluşturan, sorumlu, oluşturulma / güncellenme / kapanış tarihleri.
Talep numarası `TLP-00001` biçimindedir; kimlikten türetildiği için benzersiz ve değişmezdir.

**Durumlar ve geçişler:** Yeni, İnceleniyor, İşlemde, Çözüldü, Reddedildi. Geçişler servis katmanında
(`app/services/tickets.py`) doğrulanır; geçersiz değer ve geçersiz geçiş reddedilir.

| Kimden | Hangi geçişler |
|---|---|
| Yönetici | Yeni → İnceleniyor / İşlemde / Reddedildi; İnceleniyor → İşlemde / Çözüldü / Reddedildi; İşlemde → İnceleniyor / Çözüldü; Çözüldü → İşlemde (yeniden açma); Reddedildi → İnceleniyor (yeniden açma) |
| Talebin sorumlusu (personel) | Yeni → İnceleniyor / İşlemde; İnceleniyor → İşlemde / Çözüldü; İşlemde → İnceleniyor / Çözüldü. Reddedemez, yeniden açamaz |
| Diğer herkes | Durum değiştiremez |

Çözüldü, Reddedildi ve yeniden açma için işlem notu **zorunludur** (çözüm açıklaması / ret gerekçesi / açma nedeni).
Kapanış tarihi yalnızca bu iki durumda dolu olabilir (veritabanı kısıtı). Yeniden açılan talepte kapanış alanları
temizlenir; önceki kapanış değiştirilemez geçmişte kalır.

**Yetkiler (Aşama 5'ten itibaren izin tabanlı; aşağıdaki "yönetici" = `tickets.manage` + `tickets.assign` izinleri):**
- Her kullanıcı talep açabilir.
- Personel yalnızca **kendi açtığı** ve **kendisine atanan** talepleri görür. Başkasının talebine doğrudan
  adresle gidilirse **404** döner (talep numarası tahmini ile varlık sızmaz).
- Sorumlu atama / yeniden atama yalnızca yöneticidedir (personel için 403). Yalnızca **etkin** kullanıcı atanabilir;
  kapalı talebe atama yapılamaz (önce yeniden açılır).
- Tüm kontroller sunucu tarafındadır; şablonlardaki buton gizleme güvenlik sayılmaz.

**İşlem geçmişi:** talep oluşturma, durum değişikliği, atama ve yeniden atama; işlemi yapan, zaman ve not ile
`ticket_events` tablosunda tutulur. Kayıtlar eklenir ama **değiştirilemez ve silinemez**: uygulama katmanında
(ORM) ve SQLite veritabanı tetikleyicileriyle (doğrudan SQL dahil). Aynı işlemler ayrıca denetim kayıtlarına yazılır
(not metni denetim kaydına yazılmaz). Talep silme özelliği yoktur.

**Bu aşamada olmayanlar:** talep düzenleme (başlık/açıklama), sorumlu kaldırma (yalnızca yeniden atama), tarih
aralığı filtresi, e-posta/SMS bildirimi, dosya eki.

### Değişen ve yeni dosyalar (Aşama 3)

Yeni:
- `app/models/ticket.py`, `app/services/tickets.py`
- `app/tickets/__init__.py`, `app/tickets/forms.py`, `app/tickets/routes.py`
- `app/templates/tickets/` altında `list.html`, `detail.html`, `form.html`, `status.html`, `assign.html`
- `migrations/versions/84674ae864e7_asama_3_ariza_ve_talep.py`
- `tests/ticket_helpers.py`, `tests/test_tickets.py`, `tests/test_ticket_workflow.py`, `tests/test_ticket_migration.py`

Değişen (küçük eklemeler): `app/__init__.py` (blueprint kaydı), `app/models/__init__.py`, `app/models/audit_log.py`
(yeni işlem türleri), `app/dashboard/routes.py` ve `app/templates/dashboard/index.html` (talep özeti),
`app/templates/base.html` (menü bağlantısı), `README.md`.

`requirements.txt` değişmedi; yeni paket gerekmez.

### Mevcut kuruluma uygulama (Windows PowerShell)

Güncellemeyi uygulamadan önce uygulamayı (`python run.py`) durdurun.

```powershell
cd C:\Users\can78\Documents\karabuk_takip

# 1) Yedek (kod, testler, migration'lar ve veritabanı; .venv hariç)
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item -Recurse -Path app, tests, instance, migrations, README.md -Destination "..\karabuk_takip_yedek_asama3_oncesi_$stamp"

# 2) Güncellemeyi geçici klasöre aç ve proje üzerine kopyala
Expand-Archive -Path "$HOME\Downloads\guncelleme_asama3_ariza_talep.zip" -DestinationPath "$env:TEMP\guncelleme_asama3" -Force
Copy-Item -Recurse -Force -Path "$env:TEMP\guncelleme_asama3\guncelleme_asama3_ariza_talep\*" -Destination .

# 3) Sanal ortamı etkinleştir, migration'ı uygula (mevcut veriler korunur)
.\.venv\Scripts\Activate.ps1
flask --app run db current          # 0a6f12ac96e2 (head) görünmeli (Aşama 2)
flask --app run db upgrade
flask --app run db current          # 84674ae864e7 (head) görünmeli

# 4) Testler ve çalıştırma
python -m pytest -q                 # beklenen: 262 passed
python run.py
```

Tarayıcıda http://127.0.0.1:5000 adresini açın; menüde **Arıza ve Talepler** görünmelidir.

### Geri dönüş

- **Yalnızca Aşama 3'ü geri almak (veritabanı):** `flask --app run db downgrade` talep tablolarını ve tetikleyicileri
  kaldırır; kullanıcı, demirbaş, zimmet ve denetim verileri kalır. **Dikkat:** o ana kadar girilen talepler silinir.
  Önce yedek alın.
- **Tam geri dönüş:** uygulamayı durdurun; 1. adımdaki yedek klasöründen `app`, `tests`, `migrations`, `instance` ve
  `README.md` dosyalarını proje klasörüne geri kopyalayın (veritabanı dahil önceki hâle döner).

## Araç ve İş Makinesi Bakım Takip modülü (Aşama 4)

Adres: `/araclar/`. Giriş yapan herkes görüntüler; kayıt oluşturma, düzenleme, bakım ekleme, bakım iptali ve
arşivleme yalnızca **yönetici** içindir (sunucu tarafında izin dekoratörleri ve servis katmanı ile zorlanır: `vehicles.*`, `maintenance.*`).

- **Araç kaydı:** tür, plaka veya envanter kodu (benzersiz; büyük harfe çevrilir, boşluklar silinir), marka/model,
  isteğe bağlı model yılı, sayaç türü (kilometre veya çalışma saati), güncel sayaç, durum (Aktif / Bakımda /
  Kullanım dışı). Yalnızca "Kullanım dışı" kayıt arşivlenebilir; kayıtlar silinmez.
- **Bakım kaydı:** eklenir, ancak değiştirilemez ve silinemez (ORM koruması + SQLite tetikleyicileri). Hatalı kayıt
  gerekçeyle **iptal** edilir (`maintenance_voids`); iptal geri alınamaz, kayıt geçmişte üstü çizili kalır.
- **Sayaç:** tam sayı; maliyet kuruş cinsinden saklanır (`1250,50` girilir). Bakım kaydı olan aracın sayaç türü
  değiştirilemez. Bakım sayacı, komşu (tarihe göre) geçerli kayıtlarla tutarlı olmalıdır.
- **Uyarılar:** `app/services/vehicles.py` içinde tek yerde tanımlı eşikler: **30 gün**, **1000 km**, **50 saat**.
  Her bakım türü için **en son geçerli (iptal edilmemiş) kayıt** o türün planını belirler; sonraki tarih ve sayaç
  eşiği ikisi de boşsa plan kapanır. Türlerin planları birbirinden bağımsızdır. Genel durum: Gecikmiş > Yaklaşan >
  Normal; hiçbir türde aktif plan yoksa Planlanmamış. Kullanım dışı ve arşivdeki araçlar uyarı kapsamı dışındadır.
  Uyarılar saklanmaz ve önbelleğe alınmaz; her görüntülemede güncel kayıtlardan hesaplanır (arka plan görevi yoktur).
- **Denetim:** `vehicle_created/updated/archived/restored`, `maintenance_added/voided` (serbest metin yazılmaz).

### Değişen ve yeni dosyalar (Aşama 4.1 + 4.2)

Yolların hepsi proje klasörüne göredir (örn. `C:\Users\can78\Documents\karabuk_takip`).

Yeni:
- `app/models/vehicle.py`
- `app/services/vehicles.py`
- `app/vehicles/__init__.py`, `app/vehicles/forms.py`, `app/vehicles/routes.py`
- `app/templates/vehicles/_macros.html`, `list.html`, `detail.html`, `form.html`, `maintenance_form.html`, `void_form.html`, `upcoming.html`
- `migrations/versions/b4c7e1a9d352_asama_4_arac_ve_bakim.py`
- `tests/vehicle_helpers.py`, `tests/test_vehicles.py`, `tests/test_maintenance.py`, `tests/test_maintenance_alerts.py`,
  `tests/test_vehicle_alerts_integration.py`, `tests/test_vehicle_migration.py`

Değişen (küçük eklemeler):
- `app/__init__.py` (blueprint kaydı, `tl` para filtresi)
- `app/models/__init__.py` (yeni modeller)
- `app/models/audit_log.py` (6 yeni işlem türü)
- `app/utils.py` (`format_money`)
- `app/dashboard/routes.py`, `app/templates/dashboard/index.html` (araç/bakım özeti)
- `app/templates/base.html` (menü bağlantısı)
- `tests/test_ticket_migration.py` (aşağıdaki dar kapsam düzeltmesi)
- `README.md`

Mevcut migration dosyalarına dokunulmadı. `requirements.txt` değişmedi; yeni paket gerekmez.

### Migration (Aşama 4)

Yeni revision `b4c7e1a9d352` (üst: `84674ae864e7`; zincir: `da9bc564aab8` → `0a6f12ac96e2` → `84674ae864e7` →
`b4c7e1a9d352`). Yalnızca yeni nesneleri ekler: `vehicles`, `maintenance_records`, `maintenance_voids` tabloları, indeksleri,
CHECK kısıtları ve 5 SQLite tetikleyicisi (bakım ve iptal kayıtları değiştirilemez/silinemez; bakım kaydı olan aracın
sayaç türü değiştirilemez). Mevcut tablolara ve verilere dokunmaz. Downgrade yalnızca bu nesneleri siler.

> **Önemli: Bu paket hazırlanırken `flask db upgrade` hiçbir veritabanında ÇALIŞTIRILMADI.** Migration yalnızca
> geçici test veritabanlarında (pytest) denendi. Sizin veritabanınız şu an hâlâ Aşama 3'te (`84674ae864e7`) olmalıdır;
> migration'ı aşağıdaki 3. adımda siz uygulayacaksınız.

### `tests/test_ticket_migration.py` kapsam düzeltmesi (diff)

Aşama 3 testleri "güncel head Aşama 2'nin devamıdır" ve "head'de tam 2 tetikleyici vardır" diye sabitlenmişti; Aşama 4
migration'ı yeni head ve yeni tetikleyiciler getirdiği için bu iki doğrulama kapsamı doğru olacak şekilde düzeltildi.
Veri koruma, kısıt, değiştirilemez geçmiş ve downgrade testlerine dokunulmadı. Doğrulamalar zayıflamadı:
zincir testi artık Aşama 3 revision'ının doğrudan Aşama 2'nin devamı olduğunu **ve** head'in atası olduğunu doğrular;
talep tetikleyicileri adıyla aranır ve Aşama 2'ye inildikten sonra veritabanında hiç tetikleyici kalmadığı
(`== set()`) aynen kontrol edilir.

```diff
@@ -13,6 +13,8 @@
 MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")
 STAGE1_REVISION = "da9bc564aab8"
 STAGE2_REVISION = "0a6f12ac96e2"
+STAGE3_REVISION = "84674ae864e7"
+TICKET_TRIGGERS = ("trg_ticket_events_no_update", "trg_ticket_events_no_delete")


 def _script_directory():
@@ -49,12 +51,18 @@
     }


+def _trigger_names(session):
+    return {r[0] for r in session.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
+
+
 def test_migration_chain_continues_from_stage2():
     script = _script_directory()
     assert len(script.get_heads()) == 1, "Migration zincirinde dallanma var"
-    head = script.get_revision(script.get_current_head())
-    assert head.down_revision == STAGE2_REVISION
+    # Aşama 3 migration'ı doğrudan Aşama 2'nin devamıdır ve sonraki aşamalar da onun üzerine kurulur.
+    assert script.get_revision(STAGE3_REVISION).down_revision == STAGE2_REVISION
     assert script.get_revision(STAGE2_REVISION).down_revision == STAGE1_REVISION
+    ancestors = {r.revision for r in script.iterate_revisions(script.get_current_head(), STAGE1_REVISION)}
+    assert STAGE3_REVISION in ancestors, "Aşama 3 migration'ı güncel head'in atası olmalı"


 def test_stage3_upgrade_preserves_existing_data_and_adds_tables(tmp_path):
@@ -116,7 +124,7 @@
         _seed_stage2_data(db.session)
         before = _stage2_snapshot(db.session)
         upgrade(directory=MIGRATIONS, revision="head")
-        assert db.session.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger'")).scalar() == 2
+        assert _trigger_names(db.session) >= set(TICKET_TRIGGERS)  # geçmiş tetikleyicileri head'de de durur

         downgrade(directory=MIGRATIONS, revision=STAGE2_REVISION)

@@ -124,7 +132,7 @@
         assert "tickets" not in tables and "ticket_events" not in tables
         assert {"users", "assets", "asset_assignments", "audit_logs"} <= tables
         assert _stage2_snapshot(db.session) == before
-        assert db.session.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger'")).scalar() == 0
+        assert _trigger_names(db.session) == set()

         upgrade(directory=MIGRATIONS, revision="head")  # tekrar yükseltme sorunsuz
         assert "tickets" in inspect(db.engine).get_table_names()
```

### Mevcut kuruluma uygulama (Windows PowerShell)

Uygulamayı (`python run.py`) durdurun. Aşağıdaki komutlar `.env` dosyasına dokunmaz; yedek `.env` içermez.

```powershell
cd C:\Users\can78\Documents\karabuk_takip

# 1) Yedek (kod, testler, migration'lar ve veritabanı; .venv ve .env hariç)
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item -Recurse -Path app, tests, instance, migrations, README.md -Destination "..\karabuk_takip_yedek_asama4_oncesi_$stamp"

# 2) Güncellemeyi geçici klasöre aç ve proje üzerine kopyala (migration'ı UYGULAMAZ)
Expand-Archive -Path "$HOME\Downloads\guncelleme_asama4_arac_bakim.zip" -DestinationPath "$env:TEMP\guncelleme_asama4" -Force
Copy-Item -Recurse -Force -Path "$env:TEMP\guncelleme_asama4\guncelleme_asama4_arac_bakim\*" -Destination .

# 3) Sanal ortamı etkinleştir, migration'ı uygula (mevcut veriler korunur)
.\.venv\Scripts\Activate.ps1
flask --app run db current          # 84674ae864e7 (head) görünmeli (Aşama 3). Farklıysa DURUN.
flask --app run db upgrade
flask --app run db current          # b4c7e1a9d352 (head) görünmeli

# 4) Testler ve çalıştırma
python -m pytest -q                 # beklenen: 393 passed (hazırlık ortamında alınan sonuç)
python run.py
```

Tarayıcıda http://127.0.0.1:5000 adresini açın; menüde **Araç ve Bakım** etkin olmalı, panelde "Araç ve Bakım" kartı
görünmelidir. 3. adımdan önce 2. adımı atlamayın: kod, migration olmadan açılırsa kontrol paneli ve araç sayfaları tablo bulunamadığı için hata verir.

### Geri dönüş

- **Yalnızca Aşama 4 veritabanı nesnelerini geri almak:** `flask --app run db downgrade` (üç yeni tabloyu ve tetikleyicileri
  siler; kullanıcı, demirbaş, zimmet, talep ve denetim verileri kalır). **Dikkat:** o ana kadar girilen araç ve bakım
  kayıtları silinir. Önce yedek alın. Sonra `db current` çıktısı `84674ae864e7` olmalıdır; kod hâlâ Aşama 4 ise
  araç sayfaları çalışmaz, bu yüzden aşağıdaki kod geri dönüşünü de uygulayın.
- **Tam geri dönüş:** uygulamayı durdurun; 1. adımdaki yedek klasöründen `app`, `tests`, `migrations`, `instance` ve
  `README.md` öğelerini proje klasörüne geri kopyalayın (veritabanı dahil önceki hâle döner). Yedekte olmayan Aşama 4
  dosyaları (`app\vehicles`, `app\templates\vehicles`, `migrations\versions\b4c7e1a9d352_*` vb.) kopyalamayla silinmez;
  geri dönüşte bunları elle silmeniz gerekir. Silmeden önce yedeğin eksiksiz olduğundan emin olun.

## Personel kıdem ve yetki yönetimi (Aşama 5)

Yetki **kıdemden bağımsızdır**: kıdem seviyesi (1-5) yalnızca kurumdaki hiyerarşik düzeyi gösterir ve hiçbir
kontrolde kullanılmaz. Ne yapılabileceğini **izinler** belirler. Yüksek kıdem kullanıcı yönetimi veya silme yetkisi vermez.

**Roller:** `superadmin` (sistemin tek aktif üst yöneticisi), `birim_sorumlusu`, `bakim_personeli`, `ariza_kontrol`,
`goruntuleyici`. Rol, verilebilecek izinlerin **tavanını** belirler; etkin izin = atanan izinler ∩ rol tavanı.
Kaynak: `app/permissions.py` (tek yer; roller, izinler, tavanlar, önerilen setler).

| İzin | Anlamı |
|---|---|
| `users.manage` | Kullanıcı yönetimi — **yalnızca süper admin**; hiçbir role atanamaz (kod, form ve veritabanı CHECK'i reddeder) |
| `audit.view` | Denetim kayıtlarını görüntüle |
| `assets.view / create / update / assign` | Demirbaş görüntüle / oluştur / düzenle-arşivle / zimmet ver-iade al |
| `tickets.view` | Kendi açtığı ve kendisine atanan talepleri görüntüle |
| `tickets.view_all` | Tüm talepleri görüntüle |
| `tickets.create` | Talep aç |
| `tickets.update` | Kendisine atanan talebi ilerlet (reddetme/yeniden açma hariç) |
| `tickets.manage` | Görebildiği her talepte her durum geçişi (reddet, yeniden aç) |
| `tickets.assign` | Talebe sorumlu ata (sorumlu `tickets.update` iznine sahip olmalı) |
| `vehicles.view / create / update` | Araç görüntüle / oluştur / düzenle-arşivle |
| `maintenance.view / create / void` | Bakım geçmişi ve uyarıları görüntüle / bakım ekle / bakım iptal et |

Rol tavanları: `birim_sorumlusu` = verilebilen tüm izinler; `bakim_personeli` = araç + bakım + görüntüleme +
talep açma; `ariza_kontrol` = talep modülünün tamamı + demirbaş/araç/bakım görüntüleme; `goruntuleyici` yalnızca
**görüntüleme** izinleri (`tickets.view_all` tavandadır ancak önerilen sette yoktur, ayrıca verilir).

**Tek süper admin:** veritabanında kısmi benzersiz indeks (`ux_users_single_active_superadmin`) en fazla bir aktif
süper admine izin verir. Süper admin rolünü düşüremez, hesabını devre dışı bırakamaz; kullanıcı ekranından süper admin
rolü verilemez. Yetki devri yalnızca sunucuda, tek veritabanı işleminde yapılır:

```powershell
flask --app run transfer-superadmin --to yeni.kullanici      # onay sorar; --yes ile atlanır
flask --app run transfer-superadmin --to yeni.kullanici --former-role bakim_personeli
```

Eski süper admin seçilen role iner ve o rolün tavanındaki tüm izinleri alır (iş kesintisi olmasın; sonra Kullanıcılar
ekranından daraltılabilir). Hata olursa hiçbir hesap değişmez. Devir ve rol değişiklikleri denetim kaydına yazılır.
**Komutu çalıştırmadan önce veritabanı yedeği alın.**

**Kullanıcı ekranı (Yönetim > Kullanıcılar):** süper admin hesap oluşturur; ad soyad, rol, kıdem (1-5), modül izinleri
ve aktiflik düzenlenir. Parola hiçbir ekranda gösterilmez. Rol tavanını aşan veya `users.manage` içeren istekler
sessizce kırpılmaz, reddedilir.

**Denetim kayıtları:** `user_created`, `user_updated` (ad), `user_role_changed`, `user_seniority_changed`,
`user_permissions_changed` (eklenen/çıkarılan izin anahtarları; uzun listeler birden çok kayda bölünür),
`user_activated`, `user_deactivated`, `superadmin_transferred`. Parola ve parola özeti hiçbir kayda yazılmaz.

**Denetim katmanları:** (1) route dekoratörü `permission_required` (`app/decorators.py`) — yetkisiz istek 403;
(2) servis fonksiyonları `ensure_permission` ile kendi iznini yeniden doğrular (`app/services/authz.py`);
(3) veritabanı: tek aktif süper admin indeksi, `users.manage` yasağı CHECK'i, rol/kıdem CHECK'leri. Menü ve düğme
gizleme yalnızca kolaylıktır. İzinler her istekte veritabanından okunur; değişiklik hemen geçerli olur.

**Eski kullanıcıların geçişi (migration `c5d8f2b7a461`):** tek aktif `admin` → `superadmin`; `staff` →
`ariza_kontrol` + bugünkü yetkilerinin birebir karşılığı olan 6 izin; pasif `admin` → `birim_sorumlusu`. Birden fazla
aktif `admin` varsa migration **hiçbir değişiklik yapmadan durur** (fazlalar önce devre dışı bırakılmalı). Geri alma
kayıplıdır (rol ayrıntısı, kıdem ve izinler kaybolur). `migrations/env.py` SQLite'ta migration süresince yabancı anahtar
denetimini kapatır ve (hata olsa da) yeniden açar. Kurulum ve geri dönüş adımları aşağıdadır.

### Mevcut kuruluma uygulama (Windows PowerShell) — Aşama 5

> **Bu paket migration'ı kendiliğinden UYGULAMAZ.** Aşağıdaki adımları sırayla, her bloğu ayrı ayrı çalıştırın.
> Aşama 5 migration'ı `users` tablosunu yeniden oluşturur; **yedek almadan 5. adıma geçmeyin.**
> Hiçbir adım `.env` dosyasını okumaz, değiştirmez veya yedeğe/pakete koymaz.

**Paket içeriği:** değişen/yeni uygulama dosyaları (`app\`), yeni migration `c5d8f2b7a461`, değişen `migrations\env.py`
(**migration'ın çalışması için gerekli**: SQLite yabancı anahtar denetimini migration süresince kapatır, hata olsa da yeniden açar ve
migration'ı tek bir gerçek veritabanı işlemi içinde çalıştırır; `env.py` eski sürümdeyse migration hiçbir şey yapmadan durur),
testler ve bu README. Veritabanı, `.env`, `.venv`, `__pycache__`, `.git` veya yedek **içermez**. Mevcut migration
dosyaları değişmemiştir; migration zincirinde tek head vardır: `c5d8f2b7a461`.

#### 0) Hazırlık: uygulamayı durdurun ve salt okunur kontrol betiğini oluşturun

`python run.py` çalışan pencerede `Ctrl+C` ile uygulamayı durdurun. Sonra:

```powershell
cd C:\Users\can78\Documents\karabuk_takip
.\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8 = "1"
$proje = (Get-Location).Path
$db    = "$proje\instance\karabuk_takip_dev.db"
Get-ChildItem "$proje\instance"     # karabuk_takip_dev.db yanında -wal / -journal dosyası görünmemeli (uygulama kapalıysa görünmez)

# Salt okunur kontrol betiği (veritabanını SALT OKUNUR açar, parola özeti yazdırmaz). Projeye değil TEMP'e yazılır.
$kontrol = "$env:TEMP\asama5_kontrol.py"
@'
"""Aşama 5 geçiş kontrolü. YALNIZCA OKUR (veritabanı salt okunur açılır); parola özeti yazdırılmaz.

Kullanım:  python asama5_kontrol.py <veritabani_yolu>            tam rapor
           python asama5_kontrol.py <veritabani_yolu> --degismez  yalnızca migration'ın DEĞİŞTİRMEMESİ gereken özetler
"""
import hashlib
import pathlib
import sqlite3
import sys

PLAIN = ["assets", "asset_assignments", "tickets", "ticket_events", "vehicles", "maintenance_records",
         "maintenance_voids"]
USER_COLUMNS = "id, username, full_name, password_hash, department, active, created_at, updated_at, last_login_at"


def connect(path):
    file = pathlib.Path(path).resolve()
    if not file.is_file():
        sys.exit(f"Veritabanı dosyası bulunamadı: {file}")
    return sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)


def table_names(c):
    return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}


def fingerprint(c, sql):
    h, n = hashlib.sha256(), 0
    for row in c.execute(sql):
        h.update(repr(row).encode("utf-8"))
        n += 1
    return n, h.hexdigest()[:16]


def invariants(c):
    present = table_names(c)
    lines = []
    queries = [("users (rol ve kıdem hariç)", "users", f"select {USER_COLUMNS} from users order by id")]
    queries += [(t, t, f"select * from {t} order by id") for t in PLAIN]
    queries.append(("audit_logs (user_role_changed hariç)", "audit_logs",
                    "select * from audit_logs where action <> 'user_role_changed' order by id"))
    for label, table, sql in queries:
        if table not in present:
            lines.append(f"{label}: tablo yok")
        else:
            n, digest = fingerprint(c, sql)
            lines.append(f"{label}: satir={n} ozet={digest}")
    return lines


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    c = connect(sys.argv[1])
    inv = invariants(c)
    if "--degismez" in sys.argv:
        print("\n".join(inv))
        return
    present = table_names(c)
    version = c.execute("select version_num from alembic_version").fetchall() if "alembic_version" in present else []
    print("== Sürüm ==")
    print("alembic_version:", ", ".join(v[0] for v in version) or "yok")
    print("== Değişmemesi gerekenler (migration öncesi ve sonrası AYNI olmalı) ==")
    print("\n".join(inv))
    if "audit_logs" in present:
        n = c.execute("select count(*) from audit_logs").fetchone()[0]
        m = c.execute("select count(*) from audit_logs where action = 'user_role_changed'").fetchone()[0]
        print(f"audit_logs toplam: {n} (bunların user_role_changed olanı: {m})")
    print("== Rol dağılımı (rol, aktif, adet) ==")
    for role, active, n in c.execute("select role, active, count(*) from users group by 1, 2 order by 1, 2"):
        print(f"{role}\taktif={active}\t{n}")
    print("== Kullanıcılar ==")
    if "user_permissions" in present:
        rows = c.execute(
            "select u.username, u.role, u.active, u.seniority_level, "
            "(select count(*) from user_permissions p where p.user_id = u.id) from users u order by u.id")
        for username, role, active, level, perms in rows:
            print(f"{username}\trol={role}\taktif={active}\tkidem={level}\tizin_satiri={perms}")
        sup = c.execute("select count(*) from users where role='superadmin' and active=1").fetchone()[0]
        bad = c.execute("select count(*) from user_permissions where permission='users.manage'").fetchone()[0]
        print(f"aktif süper admin sayısı: {sup} (beklenen: 1; 0 ise 'create-admin' ile oluşturulmalı)")
        print(f"users.manage izin satırı: {bad} (beklenen: 0)")
    else:
        for username, role, active in c.execute("select username, role, active from users order by id"):
            print(f"{username}\trol={role}\taktif={active}")
        admins = c.execute("select count(*) from users where role='admin' and active=1").fetchone()[0]
        print(f"aktif admin sayısı: {admins} (migration için en fazla 1 olmalı)")
    print("== Bütünlük ==")
    print("integrity_check:", c.execute("pragma integrity_check").fetchone()[0])
    violations = c.execute("pragma foreign_key_check").fetchall()
    print(f"foreign_key_check: {len(violations)} ihlal (beklenen: 0)")


main()
'@ | Set-Content -Path $kontrol -Encoding utf8
```

#### 1) Yedek alın (kod, testler, migration'lar ve veritabanı; `.env` ve `.venv` hariç)

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$yedek = "$proje\..\karabuk_takip_yedek_asama5_oncesi_$stamp"
New-Item -ItemType Directory -Path $yedek | Out-Null
Copy-Item -Recurse -Path app, tests, instance, migrations, README.md -Destination $yedek
$yedek = (Resolve-Path $yedek).Path
"Yedek klasörü: $yedek"
```

#### 2) Yedeğin varlığını ve bütünlüğünü doğrulayın

```powershell
# a) Veritabanı yedeği canlı dosyayla birebir aynı olmalı (uygulama kapalı): iki SHA-256 değeri AYNI çıkmalı
(Get-FileHash $db -Algorithm SHA256).Hash
(Get-FileHash "$yedek\instance\karabuk_takip_dev.db" -Algorithm SHA256).Hash

# b) Dosya sayıları AYNI olmalı
(Get-ChildItem -Recurse -File app, tests, migrations, instance | Measure-Object).Count
(Get-ChildItem -Recurse -File "$yedek\app", "$yedek\tests", "$yedek\migrations", "$yedek\instance" | Measure-Object).Count

# c) Yedek veritabanı sağlam olmalı: integrity_check: ok  ve  foreign_key_check: 0 ihlal
python $kontrol "$yedek\instance\karabuk_takip_dev.db"
```

**Hash'ler farklıysa, sayılar tutmuyorsa veya `integrity_check` "ok" değilse DURUN**, yedeği yeniden alın.
Veritabanı hash değerini not edin: geri dönüşte (7. adım) yeniden karşılaştıracaksınız.

#### 3) Migration ÖNCESİ kontroller (henüz yeni dosyaları kopyalamayın)

```powershell
flask --app run db current          # b4c7e1a9d352 (head) görünmeli. Farklıysa DURUN: önce Aşama 4 uygulanmalıdır.
python $kontrol $db                 # tam rapor
# Migration'ın DEĞİŞTİRMEMESİ gereken tabloların özetini yedek klasörüne kaydedin (6. adımda karşılaştırılacak)
python $kontrol $db --degismez | Out-File "$yedek\oncesi_degismez.txt" -Encoding utf8
```

Raporda şunlara bakın:

- `aktif admin sayısı` **en fazla 1** olmalıdır. Bu hesap migration'da **süper admin** olur.
- `foreign_key_check: 0 ihlal` ve `integrity_check: ok` olmalıdır. İhlal varsa migration zaten hiçbir değişiklik yapmadan durur; önce sorunu çözün.
- Dönüşüm: tek aktif `admin` → `superadmin`; `staff` → `ariza_kontrol` (bugünkü 6 yetkisinin birebir karşılığı olan izinlerle);
  pasif `admin` → `birim_sorumlusu` (verilebilen tüm izinlerle). Tüm kullanıcıların kıdemi 1 olur; sonra ekrandan düzenlenir.

**Birden fazla aktif `admin` varsa (izlenecek yol):** migration kimin süper admin olacağına sizin yerinize karar vermez ve
**hiçbir değişiklik yapmadan durur**. Bu durumu yeni kodu kopyalamadan ÖNCE, hâlâ eski uygulamayla çözün:

1. Hangi hesabın süper admin olacağına karar verin (diğerleri bu kararla devre dışı kalacak).
2. `python run.py` ile eski uygulamayı başlatın, süper admin olacak hesapla girin, **Yönetim > Kullanıcılar** ekranından
   diğer aktif admin hesaplarını **Devre dışı bırak** ile pasifleştirin (pasif admin migration'da `birim_sorumlusu` olur; verileri silinmez).
3. Uygulamayı durdurun, `python $kontrol $db` ile `aktif admin sayısı: 1` olduğunu doğrulayın.
4. **1. ve 2. adımları (yedek + doğrulama) yeniden yapın** — veritabanı değişti — ve bu adımdan devam edin.

**Hiç aktif `admin` yoksa:** migration süper admin üretmez. Yükseltmeden sonra `flask --app run create-admin` ile oluşturun
(komut yalnızca aktif süper admin yokken çalışır).

#### 4) Paketi doğrulayıp uygulama dosyalarını güncelleyin (migration'ı UYGULAMAZ)

```powershell
$zip = "$HOME\Downloads\guncelleme_asama5_kidem_yetki.zip"
(Get-FileHash $zip -Algorithm SHA256).Hash     # Raporda verilen SHA-256 değeriyle AYNI olmalı; değilse DURUN
$tmp = "$env:TEMP\guncelleme_asama5"
Expand-Archive -Path $zip -DestinationPath $tmp -Force
Get-ChildItem -Recurse -File $tmp | Select-Object -ExpandProperty FullName   # .env, *.db, .venv, __pycache__ olmamalı
Copy-Item -Recurse -Force -Path "$tmp\guncelleme_asama5_kidem_yetki\*" -Destination $proje
```

Kopyalamadan sonra **uygulamayı başlatmayın** (kod yeni, veritabanı henüz eski şemada). Kontroller:

```powershell
flask --app run db heads            # yalnızca c5d8f2b7a461 (head) görünmeli
flask --app run db current          # hâlâ b4c7e1a9d352 görünmeli (migration uygulanmadı)
python -m pytest -q                 # beklenen: 621 passed (testler bellekte/geçici veritabanlarında çalışır; gerçek veritabanına bağlanmaz)
```

#### 5) Migration'ı uygulayın (siz elle çalıştırırsınız)

```powershell
flask --app run db upgrade
flask --app run db current          # c5d8f2b7a461 (head) görünmeli
```

**Migration hata verirse ne olur (neyin kanıtlandığı ve neyin kanıtlanamadığı):**

- Migration tüm adımlarını (yeni tablolar, veri kopyası, izinler, denetim kayıtları, eski `users` tablosunu silme, yeniden
  adlandırma, indeksler ve `alembic_version` güncellemesi) **tek bir SQLite işlemi** içinde çalıştırır. Pakete eklenen testler
  bu adımların **her birinde** ve commit anında hata üretir; her durumda veritabanının şeması ve tüm tablolarının tüm satırları
  (kullanıcılar, izinler, denetim kayıtları, demirbaş/talep/araç/bakım tabloları, `alembic_version`) hatadan önceki hâliyle
  **aynı** kalır, yabancı anahtar denetimi yeniden açılır ve hata giderilince migration aynı veritabanında yeniden çalışır.
  Bu durumda `flask --app run db current` hâlâ `b4c7e1a9d352` gösterir.
- Ön kontrol hataları (birden fazla aktif `admin`, mevcut yabancı anahtar tutarsızlığı, eski `env.py`) migration ilk ifadesini
  çalıştırmadan önce durur.
- **Test edilemeyen durumlar:** çalışırken bilgisayarın kapanması/elektrik kesilmesi, disk dolması, antivirüs/senkronizasyon
  yazılımının dosyayı kilitlemesi veya Python işleminin zorla sonlandırılması gibi altyapı arızaları simüle edilemez. SQLite
  yarım kalan işlemi bir sonraki açılışta geri alır, ancak bu **sizin ortamınızda doğrulanmış değildir**. Bu yüzden 1–2. adımlardaki
  yedek **zorunludur**; migration sonrasında veritabanından şüphe duyarsanız `Get-FileHash` değerini yedekle karşılaştırın,
  `python $kontrol $db` raporuna bakın ve değişiklik varsa 7-A (yedekten geri yükleme) uygulayın.
- Başarısız çalıştırma sonrası kontrol: `flask --app run db current` çıktısı ile `(Get-FileHash $db -Algorithm SHA256).Hash`
  değerini yedekle karşılaştırın. Aynıysa dosya birebir yedektir (SHA-256 tam eşleşir). Farklıysa (SQLite dosya sayfaları işlem
  sonrası farklı düzenlenmiş olabilir) `python $kontrol $db --degismez` çıktısını `oncesi_degismez.txt` ile karşılaştırın; fark
  varsa 7-A uygulanır.

Mesajda "aktif 'admin'" geçiyorsa 3. adımdaki "birden fazla aktif admin" yolunu uygulayın; "tutarsizlik" geçiyorsa
veritabanında yabancı anahtar tutarsızlığı vardır; "tek bir veritabani islemi" geçiyorsa `migrations\env.py` dosyası pakettekiyle
değiştirilmemiştir. Her durumda hata mesajını olduğu gibi iletin. Hata sürerse 7. adımdaki geri dönüşü uygulayın.

#### 6) Migration SONRASI doğrulama

```powershell
python $kontrol $db                                     # tam rapor
python $kontrol $db --degismez | Out-File "$env:TEMP\sonrasi_degismez.txt" -Encoding utf8
Compare-Object (Get-Content "$yedek\oncesi_degismez.txt") (Get-Content "$env:TEMP\sonrasi_degismez.txt")
```

Beklenen sonuç:

- `Compare-Object` **hiçbir çıktı vermemeli**: kullanıcılar (rol/kıdem hariç), demirbaşlar, zimmetler, talepler ve geçmişi,
  araçlar, bakım kayıtları, bakım iptalleri ve mevcut denetim kayıtlarının satır sayıları ve içerik özetleri aynıdır.
  Fark çıkarsa **uygulamayı başlatmayın**, 7. adımdaki geri dönüşü uygulayın.
- `alembic_version: c5d8f2b7a461`, `integrity_check: ok`, `foreign_key_check: 0 ihlal`.
- Rol dağılımı: eski aktif `admin` → `superadmin` (aktif, **tam 1 tane**); `staff` hesapları → `ariza_kontrol`
  (`izin_satiri=6`); pasif `admin` → `birim_sorumlusu` (`izin_satiri=17`). `users.manage izin satırı: 0`.
- `audit_logs toplam` = eski toplam + dönüştürülen hesap sayısı (her hesap için bir `user_role_changed` kaydı).
- Süper admin sayısı 0 ise: `flask --app run create-admin`.

Sonra uygulamayı başlatıp elle kontrol edin:

```powershell
python run.py
```

Süper admin ile giriş yapın: **Yönetim > Kullanıcılar** ekranında rol/kıdem/izin sütunlarını, bir hesabın **Düzenle** ekranını,
Demirbaş, Talepler ve Araç/Bakım ekranlarındaki kayıtları ve **Denetim Kayıtları** ekranında `Kullanıcı rolü değişti` kayıtlarını görün.
Eski bir personel hesabıyla girip önceki yetkilerinin (görüntüleme, talep açma/ilerletme) korunduğunu doğrulayın.

#### 7) Geri dönüş

> **UYARI — her iki yöntem de kayıplı olabilir.** Veritabanı yedeğine dönmek, yedek alındıktan sonra
> (migration sonrası dahil) girilen **tüm kayıtları siler**: yeni kullanıcılar, rol/izin değişiklikleri, demirbaş,
> zimmet, talep, araç, bakım kayıtları ve denetim kayıtları. `db downgrade` ise verileri korur ama **rol ayrıntılarını,
> kıdem seviyelerini ve izinleri kaybettirir** (aşağıya bakın). Geri dönmeden önce geçerli durumun ayrı bir kopyasını alın.

**A) Tam geri dönüş (önerilen): kodu ve veritabanını yedekten geri yükleyin**

```powershell
# Uygulamayı durdurun. Mevcut klasörleri silmeden yeniden adlandırın (inceleme için saklanır).
$t = Get-Date -Format "yyyyMMdd_HHmmss"
foreach ($d in "app", "tests", "migrations", "instance") { Rename-Item -Path "$proje\$d" -NewName "${d}_asama5_$t" }
Copy-Item -Recurse -Path "$yedek\app", "$yedek\tests", "$yedek\migrations", "$yedek\instance" -Destination $proje
Copy-Item -Force -Path "$yedek\README.md" -Destination $proje

# Doğrulama
(Get-FileHash $db -Algorithm SHA256).Hash      # 2. adımda not ettiğiniz yedek hash'iyle AYNI olmalı
flask --app run db current                     # b4c7e1a9d352 (head) görünmeli
python $kontrol $db
python -m pytest -q                            # beklenen: 393 passed (Aşama 4 sonrası test sayısı)
```

Yeniden adlandırılan `*_asama5_*` klasörleri, her şeyin yolunda olduğundan emin olana kadar silinmemelidir.

**B) Yalnızca veritabanı şemasını geri almak (`db downgrade`) — veriyi korur, ayrıntıyı kaybettirir**

```powershell
flask --app run db downgrade                   # c5d8f2b7a461 -> b4c7e1a9d352 (Aşama 5 kodunun/migration dosyasının dizinde olması gerekir)
flask --app run db current                     # b4c7e1a9d352 görünmeli
```

- `superadmin` → `admin`; **diğer tüm roller → `staff`** olur. `birim_sorumlusu`, `bakim_personeli`, `goruntuleyici` hesapları
  eski modelde yönetici yetkisini kaybeder. Kıdem seviyeleri ve `user_permissions` tablosu (tüm izinler) **silinir**; yeniden
  yükseltme bu bilgileri geri getirmez (yalnızca eski `admin`/`staff` kurallarıyla yeniden dönüştürür).
- Demirbaş, zimmet, talep, araç, bakım ve mevcut denetim kayıtları korunur; migration'ın yazdığı `user_role_changed` denetim kayıtları kalır.
- Sonra **A** bölümündeki kod geri yüklemesini yalnızca `app`, `tests`, `migrations` klasörleri için uygulayın (`instance`'a
  dokunmayın); çünkü Aşama 5 kodu eski şemada çalışmaz.

## Testler

```powershell
python -m pytest -q
```

## Notlar

- `.env` ve `instance/` Git'e eklenmez. Gerçek parola veya anahtar kaynak koda yazılmaz.
- `create-admin` yalnızca aktif süper admin yokken çalışır. Parola etkileşimli sorulur.
- Personel hesapları artık yönetici girişinden sonra **Yönetim > Kullanıcılar** ekranından oluşturulur.
  (`flask --app run create-staff` komutu geliştirme için duruyor.)
- Bilinen eksik: parola değiştirme/sıfırlama henüz yok; yönetici geçici parolayı hesap sahibine iletir.
- Bootstrap 5.3.8 yerel olarak `app/static/vendor/bootstrap/` altında sunulur (CDN kullanılmaz).
- `python run.py` yalnızca yerel geliştirme içindir; gerçek kurumsal dağıtım ayrı bir aşamadır.

"""Yerel geliştirme sunucusu. Üretimde bu dosya doğrudan çalıştırılmaz."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    # Yalnızca yerel makineden erişim (127.0.0.1). Debug yalnızca geliştirme yapılandırmasında açıktır.
    app.run(host="127.0.0.1", port=5000, debug=app.config["DEBUG"])

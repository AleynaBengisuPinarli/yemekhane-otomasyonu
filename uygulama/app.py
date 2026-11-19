import json
import math
import os
import secrets
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import bcrypt
import pymysql
import redis
from flask import Flask, jsonify, request

from veritabani import (
    alerjenleri_getir,
    bakiye_yukle,
    bugunun_menusu,
    degerlendirme_yap,
    haftalik_menuler,
    kullanici_alerjen_idlerini_getir,
    kullanici_alerjenlerini_kaydet,
    kullanici_bakiye_hareketleri,
    kullanici_epostayla_getir,
    kullanici_rezervasyonlari,
    menu_kaydet,
    rezervasyon_iptal_et,
    rezervasyon_yap,
    yonetici_menu_ozeti,
)


app = Flask(__name__)
app.logger.setLevel("INFO")

ISTANBUL = ZoneInfo("Europe/Istanbul")

redis_baglantisi = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    decode_responses=True,
)


def gun_sonu_ttl(tarih):
    tarih = datetime.strptime(str(tarih)[:10], "%Y-%m-%d").date()
    yarin = tarih + timedelta(days=1)
    gun_sonu = datetime.combine(yarin, time.min, tzinfo=ISTANBUL)
    simdi = datetime.now(ISTANBUL)
    return max(1, int((gun_sonu - simdi).total_seconds()))


def menu_listesi_onbellekten_getir(indeks_anahtari, yukleyici):
    try:
        anahtarlar_json = redis_baglantisi.get(indeks_anahtari)

        if anahtarlar_json is not None:
            anahtarlar = json.loads(anahtarlar_json)
            kayitlar_json = redis_baglantisi.mget(anahtarlar)

            if all(kayit is not None for kayit in kayitlar_json):
                sonuc = []

                for kayit in kayitlar_json:
                    sonuc.extend(json.loads(kayit))

                app.logger.info(
                    "Menü önbelleği HIT: yanıt Redis'ten verildi."
                )
                return sonuc

    except (redis.RedisError, json.JSONDecodeError):
        app.logger.exception("Menü önbelleği okunamadı.")

    app.logger.info(
        "Menü önbelleği MISS: veriler veritabanından okunuyor."
    )
    sonuc = yukleyici()
    gruplar = {}

    for satir in sonuc:
        menu_anahtari = (
            f"menu:{satir['yemekhane_id']}:"
            f"{satir['tarih']}:"
            f"{satir['ogun_id']}"
        )
        gruplar.setdefault(menu_anahtari, []).append(satir)

    bugun = datetime.now(ISTANBUL).date().isoformat()
    indeks_ttl = gun_sonu_ttl(bugun)
    anahtarlar = list(gruplar.keys())

    try:
        pipeline = redis_baglantisi.pipeline()

        for menu_anahtari, menu_satirlari in gruplar.items():
            ttl = gun_sonu_ttl(menu_satirlari[0]["tarih"])
            pipeline.setex(
                menu_anahtari,
                ttl,
                json.dumps(
                    menu_satirlari,
                    ensure_ascii=False,
                    default=str,
                ),
            )

        pipeline.setex(
            indeks_anahtari,
            indeks_ttl,
            json.dumps(anahtarlar, ensure_ascii=False),
        )
        pipeline.execute()

    except redis.RedisError:
        app.logger.exception("Menü önbelleğine yazılamadı.")

    return sonuc


def menu_onbelleklerini_temizle():
    try:
        anahtarlar = list(redis_baglantisi.scan_iter(match="menu:*"))

        if anahtarlar:
            redis_baglantisi.delete(*anahtarlar)

    except redis.RedisError:
        app.logger.exception("Menü önbellekleri temizlenemedi.")


def oturum_bilgisi():
    yetki = request.headers.get("Authorization", "")

    if not yetki.startswith("Bearer "):
        return None

    token = yetki.removeprefix("Bearer ").strip()
    anahtar = f"oturum:{token}"
    kayit = redis_baglantisi.get(anahtar)

    if kayit is None:
        return None

    redis_baglantisi.expire(anahtar, 1800)
    return json.loads(kayit)


@app.before_request
def oturumu_yenile():
    yetki = request.headers.get("Authorization", "")

    if yetki.startswith("Bearer "):
        token = yetki.removeprefix("Bearer ").strip()
        redis_baglantisi.expire(f"oturum:{token}", 1800)


@app.get("/api/")
def api_durumu():
    return jsonify({
        "durum": "çalışıyor",
        "mesaj": "Yemekhane API hazır.",
    })


@app.get("/api/menuler")
def menuler():
    bugun = datetime.now(ISTANBUL).date().isoformat()
    veri = menu_listesi_onbellekten_getir(
        f"menu:index:bugun:{bugun}",
        bugunun_menusu,
    )
    return jsonify(veri)


@app.get("/api/menuler/haftalik")
def haftalik_menu_api():
    bugun = datetime.now(ISTANBUL).date().isoformat()
    veri = menu_listesi_onbellekten_getir(
        f"menu:index:haftalik:{bugun}",
        haftalik_menuler,
    )
    return jsonify(veri)


@app.get("/api/rezervasyonlarim")
def rezervasyonlarim():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({
            "hata": "Rezervasyonlarınızı görmek için giriş yapmalısınız.",
        }), 401

    return jsonify(
        kullanici_rezervasyonlari(kullanici["kullanici_id"])
    )


@app.post("/api/giris")
def giris():
    veri = request.get_json(silent=True) or {}
    eposta = veri.get("eposta", "")
    sifre = veri.get("sifre", "")

    if not isinstance(eposta, str) or not isinstance(sifre, str):
        return jsonify({"hata": "E-posta ve şifre gerekli."}), 400

    kullanici = kullanici_epostayla_getir(eposta.strip().lower())

    if (
        not kullanici
        or not kullanici["aktif"]
        or not bcrypt.checkpw(
            sifre.encode("utf-8"),
            kullanici["sifre_hash"].encode("utf-8"),
        )
    ):
        return jsonify({"hata": "E-posta veya şifre hatalı."}), 401

    token = secrets.token_urlsafe(32)
    oturum = {
        "kullanici_id": kullanici["id"],
        "ad": kullanici["ad"],
        "soyad": kullanici["soyad"],
        "eposta": kullanici["eposta"],
        "rol": kullanici["rol"],
    }

    redis_baglantisi.setex(
        f"oturum:{token}",
        1800,
        json.dumps(oturum, ensure_ascii=False),
    )

    return jsonify({
        "token": token,
        "kullanici": oturum,
    })


@app.get("/api/oturum")
def oturum():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({"hata": "Oturum bulunamadı."}), 401

    return jsonify({"kullanici": kullanici})


@app.route("/api/profil/alerjenler", methods=["GET", "PUT"])
def profil_alerjenleri():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({
            "hata": "Alerjen tercihlerinizi yönetmek için giriş yapmalısınız.",
        }), 401

    kullanici_id = kullanici["kullanici_id"]

    if request.method == "GET":
        try:
            return jsonify({
                "alerjenler": alerjenleri_getir(),
                "secili_alerjen_idleri":
                    kullanici_alerjen_idlerini_getir(kullanici_id),
            })
        except pymysql.MySQLError:
            app.logger.exception("Alerjen tercihleri alınamadı.")
            return jsonify({
                "hata": "Alerjen tercihleri şu anda alınamadı.",
            }), 500

    veri = request.get_json(silent=True)

    if not isinstance(veri, dict):
        return jsonify({"hata": "Geçerli bir JSON gönderin."}), 400

    alerjen_idleri = veri.get("alerjen_idleri")

    if not isinstance(alerjen_idleri, list):
        return jsonify({
            "hata": "Alerjen seçimleri liste biçiminde gönderilmeli.",
        }), 400

    try:
        kaydedilen_idler = kullanici_alerjenlerini_kaydet(
            kullanici_id,
            alerjen_idleri,
        )
        return jsonify({
            "mesaj": "Alerjen tercihleriniz kaydedildi.",
            "secili_alerjen_idleri": kaydedilen_idler,
        })
    except ValueError as hata:
        return jsonify({"hata": str(hata)}), 400
    except pymysql.MySQLError:
        app.logger.exception("Alerjen tercihleri kaydedilemedi.")
        return jsonify({
            "hata": "Alerjen tercihleri şu anda kaydedilemedi.",
        }), 500


@app.post("/api/cikis")
def cikis():
    yetki = request.headers.get("Authorization", "")

    if yetki.startswith("Bearer "):
        token = yetki.removeprefix("Bearer ").strip()
        redis_baglantisi.delete(f"oturum:{token}")

    return jsonify({"mesaj": "Çıkış yapıldı."})


@app.post("/api/rezervasyon")
def rezervasyon():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({
            "hata": "Rezervasyon için giriş yapmalısınız.",
        }), 401

    veri = request.get_json(silent=True) or {}

    if not isinstance(veri, dict):
        return jsonify({"hata": "Geçerli bir JSON gönderin."}), 400

    try:
        menu_id = int(veri.get("menu_id"))
    except (TypeError, ValueError):
        return jsonify({"hata": "Geçerli bir menü seçin."}), 400

    if menu_id < 1:
        return jsonify({"hata": "Geçerli bir menü seçin."}), 400

    yarim_porsiyon = veri.get("yarim_porsiyon", False)

    if not isinstance(yarim_porsiyon, bool):
        return jsonify({
            "hata": "Yarım porsiyon seçimi doğru biçimde gönderilmedi.",
        }), 400

    try:
        sonuc = rezervasyon_yap(
            kullanici["kullanici_id"],
            menu_id,
            yarim_porsiyon,
        )
        menu_onbelleklerini_temizle()
        return jsonify(sonuc or {})
    except pymysql.MySQLError as hata:
        if hata.args and hata.args[0] == 1644:
            return jsonify({"hata": str(hata.args[1])}), 400

        app.logger.exception("Rezervasyon işlemi başarısız oldu.")
        return jsonify({
            "hata": "Rezervasyon şu anda yapılamadı.",
        }), 500


@app.post("/api/rezervasyon-iptal")
def rezervasyon_iptal():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({
            "hata": "İptal için giriş yapmalısınız.",
        }), 401

    veri = request.get_json(silent=True) or {}

    try:
        rezervasyon_id = int(veri.get("rezervasyon_id"))
    except (TypeError, ValueError):
        return jsonify({"hata": "Geçerli bir rezervasyon seçin."}), 400

    if rezervasyon_id < 1:
        return jsonify({"hata": "Geçerli bir rezervasyon seçin."}), 400

    try:
        sonuc = rezervasyon_iptal_et(
            kullanici["kullanici_id"],
            rezervasyon_id,
        )
        menu_onbelleklerini_temizle()
        return jsonify(sonuc or {"mesaj": "Rezervasyon iptal edildi."})
    except pymysql.MySQLError as hata:
        if hata.args and hata.args[0] == 1644:
            return jsonify({"hata": str(hata.args[1])}), 400

        app.logger.exception("Rezervasyon iptali başarısız oldu.")
        return jsonify({
            "hata": "Rezervasyon şu anda iptal edilemedi.",
        }), 500


@app.get("/api/bakiye")
def bakiye():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({
            "hata": "Bakiyeyi görmek için giriş yapmalısınız.",
        }), 401

    sonuc = kullanici_bakiye_hareketleri(
        kullanici["kullanici_id"]
    )

    if sonuc is None:
        return jsonify({"hata": "Kullanıcı bulunamadı."}), 404

    return jsonify(sonuc)


@app.post("/api/bakiye-yukle")
def bakiye_yukle_api():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({
            "hata": "Bakiye yüklemek için giriş yapmalısınız.",
        }), 401

    veri = request.get_json(silent=True) or {}

    try:
        tutar = float(veri.get("tutar"))
    except (TypeError, ValueError):
        return jsonify({
            "hata": "Geçerli bir yükleme tutarı girin.",
        }), 400

    if not math.isfinite(tutar) or not 10 <= tutar <= 1000:
        return jsonify({
            "hata": "Yükleme tutarı 10 ile 1000 TL arasında olmalı.",
        }), 400

    try:
        sonuc = bakiye_yukle(
            kullanici["kullanici_id"],
            tutar,
        )
        return jsonify(sonuc or {})
    except pymysql.MySQLError as hata:
        if hata.args and hata.args[0] == 1644:
            return jsonify({"hata": str(hata.args[1])}), 400

        app.logger.exception("Bakiye yükleme işlemi başarısız oldu.")
        return jsonify({
            "hata": "Bakiye şu anda yüklenemedi.",
        }), 500


@app.post("/api/degerlendirme")
def degerlendirme():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({
            "hata": "Değerlendirme yapmak için giriş yapmalısınız.",
        }), 401

    veri = request.get_json(silent=True) or {}

    try:
        menu_id = int(veri.get("menu_id"))
        puan = int(veri.get("puan"))
    except (TypeError, ValueError):
        return jsonify({
            "hata": "Menü numarası ve 1–5 arasında puan gerekli.",
        }), 400

    if menu_id < 1 or puan < 1 or puan > 5:
        return jsonify({
            "hata": "Menü numarası geçerli olmalı; puan 1–5 arasında olmalı.",
        }), 400

    yorum = veri.get("yorum", "")

    if not isinstance(yorum, str):
        return jsonify({"hata": "Yorum metin olmalı."}), 400

    yorum = yorum.strip() or None

    if yorum and len(yorum) > 500:
        return jsonify({
            "hata": "Yorum en fazla 500 karakter olabilir.",
        }), 400

    try:
        sonuc = degerlendirme_yap(
            kullanici["kullanici_id"],
            menu_id,
            puan,
            yorum,
        )
        return jsonify(sonuc), 201
    except ValueError as hata:
        return jsonify({"hata": str(hata)}), 400
    except pymysql.MySQLError as hata:
        if hata.args and hata.args[0] == 1062:
            return jsonify({
                "hata": "Bu menüyü daha önce değerlendirdiniz.",
            }), 409

        app.logger.exception("Değerlendirme kaydedilemedi.")
        return jsonify({
            "hata": "Değerlendirme şu anda kaydedilemedi.",
        }), 500


@app.get("/api/yonetici/menuler")
def yonetici_menuleri():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({"hata": "Giriş yapmalısınız."}), 401

    if kullanici.get("rol") != "yonetici":
        return jsonify({
            "hata": "Bu işlem için yönetici yetkisi gerekli.",
        }), 403

    return jsonify(yonetici_menu_ozeti())


@app.route("/api/yonetici/menuler", methods=["POST", "PUT"])
def yonetici_menu_kaydet():
    kullanici = oturum_bilgisi()

    if not kullanici:
        return jsonify({"hata": "Giriş yapmalısınız."}), 401

    if kullanici.get("rol") != "yonetici":
        return jsonify({
            "hata": "Bu işlem için yönetici yetkisi gerekli.",
        }), 403

    veri = request.get_json(silent=True)

    if not isinstance(veri, dict):
        return jsonify({"hata": "Geçerli bir JSON gönderin."}), 400

    try:
        yemekhane_id = int(veri.get("yemekhane_id"))
        ogun_id = int(veri.get("ogun_id"))
        ogrenci_fiyati = float(veri.get("ogrenci_fiyati"))
        personel_fiyati = float(veri.get("personel_fiyati"))
        yemek_idler = veri.get("yemek_idler")
    except (TypeError, ValueError):
        return jsonify({
            "hata": "Yemekhane, öğün ve fiyat alanları geçerli olmalı.",
        }), 400

    tarih = veri.get("tarih")
    menu_id = veri.get("menu_id")

    if request.method == "PUT":
        try:
            menu_id = int(menu_id)
        except (TypeError, ValueError):
            return jsonify({
                "hata": "Düzenlenecek menünün numarası gerekli.",
            }), 400

        if menu_id < 1:
            return jsonify({"hata": "Menü numarası geçersiz."}), 400
    else:
        menu_id = None

    if not isinstance(tarih, str) or not tarih.strip():
        return jsonify({"hata": "Menü tarihi gerekli."}), 400

    if yemekhane_id < 1 or ogun_id < 1:
        return jsonify({
            "hata": "Yemekhane ve öğün seçimi geçerli olmalı.",
        }), 400

    if (
        not math.isfinite(ogrenci_fiyati)
        or not math.isfinite(personel_fiyati)
        or ogrenci_fiyati < 0
        or personel_fiyati < 0
    ):
        return jsonify({
            "hata": "Fiyatlar sıfır veya daha büyük olmalı.",
        }), 400

    if not isinstance(yemek_idler, list) or not yemek_idler:
        return jsonify({
            "hata": "Menüye en az bir yemek eklemelisiniz.",
        }), 400

    try:
        yemek_idler = [int(yemek_id) for yemek_id in yemek_idler]
    except (TypeError, ValueError):
        return jsonify({
            "hata": "Yemek numaraları geçerli olmalı.",
        }), 400

    if any(yemek_id < 1 for yemek_id in yemek_idler):
        return jsonify({
            "hata": "Yemek numaraları geçerli olmalı.",
        }), 400

    try:
        sonuc = menu_kaydet(
            yemekhane_id,
            tarih.strip(),
            ogun_id,
            ogrenci_fiyati,
            personel_fiyati,
            yemek_idler,
            menu_id=menu_id,
        )
        menu_onbelleklerini_temizle()
        return jsonify(sonuc or {"mesaj": "Menü kaydedildi."}), 201
    except ValueError as hata:
        return jsonify({"hata": str(hata)}), 400
    except pymysql.MySQLError as hata:
        if hata.args and hata.args[0] == 1062:
            return jsonify({
                "hata": "Bu yemekhane, tarih ve öğün için menü zaten var.",
            }), 409

        app.logger.exception("Yönetici menüsü kaydedilemedi.")
        return jsonify({
            "hata": "Menü şu anda kaydedilemedi.",
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
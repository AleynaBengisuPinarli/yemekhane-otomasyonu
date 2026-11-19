import os

import pymysql


def _baglanti(host):
    return pymysql.connect(
        host=os.environ[host],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_KULLANICI"],
        password=os.environ["DB_SIFRE"],
        database=os.environ["DB_ADI"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def yazma_baglantisi():
    return _baglanti("DB_YAZMA_HOST")


def okuma_baglantisi():
    return _baglanti("DB_OKUMA_HOST")


def bugunun_menusu():
    db = okuma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT
                    menu_id,
                    DATE_FORMAT(tarih, '%Y-%m-%d') AS tarih,
                    yemekhane_id,
                    yemekhane,
                    ogun_id,
                    ogun,
                    ogun_sira,
                    DATE_FORMAT(
                        rezervasyon_son,
                        '%Y-%m-%d %H:%i:%s'
                    ) AS rezervasyon_son,
                    ogrenci_fiyati,
                    personel_fiyati,
                    yemek_id,
                    yemek,
                    kategori_id,
                    kategori,
                    kategori_sira,
                    kalori,
                    vejetaryen,
                    alerjenler,
                    alerjen_idler
                FROM v_menu_detay
                WHERE tarih = CURDATE()
                ORDER BY
                    yemekhane_id,
                    ogun_sira,
                    kategori_sira,
                    yemek
            """)
            return cursor.fetchall()
    finally:
        db.close()


def kullanici_epostayla_getir(eposta):
    db = okuma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT
                    id,
                    ad,
                    soyad,
                    eposta,
                    sifre_hash,
                    rol,
                    bakiye,
                    aktif
                FROM kullanicilar
                WHERE eposta = %s
                LIMIT 1
            """, (eposta,))
            return cursor.fetchone()
    finally:
        db.close()


def haftalik_menuler():
    db = okuma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT
                    d.menu_id,
                    DATE_FORMAT(d.tarih, '%Y-%m-%d') AS tarih,
                    d.yemekhane_id,
                    d.yemekhane,
                    d.ogun_id,
                    d.ogun,
                    d.ogun_sira,
                    d.ogrenci_fiyati,
                    d.personel_fiyati,
                    d.yemek_id,
                    d.yemek,
                    d.kategori,
                    d.kategori_sira,
                    d.kalori,
                    d.vejetaryen,
                    d.alerjenler,
                    d.alerjen_idler,
                    DATE_FORMAT(
                        d.rezervasyon_son,
                        '%Y-%m-%d %H:%i:%s'
                    ) AS rezervasyon_son,
                    v.kapasite,
                    v.rezerve,
                    v.bos_yer,
                    v.doluluk_yuzde
                FROM v_menu_detay AS d
                JOIN v_menu_doluluk AS v
                    ON v.menu_id = d.menu_id
                WHERE d.tarih >= CURDATE()
                  AND d.tarih < DATE_ADD(CURDATE(), INTERVAL 7 DAY)
                ORDER BY
                    d.tarih,
                    d.yemekhane_id,
                    d.ogun_sira,
                    d.kategori_sira,
                    d.yemek
            """)
            return cursor.fetchall()
    finally:
        db.close()


def kullanici_rezervasyonlari(kullanici_id):
    db = okuma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT
                    r.id AS rezervasyon_id,
                    r.menu_id,
                    r.durum,
                    r.ucret,
                    r.yarim_porsiyon,
                    DATE_FORMAT(
                        r.olusturma,
                        '%%Y-%%m-%%d %%H:%%i:%%s'
                    ) AS olusturma,
                    DATE_FORMAT(
                        r.kullanma_zamani,
                        '%%Y-%%m-%%d %%H:%%i:%%s'
                    ) AS kullanma_zamani,
                    DATE_FORMAT(
                        r.iptal_zamani,
                        '%%Y-%%m-%%d %%H:%%i:%%s'
                    ) AS iptal_zamani,
                    DATE_FORMAT(
                        m.tarih,
                        '%%Y-%%m-%%d'
                    ) AS tarih,
                    yh.ad AS yemekhane,
                    o.ad AS ogun,
                    DATE_FORMAT(
                        TIMESTAMP(m.tarih, o.son_saat),
                        '%%Y-%%m-%%d %%H:%%i:%%s'
                    ) AS rezervasyon_son,
                    (d.id IS NOT NULL) AS degerlendirildi
                FROM rezervasyonlar AS r
                JOIN menuler AS m
                    ON m.id = r.menu_id
                JOIN yemekhaneler AS yh
                    ON yh.id = m.yemekhane_id
                JOIN ogunler AS o
                    ON o.id = m.ogun_id
                LEFT JOIN degerlendirmeler AS d
                    ON d.kullanici_id = r.kullanici_id
                   AND d.menu_id = r.menu_id
                WHERE r.kullanici_id = %s
                ORDER BY m.tarih DESC, o.sira
            """, (kullanici_id,))
            return cursor.fetchall()
    finally:
        db.close()


def rezervasyon_yap(kullanici_id, menu_id, yarim_porsiyon=False):
    db = yazma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute(
                "CALL sp_rezervasyon_yap(%s, %s, %s)",
                (
                    kullanici_id,
                    menu_id,
                    int(bool(yarim_porsiyon)),
                ),
            )
            return cursor.fetchone()
    finally:
        db.close()


def rezervasyon_iptal_et(kullanici_id, rezervasyon_id):
    db = yazma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute(
                "CALL sp_rezervasyon_iptal(%s, %s)",
                (kullanici_id, rezervasyon_id),
            )
            return cursor.fetchone()
    finally:
        db.close()


def kullanici_bakiye_hareketleri(kullanici_id):
    db = okuma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT bakiye
                FROM kullanicilar
                WHERE id = %s
                LIMIT 1
            """, (kullanici_id,))
            kullanici = cursor.fetchone()

            if kullanici is None:
                return None

            cursor.execute("""
                SELECT
                    id,
                    tur,
                    tutar,
                    aciklama,
                    DATE_FORMAT(
                        olusturma,
                        '%%Y-%%m-%%d %%H:%%i:%%s'
                    ) AS olusturma
                FROM bakiye_hareketleri
                WHERE kullanici_id = %s
                ORDER BY olusturma DESC, id DESC
                LIMIT 50
            """, (kullanici_id,))

            return {
                "bakiye": kullanici["bakiye"],
                "hareketler": cursor.fetchall(),
            }
    finally:
        db.close()


def bakiye_yukle(kullanici_id, tutar):
    db = yazma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute(
                "CALL sp_bakiye_yukle(%s, %s)",
                (kullanici_id, tutar),
            )
            return cursor.fetchone()
    finally:
        db.close()


def degerlendirme_yap(kullanici_id, menu_id, puan, yorum):
    db = yazma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                INSERT INTO degerlendirmeler (
                    kullanici_id,
                    menu_id,
                    puan,
                    yorum
                )
                SELECT %s, %s, %s, %s
                WHERE EXISTS (
                    SELECT 1
                    FROM rezervasyonlar
                    WHERE kullanici_id = %s
                      AND menu_id = %s
                      AND durum = 'kullanildi'
                )
            """, (
                kullanici_id,
                menu_id,
                puan,
                yorum,
                kullanici_id,
                menu_id,
            ))

            if cursor.rowcount == 0:
                raise ValueError(
                    "Yalnızca kullanılmış bir öğün değerlendirilebilir."
                )

            return {
                "mesaj": "Değerlendirmeniz kaydedildi.",
                "degerlendirme_id": cursor.lastrowid,
            }
    finally:
        db.close()


def yonetici_menu_ozeti():
    db = okuma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT
                    m.id AS menu_id,
                    DATE_FORMAT(m.tarih, '%Y-%m-%d') AS tarih,
                    yh.ad AS yemekhane,
                    o.ad AS ogun,
                    m.ogrenci_fiyati,
                    m.personel_fiyati,
                    d.kapasite,
                    d.rezerve,
                    d.bos_yer,
                    d.doluluk_yuzde,
                    p.oy_sayisi,
                    p.ortalama_puan
                FROM menuler AS m
                JOIN yemekhaneler AS yh
                    ON yh.id = m.yemekhane_id
                JOIN ogunler AS o
                    ON o.id = m.ogun_id
                JOIN v_menu_doluluk AS d
                    ON d.menu_id = m.id
                JOIN v_menu_puan AS p
                    ON p.menu_id = m.id
                WHERE m.tarih >= CURDATE()
                ORDER BY
                    m.tarih,
                    yh.ad,
                    o.sira
            """)
            return cursor.fetchall()
    finally:
        db.close()


def menu_kaydet(
    yemekhane_id,
    tarih,
    ogun_id,
    ogrenci_fiyati,
    personel_fiyati,
    yemek_idler,
    menu_id=None,
):
    try:
        yemek_idler = list(
            dict.fromkeys(int(yemek_id) for yemek_id in yemek_idler)
        )
    except (TypeError, ValueError):
        raise ValueError("Yemek listesinde geçersiz bir yemek numarası var.")

    if not yemek_idler:
        raise ValueError("Menüye en az bir yemek ekleyin.")

    db = yazma_baglantisi()

    try:
        db.begin()

        with db.cursor() as cursor:
            if menu_id is None:
                cursor.execute("""
                    INSERT INTO menuler (
                        yemekhane_id,
                        tarih,
                        ogun_id,
                        ogrenci_fiyati,
                        personel_fiyati
                    )
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    yemekhane_id,
                    tarih,
                    ogun_id,
                    ogrenci_fiyati,
                    personel_fiyati,
                ))
                kaydedilen_menu_id = cursor.lastrowid
            else:
                cursor.execute(
                    "SELECT id FROM menuler WHERE id = %s FOR UPDATE",
                    (menu_id,),
                )
                if cursor.fetchone() is None:
                    raise ValueError("Düzenlenecek menü bulunamadı.")

                cursor.execute("""
                    UPDATE menuler
                    SET
                        yemekhane_id = %s,
                        tarih = %s,
                        ogun_id = %s,
                        ogrenci_fiyati = %s,
                        personel_fiyati = %s
                    WHERE id = %s
                """, (
                    yemekhane_id,
                    tarih,
                    ogun_id,
                    ogrenci_fiyati,
                    personel_fiyati,
                    menu_id,
                ))
                kaydedilen_menu_id = menu_id

                cursor.execute(
                    "DELETE FROM menu_yemekleri WHERE menu_id = %s",
                    (kaydedilen_menu_id,),
                )

            cursor.executemany("""
                INSERT INTO menu_yemekleri (menu_id, yemek_id)
                VALUES (%s, %s)
            """, [
                (kaydedilen_menu_id, yemek_id)
                for yemek_id in yemek_idler
            ])

        db.commit()
        return {"menu_id": kaydedilen_menu_id}

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def alerjenleri_getir():
    db = okuma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT id, ad
                FROM alerjenler
                ORDER BY ad
            """)
            return cursor.fetchall()
    finally:
        db.close()


def kullanici_alerjen_idlerini_getir(kullanici_id):
    db = okuma_baglantisi()

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT alerjen_id
                FROM kullanici_alerjenleri
                WHERE kullanici_id = %s
                ORDER BY alerjen_id
            """, (kullanici_id,))
            return [
                kayit["alerjen_id"]
                for kayit in cursor.fetchall()
            ]
    finally:
        db.close()


def kullanici_alerjenlerini_kaydet(kullanici_id, alerjen_idler):
    try:
        alerjen_idler = list(
            dict.fromkeys(int(alerjen_id) for alerjen_id in alerjen_idler)
        )
    except (TypeError, ValueError):
        raise ValueError("Alerjen seçimleri geçerli numaralardan oluşmalı.")

    if any(alerjen_id < 1 for alerjen_id in alerjen_idler):
        raise ValueError("Alerjen numaraları pozitif olmalı.")

    db = yazma_baglantisi()

    try:
        db.begin()

        with db.cursor() as cursor:
            if alerjen_idler:
                yer_tutucular = ", ".join(
                    ["%s"] * len(alerjen_idler)
                )
                cursor.execute(
                    f"""
                    SELECT id
                    FROM alerjenler
                    WHERE id IN ({yer_tutucular})
                    """,
                    alerjen_idler,
                )
                bulunan_idler = {
                    kayit["id"]
                    for kayit in cursor.fetchall()
                }

                if bulunan_idler != set(alerjen_idler):
                    raise ValueError(
                        "Alerjen listesinde geçersiz bir numara var."
                    )

            cursor.execute("""
                DELETE FROM kullanici_alerjenleri
                WHERE kullanici_id = %s
            """, (kullanici_id,))

            if alerjen_idler:
                cursor.executemany("""
                    INSERT INTO kullanici_alerjenleri (
                        kullanici_id,
                        alerjen_id
                    )
                    VALUES (%s, %s)
                """, [
                    (kullanici_id, alerjen_id)
                    for alerjen_id in alerjen_idler
                ])

        db.commit()
        return alerjen_idler

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
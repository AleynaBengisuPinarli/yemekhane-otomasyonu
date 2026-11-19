-- Eski rezervasyonlar varsayılan olarak tam porsiyon (0) sayılır.
ALTER TABLE rezervasyonlar
    ADD COLUMN yarim_porsiyon TINYINT(1) NOT NULL DEFAULT 0
    AFTER ucret;

-- Kullanıcıların seçtiği alerjen tercihleri.
CREATE TABLE kullanici_alerjenleri (
    kullanici_id INT UNSIGNED NOT NULL,
    alerjen_id TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (kullanici_id, alerjen_id),
    CONSTRAINT fk_kullanici_alerjen_kullanici
        FOREIGN KEY (kullanici_id)
        REFERENCES kullanicilar (id)
        ON DELETE CASCADE,
    CONSTRAINT fk_kullanici_alerjen_alerjen
        FOREIGN KEY (alerjen_id)
        REFERENCES alerjenler (id)
        ON DELETE CASCADE
);

-- Yarım porsiyon seçimini desteklemek için rezervasyon yordamını güncelle.
DROP PROCEDURE IF EXISTS sp_rezervasyon_yap;

DELIMITER $$

CREATE PROCEDURE sp_rezervasyon_yap(
    IN p_kullanici_id INT UNSIGNED,
    IN p_menu_id INT UNSIGNED,
    IN p_yarim_porsiyon TINYINT UNSIGNED
)
BEGIN
    DECLARE v_rol VARCHAR(10) DEFAULT NULL;
    DECLARE v_aktif TINYINT DEFAULT NULL;
    DECLARE v_bakiye DECIMAL(10,2) DEFAULT NULL;
    DECLARE v_son DATETIME DEFAULT NULL;
    DECLARE v_kapasite INT DEFAULT NULL;
    DECLARE v_ogr_fiyat DECIMAL(8,2) DEFAULT NULL;
    DECLARE v_per_fiyat DECIMAL(8,2) DEFAULT NULL;
    DECLARE v_ucret DECIMAL(8,2) DEFAULT NULL;
    DECLARE v_ogun_id TINYINT UNSIGNED DEFAULT NULL;
    DECLARE v_dolu INT DEFAULT 0;
    DECLARE v_mevcut_id INT UNSIGNED DEFAULT NULL;
    DECLARE v_mevcut_durum VARCHAR(12) DEFAULT NULL;
    DECLARE v_rez_id INT UNSIGNED DEFAULT NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    DECLARE CONTINUE HANDLER FOR NOT FOUND
    BEGIN
    END;

    START TRANSACTION;

    IF p_yarim_porsiyon IS NULL OR p_yarim_porsiyon NOT IN (0, 1) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Porsiyon seçimi geçersiz.';
    END IF;

    SELECT rol, aktif, bakiye
      INTO v_rol, v_aktif, v_bakiye
      FROM kullanicilar
     WHERE id = p_kullanici_id
       FOR UPDATE;

    IF v_rol IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Kullanıcı bulunamadı.';
    END IF;

    IF v_aktif = 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Hesabınız pasif durumda.';
    END IF;

    SELECT
        TIMESTAMP(m.tarih, o.son_saat),
        yh.kapasite,
        m.ogrenci_fiyati,
        m.personel_fiyati,
        m.ogun_id
      INTO
        v_son,
        v_kapasite,
        v_ogr_fiyat,
        v_per_fiyat,
        v_ogun_id
      FROM menuler AS m
      JOIN ogunler AS o
        ON o.id = m.ogun_id
      JOIN yemekhaneler AS yh
        ON yh.id = m.yemekhane_id
     WHERE m.id = p_menu_id
       FOR UPDATE;

    IF v_son IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Menü bulunamadı.';
    END IF;

    IF NOW() >= v_son THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Bu menü için rezervasyon süresi doldu.';
    END IF;

    -- 1 numaralı öğün kahvaltıdır; kahvaltıda yarım porsiyon seçilemez.
    IF p_yarim_porsiyon = 1 AND v_ogun_id = 1 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Kahvaltıda yarım porsiyon seçilemez.';
    END IF;

    SELECT id, durum
      INTO v_mevcut_id, v_mevcut_durum
      FROM rezervasyonlar
     WHERE kullanici_id = p_kullanici_id
       AND menu_id = p_menu_id
       FOR UPDATE;

    IF v_mevcut_durum IN ('aktif', 'kullanildi') THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Bu menü için zaten rezervasyonunuz var.';
    END IF;

    SELECT COUNT(*)
      INTO v_dolu
      FROM rezervasyonlar
     WHERE menu_id = p_menu_id
       AND durum IN ('aktif', 'kullanildi');

    IF v_dolu >= v_kapasite THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Yemekhane kapasitesi dolu.';
    END IF;

    SET v_ucret = IF(
        v_rol = 'ogrenci',
        v_ogr_fiyat,
        v_per_fiyat
    );

    IF p_yarim_porsiyon = 1 THEN
        SET v_ucret = ROUND(v_ucret * 0.60, 2);
    END IF;

    IF v_bakiye < v_ucret THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Bakiye yetersiz.';
    END IF;

    IF v_mevcut_id IS NULL THEN
        INSERT INTO rezervasyonlar (
            kullanici_id,
            menu_id,
            durum,
            ucret,
            yarim_porsiyon
        )
        VALUES (
            p_kullanici_id,
            p_menu_id,
            'aktif',
            v_ucret,
            p_yarim_porsiyon
        );

        SET v_rez_id = LAST_INSERT_ID();
    ELSE
        -- Daha önce iptal edilmiş rezervasyon yeniden etkinleştirilir.
        UPDATE rezervasyonlar
           SET durum = 'aktif',
               ucret = v_ucret,
               yarim_porsiyon = p_yarim_porsiyon,
               olusturma = NOW(),
               iptal_zamani = NULL,
               kullanma_zamani = NULL
         WHERE id = v_mevcut_id;

        SET v_rez_id = v_mevcut_id;
    END IF;

    UPDATE kullanicilar
       SET bakiye = bakiye - v_ucret
     WHERE id = p_kullanici_id;

    INSERT INTO bakiye_hareketleri (
        kullanici_id,
        tur,
        tutar,
        rezervasyon_id,
        aciklama
    )
    VALUES (
        p_kullanici_id,
        'rezervasyon',
        -v_ucret,
        v_rez_id,
        'Rezervasyon'
    );

    COMMIT;

    SELECT
        v_rez_id AS rezervasyon_id,
        v_ucret AS ucret,
        v_bakiye - v_ucret AS bakiye,
        p_yarim_porsiyon AS yarim_porsiyon;
END$$

DELIMITER ;

GRANT EXECUTE ON PROCEDURE yemekhane.sp_rezervasyon_yap
TO 'yemekhane_app'@'%';
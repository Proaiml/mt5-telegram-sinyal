# -*- coding: utf-8 -*-
"""Gercek MT5 ve Telegram olmadan uctan uca testler."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mt5_telegram as mt  # noqa: E402


class SahteMT5:
    def __init__(self):
        self.pozlar = []
        self.emirler = []
        self.deals = []
        self.okunamaz = False

    def positions_get(self, **_k):
        return None if self.okunamaz else list(self.pozlar)

    def orders_get(self, **_k):
        return None if self.okunamaz else list(self.emirler)

    def history_deals_get(self, position=None, **_k):
        return [d for d in self.deals if d.position_id == position]

    def symbol_info(self, _s):
        return SimpleNamespace(digits=2, point=0.01)

    # yardimcilar
    def ac(self, bilet, tur, lot, fiyat, sl=0.0, tp=0.0, sembol="XAUUSD"):
        p = SimpleNamespace(ticket=bilet, symbol=sembol, type=tur, volume=lot,
                            price_open=fiyat, sl=sl, tp=tp)
        self.pozlar.append(p)
        return p

    def kapat(self, bilet, fiyat, neden, hacim=None):
        p = next(x for x in self.pozlar if x.ticket == bilet)
        hacim = p.volume if hacim is None else hacim
        self.deals.append(SimpleNamespace(position_id=bilet, entry=1, volume=hacim, price=fiyat,
                                          reason=neden, time_msc=int(time.time() * 1000) + len(self.deals)))
        if hacim >= p.volume - 1e-9:
            self.pozlar.remove(p)
        else:
            p.volume = round(p.volume - hacim, 2)


class Toplayici:
    def __init__(self):
        self.mesajlar = []

    def ekle(self, metin, yanit=None, gonderildi=None):
        mid = 1000 + len(self.mesajlar)
        self.mesajlar.append((mid, metin, yanit() if yanit else None))
        if gonderildi:
            gonderildi(mid)


def ayar(**ek):
    a = dict(mt.VARSAYILAN_AYARLAR)
    a.update({"birlestirme_ms": 400})
    a.update(ek)
    return a


class TakipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.durum = Path(self.tmp.name) / "durum.json"
        mt.LOG_DOSYASI = Path(self.tmp.name) / "log.txt"
        self.mt5 = SahteMT5()
        self.tg = Toplayici()
        self.t = 1_000_000.0

    def tearDown(self):
        self.tmp.cleanup()

    def tur(self, adim=0.1, **ayar_ek):
        if not hasattr(self, "tk"):
            self.tk = mt.Takipci(self.mt5, ayar(**ayar_ek), self.tg, self.durum)
            self.tk.yukle()
        self.t += adim
        self.tk.tur(self.t)

    def metinler(self):
        return [m for _, m, _ in self.tg.mesajlar]

    def test_baslangicta_acik_olanlar_gonderilmez(self):
        self.mt5.ac(1, 0, 0.1, 4489.31)
        self.tur()
        self.tur()
        self.assertEqual([], self.tg.mesajlar)

    def test_uc_pozisyon_tek_mesaj_ve_tam_yasam_dongusu(self):
        self.tur()  # bos baslangic
        for b, f, tp in ((11, 4283.00, 4276.0), (12, 4282.91, 4253.0), (13, 4282.91, 4253.0)):
            self.mt5.ac(b, 1, 0.59, f, sl=4286.0, tp=tp)
        self.tur()
        self.assertEqual([], self.tg.mesajlar)          # birlestirme penceresi
        self.tur(0.4)
        self.assertEqual(1, len(self.tg.mesajlar))
        mid, acilis, _ = self.tg.mesajlar[0]
        self.assertIn("🔴 SELL XAUUSD", acilis)
        self.assertIn("Giriş: 4283.00 / 4282.91", acilis)
        self.assertIn("SL: 4286.00", acilis)
        self.assertIn("TP: 4276.00 / 4253.00", acilis)
        self.assertNotIn("Lot", acilis)

        # stoplar ayni anda 4304'e
        for p in self.mt5.pozlar:
            p.sl = 4304.0
        self.tur()
        self.assertEqual(2, len(self.tg.mesajlar))
        _, guncel, yanit = self.tg.mesajlar[1]
        self.assertEqual("✏️ XAUUSD SELL güncellendi\nSL: 4286.00 → 4304.00", guncel)
        self.assertEqual(mid, yanit)

        # kismi kar
        self.mt5.kapat(12, 4270.10, 3, hacim=0.30)
        self.tur()
        self.assertIn("✂️ XAUUSD SELL kısmi kâr alındı\nFiyat: 4270.10 (+128 pip)", self.metinler()[-1])

        # TP1 bacagi kapandi
        self.mt5.kapat(11, 4276.00, mt.NEDEN_TP)
        self.tur(); self.tur()
        self.assertEqual("✅ XAUUSD SELL TP\nFiyat: 4276.00 (+70 pip)", self.metinler()[-1])
        self.assertEqual(mid, self.tg.mesajlar[-1][2])

        # kalan iki bacak ayni anda TP2
        self.mt5.kapat(12, 4253.00, mt.NEDEN_TP)
        self.mt5.kapat(13, 4253.00, mt.NEDEN_TP)
        self.tur(); self.tur()
        self.assertEqual("✅ XAUUSD SELL TP\nFiyat: 4253.00 (+299 pip)", self.metinler()[-1])
        self.assertEqual(5, len(self.tg.mesajlar))

    def test_okunamayan_ve_tek_turluk_bosluk_kapanis_sayilmaz(self):
        self.tur()
        self.mt5.ac(21, 0, 0.5, 4300.0, sl=4297.0, tp=4307.0)
        self.tur(); self.tur(0.4)
        self.assertEqual(1, len(self.tg.mesajlar))
        self.mt5.okunamaz = True
        for _ in range(20):
            self.tur()
        self.mt5.okunamaz = False
        yedek = self.mt5.pozlar
        self.mt5.pozlar = []
        self.tur()                      # tek tur yok gorundu
        self.mt5.pozlar = yedek
        self.tur(); self.tur()
        self.assertEqual(1, len(self.tg.mesajlar))

    def test_sl_ile_zarar_girişte_ve_karda_stop_ayri_yazilir(self):
        self.tur()
        self.mt5.ac(31, 0, 0.5, 4300.0, sl=4297.0)
        self.mt5.ac(32, 0, 0.5, 4400.0, sl=4400.0)
        self.mt5.ac(33, 0, 0.5, 4500.0, sl=4505.0, sembol="XAUUSD")
        self.tur(); self.tur(0.4)
        self.mt5.kapat(31, 4297.0, mt.NEDEN_SL)
        self.tur(); self.tur()
        self.assertIn("🛑 XAUUSD BUY SL\nFiyat: 4297.00 (-30 pip)", self.metinler())

    def test_gecmis_gec_gelse_de_ayni_anda_kapananlar_tek_mesaj(self):
        self.tur()
        self.mt5.ac(41, 1, 0.5, 4303.3, sl=4304.0)
        self.mt5.ac(42, 1, 0.5, 4303.3, sl=4304.0)
        self.tur(); self.tur(0.4)
        self.mt5.kapat(41, 4304.0, mt.NEDEN_SL)
        self.mt5.kapat(42, 4304.0, mt.NEDEN_SL)
        gecikmeli = [d for d in self.mt5.deals if d.position_id == 42]
        self.mt5.deals = [d for d in self.mt5.deals if d.position_id != 42]
        self.tur(); self.tur(); self.tur()
        self.assertEqual(1, len(self.tg.mesajlar))       # 42'nin gecmisi bekleniyor
        self.mt5.deals += gecikmeli
        self.tur()
        self.assertEqual(2, len(self.tg.mesajlar))
        self.assertEqual("🛑 XAUUSD SELL SL\nFiyat: 4304.00 (-7 pip)", self.metinler()[-1])

    def test_yeniden_baslatmada_tekrar_gondermez_yanit_zinciri_korunur(self):
        self.tur()
        self.mt5.ac(51, 0, 0.5, 4300.0, sl=4297.0, tp=4307.0)
        self.tur(); self.tur(0.4)
        acilis_id = self.tg.mesajlar[0][0]
        self.tk.kaydet()
        del self.tk                                   # program kapandi
        self.tur()                                    # yeni surec
        self.tur()
        self.assertEqual(1, len(self.tg.mesajlar))
        self.mt5.kapat(51, 4307.0, mt.NEDEN_TP)
        self.tur(); self.tur()
        self.assertEqual(acilis_id, self.tg.mesajlar[-1][2])

    def test_kapaliyken_kapanan_pozisyon_acilista_bildirilir(self):
        self.tur()
        self.mt5.ac(61, 0, 0.5, 4300.0, sl=4297.0, tp=4307.0)
        self.tur(); self.tur(0.4)
        self.tk.kaydet()
        del self.tk
        self.mt5.kapat(61, 4307.0, mt.NEDEN_TP)
        self.tur(); self.tur()
        self.assertEqual("✅ XAUUSD BUY TP\nFiyat: 4307.00 (+70 pip)", self.metinler()[-1])

    def test_bekleyen_emir_tetiklenir_ve_iptal(self):
        self.tur()
        self.mt5.emirler.append(SimpleNamespace(ticket=71, symbol="XAUUSD", type=2, volume_current=0.5,
                                                price_open=4290.0, sl=4287.0, tp=4297.0))
        self.tur()
        emir_id = self.tg.mesajlar[0][0]
        self.assertIn("⏳ BUY LIMIT XAUUSD\nFiyat: 4290.00", self.metinler()[0])
        self.mt5.emirler = []
        self.mt5.ac(71, 0, 0.5, 4290.0, sl=4287.0, tp=4297.0)
        self.tur(); self.tur(0.4)
        self.assertIn("⚡ Emir gerçekleşti", self.metinler()[-1])
        self.assertEqual(emir_id, self.tg.mesajlar[-1][2])
        # iptal edilen emir
        self.mt5.emirler.append(SimpleNamespace(ticket=72, symbol="XAUUSD", type=3, volume_current=0.5,
                                                price_open=4320.0, sl=0.0, tp=0.0))
        self.tur()
        self.mt5.emirler = []
        self.tur(); self.tur(3.5)
        self.assertIn("❌ SELL LIMIT XAUUSD 4320.00 iptal edildi", self.metinler()[-1])

    def test_ek_giris_yanit_olarak_gider_ve_filtre(self):
        self.tur(semboller=["XAU"])
        self.mt5.ac(81, 0, 0.5, 4300.0, sl=4297.0)
        self.tur(); self.tur(0.4)
        self.mt5.ac(82, 0, 0.5, 4299.0, sl=4297.0)
        self.mt5.ac(83, 0, 0.5, 1.1000, sembol="EURUSD")
        self.tur(2.0); self.tur(0.4)
        self.assertEqual(2, len(self.tg.mesajlar))
        self.assertIn("➕ Ek giriş BUY XAUUSD", self.metinler()[-1])
        self.assertEqual(self.tg.mesajlar[0][0], self.tg.mesajlar[-1][2])

    def test_lot_goster(self):
        self.tur(lot_goster=True, sembol_adlari={"XAUUSD": "GOLD"})
        self.mt5.ac(91, 0, 0.59, 4300.0)
        self.mt5.ac(92, 0, 0.59, 4300.0)
        self.tur(); self.tur(0.4)
        self.assertIn("🟢 BUY GOLD", self.metinler()[0])
        self.assertIn("Lot: 1.18", self.metinler()[0])


class TelegramKuyrukTest(unittest.TestCase):
    def test_hata_ve_429_sonrasi_sirayla_gonderir_yanit_id_gonderimde_cozulur(self):
        mt.LOG_DOSYASI = Path(tempfile.gettempdir()) / "mt5_tg_test_log.txt"
        cagri = []
        durumlar = [OSError("ag yok"), {"ok": False, "error_code": 429, "parameters": {"retry_after": 0}}]

        def gonder(_metod, veri):
            if durumlar:
                d = durumlar.pop(0)
                if isinstance(d, Exception):
                    raise d
                return d
            cagri.append((veri["text"], veri.get("reply_to_message_id")))
            return {"ok": True, "result": {"message_id": 500 + len(cagri)}}

        tg = mt.Telegram("t", "c", gonder=gonder)
        saklanan = {}
        tg.ekle("bir", None, lambda m: saklanan.setdefault("id", m))
        tg.ekle("iki", lambda: saklanan.get("id"))
        tg.baslat()
        son = time.time() + 10
        while len(cagri) < 2 and time.time() < son:
            time.sleep(0.05)
        tg.durdur(1.0)
        self.assertEqual([("bir", None), ("iki", "501")], cagri)


if __name__ == "__main__":
    unittest.main(verbosity=2)

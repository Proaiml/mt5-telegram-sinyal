# -*- coding: utf-8 -*-
"""MT5 -> Telegram anlik islem bildirimi.

MetaTrader 5 terminalindeki pozisyonlari ve bekleyen emirleri izler; her
acilis, SL/TP degisikligi, kismi kapanis ve kapanisi aninda Telegram kanalina
yazar. Terminalden yalnizca OKUR, hicbir emir gondermez (yatirimci/salt-okunur
sifresiyle calisir).

Kullanim:
    python mt5_telegram.py            # calistir
    python mt5_telegram.py --test     # kanala deneme mesaji gonder
    python mt5_telegram.py --kuru     # Telegram'a gondermeden ekrana yaz
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

KLASOR = Path(__file__).resolve().parent
AYAR_DOSYASI = KLASOR / "config.json"
DURUM_DOSYASI = KLASOR / "durum.json"
LOG_DOSYASI = KLASOR / "log.txt"

VARSAYILAN_AYARLAR: dict[str, Any] = {
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "semboller": [],
    "sembol_adlari": {},
    "lot_goster": False,
    "pip_goster": True,
    "bekleyen_emirler": True,
    "kapanislari_bildir": True,
    "sessiz_bildirim": False,
    "yoklama_ms": 50,
    "birlestirme_ms": 300,
    "ek_giris_saniye": 30,
    "mt5_yolu": "",
    "mt5_login": 0,
    "mt5_sifre": "",
    "mt5_sunucu": "",
}

POZ_BUY, POZ_SELL = 0, 1
EMIR_TURLERI = {2: "BUY LIMIT", 3: "SELL LIMIT", 4: "BUY STOP", 5: "SELL STOP",
                6: "BUY STOP LIMIT", 7: "SELL STOP LIMIT"}
DEAL_GIRIS, DEAL_CIKIS, DEAL_TERS, DEAL_CIKIS_BY = 0, 1, 2, 3
NEDEN_SL, NEDEN_TP, NEDEN_SO = 4, 5, 6
EPS = 1e-9


# ---------------------------------------------------------------- yardimcilar
_log_kilit = threading.Lock()


def log(metin: str) -> None:
    satir = f"{datetime.now():%Y-%m-%d %H:%M:%S} {metin}"
    with _log_kilit:
        try:
            print(satir, flush=True)
        except Exception:
            pass
        try:
            if LOG_DOSYASI.exists() and LOG_DOSYASI.stat().st_size > 5_000_000:
                LOG_DOSYASI.replace(LOG_DOSYASI.with_suffix(".eski.txt"))
            with LOG_DOSYASI.open("a", encoding="utf-8") as f:
                f.write(satir + "\n")
        except OSError:
            pass


def ayarlari_oku(yol: Path = AYAR_DOSYASI) -> dict[str, Any]:
    if not yol.exists():
        raise SystemExit(
            f"{yol.name} bulunamadi. config.example.json dosyasini config.json "
            "adiyla kopyalayip bot token ve kanal ID'sini yazin."
        )
    try:
        gelen = json.loads(yol.read_text(encoding="utf-8-sig"))
    except ValueError as ex:
        raise SystemExit(f"{yol.name} hatali (satir {getattr(ex, 'lineno', '?')}): {ex}")
    ayar = dict(VARSAYILAN_AYARLAR)
    ayar.update(gelen)
    for anahtar in gelen:
        if anahtar not in VARSAYILAN_AYARLAR:
            log(f"[UYARI] config.json: '{anahtar}' taninmiyor, yok sayildi.")
    return ayar


def atomik_yaz(yol: Path, veri: Any) -> None:
    gecici = yol.with_suffix(yol.suffix + ".tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
    for deneme in range(5):
        try:
            os.replace(gecici, yol)
            return
        except PermissionError:
            if deneme == 4:
                raise
            time.sleep(0.05)


def ayni(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= 1e-7


# ---------------------------------------------------------------- telegram
class Telegram:
    """Sirali gonderim kuyrugu. Ag hatasinda mesaj kaybolmaz, sira bozulmaz."""

    def __init__(self, token: str, chat_id: str, sessiz: bool = False,
                 gonder: Callable[[str, dict], dict] | None = None) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.sessiz = sessiz
        self._gonder = gonder or self._http
        self.kuyruk: queue.Queue = queue.Queue()
        self._dur = threading.Event()
        self._is = threading.Thread(target=self._calis, name="telegram", daemon=True)

    def baslat(self) -> None:
        self._is.start()

    def durdur(self, bekle: float = 5.0) -> None:
        son = time.time() + bekle
        while not self.kuyruk.empty() and time.time() < son:
            time.sleep(0.05)
        self._dur.set()

    def ekle(self, metin: str, yanit: Callable[[], int | None] | None = None,
             gonderildi: Callable[[int], None] | None = None) -> None:
        self.kuyruk.put((metin, yanit, gonderildi))

    def _http(self, metod: str, veri: dict) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{metod}"
        govde = urllib.parse.urlencode(veri).encode()
        istek = urllib.request.Request(url, data=govde, method="POST")
        try:
            with urllib.request.urlopen(istek, timeout=10) as yanit:
                return json.loads(yanit.read().decode())
        except urllib.error.HTTPError as ex:
            try:
                return json.loads(ex.read().decode())
            except Exception:
                return {"ok": False, "error_code": ex.code, "description": str(ex)}

    def mesaj_gonder(self, metin: str, yanit_id: int | None = None) -> dict:
        veri = {"chat_id": self.chat_id, "text": metin,
                "disable_web_page_preview": "true",
                "disable_notification": "true" if self.sessiz else "false"}
        if yanit_id:
            veri["reply_to_message_id"] = str(int(yanit_id))
            veri["allow_sending_without_reply"] = "true"
        return self._gonder("sendMessage", veri)

    def _calis(self) -> None:
        while not self._dur.is_set():
            try:
                metin, yanit, gonderildi = self.kuyruk.get(timeout=0.2)
            except queue.Empty:
                continue
            bekleme = 1.0
            while not self._dur.is_set():
                try:
                    sonuc = self.mesaj_gonder(metin, yanit() if yanit else None)
                except Exception as ex:  # ag yok, zaman asimi...
                    log(f"[TELEGRAM] gonderilemedi ({type(ex).__name__}: {ex}); {bekleme:.0f} sn sonra tekrar.")
                    time.sleep(bekleme)
                    bekleme = min(bekleme * 2, 30.0)
                    continue
                if sonuc.get("ok"):
                    if gonderildi:
                        try:
                            gonderildi(int(sonuc["result"]["message_id"]))
                        except Exception:
                            pass
                    break
                kod = int(sonuc.get("error_code") or 0)
                aciklama = str(sonuc.get("description") or "")
                if kod == 429:
                    sure = float((sonuc.get("parameters") or {}).get("retry_after") or 3)
                    time.sleep(sure + 0.2)
                    continue
                log(f"[TELEGRAM] hata {kod}: {aciklama}; {bekleme:.0f} sn sonra tekrar.")
                time.sleep(bekleme)
                bekleme = min(bekleme * 2, 30.0)
            self.kuyruk.task_done()


class KuruTelegram:
    """--kuru modu: mesajlari yalnizca ekrana yazar."""

    def __init__(self) -> None:
        self._no = 0

    def baslat(self) -> None:
        pass

    def durdur(self, bekle: float = 0.0) -> None:
        pass

    def ekle(self, metin, yanit=None, gonderildi=None) -> None:
        self._no += 1
        y = yanit() if yanit else None
        print(f"\n--- MESAJ #{self._no}{f' (yanit: #{y})' if y else ''} ---\n{metin}\n", flush=True)
        if gonderildi:
            gonderildi(self._no)


# ---------------------------------------------------------------- takip
@dataclass
class Poz:
    ticket: int
    sembol: str
    tur: int
    lot: float
    fiyat: float
    sl: float
    tp: float


def _poz(p: Any) -> Poz:
    return Poz(int(p.ticket), str(p.symbol), int(p.type), float(p.volume),
               float(p.price_open), float(p.sl or 0.0), float(p.tp or 0.0))


def _emir(o: Any) -> Poz:
    return Poz(int(o.ticket), str(o.symbol), int(o.type), float(o.volume_current),
               float(o.price_open), float(o.sl or 0.0), float(o.tp or 0.0))


class Takipci:
    def __init__(self, mt5: Any, ayar: dict[str, Any], telegram: Any,
                 durum_yolu: Path | None = DURUM_DOSYASI) -> None:
        self.mt5 = mt5
        self.ayar = ayar
        self.tg = telegram
        self.durum_yolu = durum_yolu
        self.kilit = threading.RLock()
        self.durum: dict[str, Any] = {"pozlar": {}, "emirler": {}, "gruplar": {}, "sayac": 0}
        self._yeni: dict[int, tuple[float, Poz]] = {}
        self._kayip: dict[int, int] = {}
        self._kapanis_bekleyen: dict[int, tuple[float, Poz, float]] = {}
        self._bilgi: dict[str, Any] = {}
        self._kapanis_bekleyen_grup: dict[int, str | None] = {}
        self._son_kayit = ""
        self._okunamayan = 0
        self._ilk = True

    # ---- durum
    def yukle(self) -> None:
        if self.durum_yolu and self.durum_yolu.exists():
            try:
                veri = json.loads(self.durum_yolu.read_text(encoding="utf-8"))
                if isinstance(veri, dict):
                    self.durum.update(veri)
            except (OSError, ValueError):
                log("[UYARI] durum.json okunamadi; bastan baslaniyor.")

    def kaydet(self) -> None:
        if not self.durum_yolu:
            return
        with self.kilit:
            metin = json.dumps(self.durum, ensure_ascii=False, sort_keys=True)
            if metin == self._son_kayit:
                return
            try:
                atomik_yaz(self.durum_yolu, self.durum)
                self._son_kayit = metin
            except OSError as ex:
                log(f"[UYARI] durum kaydedilemedi: {ex}")

    # ---- sembol yardimcilari
    def _sembol_bilgi(self, sembol: str) -> Any:
        b = self._bilgi.get(sembol)
        if b is None:
            b = self.mt5.symbol_info(sembol)
            if b is not None:
                self._bilgi[sembol] = b
        return b

    def _basamak(self, sembol: str) -> int:
        b = self._sembol_bilgi(sembol)
        return int(getattr(b, "digits", 2) or 2) if b is not None else 2

    def _pip(self, sembol: str) -> float:
        s = sembol.upper()
        if "XAU" in s or "GOLD" in s:
            return 0.1
        b = self._sembol_bilgi(sembol)
        nokta = float(getattr(b, "point", 0.0001) or 0.0001) if b is not None else 0.0001
        basamak = self._basamak(sembol)
        return nokta * 10 if basamak in (3, 5) else nokta

    def _f(self, sembol: str, fiyat: float) -> str:
        return f"{fiyat:.{self._basamak(sembol)}f}" if fiyat else "-"

    def _ad(self, sembol: str) -> str:
        return str((self.ayar.get("sembol_adlari") or {}).get(sembol, sembol))

    def _izleniyor(self, sembol: str) -> bool:
        liste = [str(x).upper() for x in (self.ayar.get("semboller") or [])]
        return not liste or any(x in sembol.upper() for x in liste)

    def _pip_metni(self, sembol: str, yon: int, giris: float, cikis: float) -> str:
        if not self.ayar.get("pip_goster", True) or not giris or not cikis:
            return ""
        pip = (cikis - giris) * (1 if yon == POZ_BUY else -1) / self._pip(sembol)
        return f" ({pip:+.0f} pip)"

    def _lot_metni(self, lot: float) -> str:
        return f"\nLot: {lot:.2f}" if self.ayar.get("lot_goster") else ""

    # ---- gruplar
    def _yeni_grup(self, sembol: str, yon: int) -> str:
        self.durum["sayac"] = int(self.durum.get("sayac", 0)) + 1
        gid = str(self.durum["sayac"])
        self.durum["gruplar"][gid] = {"sembol": sembol, "yon": yon, "mesaj": None,
                                      "biletler": [], "acilis": time.time()}
        return gid

    def _grup_mesaji(self, gid: str) -> Callable[[], int | None]:
        def bul() -> int | None:
            with self.kilit:
                return (self.durum["gruplar"].get(gid) or {}).get("mesaj")
        return bul

    def _mesaj_kaydedici(self, gid: str) -> Callable[[int], None]:
        def kaydet(mid: int) -> None:
            with self.kilit:
                g = self.durum["gruplar"].get(gid)
                if g is not None and g.get("mesaj") is None:
                    g["mesaj"] = mid
            self.kaydet()
        return kaydet

    def _grup_pozlari(self, gid: str) -> list[dict]:
        return [p for p in self.durum["pozlar"].values() if p.get("grup") == gid]

    # ---- MT5 okuma
    def _oku(self) -> tuple[list[Poz], list[Poz]] | None:
        pozlar = self.mt5.positions_get()
        emirler = self.mt5.orders_get() if self.ayar.get("bekleyen_emirler", True) else ()
        if pozlar is None or emirler is None:
            return None
        return ([_poz(p) for p in pozlar if self._izleniyor(str(p.symbol))],
                [_emir(o) for o in emirler if self._izleniyor(str(o.symbol))])

    def _son_cikis(self, bilet: int, onceki_toplam: float) -> tuple[float, float, int] | None:
        """Daha once bildirilmis cikislardan SONRAKI cikis deal'lari:
        (hacim, ortalama fiyat, son neden). Gecmis henuz yoksa None."""
        deals = self.mt5.history_deals_get(position=int(bilet))
        if not deals:
            return None
        cikis = sorted((d for d in deals if int(d.entry) in (DEAL_CIKIS, DEAL_TERS, DEAL_CIKIS_BY)),
                       key=lambda d: int(getattr(d, "time_msc", 0) or 0))
        toplam = 0.0
        yeni = []
        for d in cikis:
            if toplam + EPS >= onceki_toplam:
                yeni.append(d)
            toplam += float(d.volume)
        if not yeni:
            return None
        hacim = sum(float(d.volume) for d in yeni)
        fiyat = sum(float(d.price) * float(d.volume) for d in yeni) / hacim if hacim else 0.0
        return hacim, fiyat, int(yeni[-1].reason)

    # ---- ana tur
    def tur(self, an: float | None = None) -> None:
        an = time.time() if an is None else an
        okunan = self._oku()
        if okunan is None:
            # Okuyamadim != kapandi. Hicbir karar verilmez.
            self._okunamayan += 1
            return
        self._okunamayan = 0
        pozlar, emirler = okunan
        with self.kilit:
            if self._ilk:
                self._ilk = False
                self._baslangic(pozlar, emirler)
                return
            self._emirleri_isle(emirler, an)
            self._pozlari_isle(pozlar, an)
        self.kaydet()

    def _baslangic(self, pozlar: list[Poz], emirler: list[Poz]) -> None:
        """Acilista zaten acik olanlar tekrar sinyal diye gonderilmez."""
        kayitli = self.durum["pozlar"]
        gorulen = {str(p.ticket) for p in pozlar}
        for anahtar in list(kayitli):
            if anahtar not in gorulen:
                kayit = kayitli.pop(anahtar)
                if self.ayar.get("kapanislari_bildir", True):
                    self._kapanis_bekleyen[int(anahtar)] = (time.time(), Poz(**kayit["p"]),
                                                            float(kayit.get("cikan", 0.0)))
                    self._kapanis_bekleyen_grup[int(anahtar)] = kayit.get("grup")
        for p in pozlar:
            if str(p.ticket) not in kayitli:
                gid = self._yeni_grup(p.sembol, p.tur)
                self.durum["gruplar"][gid]["biletler"].append(p.ticket)
                kayitli[str(p.ticket)] = {"p": p.__dict__, "grup": gid, "cikan": 0.0}
            else:
                kayitli[str(p.ticket)]["p"] = p.__dict__
        self.durum["emirler"] = {str(o.ticket): {"p": o.__dict__, "mesaj": (self.durum["emirler"].get(str(o.ticket)) or {}).get("mesaj")}
                                 for o in emirler}
        log(f"[BASLANGIC] {len(pozlar)} acik pozisyon, {len(emirler)} bekleyen emir izleniyor "
            "(mevcut olanlar kanala tekrar gonderilmez).")
        self.kaydet()

    def _emirleri_isle(self, emirler: list[Poz], an: float) -> None:
        kayit = self.durum["emirler"]
        simdiki = {str(o.ticket): o for o in emirler}
        for anahtar, o in simdiki.items():
            eski = kayit.get(anahtar)
            if eski is None:
                kayit[anahtar] = {"p": o.__dict__, "mesaj": None}
                self._emir_mesaji(anahtar, o)
            else:
                e = Poz(**eski["p"])
                if not (ayni(e.fiyat, o.fiyat) and ayni(e.sl, o.sl) and ayni(e.tp, o.tp)):
                    self._emir_guncelle_mesaji(anahtar, e, o)
                eski["p"] = o.__dict__
        for anahtar in list(kayit):
            if anahtar not in simdiki:
                e = kayit.pop(anahtar)
                # Pozisyona donustuyse (tetiklendi) acilis mesaji o emre yanit verir.
                self.durum.setdefault("tetiklenen", {})[anahtar] = {"mesaj": e.get("mesaj"), "zaman": an,
                                                                   "p": e["p"]}

    def _emir_mesaji(self, anahtar: str, o: Poz) -> None:
        metin = (f"⏳ {EMIR_TURLERI.get(o.tur, 'EMIR')} {self._ad(o.sembol)}\n"
                 f"Fiyat: {self._f(o.sembol, o.fiyat)}\nSL: {self._f(o.sembol, o.sl)}\n"
                 f"TP: {self._f(o.sembol, o.tp)}{self._lot_metni(o.lot)}")

        def kaydet(mid: int) -> None:
            with self.kilit:
                e = self.durum["emirler"].get(anahtar) or self.durum.get("tetiklenen", {}).get(anahtar)
                if e is not None:
                    e["mesaj"] = mid
            self.kaydet()
        self.tg.ekle(metin, None, kaydet)

    def _emir_mesaj_bul(self, anahtar: str) -> Callable[[], int | None]:
        def bul() -> int | None:
            with self.kilit:
                e = self.durum["emirler"].get(anahtar) or self.durum.get("tetiklenen", {}).get(anahtar) or {}
                return e.get("mesaj")
        return bul

    def _emir_guncelle_mesaji(self, anahtar: str, e: Poz, o: Poz) -> None:
        satir = []
        if not ayni(e.fiyat, o.fiyat):
            satir.append(f"Fiyat: {self._f(o.sembol, e.fiyat)} → {self._f(o.sembol, o.fiyat)}")
        if not ayni(e.sl, o.sl):
            satir.append(f"SL: {self._f(o.sembol, e.sl)} → {self._f(o.sembol, o.sl)}")
        if not ayni(e.tp, o.tp):
            satir.append(f"TP: {self._f(o.sembol, e.tp)} → {self._f(o.sembol, o.tp)}")
        self.tg.ekle(f"✏️ {EMIR_TURLERI.get(o.tur, 'EMIR')} {self._ad(o.sembol)} güncellendi\n" + "\n".join(satir),
                     self._emir_mesaj_bul(anahtar))

    def _iptalleri_bildir(self, an: float) -> None:
        tet = self.durum.get("tetiklenen", {})
        for anahtar in list(tet):
            if an - float(tet[anahtar].get("zaman") or 0) > 3.0:
                e = tet.pop(anahtar)
                o = Poz(**e["p"])
                self.tg.ekle(f"❌ {EMIR_TURLERI.get(o.tur, 'EMIR')} {self._ad(o.sembol)} "
                             f"{self._f(o.sembol, o.fiyat)} iptal edildi",
                             (lambda m=e.get("mesaj"): m))

    def _pozlari_isle(self, pozlar: list[Poz], an: float) -> None:
        kayitli = self.durum["pozlar"]
        simdiki = {str(p.ticket): p for p in pozlar}

        # 1) yeni pozisyonlar: kisa birlestirme penceresinde toplanir
        for anahtar, p in simdiki.items():
            if anahtar not in kayitli and p.ticket not in self._yeni:
                self._yeni[p.ticket] = (an, p)
        for bilet in list(self._yeni):
            if str(bilet) not in simdiki:
                self._yeni.pop(bilet)       # birlestirme icinde kapandi: aninda sonuc
        bekleme = max(0.0, float(self.ayar.get("birlestirme_ms", 300))) / 1000.0
        if self._yeni and an - min(t for t, _ in self._yeni.values()) >= bekleme:
            hazir = [p for _, p in sorted(self._yeni.values(), key=lambda x: x[1].ticket)]
            self._yeni.clear()
            self._acilislari_bildir(hazir, simdiki, an)

        # 2) degisiklikler ve kapanislar
        degisenler: dict[str, list[tuple[Poz, Poz]]] = {}
        kismiler: dict[str, list[tuple[Poz, float, float]]] = {}
        for anahtar in list(kayitli):
            kayit = kayitli[anahtar]
            eski = Poz(**kayit["p"])
            yeni = simdiki.get(anahtar)
            if yeni is None:
                # Tek turluk bosluk yanlis kapanis sayilmasin: iki tur ust uste yok olmali.
                self._kayip[int(anahtar)] = self._kayip.get(int(anahtar), 0) + 1
                if self._kayip[int(anahtar)] >= 2:
                    self._kayip.pop(int(anahtar), None)
                    kayitli.pop(anahtar)
                    if self.ayar.get("kapanislari_bildir", True):
                        self._kapanis_bekleyen[int(anahtar)] = (an, eski, float(kayit.get("cikan", 0.0)))
                        self._kapanis_bekleyen_grup[int(anahtar)] = kayit.get("grup")
                continue
            self._kayip.pop(int(anahtar), None)
            gid = kayit.get("grup")
            if not (ayni(eski.sl, yeni.sl) and ayni(eski.tp, yeni.tp)):
                degisenler.setdefault(gid, []).append((eski, yeni))
            if yeni.lot < eski.lot - EPS:
                bilgi = self._son_cikis(yeni.ticket, float(kayit.get("cikan", 0.0)))
                kapanan = eski.lot - yeni.lot
                fiyat = bilgi[1] if bilgi else 0.0
                kayit["cikan"] = float(kayit.get("cikan", 0.0)) + kapanan
                kismiler.setdefault(gid, []).append((yeni, kapanan, fiyat))
            kayit["p"] = yeni.__dict__
        for gid, liste in degisenler.items():
            self._guncelleme_mesaji(gid, liste)
        for gid, liste in kismiler.items():
            self._kismi_mesaji(gid, liste)

        # 3) kapanislar (MT5 gecmisi gelene kadar kisa sure beklenir)
        self._kapanislari_bildir(an)
        self._iptalleri_bildir(an)

    def _acilislari_bildir(self, hazir: list[Poz], simdiki: dict[str, Poz], an: float) -> None:
        tet = self.durum.get("tetiklenen", {})
        kumeler: dict[tuple[str, int], list[Poz]] = {}
        for p in hazir:
            kumeler.setdefault((p.sembol, p.tur), []).append(p)
        ek_sure = float(self.ayar.get("ek_giris_saniye", 30))
        for (sembol, yon), liste in kumeler.items():
            ust = None
            for p in liste:
                if str(p.ticket) in tet:
                    ust = tet.pop(str(p.ticket)).get("mesaj")
            # Ayni yonde az once acilmis ve hala acik bir grup varsa "ek giris".
            eski_gid = None
            for gid, g in sorted(self.durum["gruplar"].items(), key=lambda x: -int(x[0])):
                if (g["sembol"] == sembol and g["yon"] == yon and an - float(g.get("acilis", 0)) <= ek_sure
                        and self._grup_pozlari(gid)):
                    eski_gid = gid
                    break
            gid = eski_gid or self._yeni_grup(sembol, yon)
            for p in liste:
                self.durum["gruplar"][gid]["biletler"].append(p.ticket)
                self.durum["pozlar"][str(p.ticket)] = {"p": p.__dict__, "grup": gid, "cikan": 0.0}
            self._acilis_mesaji(gid, liste, ek=eski_gid is not None, ust=ust)

    def _cift(self, sembol: str, degerler: list[float]) -> str:
        tekil: list[float] = []
        for d in degerler:
            if not any(ayni(d, x) for x in tekil):
                tekil.append(d)
        return " / ".join(self._f(sembol, d) for d in tekil) if tekil else "-"

    def _acilis_mesaji(self, gid: str, liste: list[Poz], ek: bool, ust: int | None) -> None:
        p0 = liste[0]
        yon = "BUY" if p0.tur == POZ_BUY else "SELL"
        simge = "🟢" if p0.tur == POZ_BUY else "🔴"
        lot = sum(p.lot for p in liste)
        girisler = self._cift(p0.sembol, [p.fiyat for p in liste])
        baslik = f"➕ Ek giriş {yon} {self._ad(p0.sembol)}" if ek else f"{simge} {yon} {self._ad(p0.sembol)}"
        if ust and not ek:
            baslik = f"⚡ Emir gerçekleşti\n{baslik}"
        metin = (f"{baslik}\nGiriş: {girisler}\n"
                 f"SL: {self._cift(p0.sembol, [p.sl for p in liste if p.sl])}\n"
                 f"TP: {self._cift(p0.sembol, [p.tp for p in liste if p.tp])}"
                 f"{self._lot_metni(lot)}")
        if ek:
            self.tg.ekle(metin, self._grup_mesaji(gid))
        else:
            self.tg.ekle(metin, (lambda m=ust: m) if ust else None, self._mesaj_kaydedici(gid))
        log(f"[ACILIS] grup {gid} {yon} {p0.sembol} {girisler} lot {lot:.2f}")

    def _baslik(self, gid: str) -> tuple[str, str, int]:
        g = self.durum["gruplar"].get(gid) or {}
        sembol = str(g.get("sembol", ""))
        yon = int(g.get("yon", 0))
        return sembol, f"{self._ad(sembol)} {'BUY' if yon == POZ_BUY else 'SELL'}", yon

    def _guncelleme_mesaji(self, gid: str, liste: list[tuple[Poz, Poz]]) -> None:
        sembol, ad, _ = self._baslik(gid)
        satir = []
        for alan, ad_ in (("sl", "SL"), ("tp", "TP")):
            ciftler = [(getattr(e, alan), getattr(y, alan)) for e, y in liste
                       if not ayni(getattr(e, alan), getattr(y, alan))]
            if not ciftler:
                continue
            eskiler = self._cift(sembol, [a for a, _ in ciftler])
            yeniler = self._cift(sembol, [b for _, b in ciftler])
            satir.append(f"{ad_}: {eskiler} → {yeniler}")
        if satir:
            self.tg.ekle(f"✏️ {ad} güncellendi\n" + "\n".join(satir), self._grup_mesaji(gid))
            log(f"[GUNCELLEME] grup {gid} " + " | ".join(satir))

    def _kismi_mesaji(self, gid: str, liste: list[tuple[Poz, float, float]]) -> None:
        sembol, ad, yon = self._baslik(gid)
        kapanan = sum(k for _, k, _ in liste)
        fiyatli = [(k, f) for _, k, f in liste if f]
        fiyat = sum(k * f for k, f in fiyatli) / sum(k for k, _ in fiyatli) if fiyatli else 0.0
        giris = sum(p.fiyat * k for p, k, _ in liste) / kapanan if kapanan else 0.0
        kalan = sum(float(p["p"]["lot"]) for p in self._grup_pozlari(gid))
        lot = f"\nKapanan: {kapanan:.2f} lot, kalan: {kalan:.2f} lot" if self.ayar.get("lot_goster") else ""
        self.tg.ekle(f"✂️ {ad} kısmi kâr alındı\nFiyat: {self._f(sembol, fiyat)}"
                     f"{self._pip_metni(sembol, yon, giris, fiyat)}{lot}", self._grup_mesaji(gid))
        log(f"[KISMI] grup {gid} {kapanan:.2f} lot @ {fiyat}")

    def _kapanislari_bildir(self, an: float) -> None:
        if not self._kapanis_bekleyen:
            return
        # Ayni gruptan ayni anda kapananlar tek mesajda toplanir: grubun
        # butun kapanis gecmisi gelene (ya da 3 sn dolana) kadar beklenir.
        aday: dict[str, list[tuple[int, Poz, tuple[float, float, int] | None, float]]] = {}
        for bilet, (t0, p, cikan) in list(self._kapanis_bekleyen.items()):
            gid = self._kapanis_bekleyen_grup.get(bilet) or self._bilet_grubu(bilet) or f"?{bilet}"
            aday.setdefault(gid, []).append((bilet, p, self._son_cikis(bilet, cikan), t0))
        for gid, liste in aday.items():
            if any(b is None and an - t0 < 3.0 for _, _, b, t0 in liste):
                continue                              # gecmis henuz tam gelmedi
            hazir: dict[str, list[tuple[Poz, float, float]]] = {}
            for bilet, p, bilgi, _ in liste:
                self._kapanis_bekleyen.pop(bilet, None)
                self._kapanis_bekleyen_grup.pop(bilet, None)
                if bilgi is None:
                    hazir.setdefault("EL", []).append((p, p.lot, 0.0))
                    continue
                _, fiyat, neden = bilgi
                etiket = ("TP" if neden == NEDEN_TP else "SL" if neden == NEDEN_SL
                          else "SO" if neden == NEDEN_SO else "EL")
                hazir.setdefault(etiket, []).append((p, p.lot, fiyat))
            for etiket, kume in hazir.items():
                self._kapanis_mesaji(gid, etiket, kume)

    def _bilet_grubu(self, bilet: int) -> str | None:
        for gid, g in self.durum["gruplar"].items():
            if int(bilet) in [int(x) for x in g.get("biletler", [])]:
                return gid
        return None

    def _kapanis_mesaji(self, gid: str, etiket: str, liste: list[tuple[Poz, float, float]]) -> None:
        if gid in self.durum["gruplar"]:
            sembol, ad, yon = self._baslik(gid)
        else:
            p0 = liste[0][0]
            sembol, yon = p0.sembol, p0.tur
            ad = f"{self._ad(sembol)} {'BUY' if yon == POZ_BUY else 'SELL'}"
        lot = sum(k for _, k, _ in liste)
        fiyatli = [(k, f) for _, k, f in liste if f]
        fiyat = sum(k * f for k, f in fiyatli) / sum(k for k, _ in fiyatli) if fiyatli else 0.0
        giris = sum(p.fiyat * k for p, k, _ in liste) / lot if lot else 0.0
        pip = (fiyat - giris) * (1 if yon == POZ_BUY else -1) / self._pip(sembol) if fiyat else 0.0
        if etiket == "TP":
            baslik = f"✅ {ad} TP"
        elif etiket == "SL":
            baslik = f"🛑 {ad} SL" if pip < -0.5 else (f"🔒 {ad} girişte kapandı" if pip <= 0.5 else f"🔒 {ad} stop kârda kapandı")
        elif etiket == "SO":
            baslik = f"⚠️ {ad} stop out"
        else:
            baslik = f"🔒 {ad} kapatıldı"
        kalan = [p for p in self._grup_pozlari(gid)] if gid in self.durum["gruplar"] else []
        lot_satir = ""
        if self.ayar.get("lot_goster"):
            lot_satir = f"\nKapanan: {lot:.2f} lot" + (f", kalan: {sum(float(p['p']['lot']) for p in kalan):.2f} lot" if kalan else "")
        self.tg.ekle(f"{baslik}\nFiyat: {self._f(sembol, fiyat)}{self._pip_metni(sembol, yon, giris, fiyat)}{lot_satir}",
                     self._grup_mesaji(gid) if gid in self.durum["gruplar"] else None)
        log(f"[KAPANIS] grup {gid} {etiket} {lot:.2f} lot @ {fiyat}")
        if gid in self.durum["gruplar"] and not kalan:
            g = self.durum["gruplar"][gid]
            g["kapandi"] = time.time()
            # Eski kapanmis gruplari durum dosyasinda biriktirme.
            kapali = sorted((k for k, v in self.durum["gruplar"].items() if v.get("kapandi")), key=int)
            for k in kapali[:-200]:
                self.durum["gruplar"].pop(k, None)


# ---------------------------------------------------------------- calistirma
def tek_ornek_kilidi(port: int = 47931) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError:
        s.close()
        raise SystemExit("Program zaten calisiyor (ikinci kopya acilmadi).")


def mt5_baglan(mt5: Any, ayar: dict[str, Any]) -> bool:
    arg: dict[str, Any] = {}
    if ayar.get("mt5_yolu"):
        arg["path"] = str(ayar["mt5_yolu"])
    if int(ayar.get("mt5_login") or 0):
        arg["login"] = int(ayar["mt5_login"])
        arg["password"] = str(ayar.get("mt5_sifre") or "")
        arg["server"] = str(ayar.get("mt5_sunucu") or "")
    ok = mt5.initialize(**arg)
    if not ok:
        log(f"[MT5] baglanilamadi: {mt5.last_error()}")
        return False
    hesap = mt5.account_info()
    log(f"[MT5] baglandi | hesap {getattr(hesap, 'login', '?')} | {getattr(hesap, 'server', '?')}")
    return True


def calistir(kuru: bool) -> int:
    ayar = ayarlari_oku()
    if not kuru and (not ayar["telegram_bot_token"] or not ayar["telegram_chat_id"]):
        raise SystemExit("config.json: telegram_bot_token ve telegram_chat_id doldurulmali.")
    import MetaTrader5 as mt5  # noqa: WPS433 - yalnizca Windows'ta gerekli
    kilit = tek_ornek_kilidi()
    tg: Any = KuruTelegram() if kuru else Telegram(
        ayar["telegram_bot_token"], ayar["telegram_chat_id"], bool(ayar.get("sessiz_bildirim")))
    tg.baslat()
    takip = Takipci(mt5, ayar, tg)
    takip.yukle()
    aralik = max(0.02, float(ayar.get("yoklama_ms", 50)) / 1000.0)
    bekleme = 1.0
    try:
        while not mt5_baglan(mt5, ayar):
            time.sleep(bekleme)
            bekleme = min(bekleme * 2, 30.0)
        log("[HAZIR] Islemler izleniyor. Durdurmak icin Ctrl+C.")
        son_uyari = 0.0
        while True:
            try:
                takip.tur()
            except Exception as ex:
                if time.time() - son_uyari > 60:
                    log(f"[HATA] {type(ex).__name__}: {ex}")
                    son_uyari = time.time()
            if takip._okunamayan >= int(5 / aralik):
                log("[MT5] terminalden veri alinamiyor; yeniden baglaniliyor...")
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                bekleme = 1.0
                while not mt5_baglan(mt5, ayar):
                    time.sleep(bekleme)
                    bekleme = min(bekleme * 2, 30.0)
                takip._okunamayan = 0
            time.sleep(aralik)
    except KeyboardInterrupt:
        log("[DUR] Kapatiliyor; bekleyen mesajlar gonderiliyor...")
        return 0
    finally:
        takip.kaydet()
        tg.durdur(5.0)
        try:
            mt5.shutdown()
        except Exception:
            pass
        kilit.close()


def test_mesaji() -> int:
    ayar = ayarlari_oku()
    tg = Telegram(ayar["telegram_bot_token"], ayar["telegram_chat_id"])
    sonuc = tg.mesaj_gonder("✅ Bağlantı testi başarılı. İşlemler bu kanala aktarılacak.")
    if sonuc.get("ok"):
        print("Test mesaji gonderildi.")
        return 0
    print(f"Gonderilemedi: {sonuc.get('error_code')} {sonuc.get('description')}")
    print("Kontrol: bot kanala yonetici olarak eklendi mi, chat_id dogru mu (-100... ile baslar)?")
    return 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="MT5 islemlerini Telegram kanalina aninda aktarir.")
    ap.add_argument("--test", action="store_true", help="kanala deneme mesaji gonder")
    ap.add_argument("--kuru", action="store_true", help="Telegram'a gondermeden ekrana yaz")
    arg = ap.parse_args()
    if arg.test:
        return test_mesaji()
    return calistir(arg.kuru)


if __name__ == "__main__":
    raise SystemExit(main())

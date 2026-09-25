# MT5 → Telegram Sinyal Aktarıcı

MetaTrader 5'te açtığınız her işlemi **anında** Telegram kanalınıza gönderir:

- 🟢/🔴 Yeni işlem (giriş, SL, TP)
- ✏️ SL / TP değişikliği
- ✂️ Kısmi kâr alma
- ✅ TP, 🛑 SL, 🔒 girişte / kârda kapanış
- ⏳ Bekleyen emirler (limit / stop), ⚡ gerçekleşme, ❌ iptal

Güncellemeler ve kapanışlar, ilgili işlemin ilk mesajına **yanıt** olarak gider; kanalda her işlem kendi zinciri içinde takip edilir. Aynı anda açılan birden fazla pozisyon tek mesajda birleşir.

Program terminalden **yalnızca okur**, hiçbir emir göndermez. İsterseniz MT5'e **yatırımcı (salt okunur) şifresiyle** giriş yaparak kullanabilirsiniz. Telefondan açılan işlemler de aynı hesapta olduğu için anında yakalanır.

## Gereksinimler

- Windows bilgisayar veya VPS
- MetaTrader 5 masaüstü terminali, hesabınıza giriş yapılmış ve açık
- Python 3.10 veya üzeri ([python.org](https://www.python.org/downloads/)). Kurulumda **"Add Python to PATH"** kutusunu işaretleyin.

## Kurulum (bir kez)

1. **Telegram botu oluşturun:** Telegram'da [@BotFather](https://t.me/BotFather) → `/newbot` → size verilen **token**'ı kopyalayın.
2. **Botu kanalınıza yönetici yapın:** Kanal → Yöneticiler → botu ekleyin, "Mesaj gönderme" yetkisi açık olsun.
3. **Kanal ID'sini bulun:** Açık kanalda `@kanaladi` yazabilirsiniz. Özel kanalda ID `-100` ile başlayan bir sayıdır. Kanala bir mesaj atıp [@RawDataBot](https://t.me/RawDataBot)'a iletirseniz ID'yi gösterir.
4. `config.example.json` dosyasını kopyalayıp adını **`config.json`** yapın ve doldurun:

```json
{
  "telegram_bot_token": "123456789:AA...",
  "telegram_chat_id": "-1001234567890"
}
```

5. `kur.bat` dosyasına çift tıklayın (gerekli paketi kurar).
6. `test.bat` dosyasına çift tıklayın. Kanalınıza bir deneme mesajı gelmelidir.

## Kullanım

MT5 terminali açıkken **`baslat.bat`** dosyasına çift tıklayın. Pencere açık kaldığı sürece işlemleriniz kanala aktarılır. Durdurmak için pencerede `Ctrl+C`.

- Program açıldığında zaten açık olan işlemler **tekrar gönderilmez**; yalnızca sonraki değişiklikler bildirilir.
- Program kapalıyken kapanan işlemler, tekrar açıldığında bildirilir.
- İnternet veya Telegram geçici olarak giderse mesajlar sırayla bekletilir, bağlantı gelince gönderilir.
- MT5 bağlantısı koparsa program kendiliğinden yeniden bağlanır.

Telegram'a göndermeden sadece ekranda görmek için: `python mt5_telegram.py --kuru`

## Örnek mesajlar

```
🔴 SELL XAUUSD
Giriş: 4283.00 / 4282.91
SL: 4286.00
TP: 4276.00 / 4253.00
```
```
✏️ XAUUSD SELL güncellendi
SL: 4286.00 → 4304.00
```
```
✅ XAUUSD SELL TP
Fiyat: 4253.00 (+299 pip)
```

## Ayarlar (`config.json`)

| Ayar | Varsayılan | Açıklama |
|---|---|---|
| `telegram_bot_token` | — | BotFather'dan alınan token |
| `telegram_chat_id` | — | Kanal ID'si veya `@kanaladi` |
| `semboller` | `[]` | Sadece bu sembolleri aktar, ör. `["XAU", "GOLD"]`. Boş = hepsi |
| `sembol_adlari` | `{}` | Kanalda görünecek ad, ör. `{"XAUUSD": "GOLD"}` |
| `lot_goster` | `false` | Mesajlarda lot miktarını göster |
| `pip_goster` | `true` | Kapanışlarda pip sonucunu göster (altında 1 pip = 0.10) |
| `bekleyen_emirler` | `true` | Limit/stop emirlerini de bildir |
| `kapanislari_bildir` | `true` | TP/SL/kapanış mesajları |
| `sessiz_bildirim` | `false` | Mesajlar sessiz (bildirim sesi olmadan) gitsin |
| `yoklama_ms` | `50` | Terminal kontrol aralığı (milisaniye) |
| `birlestirme_ms` | `300` | Bu süre içinde açılan pozisyonlar tek mesajda birleşir |
| `ek_giris_saniye` | `30` | Bu süre içinde aynı yönde açılan yeni pozisyon "Ek giriş" olarak yazılır |
| `mt5_yolu` | `""` | Birden fazla MT5 kuruluysa `terminal64.exe` tam yolu |
| `mt5_login` / `mt5_sifre` / `mt5_sunucu` | boş | Terminal otomatik giriş yapmıyorsa hesap bilgileri (yatırımcı şifresi yeterli) |

## Sorun giderme

- **Test mesajı gelmiyor:** Bot kanalda yönetici mi? Özel kanal ID'si `-100` ile mi başlıyor?
- **"MT5 bağlanılamadı":** Terminal açık ve hesaba giriş yapılmış olmalı. Birden fazla terminal varsa `mt5_yolu` ayarını doldurun.
- **"Program zaten çalışıyor":** Açık kalan diğer pencereyi kapatın.
- Kayıtlar `log.txt` dosyasına yazılır.

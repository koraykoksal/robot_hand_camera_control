-- ============================================================
-- Robot El TCP İstemci - Endüstriyel Lua (HMI/PLC)
-- ============================================================
-- 
-- Bu kod sizin mevcut Lua API'nize uygun şekilde yazılmıştır.
-- Plain text protokolü kullanır:
--   Gönder:   "BARDAK_AL"
--   Cevap:    "BARDAK_ALOK"  (başarılı)
--             "BARDAK_ALFAIL" (başarısız)
--             "BARDAK_ALOVERGRIP" (aşırı sıkma)
--
-- ============================================================

-- ============================================================
-- BAĞLANTI BİLGİLERİ
-- ============================================================
ip   = "192.168.1.40"   -- Sunucu IP (sizin bilgisayarınız)
port = 9090             -- Sunucu portu (run_tcp.py port 9090'da)

-- ============================================================
-- 1. BARDAK ALMA
-- ============================================================
tcp = SocketOpen(ip, port, "socket_0")

SocketSendString("BARDAK_AL", "socket_0", 0)
X = SocketReadString("socket_0", 0)
RegisterVar("string", "X")
WaitMs(500)

if X == "BARDAK_ALOK" then
    -- Bardak başarıyla kavrandı
    -- Buraya devam mantığı ekleyin (örn. başka pozisyona git)
else
    Pause(0)  -- veya hata yönetimi
end

-- ============================================================
-- 2. BARDAK BIRAKMA (örnek)
-- ============================================================
SocketSendString("BARDAK_BIRAK", "socket_0", 0)
Y = SocketReadString("socket_0", 0)
RegisterVar("string", "Y")
WaitMs(500)

if Y == "BARDAK_BIRAKOK" then
    -- Bardak başarıyla bırakıldı
else
    Pause(0)
end

-- ============================================================
-- 3. SIFIR POZİSYONU (örnek)
-- ============================================================
SocketSendString("ZERO", "socket_0", 0)
Z = SocketReadString("socket_0", 0)
RegisterVar("string", "Z")
WaitMs(500)

if Z == "ZEROOK" then
    -- Sıfır pozisyonuna ulaşıldı
end

-- Bağlantıyı kapat (eğer SocketClose API'niz varsa)
-- SocketClose("socket_0")

::LBL2::do end

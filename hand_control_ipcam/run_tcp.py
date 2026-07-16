# -*- coding: utf-8 -*-
"""
TCP Socket Sunucusu
====================

Robot el için basit TCP soket sunucusu.

Akış:
1. İstemci bağlanır → Sunucu "Connected" mesajı gönderir
2. İstemci komut gönderir (örn. {"cmd": "move", "pose": "BARDAK_AL"})
3. Sunucu komutu işler → cevap gönderir

Mesaj formatı: JSON Lines (her satır bir JSON, \\n ile sonlanır)

Kullanım:
    python run_tcp.py

Test için:
    python tcp_client_test.py
"""

import json
import socket
import threading
import time
from typing import Optional

import sys
import os

# ---------------------------------------------------------------------------
# sys.path setup (run_api.py ile aynı yöntem)
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _sub in ("core", "services", "cli", "config"):
    _path = os.path.join(_THIS_DIR, _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from worker_proxy import EcatWorkerProxy
from adapter_resolver import (
    enrich_adapters,
    load_adapter_config,
    find_saved_adapter_index,
)
from position_manager import PositionManager


# ---------------------------------------------------------------------------
# AYARLAR
# ---------------------------------------------------------------------------
TCP_HOST = "0.0.0.0"   # Tüm ağ arayüzlerinden bağlantı kabul et
TCP_PORT = 9090        # Dinleyeceği port
BUFFER_SIZE = 4096     # Bir seferde okunabilecek max byte


class TcpServer:
    """Basit JSON Lines TCP sunucusu."""

    def __init__(self, host: str = TCP_HOST, port: int = TCP_PORT):
        self.host = host
        self.port = port
        self._server_sock: Optional[socket.socket] = None
        self._running = False

        # Worker proxy (EtherCAT ile konuşmak için)
        self.proxy = EcatWorkerProxy(dof=6)
        self.position_manager = PositionManager(
            os.path.join(_THIS_DIR, "config", "positions.json")
        )

    # ----------------------------------------------------------------------
    # Sunucu başlatma / durdurma
    # ----------------------------------------------------------------------
    def start(self) -> bool:
        """Worker'ı başlat, EtherCAT bağlantısını kur ve socket'i aç."""
        print("=" * 60)
        print("  Robot El TCP Sunucusu")
        print("=" * 60)

        # 1. Worker process başlat
        print("\n🚀 Worker process başlatılıyor...")
        if not self.proxy.start():
            print("❌ Worker başlamadı")
            return False

        # 2. EtherCAT bağlantısı kur (otomatik adapter seçimi)
        if not self._auto_connect():
            print("❌ EtherCAT bağlantısı kurulamadı")
            self.proxy.stop()
            return False

        # 3. Motorları enable et
        print("⚙️ Motorlar enable ediliyor...")
        ok = self.proxy.enable_all()
        if not ok:
            print("⚠️ Enable başarısız (devam ediliyor)")
        else:
            print("✅ Motorlar hazır")

        # 4. TCP socket'i aç
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self.host, self.port))
            self._server_sock.listen(5)
            self._running = True
            print(f"\n✅ TCP sunucu dinliyor: {self.host}:{self.port}")
            print(f"   İstemci bağlanması bekleniyor...")
            print(f"   Çıkış için Ctrl+C")
            return True
        except Exception as e:
            print(f"❌ TCP socket hatası: {e}")
            return False

    def _auto_connect(self) -> bool:
        """Kayıtlı adapter varsa otomatik bağlan, yoksa kullanıcıya sor."""
        print("\n📡 Adapter taranıyor...")
        adapters_raw = self.proxy.scan_adapters()
        adapters = enrich_adapters(adapters_raw)

        if not adapters:
            print("❌ Hiç adapter bulunamadı")
            return False

        config_path = os.path.join(_THIS_DIR, "config", "ecat_config.json")
        saved = load_adapter_config(config_path)
        saved_idx = find_saved_adapter_index(adapters, saved)

        if saved_idx is not None:
            adapter_idx = saved_idx
            print(f"✅ Kayıtlı adapter: {adapters[adapter_idx].display_name}")
        else:
            print("\nAdapter seçin:")
            for a in adapters:
                print(f"  [{a.index}] {a.display_name}")
            try:
                adapter_idx = int(input("➤ Index: ").strip())
            except (ValueError, KeyboardInterrupt):
                return False

        print(f"🔌 Bağlanılıyor (adapter {adapter_idx})...")
        ok, msg = self.proxy.connect(adapter_idx)
        if ok:
            print(f"✅ Bağlandı")
            return True
        else:
            print(f"❌ {msg}")
            return False

    def serve_forever(self):
        """Ana döngü — istemci bağlantılarını kabul eder."""
        if not self._running:
            return

        try:
            while self._running:
                try:
                    self._server_sock.settimeout(1.0)
                    client_sock, addr = self._server_sock.accept()
                    print(f"\n🟢 Yeni istemci: {addr[0]}:{addr[1]}")

                    # Her istemci için ayrı thread
                    t = threading.Thread(
                        target=self._handle_client,
                        args=(client_sock, addr),
                        daemon=True,
                    )
                    t.start()
                except socket.timeout:
                    continue
                except OSError:
                    break  # Socket kapatıldı
        except KeyboardInterrupt:
            print("\n⏹️ Sunucu kapatılıyor...")

    def stop(self):
        """Sunucuyu temiz bir şekilde kapat."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

        print("🔻 Worker kapatılıyor...")
        try:
            self.proxy.disconnect()
        except Exception:
            pass
        self.proxy.stop(timeout_s=3.0)
        print("👋 Sunucu kapandı\n")

    # ----------------------------------------------------------------------
    # İstemci komut işleme
    # ----------------------------------------------------------------------
    def _handle_client(self, sock: socket.socket, addr: tuple):
        """Tek bir istemcinin yaşam döngüsü."""
        try:
            # 1. Hoş geldin mesajı GÖNDERİLMEZ — eski Lua/PLC istemcileri
            #    bu mesajı beklemediği için problem olur.
            #    İstemci komut gönderdiğinde anlayacağız ki bağlandı.
            print(f"   [{addr[0]}] Bağlantı bekleniyor — komut gelene kadar sessiz")

            # 2. Komut döngüsü
            buffer = ""
            while self._running:
                try:
                    sock.settimeout(60.0)  # 60 sn boşta kalırsa timeout
                    data = sock.recv(BUFFER_SIZE)
                    if not data:
                        # İstemci bağlantıyı kapattı
                        break
                    buffer += data.decode("utf-8", errors="replace")

                    # Önce newline ayırıcı var mı kontrol et (JSON Lines)
                    if "\n" in buffer:
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            self._process_message(sock, addr, line)
                    else:
                        # Newline yok - belki plain text komut (BARDAK_AL vs.)
                        # Buffer'ın tamamını bir komut olarak dene
                        cleaned = buffer.strip()
                        if cleaned and self._looks_like_plain_command(cleaned):
                            self._process_message(sock, addr, cleaned)
                            buffer = ""

                except socket.timeout:
                    # Boşta — devam et
                    continue
                except ConnectionResetError:
                    print(f"   [{addr[0]}] İstemci bağlantıyı sıfırladı")
                    break
                except Exception as e:
                    print(f"   [{addr[0]}] Okuma hatası: {e}")
                    break
        finally:
            try:
                sock.close()
            except Exception:
                pass
            print(f"🔴 İstemci ayrıldı: {addr[0]}:{addr[1]}")

    def _looks_like_plain_command(self, text: str) -> bool:
        """Plain text komut görünüyor mu? (BARDAK_AL, ZERO vb.)"""
        # JSON değilse ve sadece harf/rakam/altçizgi içeriyorsa plain command
        if text.startswith("{") or text.startswith("["):
            return False
        # Bir pozisyon adı olabilir mi kontrol et
        return all(c.isalnum() or c == "_" for c in text)

    def _process_message(self, sock: socket.socket, addr: tuple, line: str):
        """Bir komut mesajını parse edip işle. JSON veya plain text."""

        # 1. JSON mu, plain text mi kontrol et
        if line.startswith("{") or line.startswith("["):
            # JSON formatı
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._send_json(sock, {
                    "status": "error",
                    "message": "Geçersiz JSON formatı",
                })
                return
            self._handle_json_command(sock, addr, msg)
        else:
            # Plain text formatı (örn: "BARDAK_AL")
            print(f"   [{addr[0]}] Plain komut: {line}")
            self._handle_plain_command(sock, addr, line)

    def _handle_json_command(self, sock: socket.socket, addr: tuple, msg: dict):
        """JSON formatındaki komutu işle."""
        cmd = msg.get("cmd", "").lower()
        print(f"   [{addr[0]}] JSON komut: {msg}")

        if cmd == "ping":
            self._send_json(sock, {"status": "ok", "message": "pong"})

        elif cmd == "move":
            self._handle_move(sock, msg)

        elif cmd == "status":
            self._handle_status(sock)

        else:
            self._send_json(sock, {
                "status": "error",
                "message": f"Bilinmeyen komut: {cmd}",
                "supported_cmds": ["move", "status", "ping"],
            })

    def _handle_plain_command(self, sock: socket.socket, addr: tuple, line: str):
        """
        Plain text komut işle.

        Eski Lua HMI/PLC için uyumluluk modu:
            İstemci gönderir:  "BARDAK_AL"
            Sunucu cevap:      "BARDAK_ALOK"   (başarılı)
                       veya:   "BARDAK_ALFAIL" (başarısız)
        """
        pose_name = line.strip().upper()

        # Geçersiz/boş komutları sessizce atla (Lua'dan gelen "null" vb.)
        invalid_commands = {"NULL", "NIL", "NONE", "UNDEFINED", ""}
        if pose_name in invalid_commands:
            print(f"   [{addr[0]}] ⚠️ Geçersiz komut yok sayıldı: '{line}'")
            self._send_plain(sock, "ERROR_INVALID_COMMAND")
            return

        # ----------------------------------------------------------
        # BAĞLANTI TEST KOMUTLARI
        # ----------------------------------------------------------

        # PING: Basit canlılık testi (TCP socket çalışıyor mu?)
        if pose_name == "PING":
            self._send_plain(sock, "PONG")
            return

        # HEALTH: Detaylı sağlık kontrolü (EtherCAT + motor durumu)
        if pose_name == "HEALTH":
            self._handle_health_check(sock, addr)
            return

        # STATUS: EtherCAT bağlantı durumu
        if pose_name == "STATUS":
            self._handle_status_check(sock, addr)
            return

        # RECONNECT: Yeniden bağlanmayı tetikle
        if pose_name == "RECONNECT":
            self._handle_reconnect(sock, addr)
            return

        # Pozisyonu bul
        pose = self.position_manager.get(pose_name)
        if not pose:
            self._send_plain(sock, "ERROR_POSE_NOT_FOUND")
            print(f"   [{addr[0]}] Pozisyon bulunamadı: {pose_name}")
            return

        # Hareketi başlat
        vel = pose.vel[0] if isinstance(pose.vel, list) else pose.vel
        cur = pose.cur[0] if isinstance(pose.cur, list) else pose.cur

        print(f"   ▶️ Hareket başlıyor: {pose_name}")
        status_code, message = self.proxy.move_to_position(
            position=list(pose.pos),
            velocity=vel,
            max_current=cur,
            wait=True,
            timeout_s=5.0,
            pos_tolerance=pose.pos_tol,
        )

        # GRIP analizi
        grip_status, grip_detail = self.proxy.check_grip(
            target_position=list(pose.pos)
        )

        # Sonucu plain text olarak dön
        if grip_status == "GRIP_OK":
            response = f"{pose_name}OK"
        elif grip_status == "OVERGRIP":
            response = f"{pose_name}OVERGRIP"
        elif status_code == 200:
            response = f"{pose_name}OK"
        elif status_code == 408 and grip_status in ("UNCERTAIN", "NO_OBJECT"):
            response = f"{pose_name}FAIL"
        else:
            response = f"{pose_name}FAIL"

        self._send_plain(sock, response)
        print(f"   ✅ Plain cevap: {response}")

    def _handle_move(self, sock: socket.socket, msg: dict):
        """Pozisyon hareketi komutu."""
        pose_name = msg.get("pose")
        if not pose_name:
            self._send_json(sock, {
                "status": "error",
                "message": "'pose' alanı gerekli (örn: BARDAK_AL)",
            })
            return

        # Pozisyonu pozisyon yöneticisinden al
        pose = self.position_manager.get(pose_name)
        if not pose:
            available = self.position_manager.list_names()
            self._send_json(sock, {
                "status": "error",
                "message": f"Pozisyon bulunamadı: {pose_name}",
                "available": available,
            })
            return

        # Pose değerlerini çıkar
        vel = pose.vel[0] if isinstance(pose.vel, list) else pose.vel
        cur = pose.cur[0] if isinstance(pose.cur, list) else pose.cur

        # Hareketi başlat
        print(f"   ▶️ Hareket başlıyor: {pose_name}")
        status_code, message = self.proxy.move_to_position(
            position=list(pose.pos),
            velocity=vel,
            max_current=cur,
            wait=True,
            timeout_s=5.0,
            pos_tolerance=pose.pos_tol,
        )

        # GRIP analizi (otomatik)
        grip_status, grip_detail = self.proxy.check_grip(
            target_position=list(pose.pos)
        )

        # Sonucu yorumla (Smart 200 mantığı)
        if grip_status == "GRIP_OK":
            self._send_json(sock, {
                "status": "ok",
                "message": f"Object grasped ({pose_name})",
                "pose": pose_name,
                "grip_status": "GRIP_OK",
                "details": grip_detail,
            })
        elif grip_status == "OVERGRIP":
            self._send_json(sock, {
                "status": "warning",
                "message": "Overgrip detected",
                "pose": pose_name,
                "grip_status": "OVERGRIP",
                "details": grip_detail,
            })
        elif status_code == 200:
            self._send_json(sock, {
                "status": "ok",
                "message": f"Position reached ({pose_name})",
                "pose": pose_name,
                "grip_status": grip_status,
            })
        elif status_code == 408 and grip_status in ("UNCERTAIN", "NO_OBJECT"):
            # Gerçek timeout
            self._send_json(sock, {
                "status": "error",
                "message": f"Could not reach position: {message}",
                "pose": pose_name,
                "grip_status": grip_status,
            })
        else:
            self._send_json(sock, {
                "status": "error",
                "message": message,
                "pose": pose_name,
                "code": status_code,
            })

        print(f"   ✅ Cevap gönderildi: {pose_name}")

    def _handle_status(self, sock: socket.socket):
        """Anlık motor durumu."""
        st = self.proxy.get_status()
        if st:
            self._send_json(sock, {
                "status": "ok",
                "data": st,
            })
        else:
            self._send_json(sock, {
                "status": "error",
                "message": "Status alınamadı",
            })

    # ----------------------------------------------------------------------
    # Plain text bağlantı test handler'ları
    # ----------------------------------------------------------------------

    def _handle_health_check(self, sock: socket.socket, addr: tuple):
        """
        HEALTH komutu: Tüm sistemin sağlığı

        Cevap formatı:
            "HEALTH_OK"             - Her şey yolunda
            "HEALTH_DISCONNECTED"   - EtherCAT bağlantısı yok
            "HEALTH_RECOVERING"     - Recover yapılıyor
            "HEALTH_NO_MOTORS"      - Motorlar enable değil
            "HEALTH_ALARM"          - Motor alarmı var
        """
        try:
            # 1. Worker process canlı mı?
            if not self.proxy.is_alive():
                self._send_plain(sock, "HEALTH_DISCONNECTED")
                print(f"   [{addr[0]}] HEALTH check: Worker process down")
                return

            # 2. EtherCAT bağlantısı var mı?
            conn = self.proxy.get_connection_state()
            if not conn or not conn.get("connected", False):
                self._send_plain(sock, "HEALTH_DISCONNECTED")
                print(f"   [{addr[0]}] HEALTH check: EtherCAT not connected")
                return

            # 3. Motor durumu
            st = self.proxy.get_status()
            if not st or "motors" not in st:
                self._send_plain(sock, "HEALTH_NO_STATUS")
                print(f"   [{addr[0]}] HEALTH check: Status alınamadı")
                return

            # 4. Alarm var mı?
            for motor in st["motors"]:
                if motor.get("alarm", 0) != 0:
                    self._send_plain(sock, "HEALTH_ALARM")
                    print(f"   [{addr[0]}] HEALTH check: Motor alarm")
                    return

            # 5. Motorlar enable mi?
            if not st.get("enabled", False):
                self._send_plain(sock, "HEALTH_NO_MOTORS")
                print(f"   [{addr[0]}] HEALTH check: Motors disabled")
                return

            # Her şey yolunda
            self._send_plain(sock, "HEALTH_OK")
            print(f"   [{addr[0]}] HEALTH check: OK")

        except Exception as e:
            self._send_plain(sock, "HEALTH_ERROR")
            print(f"   [{addr[0]}] HEALTH check exception: {e}")

    def _handle_status_check(self, sock: socket.socket, addr: tuple):
        """
        STATUS komutu: EtherCAT bağlantı durumu

        Cevap:
            "CONNECTED"     - OP state'inde
            "DISCONNECTED"  - bağlantı yok
            "RECOVERING"    - recover yapılıyor
        """
        try:
            conn = self.proxy.get_connection_state()
            if conn and conn.get("connected", False):
                self._send_plain(sock, "CONNECTED")
            else:
                self._send_plain(sock, "DISCONNECTED")
        except Exception:
            self._send_plain(sock, "DISCONNECTED")

    def _handle_reconnect(self, sock: socket.socket, addr: tuple):
        """
        RECONNECT komutu: Bağlantıyı yenile

        Cevap:
            "RECONNECTOK"      - Yeniden bağlandı
            "RECONNECTFAIL"    - Bağlanamadı
        """
        print(f"   [{addr[0]}] RECONNECT istendi")
        try:
            # Önce mevcut bağlantıyı kapat
            self.proxy.disconnect()

            # Otomatik adapter ile tekrar bağlan
            if self._auto_connect():
                # Motorları enable et
                self.proxy.enable_all()
                self._send_plain(sock, "RECONNECTOK")
                print(f"   [{addr[0]}] RECONNECT başarılı")
            else:
                self._send_plain(sock, "RECONNECTFAIL")
                print(f"   [{addr[0]}] RECONNECT başarısız")
        except Exception as e:
            self._send_plain(sock, "RECONNECTFAIL")
            print(f"   [{addr[0]}] RECONNECT hata: {e}")

    # ----------------------------------------------------------------------
    # Yardımcı: gönderim
    # ----------------------------------------------------------------------
    def _send_json(self, sock: socket.socket, payload: dict):
        """JSON mesajı satır olarak gönder (her zaman \\n ile bitirir)."""
        try:
            data = json.dumps(payload, ensure_ascii=False) + "\n"
            sock.sendall(data.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"⚠️ JSON gönderim hatası: {e}")

    def _send_plain(self, sock: socket.socket, text: str):
        """
        Plain text mesaj gönder (eski Lua HMI/PLC için).
        Newline EKLEMEZ — eski sistemler genelde beklemez.
        """
        try:
            sock.sendall(text.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"⚠️ Plain gönderim hatası: {e}")

    # Geriye uyumluluk için (mevcut çağrılar için)
    def _send(self, sock: socket.socket, payload):
        """Eski API uyumluluğu — dict ise JSON, str ise plain text."""
        if isinstance(payload, dict):
            self._send_json(sock, payload)
        else:
            self._send_plain(sock, str(payload))


# ---------------------------------------------------------------------------
# Ana giriş
# ---------------------------------------------------------------------------
def main():
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)

    server = TcpServer()
    if not server.start():
        return 1

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    import multiprocessing as mp
    mp.freeze_support()
    sys.exit(main())

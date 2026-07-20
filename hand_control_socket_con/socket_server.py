# -*- coding: utf-8 -*-
"""
socket_server.py — Robot el TCP (socket) sunucusu.

Amaç:
  * Socket yayınını dinle, istemci bağlantılarını kabul et
  * Gelen TEXT isteği işle, robot ele uygula
  * "<KOMUT>OK" / "<KOMUT>NOK" cevabı dön
  * İSTEMCİDEN İSTEK GELMEDİĞİNDE UYGULAMA HATAYA DÜŞMEZ

Dayanıklılık (hataya düşmeme) tasarımı:
  - accept() zaman aşımlı -> boşta beklerken donmaz, Ctrl+C çalışır
  - recv() zaman aşımı -> istemci sessizse sadece döngü devam eder, bağlantı kapanmaz
  - Her istemci ayrı thread; bir istemcideki hata SUNUCUYU ETKİLEMEZ
  - Komut işlemedeki her hata yakalanır -> istemciye NOK döner, sunucu ayakta kalır
  - Robot bağlı değilse/koptuysa NOK döner (isteğe bağlı otomatik yeniden bağlanma)

Protokol (düz metin, satır sonu opsiyonel):
  PING              -> PONG
  HEALTH            -> HEALTHOK / HEALTHNOK
  STATUS            -> JSON durum
  POSES             -> tanımlı poz adları
  CONNECT           -> CONNECTOK / CONNECTNOK
  DISCONNECT        -> DISCONNECTOK
  RECONNECT         -> RECONNECTOK / RECONNECTNOK
  HOME              -> HOMEOK / HOMENOK
  <POZ_ADI>         -> <POZ_ADI>OK / <POZ_ADI>NOK      (örn BARDAK_AL)
  JSON: {"cmd":"move","pos":[...],"vel":20000,"cur":400}
"""
import json
import os
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple

import settings as config
from robot_interface import create_hand, JOINT_ORDER


# ---------------------------------------------------------------------------
# Poz (pozisyon) yönetimi
# ---------------------------------------------------------------------------
class PoseStore:
    def __init__(self, path: str):
        self.path = path
        self.poses: Dict[str, dict] = {}
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.poses = {k.upper(): v for k, v in data.get("poses", {}).items()}
            print(f"[Poz] {len(self.poses)} poz yüklendi: {', '.join(sorted(self.poses))}")
        except Exception as e:
            self.poses = {}
            print(f"[Poz] UYARI: {self.path} okunamadı ({e}). Poz komutları çalışmaz.")

    def get(self, name: str) -> Optional[dict]:
        return self.poses.get(name.upper())

    def names(self) -> List[str]:
        return sorted(self.poses)


# ---------------------------------------------------------------------------
# Sunucu
# ---------------------------------------------------------------------------
class RobotSocketServer:
    def __init__(self):
        self.poses = PoseStore(config.POSES_PATH)
        self.robot = create_hand()
        self.robot_connected = False
        self._robot_lock = threading.Lock()      # robot komutları seri çalışsın
        self._srv: Optional[socket.socket] = None
        self._running = False
        self._clients = 0
        self._clients_lock = threading.Lock()
        self.stats = {"requests": 0, "ok": 0, "nok": 0}

    # ---------------- robot ----------------
    def connect_robot(self) -> bool:
        with self._robot_lock:
            if self.robot_connected:
                return True
            try:
                if self.robot.connect():
                    self.robot_connected = True
                    info = self.robot.get_info()
                    print(f"[Robot] ✅ Bağlandı | {info['mode']} | "
                          f"firmware={info['firmware']} | enable={info.get('enabled', '?')}")
                    return True
                print("[Robot] ❌ Bağlanamadı.")
            except Exception as e:
                print(f"[Robot] ❌ Bağlantı hatası: {e}")
            self.robot_connected = False
            return False

    def disconnect_robot(self):
        with self._robot_lock:
            if not self.robot_connected:
                return
            try:
                self.robot.disconnect()
            except Exception as e:
                print(f"[Robot] Kapatma hatası: {e}")
            finally:
                self.robot_connected = False
                print("[Robot] Bağlantı kesildi.")

    def _ensure_robot(self) -> bool:
        """Komut öncesi robot hazır mı? Değilse (izin varsa) yeniden bağlan."""
        if self.robot_connected:
            return True
        if config.AUTO_RECONNECT:
            print("[Robot] Bağlı değil, yeniden bağlanılıyor...")
            return self.connect_robot()
        return False

    def _move_to(self, pos: List[int], vel: int, cur: int, tol: int) -> Tuple[bool, str]:
        """Motorları verilen pozisyona götür; (başarılı?, mesaj) döner."""
        if not self._ensure_robot():
            return False, "robot bagli degil"
        try:
            with self._robot_lock:
                lhp = getattr(self.robot, "lhp", None)
                if lhp is None:                      # MOCK robot
                    self.robot.last_positions = list(pos)
                    return True, "mock"
                for i, name in enumerate(JOINT_ORDER):
                    mid = config.MOTOR_MAP[name]
                    lhp.set_target_position(mid, int(pos[i]))
                    lhp.set_position_velocity(mid, int(vel))
                    lhp.set_max_current(mid, int(cur))
                lhp.move_motors(0)                   # hepsi birlikte

                # hedefe ulaşmayı bekle (zaman aşımlı)
                t0 = time.time()
                while time.time() - t0 < config.MOVE_TIMEOUT_S:
                    time.sleep(0.05)
                    try:
                        now = [int(lhp.get_now_position(config.MOTOR_MAP[n]))
                               for n in JOINT_ORDER]
                    except Exception:
                        break                        # okunamıyorsa beklemeden çık
                    if all(abs(now[i] - int(pos[i])) <= tol for i in range(len(pos))):
                        return True, "hedefe ulasildi"
                return True, "gonderildi (tolerans disi olabilir)"
        except Exception as e:
            return False, f"hata: {e}"

    # ---------------- komut işleme ----------------
    def handle_command(self, line: str, addr) -> str:
        """Tek bir metin komutunu işler ve cevabı döndürür. ASLA istisna fırlatmaz."""
        try:
            self.stats["requests"] += 1
            text = line.strip()
            if not text:
                return self._nok("EMPTY")

            if config.LOG_REQUESTS:
                print(f"   [{addr[0]}] >> {text}")

            # JSON komut
            if text.startswith("{") or text.startswith("["):
                return self._handle_json(text)

            cmd = text.upper()

            # geçersiz/boş istemci verileri (Lua/PLC 'null' gönderebiliyor)
            if cmd in {"NULL", "NIL", "NONE", "UNDEFINED"}:
                return self._nok("INVALID")

            if cmd == "PING":
                return self._ok_raw("PONG")
            if cmd == "POSES":
                return self._ok_raw(",".join(self.poses.names()) or "-")
            if cmd == "STATUS":
                return self._ok_raw(json.dumps(self.status(), ensure_ascii=False))
            if cmd == "HEALTH":
                return (self._ok("HEALTH") if self.robot_connected
                        else self._nok("HEALTH"))
            if cmd == "CONNECT":
                return self._ok("CONNECT") if self.connect_robot() else self._nok("CONNECT")
            if cmd == "DISCONNECT":
                self.disconnect_robot()
                return self._ok("DISCONNECT")
            if cmd == "RECONNECT":
                self.disconnect_robot()
                return self._ok("RECONNECT") if self.connect_robot() else self._nok("RECONNECT")
            if cmd == "HOME":
                if not self._ensure_robot():
                    return self._nok("HOME")
                with self._robot_lock:
                    self.robot.home()
                return self._ok("HOME")

            # Poz komutu
            pose = self.poses.get(cmd)
            if pose is None:
                print(f"   [{addr[0]}] ⚠️ Bilinmeyen komut/poz: {cmd}")
                return self._nok(cmd)

            vel = pose["vel"][0] if isinstance(pose.get("vel"), list) else pose.get("vel", 10000)
            cur = pose["cur"][0] if isinstance(pose.get("cur"), list) else pose.get("cur", 400)
            tol = int(pose.get("pos_tol", config.POS_TOLERANCE))
            ok, msg = self._move_to(pose["pos"], vel, cur, tol)
            print(f"   [{addr[0]}] {'✅' if ok else '❌'} {cmd}: {msg}")
            return self._ok(cmd) if ok else self._nok(cmd)

        except Exception as e:
            # Hiçbir komut hatası sunucuyu düşürmez
            print(f"   [{addr[0]}] ❌ Komut işleme hatası: {e}")
            return self._nok("ERROR")

    def _handle_json(self, text: str) -> str:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return json.dumps({"status": "nok", "message": "gecersiz JSON"})
        cmd = str(msg.get("cmd", "")).lower()
        if cmd == "ping":
            return json.dumps({"status": "ok", "message": "pong"})
        if cmd == "status":
            return json.dumps({"status": "ok", "data": self.status()}, ensure_ascii=False)
        if cmd == "poses":
            return json.dumps({"status": "ok", "poses": self.poses.names()})
        if cmd == "move":
            pos = msg.get("pos")
            if not isinstance(pos, list) or len(pos) != len(JOINT_ORDER):
                return json.dumps({"status": "nok",
                                   "message": f"pos {len(JOINT_ORDER)} elemanli olmali"})
            ok, m = self._move_to(pos, int(msg.get("vel", config.MOVE_VELOCITY)),
                                  int(msg.get("cur", config.MAX_CURRENT)),
                                  int(msg.get("tol", config.POS_TOLERANCE)))
            return json.dumps({"status": "ok" if ok else "nok", "message": m})
        if cmd == "pose":
            name = str(msg.get("name", "")).upper()
            p = self.poses.get(name)
            if not p:
                return json.dumps({"status": "nok", "message": "poz bulunamadi"})
            vel = p["vel"][0] if isinstance(p.get("vel"), list) else p.get("vel", 10000)
            cur = p["cur"][0] if isinstance(p.get("cur"), list) else p.get("cur", 400)
            ok, m = self._move_to(p["pos"], vel, cur, int(p.get("pos_tol", 300)))
            return json.dumps({"status": "ok" if ok else "nok", "message": m})
        return json.dumps({"status": "nok", "message": f"bilinmeyen cmd: {cmd}",
                           "supported": ["ping", "status", "poses", "move", "pose"]})

    def status(self) -> dict:
        info = {}
        try:
            if self.robot_connected:
                info = self.robot.get_info()
        except Exception:
            pass
        return {
            "robot_connected": self.robot_connected,
            "clients": self._clients,
            "requests": self.stats["requests"],
            "ok": self.stats["ok"], "nok": self.stats["nok"],
            "comm": config.COMM_MODE,
            "firmware": info.get("firmware", "-"),
            "enabled": info.get("enabled", "-"),
            "poses": self.poses.names(),
        }

    @staticmethod
    def _safe_echo(cmd: str) -> str:
        """Cevapta yankılanacak komut adını temizle (bozuk/binary veri yankılanmasın)."""
        clean = "".join(c for c in cmd if c.isalnum() or c in "_-")[:32]
        return clean.upper() if clean else "ERROR"

    def _ok(self, cmd: str) -> str:
        self.stats["ok"] += 1
        return f"{self._safe_echo(cmd)}{config.REPLY_OK}"

    def _nok(self, cmd: str) -> str:
        self.stats["nok"] += 1
        return f"{self._safe_echo(cmd)}{config.REPLY_NOK}"

    def _ok_raw(self, text: str) -> str:
        self.stats["ok"] += 1
        return text

    # ---------------- ağ ----------------
    def start(self) -> bool:
        try:
            self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._srv.bind((config.TCP_HOST, config.TCP_PORT))
            self._srv.listen(config.MAX_CLIENTS)
            self._srv.settimeout(config.ACCEPT_TIMEOUT_S)
            self._running = True
            print(f"[Socket] 🟢 Dinleniyor: {config.TCP_HOST}:{config.TCP_PORT} "
                  f"(en fazla {config.MAX_CLIENTS} istemci)")
            print("[Socket] İstemci beklenirken uygulama boşta kalır, hata vermez.")
            return True
        except Exception as e:
            print(f"[Socket] ❌ Başlatılamadı: {e}")
            return False

    def serve_forever(self):
        idle_note = time.time()
        while self._running:
            try:
                sock, addr = self._srv.accept()
            except socket.timeout:
                # İSTEK YOK — normal durum, hata değil. Sessizce beklemeye devam.
                if time.time() - idle_note > 60:
                    idle_note = time.time()
                    print(f"[Socket] boşta bekleniyor... (istemci: {self._clients}, "
                          f"istek: {self.stats['requests']})")
                continue
            except OSError:
                if self._running:
                    print("[Socket] accept hatası, devam ediliyor.")
                continue
            except Exception as e:
                print(f"[Socket] beklenmeyen accept hatası: {e}")
                time.sleep(0.2)
                continue

            with self._clients_lock:
                self._clients += 1
            t = threading.Thread(target=self._client_loop, args=(sock, addr), daemon=True)
            t.start()

    def _client_loop(self, sock: socket.socket, addr):
        print(f"🔵 İstemci bağlandı: {addr[0]}:{addr[1]}")
        try:
            if config.TCP_KEEPALIVE:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except Exception:
                    pass
            sock.settimeout(config.CLIENT_IDLE_TIMEOUT_S)
            buf = ""
            while self._running:
                try:
                    data = sock.recv(config.BUFFER_SIZE)
                    if not data:
                        break                       # istemci kapattı
                    buf += data.decode("utf-8", errors="replace")

                    if "\n" in buf:                 # satır tabanlı
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            if line.strip():
                                self._reply(sock, self.handle_command(line, addr))
                    else:                           # satır sonu yok: tek komut olabilir
                        text = buf.strip()
                        if text and (text.startswith("{") or
                                     all(c.isalnum() or c in "_-" for c in text)):
                            self._reply(sock, self.handle_command(text, addr))
                            buf = ""
                        elif len(buf) > config.BUFFER_SIZE * 4:
                            buf = ""                # çöp birikmesin

                except socket.timeout:
                    # İSTEK GELMEDİ — hata değil; bağlantı açık kalsın, devam.
                    continue
                except (ConnectionResetError, ConnectionAbortedError):
                    print(f"   [{addr[0]}] bağlantı sıfırlandı")
                    break
                except UnicodeDecodeError:
                    buf = ""
                    continue
                except Exception as e:
                    print(f"   [{addr[0]}] okuma hatası: {e}")
                    break
        except Exception as e:
            print(f"   [{addr[0]}] istemci hatası: {e}")
        finally:
            try:
                sock.close()
            except Exception:
                pass
            with self._clients_lock:
                self._clients -= 1
            print(f"🔴 İstemci ayrıldı: {addr[0]}:{addr[1]}")

    @staticmethod
    def _reply(sock: socket.socket, text: str):
        try:
            sock.sendall((text + "\n").encode("utf-8"))
        except Exception as e:
            print(f"   cevap gönderilemedi: {e}")

    _stopped = False

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._running = False
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass
        self.disconnect_robot()
        print("[Socket] Kapatıldı. "
              f"Toplam istek={self.stats['requests']} ok={self.stats['ok']} "
              f"nok={self.stats['nok']}")

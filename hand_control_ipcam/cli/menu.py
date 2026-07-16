# -*- coding: utf-8 -*-
"""
CLI Menu
========

Robotik el için interaktif komut satırı arayüzü.

Menü yapısı:
    [1] Adaptörleri tara
    [2] Bağlan
    [3] Motorları Enable et
    [4] Home yap
    ---
    [5] Pozisyonları listele
    [6] Pozisyona git
    [7] Mevcut açıları yeni pozisyon olarak kaydet
    [8] Pozisyon sil
    [9] Sıfır pozisyonuna git
    ---
    [10] Canlı durum izle
    [11] Alarmları temizle
    [12] Otomatik reset aç/kapa
    [13] Motorları durdur
    ---
    [0] Çıkış
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional

from hand_controller import (
    HandController,
    ROBOT_DOF,
    ZERO_POS,
    DEFAULT_VELOCITY,
    DEFAULT_MAX_CURRENT,
)
from position_manager import PositionManager, Pose
from adapter_resolver import (
    AdapterInfo,
    enrich_adapters,
    load_adapter_config,
    save_adapter_config,
    find_saved_adapter_index,
)
from input_helper import gil_friendly_input


# ANSI renk kodları (Windows 10+ ve Unix'te destekli)
class C:
    R = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    GRAY = "\033[90m"


def _enable_windows_ansi():
    """Windows'ta ANSI escape kodlarını etkinleştir."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


# ---------------------------------------------------------------------------
class HandCLI:
    """Komut satırı uygulaması."""

    def __init__(
        self,
        positions_file: str = "positions.json",
        adapter_config_file: str = "ecat_config.json",
    ):
        _enable_windows_ansi()
        self.hand = HandController(
            dof=ROBOT_DOF,
            auto_reset=True,
            monitor_interval_s=10.0,
            on_alarm=self._on_alarm,
            on_disconnected=self._on_disconnected,
        )
        self.positions = PositionManager(positions_file, autosave=True)
        self._adapter_config_file: str = adapter_config_file
        self._adapters: List[AdapterInfo] = []
        self._running = True
        self._verbose_logging: bool = False

    # ---- callbacks --------------------------------------------------------
    def _on_alarm(self, motor_id: int, alarm_code: int) -> None:
        # Monitor zaten print ediyor, burada ek işlem yapmak istersen:
        pass

    def _on_disconnected(self, reason: str) -> None:
        print(f"{C.RED}❌ Bağlantı kesildi: {reason}{C.R}")

    # ---- yardımcılar ------------------------------------------------------
    @staticmethod
    def _title() -> None:
        print()
        print(f"{C.CYAN}{'═' * 60}{C.R}")
        print(f"{C.BOLD}{C.CYAN}  🤖  Robotik El Kontrol Paneli  (6 DOF){C.R}")
        print(f"{C.CYAN}{'═' * 60}{C.R}")

    def _status_line(self) -> str:
        h = self.hand
        if not h.is_connected:
            return f"{C.RED}● Bağlı değil{C.R}"
        en = f"{C.GREEN}Enabled{C.R}" if h.is_enabled else f"{C.YELLOW}Disabled{C.R}"
        alarm_count = sum(1 for s in h.get_status() if s.alarm != 0)
        alarm_txt = (
            f"{C.RED}{alarm_count} alarm{C.R}"
            if alarm_count
            else f"{C.GREEN}0 alarm{C.R}"
        )
        auto = f"{C.GREEN}AÇIK{C.R}" if h.auto_reset else f"{C.GRAY}KAPALI{C.R}"
        return (
            f"{C.GREEN}● Bağlı{C.R} | {en} | {alarm_txt} | "
            f"auto-reset: {auto}"
        )

    @staticmethod
    def _prompt(msg: str) -> str:
        try:
            return gil_friendly_input(f"{C.BOLD}{msg}{C.R}").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    @staticmethod
    def _pause() -> None:
        try:
            gil_friendly_input(f"\n{C.DIM}↵ devam için Enter...{C.R}")
        except (EOFError, KeyboardInterrupt):
            pass

    @staticmethod
    def _parse_int_list(s: str, length: int) -> Optional[list[int]]:
        """'0,0,5000,5000,0,0' veya '0 0 5000 5000 0 0' parse eder."""
        if not s:
            return None
        s = s.replace(",", " ")
        parts = s.split()
        if len(parts) != length:
            return None
        try:
            return [int(p) for p in parts]
        except ValueError:
            return None

    # ==========================================================================
    # Menü
    # ==========================================================================
    def _print_menu(self) -> None:
        self._title()
        print(f"  Durum: {self._status_line()}")
        print(f"{C.CYAN}{'─' * 60}{C.R}")

        conn = self.hand.is_connected
        g = C.R if conn else C.DIM

        print(f"{C.BOLD}  Bağlantı{C.R}")
        print(f"   {C.CYAN}[1]{C.R} Adaptörleri tara")
        print(f"   {C.CYAN}[2]{C.R} Bağlan")
        print(f"   {g}[3]{C.R} Motorları Enable et")
        print(f"   {g}[4]{C.R} Home yap")

        print(f"\n{C.BOLD}  Pozisyonlar{C.R}")
        print(f"   {C.CYAN}[5]{C.R} Pozisyonları listele  ({self.positions.count()} adet)")
        print(f"   {g}[6]{C.R} Kayıtlı pozisyona git")
        print(f"   {g}[7]{C.R} Mevcut durumu yeni pozisyon olarak kaydet")
        print(f"   {C.CYAN}[8]{C.R} Pozisyon sil")
        print(f"   {g}[9]{C.R} Sıfır pozisyonuna git")
        print(f"   {g}[14]{C.R} Elle değer girerek hareket et (anlık test)")
        print(f"   {g}[15]{C.R} Elle değer girerek yeni pozisyon oluştur ve kaydet")

        print(f"\n{C.BOLD}  Durum & Hata{C.R}")
        print(f"   {g}[10]{C.R} Canlı durum izle (Ctrl+C ile çık)")
        print(f"   {g}[11]{C.R} Alarmları temizle")
        print(f"   {C.CYAN}[12]{C.R} Otomatik reset aç/kapa ({self._toggle_str(self.hand.auto_reset)})")
        print(f"   {g}[13]{C.R} Motorları durdur")

        print(f"\n{C.BOLD}  Hareket Öncesi Hazırlık{C.R}")
        print(f"   {C.CYAN}[16]{C.R} Auto-prep aç/kapa ({self._toggle_str(self.hand.prep_before_move)})")
        print(f"        {C.DIM}Açıksa: hareket öncesi alarm clear + enable check{C.R}")
        print(f"   {C.CYAN}[17]{C.R} Home-before-move aç/kapa ({self._toggle_str(self.hand.home_before_move)})")
        print(f"        {C.DIM}Açıksa: her hareketten ÖNCE home yapılır (yavaşlar){C.R}")
        print(f"   {C.CYAN}[18]{C.R} Verbose log aç/kapa ({self._toggle_str(self._verbose_logging)})")
        print(f"        {C.DIM}Açıksa: detaylı debug mesajları basılır{C.R}")

        print(f"\n{C.BOLD}  Testler{C.R}")
        print(f"   {g}[19]{C.R} Parmak loop testi (basparmak hariç 4 parmak)")
        print(f"   {g}[20]{C.R} Bye bye! hareketi (motor 3-6 sallama, 3 tekrar)")

        print(f"\n   {C.RED}[0]{C.R} Bağlantıyı kes ve çık")
        print(f"{C.CYAN}{'─' * 60}{C.R}")

    @staticmethod
    def _toggle_str(state: bool) -> str:
        return f"{C.GREEN}AÇIK{C.R}" if state else f"{C.GRAY}KAPALI{C.R}"

    @staticmethod
    def _print_move_result(reached: bool, reason: str, name: str) -> None:
        """Move sonucunu HTTP-style formatta yazdır."""
        if reached:
            print(f"{C.GREEN}200-OK Position reached ({name}){C.R}")
        elif reason == "timeout":
            print(f"{C.YELLOW}408-TIMEOUT Position not reached in 3s ({name}){C.R}")
        elif reason == "alarm":
            print(f"{C.RED}409-CONFLICT Motor alarm triggered ({name}){C.R}")
            print(f"{C.DIM}   → Mekanik takılma olabilir, akım/pozisyon kontrol edin{C.R}")
        elif reason == "alarm_locked":
            print(f"{C.RED}503-SERVICE-UNAVAILABLE Motor locked ({name}){C.R}")
            print(f"{C.DIM}   → [11] Alarmları temizle ile motor kilidini açın{C.R}")
        elif reason == "not_connected":
            print(f"{C.RED}503-SERVICE-UNAVAILABLE Not connected ({name}){C.R}")
        else:
            print(f"{C.YELLOW}500-ERROR Move failed: {reason} ({name}){C.R}")

    def run(self) -> None:
        """Ana döngü."""
        while self._running:
            self._print_menu()
            choice = self._prompt("➤ Seçim: ")
            print()

            try:
                self._dispatch(choice)
            except KeyboardInterrupt:
                print(f"\n{C.YELLOW}⚠️ İşlem iptal edildi{C.R}")
            except Exception as e:
                print(f"{C.RED}❌ Beklenmeyen hata: {e}{C.R}")

        # Güvenli kapanış
        if self.hand.is_connected:
            self.hand.disconnect()
        print(f"\n{C.GREEN}👋 Güle güle!{C.R}\n")

    def _dispatch(self, choice: str) -> None:
        mapping = {
            "1": self._cmd_scan,
            "2": self._cmd_connect,
            "3": self._cmd_enable,
            "4": self._cmd_home,
            "5": self._cmd_list_positions,
            "6": self._cmd_goto_position,
            "7": self._cmd_save_current,
            "8": self._cmd_delete_position,
            "9": self._cmd_goto_zero,
            "10": self._cmd_live_monitor,
            "11": self._cmd_clear_alarms,
            "12": self._cmd_toggle_auto_reset,
            "13": self._cmd_stop,
            "14": self._cmd_move_manual,
            "15": self._cmd_create_manual,
            "16": self._cmd_toggle_auto_prep,
            "17": self._cmd_toggle_home_before_move,
            "18": self._cmd_toggle_verbose,
            "19": self._cmd_finger_loop_test,
            "20": self._cmd_bye_bye,
            "0": self._cmd_quit,
        }
        fn = mapping.get(choice)
        if not fn:
            print(f"{C.YELLOW}⚠️ Geçersiz seçim: {choice}{C.R}")
            time.sleep(0.8)
            return
        fn()

    # ==========================================================================
    # Komut handler'ları
    # ==========================================================================
    def _cmd_scan(self) -> None:
        print(f"{C.CYAN}🔍 EtherCAT adaptörleri taranıyor...{C.R}")
        raw_names = self.hand.scan_interfaces()
        self._adapters = enrich_adapters(raw_names)

        if not self._adapters:
            print(f"{C.RED}❌ Adaptör bulunamadı. Npcap kurulu mu?{C.R}")
            print(f"{C.DIM}   → Npcap 'WinPcap API-compatible Mode' ile kurulmalı.{C.R}")
            self._pause()
            return

        # Kaydedilmiş tercih varsa bul ve işaretle
        saved = load_adapter_config(self._adapter_config_file)
        saved_idx = find_saved_adapter_index(self._adapters, saved)

        print(f"\n{C.GREEN}Bulunan adaptörler:{C.R}")
        for a in self._adapters:
            marker = ""
            if saved_idx is not None and a.index == saved_idx:
                marker = f"  {C.YELLOW}← son kullanılan{C.R}"
            print(f"  {C.CYAN}[{a.index}]{C.R} {a.display_name}{marker}")
            print(f"       {C.DIM}{a.raw_name}{C.R}")

        if saved_idx is None and saved:
            print(f"\n{C.DIM}ℹ️ Son kaydedilen adapter ({saved.get('display_name', '')}) "
                  f"şu anda bulunamıyor (USB çıkarılmış olabilir).{C.R}")

        self._pause()

    def _cmd_connect(self) -> None:
        if self.hand.is_connected:
            print(f"{C.YELLOW}⚠️ Zaten bağlı. Önce disconnect edin.{C.R}")
            self._pause()
            return

        # Adaptörleri hazırla
        if not self._adapters:
            print(f"{C.DIM}ℹ️ Önce adaptörler taranıyor...{C.R}")
            raw_names = self.hand.scan_interfaces()
            self._adapters = enrich_adapters(raw_names)
            if not self._adapters:
                print(f"{C.RED}❌ Adaptör bulunamadı. Npcap kurulu mu?{C.R}")
                self._pause()
                return

        # Kaydedilmiş tercihi dene
        saved = load_adapter_config(self._adapter_config_file)
        saved_idx = find_saved_adapter_index(self._adapters, saved)

        print(f"{C.BOLD}Adaptörler:{C.R}")
        for a in self._adapters:
            marker = (
                f"  {C.YELLOW}← son kullanılan{C.R}"
                if saved_idx is not None and a.index == saved_idx else ""
            )
            print(f"  [{a.index}] {a.display_name}{marker}")

        # Prompt
        if saved_idx is not None:
            default_txt = f" [{saved_idx}]"
        else:
            default_txt = ""
        s = self._prompt(f"➤ Index{default_txt}: ")

        # Boş girişte kaydedilmiş tercihi kullan
        if not s and saved_idx is not None:
            idx = saved_idx
        else:
            try:
                idx = int(s)
            except ValueError:
                print(f"{C.RED}❌ Geçersiz index.{C.R}")
                self._pause()
                return

        if idx < 0 or idx >= len(self._adapters):
            print(f"{C.RED}❌ Index aralık dışında (0..{len(self._adapters)-1}).{C.R}")
            self._pause()
            return

        chosen = self._adapters[idx]
        print(f"{C.CYAN}🔌 Bağlanılıyor: {chosen.display_name}{C.R}")
        print(f"{C.DIM}   ({chosen.raw_name}){C.R}")

        ok = self.hand.connect(idx)
        if ok:
            print(f"{C.GREEN}✅ Bağlantı başarılı!{C.R}")
            # Verbose tercihini master'a da yay
            try:
                if self.hand._master:
                    self.hand._master.verbose_logging = self._verbose_logging
            except Exception:
                pass
            # Başarılı bağlantıyı kaydet
            if save_adapter_config(
                self._adapter_config_file,
                chosen.display_name,
                chosen.raw_name,
            ):
                print(f"{C.DIM}   ↳ Adapter tercihi kaydedildi: {self._adapter_config_file}{C.R}")
        else:
            print(f"{C.RED}❌ Bağlantı başarısız.{C.R}")
            print(f"{C.DIM}   → Kablo takılı mı? Güç açık mı? Adaptör doğru mu?{C.R}")
        self._pause()

    def _cmd_enable(self) -> None:
        if not self._require_connected():
            return

        s = self._prompt(f"Hız [{DEFAULT_VELOCITY}]: ") or str(DEFAULT_VELOCITY)
        c = self._prompt(f"Max akım [{DEFAULT_MAX_CURRENT}]: ") or str(DEFAULT_MAX_CURRENT)
        try:
            vel = int(s)
            cur = int(c)
        except ValueError:
            print(f"{C.RED}❌ Geçersiz sayı.{C.R}")
            self._pause()
            return

        results = self.hand.enable_all(velocity=vel, max_current=cur)
        for motor_id, ok, err in results:
            mark = f"{C.GREEN}✓{C.R}" if ok else f"{C.RED}✗{C.R}"
            extra = f" ({err})" if err else ""
            print(f"  {mark} Motor {motor_id}{extra}")
        self._pause()

    def _cmd_home(self) -> None:
        if not self._require_connected():
            return
        confirm = self._prompt(f"{C.YELLOW}⚠️ Home sekansı başlatılacak. Emin misiniz? (e/h): {C.R}")
        if confirm.lower() not in ("e", "y", "yes", "evet"):
            print(f"{C.DIM}İptal edildi.{C.R}")
            self._pause()
            return

        print(f"{C.CYAN}🏠 Home komutu gönderiliyor...{C.R}")
        self.hand.home_all()

        # Home sonrası motorlar 0'a dönmeli - bunu bekle
        reached, reason = self.hand.wait_reached(
            timeout_s=5.0,
            target_pos=ZERO_POS,
            pos_tolerance=500,
        )
        self._print_move_result(reached, reason, "HOME")
        self._pause()

    def _cmd_list_positions(self) -> None:
        names = self.positions.list_names()
        if not names:
            print(f"{C.DIM}(Kayıtlı pozisyon yok){C.R}")
            print(f"{C.DIM}Yeni pozisyon oluşturmak için:{C.R}")
            print(f"{C.DIM}  [15] Elle değer girerek yeni pozisyon oluştur{C.R}")
            print(f"{C.DIM}  [7]  Mevcut el duruşunu pozisyon olarak kaydet{C.R}")
        else:
            print(f"{C.BOLD}{'İsim':<20} {'Pozisyon':<40} {'Açıklama'}{C.R}")
            print(f"{C.DIM}{'-' * 76}{C.R}")
            for n in names:
                p = self.positions.get(n)
                if p:
                    pos_str = ",".join(str(x) for x in p.pos)
                    desc = (p.description or "")[:25]
                    print(f"  {n:<18} [{pos_str:<37}] {desc}")
        self._pause()

    def _cmd_goto_position(self) -> None:
        if not self._require_connected():
            return
        names = self.positions.list_names()
        if not names:
            print(f"{C.DIM}(Kayıtlı pozisyon yok){C.R}")
            self._pause()
            return

        for i, n in enumerate(names):
            p = self.positions.get(n)
            pos_str = ",".join(str(x) for x in p.pos) if p else ""
            print(f"  {C.CYAN}[{i}]{C.R} {n:<16} [{pos_str}]")

        s = self._prompt("➤ İsim veya index: ")
        pose: Optional[Pose] = None
        try:
            idx = int(s)
            if 0 <= idx < len(names):
                pose = self.positions.get(names[idx])
        except ValueError:
            pose = self.positions.get(s)

        if not pose:
            print(f"{C.RED}❌ Pozisyon bulunamadı.{C.R}")
            self._pause()
            return

        print(f"{C.CYAN}→ {pose.name}  {pose.pos}{C.R}")
        ok = self.hand.move_to_position(pose.pos, velocity=pose.vel, max_current=pose.cur)
        if not ok:
            print(f"{C.RED}500-ERROR Move command failed ({pose.name}){C.R}")
            self._pause()
            return

        # Ulaşmayı bekle - pos_tol ile
        reached, reason = self.hand.wait_reached(
            timeout_s=3.0,
            target_pos=pose.pos,
            pos_tolerance=pose.pos_tol,
        )
        self._print_move_result(reached, reason, pose.name)
        self._pause()

    def _cmd_save_current(self) -> None:
        if not self._require_connected():
            return
        status = self.hand.get_status()
        current_pos = [s.position for s in status]
        print(f"{C.CYAN}Mevcut pozisyon: {current_pos}{C.R}")
        name = self._prompt("İsim: ")
        if not name:
            print(f"{C.DIM}İptal edildi.{C.R}")
            self._pause()
            return

        desc = self._prompt("Açıklama (opsiyonel): ")
        pose = self.positions.add_or_update(name, current_pos, description=desc)
        print(f"{C.GREEN}✅ '{pose.name}' kaydedildi.{C.R}")
        self._pause()

    def _cmd_delete_position(self) -> None:
        names = self.positions.list_names()
        if not names:
            print(f"{C.DIM}(Kayıtlı pozisyon yok){C.R}")
            self._pause()
            return
        for i, n in enumerate(names):
            print(f"  {C.CYAN}[{i}]{C.R} {n}")
        s = self._prompt("Silinecek isim/index: ")
        target = None
        try:
            idx = int(s)
            if 0 <= idx < len(names):
                target = names[idx]
        except ValueError:
            target = s

        if target and self.positions.remove(target):
            print(f"{C.GREEN}✅ Silindi: {target}{C.R}")
        else:
            print(f"{C.RED}❌ Silinemedi.{C.R}")
        self._pause()

    def _cmd_goto_zero(self) -> None:
        if not self._require_connected():
            return
        print(f"{C.CYAN}→ Sıfır pozisyonuna gidiliyor...{C.R}")
        self.hand.move_to_zero()
        reached, reason = self.hand.wait_reached(
            timeout_s=5.0,
            target_pos=ZERO_POS,
            pos_tolerance=500,
        )
        self._print_move_result(reached, reason, "ZERO")
        self._pause()

    def _cmd_live_monitor(self) -> None:
        if not self._require_connected():
            return
        print(f"{C.CYAN}📊 Canlı izleme. Çıkmak için Ctrl+C{C.R}\n")
        try:
            while True:
                status = self.hand.get_status()
                # Ekranı temizlemek yerine satır üzerine yaz
                lines = []
                header = (
                    f"{C.BOLD}{'Motor':<6}{'Pos':>8}{'Angle':>9}{'Cur':>8}"
                    f"{'Alarm':>8}{'En':>5}{'Reached':>9}{C.R}"
                )
                lines.append(header)
                lines.append(f"{C.DIM}{'-' * 53}{C.R}")
                for s in status:
                    alarm_col = f"{C.RED}{s.alarm}{C.R}" if s.alarm else f"{C.GREEN}0{C.R}"
                    en_col = f"{C.GREEN}✓{C.R}" if s.enabled else f"{C.GRAY}✗{C.R}"
                    reach_col = f"{C.GREEN}✓{C.R}" if s.reached else f"{C.GRAY}…{C.R}"
                    lines.append(
                        f"{s.motor_id:<6}{s.position:>8}{s.angle:>9.2f}"
                        f"{s.current:>8}{alarm_col:>16}{en_col:>12}{reach_col:>16}"
                    )
                # Ekranı yeniden çiz
                print("\033[2J\033[H", end="")  # clear + home
                print(f"{C.BOLD}{C.CYAN}📊 Canlı Durum (Ctrl+C ile çık){C.R}\n")
                for ln in lines:
                    print(ln)
                time.sleep(0.2)
        except KeyboardInterrupt:
            print(f"\n{C.DIM}İzleme durduruldu.{C.R}")

    def _cmd_clear_alarms(self) -> None:
        if not self._require_connected():
            return
        s = self._prompt("Motor ID (0 = hepsi): ") or "0"
        try:
            mid = int(s)
        except ValueError:
            print(f"{C.RED}❌ Geçersiz ID.{C.R}")
            self._pause()
            return
        self.hand.clear_alarms(motor_id=mid)
        self._pause()

    def _cmd_toggle_auto_reset(self) -> None:
        self.hand.auto_reset = not self.hand.auto_reset
        state = "AÇIK" if self.hand.auto_reset else "KAPALI"
        print(f"{C.GREEN}Otomatik reset: {state}{C.R}")
        time.sleep(0.8)

    def _cmd_toggle_auto_prep(self) -> None:
        self.hand.prep_before_move = not self.hand.prep_before_move
        state = "AÇIK" if self.hand.prep_before_move else "KAPALI"
        print(f"{C.GREEN}Auto-prep (alarm clear + enable check): {state}{C.R}")
        time.sleep(0.8)

    def _cmd_toggle_home_before_move(self) -> None:
        self.hand.home_before_move = not self.hand.home_before_move
        state = "AÇIK" if self.hand.home_before_move else "KAPALI"
        print(f"{C.GREEN}Home-before-move: {state}{C.R}")
        if self.hand.home_before_move:
            print(f"{C.DIM}Her hareketten önce home yapılacak (yavaşlar ama motoru sıfırlar){C.R}")
        time.sleep(1.0)

    def _cmd_toggle_verbose(self) -> None:
        """Verbose debug log'unu aç/kapa."""
        self._verbose_logging = not self._verbose_logging
        # EthercatMaster'ın flag'ine de yay
        try:
            if self.hand._master:
                self.hand._master.verbose_logging = self._verbose_logging
        except Exception:
            pass

        state = "AÇIK" if self._verbose_logging else "KAPALI"
        print(f"{C.GREEN}Verbose log: {state}{C.R}")
        if self._verbose_logging:
            print(f"{C.DIM}Detaylı debug mesajları (ör. 'Master state not OP') basılacak{C.R}")
        else:
            print(f"{C.DIM}Sadece kritik mesajlar basılacak{C.R}")
        time.sleep(1.0)

    def _cmd_stop(self) -> None:
        if not self._require_connected():
            return
        self.hand.stop_all()
        self._pause()

    # ---- Elle değer girerek hareket / pozisyon oluşturma -------------------
    def _cmd_move_manual(self) -> None:
        """
        Kullanıcıdan 6 eksen için değer alır ve anlık olarak gönderir.
        Pozisyon kaydedilmez - sadece test amaçlı.
        """
        if not self._require_connected():
            return

        print(f"{C.BOLD}Elle pozisyon girişi{C.R}")
        print(f"{C.DIM}Eksen sırası: [basparmak_rot, basparmak_flex, isaret, orta, yuzuk, serce]{C.R}")
        print(f"{C.DIM}Değer aralığı: 0 (düz) .. 10000 (kıvrık){C.R}")
        print(f"{C.DIM}Örnek giriş: 0 0 5000 5000 0 0   (sadece isaret ve orta yarı kıvrık){C.R}")
        print(f"{C.DIM}Virgül veya boşluk ile ayırabilirsiniz.{C.R}\n")

        s = self._prompt("➤ 6 değer: ")
        pos = self._parse_int_list(s, 6)
        if pos is None:
            print(f"{C.RED}❌ Geçersiz giriş. Tam 6 sayı gerekli.{C.R}")
            self._pause()
            return

        # Opsiyonel: hız ve akım
        vel_s = self._prompt(f"Hız ({DEFAULT_VELOCITY} için Enter): ") or str(DEFAULT_VELOCITY)
        cur_s = self._prompt(f"Akım ({DEFAULT_MAX_CURRENT} için Enter): ") or str(DEFAULT_MAX_CURRENT)
        try:
            vel = int(vel_s)
            cur = int(cur_s)
        except ValueError:
            print(f"{C.RED}❌ Geçersiz hız/akım.{C.R}")
            self._pause()
            return

        print(f"{C.CYAN}→ Hedef: {pos}  vel={vel}  cur={cur}{C.R}")
        ok = self.hand.move_to_position(pos, velocity=vel, max_current=cur)
        if not ok:
            print(f"{C.RED}500-ERROR Move command failed{C.R}")
            self._pause()
            return

        reached, reason = self.hand.wait_reached(
            timeout_s=3.0,
            target_pos=pos,
            pos_tolerance=300,
        )
        self._print_move_result(reached, reason, "manual")
        self._pause()

    def _cmd_create_manual(self) -> None:
        """
        Elle değer girerek yeni bir pozisyon oluştur ve kaydet.
        İsteğe bağlı olarak robotu da o pozisyona götür.
        """
        print(f"{C.BOLD}Yeni pozisyon oluştur{C.R}")
        print(f"{C.DIM}Eksen sırası: [basparmak_rot, basparmak_flex, isaret, orta, yuzuk, serce]{C.R}")
        print(f"{C.DIM}Değer aralığı: 0 (düz) .. 10000 (kıvrık){C.R}\n")

        name = self._prompt("İsim (örn: FIST, POINT): ")
        if not name:
            print(f"{C.DIM}İptal edildi.{C.R}")
            self._pause()
            return

        if self.positions.exists(name):
            overwrite = self._prompt(
                f"{C.YELLOW}⚠️ '{name}' zaten var. Üzerine yaz? (e/h): {C.R}"
            )
            if overwrite.lower() not in ("e", "y", "yes", "evet"):
                print(f"{C.DIM}İptal edildi.{C.R}")
                self._pause()
                return

        s = self._prompt("➤ 6 değer (boşluk ile): ")
        pos = self._parse_int_list(s, 6)
        if pos is None:
            print(f"{C.RED}❌ Geçersiz giriş. Tam 6 sayı gerekli.{C.R}")
            self._pause()
            return

        desc = self._prompt("Açıklama (opsiyonel): ")

        pose = self.positions.add_or_update(name, pos, description=desc)
        print(f"{C.GREEN}✅ '{pose.name}' kaydedildi: {pose.pos}{C.R}")

        # Bağlıysa robotu oraya götürmek ister misiniz?
        if self.hand.is_connected:
            go = self._prompt(f"Şimdi bu pozisyona hareket edelim mi? (e/h): ")
            if go.lower() in ("e", "y", "yes", "evet"):
                ok = self.hand.move_to_position(
                    pose.pos, velocity=pose.vel, max_current=pose.cur
                )
                if ok:
                    reached, reason = self.hand.wait_reached(
                        timeout_s=3.0,
                        target_pos=pose.pos,
                        pos_tolerance=pose.pos_tol,
                    )
                    self._print_move_result(reached, reason, pose.name)
                else:
                    print(f"{C.RED}500-ERROR Move command failed{C.R}")
        self._pause()

    # ---- Test komutları ---------------------------------------------------
    def _cmd_finger_loop_test(self) -> None:
        """
        İki kayıtlı pozisyon arasında arka arkaya gidip gelme testi.
        Örnek: BARDAK_AL <-> BARDAK_BIRAK arasında 10 kez döngü.

        Kullanıcıdan iki pozisyon adı (veya index) ve iterasyon sayısı alır.
        """
        if not self._require_connected():
            return

        names = self.positions.list_names()
        if len(names) < 2:
            print(f"{C.RED}❌ En az 2 kayıtlı pozisyon gerekli.{C.R}")
            print(f"{C.DIM}   Mevcut: {len(names)} pozisyon{C.R}")
            print(f"{C.DIM}   Yeni pozisyon için: [15] Elle değer girerek oluştur{C.R}")
            self._pause()
            return

        # Pozisyonları listele
        print(f"{C.BOLD}Kayıtlı pozisyonlar:{C.R}")
        for i, n in enumerate(names):
            p = self.positions.get(n)
            pos_str = ",".join(str(x) for x in p.pos) if p else ""
            print(f"  {C.CYAN}[{i}]{C.R} {n:<16} [{pos_str}]")

        # Birinci pozisyon
        s1 = self._prompt("➤ 1. pozisyon (isim veya index): ")
        pose1 = self._resolve_pose(s1, names)
        if not pose1:
            print(f"{C.RED}❌ Pozisyon bulunamadı: {s1}{C.R}")
            self._pause()
            return

        # İkinci pozisyon
        s2 = self._prompt("➤ 2. pozisyon (isim veya index): ")
        pose2 = self._resolve_pose(s2, names)
        if not pose2:
            print(f"{C.RED}❌ Pozisyon bulunamadı: {s2}{C.R}")
            self._pause()
            return

        if pose1.name == pose2.name:
            print(f"{C.YELLOW}⚠️ İki pozisyon aynı olamaz{C.R}")
            self._pause()
            return

        # İterasyon sayısı
        s = self._prompt(f"Iterasyon sayısı [10]: ") or "10"
        try:
            iterations = max(1, int(s))
        except ValueError:
            print(f"{C.RED}❌ Geçersiz sayı.{C.R}")
            self._pause()
            return

        # Settle süresi
        s = self._prompt(f"Pozisyonlar arası bekleme (saniye) [1.0]: ") or "1.0"
        try:
            settle_s = max(0.1, float(s))
        except ValueError:
            print(f"{C.RED}❌ Geçersiz sayı.{C.R}")
            self._pause()
            return

        total = iterations * 2  # her iterasyon 2 hareket
        print(f"\n{C.CYAN}🔄 Test başlıyor:{C.R}")
        print(f"   Pozisyon 1: {C.BOLD}{pose1.name}{C.R}  {pose1.pos}")
        print(f"   Pozisyon 2: {C.BOLD}{pose2.name}{C.R}  {pose2.pos}")
        print(f"   {iterations} iterasyon × 2 hareket = {total} hareket toplam")
        print(f"   Bekleme: {settle_s}s")
        print(f"{C.DIM}Durdurmak için Ctrl+C{C.R}\n")

        # İstatistikler
        success = timeout = alarm = locked = errored = 0

        try:
            for it in range(1, iterations + 1):
                print(f"\n{C.BOLD}{C.CYAN}{'─' * 60}{C.R}")
                print(f"{C.BOLD}  Iterasyon {it}/{iterations}{C.R}")
                print(f"{C.CYAN}{'─' * 60}{C.R}")

                # Bu iterasyonda iki hareket: pose1 -> pose2
                for step_idx, pose in enumerate([pose1, pose2], 1):
                    ts = time.strftime("%H:%M:%S")
                    print(f"\n[{ts}] [{it}.{step_idx}] {pose.name}: {pose.pos}")

                    ok = self.hand.move_to_position(
                        pose.pos,
                        velocity=pose.vel,
                        max_current=pose.cur,
                    )
                    if not ok:
                        print(f"   {C.RED}500-ERROR Komut gönderilemedi{C.R}")
                        errored += 1
                        continue

                    reached, reason = self.hand.wait_reached(
                        timeout_s=5.0,
                        target_pos=pose.pos,
                        pos_tolerance=pose.pos_tol,
                    )

                    if reached:
                        print(f"   {C.GREEN}200-OK Position reached ({pose.name}){C.R}")
                        success += 1
                    elif reason == "timeout":
                        print(f"   {C.YELLOW}408-TIMEOUT ({pose.name}){C.R}")
                        timeout += 1
                    elif reason == "alarm":
                        print(f"   {C.RED}409-CONFLICT Motor alarm ({pose.name}){C.R}")
                        alarm += 1
                    elif reason == "alarm_locked":
                        print(f"   {C.RED}503-SERVICE-UNAVAILABLE Motor kilitli ({pose.name}){C.R}")
                        print(f"   {C.YELLOW}⚠️ Test durduruluyor - manuel müdahale gerekli{C.R}")
                        locked += 1
                        raise KeyboardInterrupt
                    else:
                        print(f"   {C.YELLOW}500-ERROR: {reason} ({pose.name}){C.R}")
                        errored += 1

                    time.sleep(settle_s)

        except KeyboardInterrupt:
            print(f"\n{C.YELLOW}⏹️ Test kullanıcı tarafından durduruldu{C.R}")

        # İstatistikler
        total_done = success + timeout + alarm + locked + errored
        print(f"\n{C.BOLD}{C.CYAN}{'═' * 60}{C.R}")
        print(f"{C.BOLD}  TEST SONUÇLARI{C.R}")
        print(f"{C.CYAN}{'═' * 60}{C.R}")
        print(f"  {pose1.name} ↔ {pose2.name}")
        print(f"  Toplam hareket : {total_done}")
        print(f"  {C.GREEN}Başarılı (200) : {success}{C.R}")
        print(f"  {C.YELLOW}Timeout (408)  : {timeout}{C.R}")
        print(f"  {C.RED}Alarm   (409)  : {alarm}{C.R}")
        print(f"  {C.RED}Kilitli (503)  : {locked}{C.R}")
        print(f"  {C.YELLOW}Hata    (500)  : {errored}{C.R}")
        if total_done > 0:
            rate = (success / total_done) * 100
            print(f"  Başarı oranı   : {rate:.1f}%")
        print(f"{C.CYAN}{'═' * 60}{C.R}")
        self._pause()

    def _resolve_pose(self, s: str, names: List[str]) -> Optional[Pose]:
        """İsim veya index string'ini Pose'a çevir."""
        if not s:
            return None
        # Index ile dene
        try:
            idx = int(s)
            if 0 <= idx < len(names):
                return self.positions.get(names[idx])
        except ValueError:
            pass
        # İsim ile dene
        return self.positions.get(s)

    def _cmd_bye_bye(self) -> None:
        """Bye bye hareketi - motor 3-6 sallama."""
        if not self._require_connected():
            return
        if not self._require_enabled():
            return

        # Kullanıcı tekrar sayısını girebilsin (Enter: varsayılan 3)
        user_input = input(f"\n{C.CYAN}➤ Kaç kere bye bye yapılsın? [3]: {C.R}").strip()
        try:
            repeat = int(user_input) if user_input else 3
            repeat = max(1, min(10, repeat))
        except ValueError:
            repeat = 3
            print(f"{C.YELLOW}   Geçersiz değer, varsayılan kullanılıyor: 3{C.R}")

        print(f"\n{C.CYAN}👋 Bye bye! başlıyor ({repeat} tekrar × 2 hareket){C.R}")
        print(f"{C.DIM}   Motor 3-6 sallanacak, başparmak sabit kalacak{C.R}\n")

        # BYE_UP ve BYE_DOWN pozisyonlarını sırayla yap
        bye_up = self.positions.get("BYE_UP")
        bye_down = self.positions.get("BYE_DOWN")

        if not bye_up or not bye_down:
            print(f"{C.RED}❌ BYE_UP/BYE_DOWN pozisyonları bulunamadı.{C.R}")
            print(f"{C.YELLOW}   positions.json dosyasında tanımlı mı?{C.R}")
            self._pause()
            return

        total = repeat * 2
        completed = 0

        try:
            for cycle in range(1, repeat + 1):
                print(f"{C.CYAN}   Tekrar {cycle}/{repeat}:{C.R}")

                # UP (parmaklar yukarı)
                print(f"     ↑ BYE_UP    ", end="", flush=True)
                self.hand.move_to_position(
                    list(bye_up.pos),
                    velocity=bye_up.vel[0] if isinstance(bye_up.vel, list) else 12000,
                    max_current=bye_up.cur[0] if isinstance(bye_up.cur, list) else 500,
                )
                reached, reason = self.hand.wait_reached(
                    timeout_s=3.0,
                    target_pos=list(bye_up.pos),
                    pos_tolerance=500,
                )
                if not reached:
                    print(f"{C.YELLOW}({reason}){C.R}")
                else:
                    print(f"{C.GREEN}✓{C.R}")
                completed += 1
                time.sleep(0.1)

                # DOWN (parmaklar aşağı)
                print(f"     ↓ BYE_DOWN  ", end="", flush=True)
                self.hand.move_to_position(
                    list(bye_down.pos),
                    velocity=bye_down.vel[0] if isinstance(bye_down.vel, list) else 12000,
                    max_current=bye_down.cur[0] if isinstance(bye_down.cur, list) else 500,
                )
                reached, reason = self.hand.wait_reached(
                    timeout_s=3.0,
                    target_pos=list(bye_down.pos),
                    pos_tolerance=500,
                )
                if not reached:
                    print(f"{C.YELLOW}({reason}){C.R}")
                else:
                    print(f"{C.GREEN}✓{C.R}")
                completed += 1
                time.sleep(0.1)

            print(f"\n{C.GREEN}✅ Bye bye tamamlandı: {completed}/{total} hareket{C.R}")

        except KeyboardInterrupt:
            print(f"\n{C.YELLOW}⏹️ Bye bye iptal edildi ({completed}/{total}){C.R}")

        self._pause()

    def _cmd_quit(self) -> None:
        self._running = False

    # ---- yardımcı ---------------------------------------------------------
    def _require_connected(self) -> bool:
        if not self.hand.is_connected:
            print(f"{C.RED}❌ Önce bağlanın (menü [2]).{C.R}")
            self._pause()
            return False
        return True


def main(
    positions_file: str = "positions.json",
    adapter_config_file: str = "ecat_config.json",
) -> None:
    cli = HandCLI(
        positions_file=positions_file,
        adapter_config_file=adapter_config_file,
    )
    cli.run()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Input Helper - GIL-friendly input
=================================

Sorun:
    Python'un standart input() fonksiyonu (Windows'ta) GIL'i (Global
    Interpreter Lock) tutuyor. Bu sırada arka plan thread'leri çalışamıyor.
    EtherCAT IO thread her 8ms'de bir PDO paketi göndermeli, gönderemezse
    slave Sync Manager Watchdog tetikliyor (AL=0x1b) ve OP'tan düşüyor.

Çözüm:
    Windows'ta msvcrt.kbhit() + msvcrt.getwch() kullanarak char-by-char
    polling yapıyoruz. Polling döngüsünde time.sleep() var, bu GIL'i
    serbest bırakıyor → arka plan thread'leri rahatça çalışıyor.

    Linux/Mac'te normal input() kullanıyoruz (bu sistemlerde GIL
    sorunu input için yok).

Kullanım:
    from input_helper import gil_friendly_input
    s = gil_friendly_input("Seçim: ")
"""

from __future__ import annotations

import sys
import time

# Windows için msvcrt
_MSVCRT = None
if sys.platform == "win32":
    try:
        import msvcrt
        _MSVCRT = msvcrt
    except ImportError:
        _MSVCRT = None


def _input_windows_polling(prompt: str = "", poll_interval: float = 0.01) -> str:
    """
    Windows'ta msvcrt ile char-by-char polling input.

    Her döngüde time.sleep() yapıldığı için GIL serbest bırakılıyor,
    arka plan thread'leri çalışabiliyor.

    Args:
        prompt: Görüntülenecek prompt metni
        poll_interval: Karakterler arası bekleme süresi (saniye).
                       0.01 = 10ms, EtherCAT 8ms cycle için yeterli margin.
    """
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()

    chars: list[str] = []

    while True:
        if _MSVCRT.kbhit():
            ch = _MSVCRT.getwch()

            # Enter (CR veya LF)
            if ch in ("\r", "\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(chars)

            # Backspace
            elif ch == "\x08":
                if chars:
                    chars.pop()
                    # Console'da bir karakter sil
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()

            # Ctrl+C
            elif ch == "\x03":
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise KeyboardInterrupt

            # Ctrl+D / EOF
            elif ch == "\x04":
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise EOFError

            # Yazdırılabilir karakter
            elif ch.isprintable():
                chars.append(ch)
                sys.stdout.write(ch)
                sys.stdout.flush()

            # Diğer kontrol karakterleri (escape, function keys vb.)
            # — bunlar genelde 2 byte gelir (örneğin ok tuşları),
            # ikinci byte'ı temizlemek için bir okuma daha:
            elif ch == "\x00" or ch == "\xe0":
                if _MSVCRT.kbhit():
                    _MSVCRT.getwch()  # ikinci byte'ı yut
                # Bilinmeyen kontrol karakterini görmezden gel

        else:
            # Tuşa basılı değil, kısa süre uyu (GIL release)
            time.sleep(poll_interval)


def gil_friendly_input(prompt: str = "") -> str:
    """
    Platform-agnostik GIL-friendly input.

    Windows'ta polling tabanlı çalışır (arka plan thread'lerini bloke etmez).
    Linux/Mac'te normal input() kullanır (bu OS'lerde gerek yok).
    """
    if _MSVCRT is not None:
        return _input_windows_polling(prompt)
    else:
        # Linux/Mac: standard input() yeterli
        return input(prompt)

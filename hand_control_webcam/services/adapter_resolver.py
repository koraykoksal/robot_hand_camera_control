# -*- coding: utf-8 -*-
r"""
Adapter Resolver
================

Windows'ta EtherCAT için kullanılan ağ adaptörleri `\Device\NPF_{GUID}`
şeklinde okunamaz bir isimle gelir. Bu modül iki şey yapar:

    1) Npcap'in wpcap.dll'inden "friendly name" (örn. "Realtek USB GbE")
       haritasını çıkarır.
    2) Son kullanılan adaptörü ecat_config.json'a kaydeder/yükler ki
       her açılışta kullanıcı tekrar seçmek zorunda kalmasın.

Linux/Mac'te çalışırsa friendly_map boş döner, raw isimler gösterilir.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import sys
from ctypes import POINTER, Structure, c_char_p, c_uint, c_void_p
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# pcap_if_t struct (wpcap.dll API)
# ---------------------------------------------------------------------------
class _pcap_if_t(Structure):
    pass


_pcap_if_t._fields_ = [
    ("next", POINTER(_pcap_if_t)),
    ("name", c_char_p),
    ("description", c_char_p),
    ("addresses", c_void_p),
    ("flags", c_uint),
]


# ---------------------------------------------------------------------------
# Friendly name map (sadece Windows'ta çalışır)
# ---------------------------------------------------------------------------
def _load_wpcap():
    """wpcap.dll'i bulmaya çalış. Yoksa None döner."""
    if sys.platform != "win32":
        return None
    dll_name = ctypes.util.find_library("wpcap") or "wpcap.dll"
    try:
        return ctypes.WinDLL(dll_name)
    except Exception:
        for alt in ("wpcap.dll", "Packet.dll"):
            try:
                return ctypes.WinDLL(alt)
            except Exception:
                pass
    return None


def get_friendly_name_map() -> Dict[str, str]:
    r"""
    `\Device\NPF_{GUID}` → "Human-readable description" eşleşmesi döner.

    Npcap/WinPcap yüklü değilse veya Linux/Mac'te çalışıyorsa boş sözlük döner.
    """
    wpcap = _load_wpcap()
    if not wpcap:
        return {}

    try:
        pcap_findalldevs = wpcap.pcap_findalldevs
        pcap_findalldevs.argtypes = [POINTER(POINTER(_pcap_if_t)), c_char_p]
        pcap_findalldevs.restype = ctypes.c_int

        pcap_freealldevs = wpcap.pcap_freealldevs
        pcap_freealldevs.argtypes = [POINTER(_pcap_if_t)]
        pcap_freealldevs.restype = None
    except Exception:
        return {}

    alldevs = POINTER(_pcap_if_t)()
    errbuf = ctypes.create_string_buffer(256)

    rc = pcap_findalldevs(ctypes.byref(alldevs), errbuf)
    if rc != 0:
        return {}

    out: Dict[str, str] = {}
    try:
        p = alldevs
        while p:
            try:
                name = (
                    p.contents.name.decode("utf-8", errors="ignore")
                    if p.contents.name else ""
                )
                desc = (
                    p.contents.description.decode("utf-8", errors="ignore")
                    if p.contents.description else ""
                )
                if name:
                    out[name] = desc or name
            except Exception:
                pass
            p = p.contents.next
    finally:
        try:
            pcap_freealldevs(alldevs)
        except Exception:
            pass

    return out


# ---------------------------------------------------------------------------
# Enriched adapter list
# ---------------------------------------------------------------------------
class AdapterInfo:
    """Bir network adaptörü için birleşik bilgi."""

    __slots__ = ("index", "raw_name", "display_name")

    def __init__(self, index: int, raw_name: str, display_name: str):
        self.index: int = index
        self.raw_name: str = raw_name         # \Device\NPF_{GUID}
        self.display_name: str = display_name  # "Realtek USB GbE ..."

    def __repr__(self) -> str:
        return f"[{self.index}] {self.display_name}"


def enrich_adapters(raw_names: List[str]) -> List[AdapterInfo]:
    """
    Ham adaptör listesini friendly isimlerle süsler.

    Args:
        raw_names: HandController.scan_interfaces()'ten gelen ham isimler

    Returns:
        AdapterInfo listesi (aynı sıra korunur)
    """
    friendly_map = get_friendly_name_map()

    result: List[AdapterInfo] = []
    for i, raw in enumerate(raw_names):
        raw = (raw or "").strip()
        display = friendly_map.get(raw, "").strip()
        if not display:
            # Fallback: GUID'nin son 6 hanesini göster, daha okunabilir olur
            tail = ""
            if "{" in raw and "}" in raw:
                try:
                    tail = raw.split("{", 1)[1].split("}", 1)[0][-6:]
                    display = f"Unknown adapter (...{tail})"
                except Exception:
                    display = raw
            else:
                display = raw

        result.append(AdapterInfo(index=i, raw_name=raw, display_name=display))

    # Duplicate display_name varsa sonuna GUID kuyruğu ekle
    counts: Dict[str, int] = {}
    for a in result:
        counts[a.display_name] = counts.get(a.display_name, 0) + 1

    for a in result:
        if counts.get(a.display_name, 0) > 1:
            raw = a.raw_name
            tail = ""
            if "{" in raw and "}" in raw:
                try:
                    tail = raw.split("{", 1)[1].split("}", 1)[0][-6:]
                except Exception:
                    tail = str(a.index)
            else:
                tail = str(a.index)
            a.display_name = f"{a.display_name} ({tail})"

    return result


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------
def load_adapter_config(config_path: str) -> Dict[str, str]:
    r"""
    Kaydedilmiş adaptör tercihini yükler.

    Returns:
        {"display_name": "...", "raw_name": "\Device\NPF_..."} veya boş dict
    """
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if isinstance(data, dict):
            return {
                "display_name": str(data.get("display_name", "")),
                "raw_name": str(data.get("raw_name", "")),
            }
    except Exception:
        pass
    return {}


def save_adapter_config(config_path: str, display_name: str, raw_name: str) -> bool:
    """Seçilen adaptörü kaydet."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(config_path)) or ".", exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(
                {"display_name": display_name, "raw_name": raw_name},
                f, indent=2, ensure_ascii=False,
            )
        return True
    except Exception as e:
        print(f"⚠️ Adapter config kaydedilemedi: {e}")
        return False


def find_saved_adapter_index(
    adapters: List[AdapterInfo],
    saved: Dict[str, str],
) -> Optional[int]:
    """
    Kaydedilmiş tercih bu sefer hangi index'e düşüyor?

    Windows GUID'leri USB çıkarıp takınca değişebilir, o yüzden önce
    raw_name ile (GUID tam eşleşme), bulunmazsa display_name ile arıyoruz.
    """
    if not saved:
        return None

    saved_raw = (saved.get("raw_name") or "").strip()
    saved_disp = (saved.get("display_name") or "").strip()

    # 1) Raw isim (GUID) ile tam eşleşme
    if saved_raw:
        for a in adapters:
            if a.raw_name == saved_raw:
                return a.index

    # 2) Display name ile eşleşme (USB port değişimine dayanıklı)
    if saved_disp:
        for a in adapters:
            if a.display_name == saved_disp:
                return a.index

    return None

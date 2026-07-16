# -*- coding: utf-8 -*-
"""
PositionManager
===============

Pozisyon JSON dosyalarını okur/yazar/yönetir.

Desteklenen formatlar (geriye uyumluluk):
    1) Basit format (robot_positions.json):
       {
           "rock": [0, 0, 9000, 9000, 9000, 9000],
           "paper": [0, 0, 0, 0, 0, 0]
       }

    2) Genişletilmiş format (tcp_positions.json):
       {
           "poses": {
               "BARDAK_AL": {
                   "pos": [4500, 3500, 3500, 4000, 4000, 4000],
                   "vel": [12000, 12000, 12000, 12000, 12000, 12000],
                   "cur": [500, 400, 400, 400, 400, 400],
                   "ok_mode": "position",
                   "cur_tol": 50,
                   "pos_tol": 2000
               }
           }
       }

Her pozisyon iç temsilde her zaman tam Pose objesi tutulur; eksik alanlar
varsayılanlarla doldurulur.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# Varsayılanlar (rogolob22.py'dan)
DEFAULT_VEL = 12000
DEFAULT_CUR = 700
DEFAULT_POS_TOL = 80
DEFAULT_CUR_TOL = 50
DEFAULT_OK_MODE = "position"  # "position" | "current"

DOF = 6


@dataclass
class Pose:
    """Bir pozisyonu tam tanımlayan yapı."""
    name: str
    pos: List[int] = field(default_factory=lambda: [0] * DOF)
    vel: List[int] = field(default_factory=lambda: [DEFAULT_VEL] * DOF)
    cur: List[int] = field(default_factory=lambda: [DEFAULT_CUR] * DOF)
    ok_mode: str = DEFAULT_OK_MODE
    cur_tol: int = DEFAULT_CUR_TOL
    pos_tol: int = DEFAULT_POS_TOL
    description: str = ""

    def to_dict(self) -> Dict:
        return {
            "pos": list(self.pos),
            "vel": list(self.vel),
            "cur": list(self.cur),
            "ok_mode": self.ok_mode,
            "cur_tol": int(self.cur_tol),
            "pos_tol": int(self.pos_tol),
            "description": self.description,
        }


class PositionManager:
    """
    Pozisyon kütüphanesi.

    Args:
        file_path: JSON dosyasının yolu. Dosya yoksa boş başlar.
        autosave: True ise add/remove/update sonrası dosyaya yazar.
    """

    def __init__(self, file_path: str = "positions.json", *, autosave: bool = True):
        self.file_path = file_path
        self.autosave = bool(autosave)
        self._poses: Dict[str, Pose] = {}
        self.load()

    # ---- IO ---------------------------------------------------------------
    def load(self) -> int:
        """
        Dosyayı yükler. Format otomatik algılanır.

        Returns:
            Yüklenen pozisyon sayısı
        """
        self._poses.clear()

        if not os.path.exists(self.file_path):
            return 0

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception as e:
            print(f"⚠️ Pozisyon dosyası okunamadı ({self.file_path}): {e}")
            return 0

        # Format algıla: {"poses": {...}} veya {"positions": {...}} veya düz {...}
        # None ile boş dict'i ayırmak için explicit kontrol:
        if "poses" in data:
            raw = data["poses"]
        elif "positions" in data:
            raw = data["positions"]
        else:
            raw = data
        if not isinstance(raw, dict):
            return 0

        for name, v in raw.items():
            pose = self._parse_pose(name, v)
            if pose is not None:
                self._poses[self._norm_key(name)] = pose

        return len(self._poses)

    def save(self) -> bool:
        """Tüm pozisyonları dosyaya yazar (genişletilmiş format)."""
        try:
            payload = {
                "poses": {name: pose.to_dict() for name, pose in self._poses.items()}
            }
            os.makedirs(os.path.dirname(os.path.abspath(self.file_path)) or ".", exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"❌ Pozisyon kaydetme hatası: {e}")
            return False

    # ---- CRUD -------------------------------------------------------------
    def list_names(self) -> List[str]:
        """Pozisyon isimlerini sıralı olarak döner."""
        return sorted(self._poses.keys())

    def count(self) -> int:
        return len(self._poses)

    def get(self, name: str) -> Optional[Pose]:
        return self._poses.get(self._norm_key(name))

    def exists(self, name: str) -> bool:
        return self._norm_key(name) in self._poses

    def add_or_update(
        self,
        name: str,
        pos: List[int],
        *,
        vel: Optional[List[int]] = None,
        cur: Optional[List[int]] = None,
        ok_mode: str = DEFAULT_OK_MODE,
        cur_tol: int = DEFAULT_CUR_TOL,
        pos_tol: int = DEFAULT_POS_TOL,
        description: str = "",
    ) -> Pose:
        """Pozisyon ekler ya da günceller."""
        key = self._norm_key(name)
        pose = Pose(
            name=key,
            pos=self._sanitize_int_list(pos, 0),
            vel=self._sanitize_int_list(vel, DEFAULT_VEL),
            cur=self._sanitize_int_list(cur, DEFAULT_CUR),
            ok_mode=self._norm_ok_mode(ok_mode),
            cur_tol=int(cur_tol),
            pos_tol=int(pos_tol),
            description=str(description or ""),
        )
        self._poses[key] = pose
        if self.autosave:
            self.save()
        return pose

    def remove(self, name: str) -> bool:
        key = self._norm_key(name)
        if key not in self._poses:
            return False
        del self._poses[key]
        if self.autosave:
            self.save()
        return True

    def rename(self, old: str, new: str) -> bool:
        old_k = self._norm_key(old)
        new_k = self._norm_key(new)
        if old_k not in self._poses:
            return False
        if new_k in self._poses and old_k != new_k:
            return False
        pose = self._poses.pop(old_k)
        pose.name = new_k
        self._poses[new_k] = pose
        if self.autosave:
            self.save()
        return True

    # ---- Parsing ----------------------------------------------------------
    @classmethod
    def _parse_pose(cls, name: str, v) -> Optional[Pose]:
        """
        v şunlardan biri olabilir:
            - List[int]   -> eski basit format
            - Dict        -> genişletilmiş format
        """
        key = cls._norm_key(name)

        if isinstance(v, list):
            return Pose(
                name=key,
                pos=cls._sanitize_int_list(v, 0),
            )

        if isinstance(v, dict):
            return Pose(
                name=key,
                pos=cls._sanitize_int_list(v.get("pos"), 0),
                vel=cls._sanitize_int_list(v.get("vel"), DEFAULT_VEL),
                cur=cls._sanitize_int_list(v.get("cur"), DEFAULT_CUR),
                ok_mode=cls._norm_ok_mode(v.get("ok_mode", DEFAULT_OK_MODE)),
                cur_tol=int(v.get("cur_tol", DEFAULT_CUR_TOL) or DEFAULT_CUR_TOL),
                pos_tol=int(v.get("pos_tol", DEFAULT_POS_TOL) or DEFAULT_POS_TOL),
                description=str(v.get("description", "") or ""),
            )

        return None

    # ---- Sanitizers -------------------------------------------------------
    @staticmethod
    def _norm_key(k: str) -> str:
        return (str(k) or "").strip().upper()

    @staticmethod
    def _norm_ok_mode(m) -> str:
        s = (str(m) or "").strip().lower()
        return "current" if s == "current" else "position"

    @staticmethod
    def _sanitize_int_list(vals, default: int) -> List[int]:
        out: List[int] = []
        src = list(vals) if isinstance(vals, list) else []
        for i in range(DOF):
            v = src[i] if i < len(src) else default
            try:
                out.append(int(v))
            except Exception:
                out.append(default)
        return out

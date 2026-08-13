import json
import math
from datetime import datetime, timezone
from typing import Any

from quiet_oppen_data.adaptrar.bas import Adapter
from quiet_oppen_data.adaptrar.transport import hamta_json, hamta_text
from quiet_oppen_data.modeller import Faktapost, Fragplan
from quiet_oppen_data.register import Kalla, hamta


class PxWebAdapter:
    """Generisk adapter för PxWeb-servrar (t.ex. SCB)."""

    def __init__(self, kalla_id: str) -> None:
        k = hamta(kalla_id)
        if not isinstance(k, Kalla):
            raise RuntimeError(f"PxWeb-källan {kalla_id} saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        # Exponerar två verktyg: ett för att lista dimensioner, ett för att hämta data
        return [
            {
                "name": f"{self.id}_lista_dimensioner",
                "description": f"Hämtar meta-information och tillgängliga dimensioner för en specifik PxWeb-tabell från {self._kalla.myndighet}.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tabell": {
                            "type": "string",
                            "description": "Tabellens ID (t.ex. TAB6445)"
                        }
                    },
                    "required": ["tabell"]
                }
            },
            {
                "name": f"{self.id}_hamta_data",
                "description": f"Hämtar data från en PxWeb-tabell från {self._kalla.myndighet}. Du MÅSTE ange alla dimensioner som returnerades av lista_dimensioner.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tabell": {
                            "type": "string",
                            "description": "Tabellens ID"
                        },
                        "dimensioner": {
                            "type": "object",
                            "description": "En map/dictionary från dimensionskod (t.ex. 'Tid') till en lista av önskade värdekoder (t.ex. ['2026M07'])."
                        }
                    },
                    "required": ["tabell", "dimensioner"]
                }
            }
        ]

    def _las_metadata(self, tabell: str) -> dict[str, dict]:
        """Hämtar och normaliserar dimensioner från SCB v2."""
        url = f"{self._kalla.bas_url}/tables/{tabell}/metadata"
        meta = hamta_json(self.id, "GET", url)
        
        dimensioner = {}
        # Parsa JSON-stat 2 formatet för metadata
        for dim_id in meta.get("id", []):
            dim = meta.get("dimension", {}).get(dim_id, {})
            label = dim.get("label", dim_id)
            cat = dim.get("category", {})
            index = cat.get("index", {})
            
            codes = list(index.keys()) if isinstance(index, dict) else index
            labels = cat.get("label", {})
            texts = [labels.get(c, c) for c in codes]
            
            dimensioner[dim_id] = {
                "label": label,
                "codes": codes,
                "texts": texts
            }
        return dimensioner

    def _formatera_valalternativ(self, tabell: str, dimensioner: dict[str, dict]) -> list[Faktapost]:
        """Returnerar valalternativen som Faktaposter för LLM:en."""
        poster = []
        for dim_id, data in dimensioner.items():
            kod_text = []
            for c, t in zip(data["codes"], data["texts"]):
                kod_text.append(f"{c} ({t})")
            
            # Förkorta om det är extremt många
            if len(kod_text) > 100:
                kod_text = kod_text[-100:] # Anta att sista 100 (tex tid) är mest relevanta
                
            varde_str = ", ".join(kod_text)
            
            manniska = ""
            if self._kalla.manniskolank_mall:
                manniska = self._kalla.manniskolank_mall.format(tabell=tabell)
                
            poster.append(
                Faktapost(
                    id="",
                    etikett=f"PxWeb Dimension '{data['label']}' (kod: {dim_id}) för tabell {tabell}",
                    varde=f"Tillåtna värden: {varde_str}",
                    kalla_id=self.id,
                    myndighet=self._kalla.myndighet or "SCB",
                    licens=self._kalla.licens,
                    hamtad=datetime.now(timezone.utc),
                    lank_manniska=manniska,
                    lank_maskin=f"{self._kalla.bas_url}/tables/{tabell}/metadata"
                )
            )
        return poster

    def hamta(self, plan: Fragplan) -> list[Faktapost]:
        tabell = plan.extra.get("tabell")
        if not tabell:
            return []

        # Identifiera om LLM försöker hämta data men missade dimensioner
        valda_dimensioner = plan.extra.get("dimensioner", {})
        
        try:
            meta_dim = self._las_metadata(tabell)
        except Exception:
            return []

        # Om det är ett anrop för att lista dimensioner ELLER om man glömt dimensioner
        if not valda_dimensioner or not all(k in valda_dimensioner for k in meta_dim.keys()):
            return self._formatera_valalternativ(tabell, meta_dim)

        # Beräkna antal celler
        celler = 1
        for dim_id, v in valda_dimensioner.items():
            if isinstance(v, list):
                celler *= len(v)
            else:
                pass # scalar, = 1
                
        maxceller = self._kalla.takt.get("maxceller", 150000) if hasattr(self._kalla, "takt") else 150000
        # Wait, maxceller is actually a top-level property on Kalla? No, it's not in Kalla. 
        # But we kan get it via kallregister / getattr.
        maxceller = getattr(self._kalla, "maxceller", 150000)
        
        if celler > maxceller:
            # Vägra, returnera tomt eller fel
            return [
                Faktapost(
                    id="",
                    etikett=f"Error för tabell {tabell}",
                    varde=f"Uttaget överskrider 150 000 celler ({celler} begärda). Minska urvalet.",
                    kalla_id=self.id,
                    myndighet=self._kalla.myndighet or "SCB",
                    licens=self._kalla.licens,
                    hamtad=datetime.now(timezone.utc),
                    lank_manniska="",
                    lank_maskin=""
                )
            ]

        # Bygg query
        selection = []
        for dim_id, values in valda_dimensioner.items():
            selection.append({
                "variableCode": dim_id,
                "valueCodes": values if isinstance(values, list) else [values]
            })

        payload = {
            "selection": selection,
            "responseFormat": "json-stat2"
        }
        
        url = f"{self._kalla.bas_url}/tables/{tabell}/data"
        try:
            # SCB v2 returnerar PX-format som standard, så vi hämtar som råtext
            raw_text = hamta_text(self.id, "POST", url, json=payload)
        except Exception:
            return []
            
        # Parsa PX format för data-delen:
        # Leta efter "DATA=" och hämta allt efter tills semikolon eller slut
        varde_str = ""
        if "DATA=" in raw_text:
            data_part = raw_text.split("DATA=")[-1].strip(" \r\n;")
            varde_str = data_part
        else:
            varde_str = raw_text.strip()
        
        manniska = ""
        if self._kalla.manniskolank_mall:
            manniska = self._kalla.manniskolank_mall.format(tabell=tabell)
            
        return [
            Faktapost(
                id="",
                etikett=f"PxWeb-data från tabell {tabell}",
                varde=varde_str,
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or "SCB",
                licens=self._kalla.licens,
                hamtad=datetime.now(timezone.utc),
                lank_manniska=manniska,
                lank_maskin=url,
                dimensioner=valda_dimensioner
            )
        ]

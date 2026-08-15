import logging
from urllib.parse import quote
from datetime import datetime, UTC
from typing import Any

from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)


class SkatteverketVagledningAdapter:
    """Bygger en sök-länk till Skatteverkets rättsliga vägledning.

    Skatteverket blockerar maskinell läsning av Rättslig vägledning (samma
    slutsats som steg 18 nådde för partner-API:et — se README.md). Den här
    adaptern hämtar därför INGEN data. Den gör exakt en sak: formaterar en
    sökterm till en klickbar länk användaren kan följa och läsa själv.

    Motsvarar Juridikchatt::skapa_lank_skatteverket() i quiet.nu:s
    hostup-sida (app/Juridikchatt.php) — porterad hit så att kapaciteten
    finns i quiet_oppen_data innan hostup-sidans egen juridikchatt
    pensioneras till förmån för den här appen.

    Faktautkastet är ett pekar-utkast, inte ett citat: 'varde' beskriver
    vad sökningen gäller, inte ett svar hämtat från Skatteverket. Fas B ska
    presentera det som "kontrollera själv hos Skatteverket", aldrig som ett
    sakpåstående om vad vägledningen säger — se instruktionstexten i
    beskriv().
    """

    def __init__(self) -> None:
        k = hamta("skatteverket_vagledning")
        if not isinstance(k, Kalla):
            raise RuntimeError("skatteverket_vagledning-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        return [{
            "name": self.id,
            "description": (
                "Bygger en sök-länk till Skatteverkets rättsliga vägledning "
                "(ställningstaganden). Hämtar INGEN text — Skatteverket "
                "blockerar maskinell läsning av sidan. Använd bara som en "
                "källa att hänvisa vidare till, aldrig som grund för ett "
                "citat eller sakpåstående om vad vägledningen säger."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sokord": {
                        "type": "string",
                        "description": "Sökord, t.ex. 'representation' eller 'traktamente'"
                    }
                },
                "required": ["sokord"]
            }
        }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        sokord = (plan.extra.get("sokord") or "").strip()
        if not sokord:
            logger.info("%s: anrop utan sökord", self.id)
            return []

        lank = f"{self._kalla.bas_url}?q={quote(sokord)}"

        return [Faktautkast(
            etikett=f"Skatteverkets rättsliga vägledning — sökning: {sokord}",
            varde=(
                f"Sökning hos Skatteverkets rättsliga vägledning för \"{sokord}\". "
                "Innehållet är inte hämtat automatiskt — följ länken och läs "
                "ställningstagandet själv."
            ),
            kalla_id=self.id,
            myndighet=self._kalla.myndighet or "Skatteverket",
            licens=self._kalla.licens,
            attribution=self._kalla.attribution,
            dataset=None,
            dimensioner={"sokord": sokord},
            lank_manniska=lank,
            lank_maskin=lank,
            hamtad=datetime.now(UTC),
        )]

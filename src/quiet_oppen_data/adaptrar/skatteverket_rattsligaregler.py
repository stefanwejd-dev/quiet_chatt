"""Adapter för Skatteverkets rättsliga regelfiler (Rules as Code).

Skatteverket publicerar sin tolkning av gällande rätt inom ett antal
skatteområden som maskinläsbara regelfiler — beslutsträd där varje utfall
bär sina egna lagrum. Filerna ligger som öppna data i myndighetens
DCAT-katalog och kräver ingen autentisering.

Tre verktyg exponeras:

    ..._lista_omraden  Vilka regelområden som finns, med version och
                       giltighetstid. Måste anropas först — områdes-id går
                       inte att gissa (ARKITEKTUR.md §5 regel 7).
    ..._fragor         Regelområdets frågor och de svarsalternativ som
                       faktiskt förekommer i regelfilen.
    ...                Exekverar reglerna mot ett antal svar och returnerar
                       utfallet med lagrum.

Varför exekvering och inte fritext: samma skäl som beräkningsverktygen i
motor/berakningar.py (ARKITEKTUR.md §5 regel 2). Om modellen fick läsa
regelfilen och själv resonera fram utfallet vore slutsatsen modellens, inte
Skatteverkets, och den vore inte spårbar. Här avgör regelfilens egna villkor,
och Faktaposten bär det utfall myndigheten faktiskt skrivit.

Adaptern tolkar inte och räknar inte. Den matchar villkor på likhet. Över
samtliga 735 villkor i de tretton katalogförda filerna förekommer bara
operatorn "equal" och konjunktionen "all" (kontrollerat 2026-08-15) — allt
annat avvisas hellre än gissas, se _matchar().
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Any

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

# Nycklar i event.params som bär källhänvisningar i det gamla schemat.
# Skatteverket är inkonsekvent med versal och plural mellan filerna —
# samtliga varianter nedan är avlästa ur faktiska filer, inte gissade.
_KALLNYCKLAR_GAMMALT = ("Källor", "Källa", "källor", "källa")


def _forsta_meningen(text: str) -> str:
    """Första meningen ur regelfilens versionstext — giltighetstiden.

    Hela texten är ett stycke på flera hundra tecken som beskriver hela
    regelområdet. Den bärs av varje utfall och skulle upprepas per Faktapost
    i syntesunderlaget. Bara inledningen ("Denna regelfil tillämpas från och
    med beskattningsåret 2026.") är den uppgift som hör till utfallet; resten
    är områdesbeskrivning som fas B inte behöver för att formulera svaret.
    """
    hoptryckt = " ".join((text or "").split())
    if not hoptryckt:
        return ""
    punkt = hoptryckt.find(". ")
    return hoptryckt if punkt == -1 else hoptryckt[: punkt + 1]


class SkatteverketRattsligaReglerAdapter:
    """Exekverar Skatteverkets maskinläsbara regelfiler.

    Regelfilerna hämtas ur den kurerade katalogen i källregistret. Adaptern
    hårdkodar varken resurs-id eller bas-URL — se ARKITEKTUR.md §0.
    """

    def __init__(self) -> None:
        k = hamta("skatteverket_rattsligaregler")
        if not isinstance(k, Kalla):
            raise RuntimeError(
                "skatteverket_rattsligaregler-källan saknas eller är blockerad i registret."
            )
        self._kalla = k
        # Bara områden som besvarar sakfrågor. Versionsvalsfilerna avgör vilken
        # regelversion som gäller ett visst år och är inget användaren frågar om.
        self._omraden = [
            d for d in (k.dataset or []) if not d.get("versionsval_for")
        ]

    @property
    def id(self) -> str:
        return self._kalla.id

    # ------------------------------------------------------------------
    # Verktygsdefinitioner
    # ------------------------------------------------------------------

    def beskriv(self) -> list[dict[str, Any]]:
        omrades_id = [d["id"] for d in self._omraden]
        return [
            {
                "name": f"{self.id}_lista_omraden",
                "description": (
                    "Listar de skatteområden där Skatteverket publicerat sin "
                    "tolkning av gällande rätt som maskinläsbara regler "
                    "(representation, traktamente, logi, resekostnader, utlägg, "
                    "skattefria gåvor till anställda, uthyrning av privatbostad). "
                    "Anropa ALLTID detta först — områdes-id går inte att gissa. "
                    "Svaret innehåller regelversion och från vilket beskattningsår "
                    "varje regeluppsättning tillämpas."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sok": {
                            "type": "string",
                            "description": (
                                "Fritext som matchas mot områdets namn och "
                                "beskrivning, t.ex. 'julgåva' eller 'traktamente'."
                            ),
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": f"{self.id}_fragor",
                "description": (
                    "Hämtar de frågor ett regelområde ställer, med de "
                    "svarsalternativ som förekommer i regelfilen. Anropa detta "
                    "innan du exekverar reglerna — svaren måste ordagrant vara "
                    "ett av alternativen, annars matchar inget villkor."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "omrade": {
                            "type": "string",
                            "description": "Områdes-id från lista_omraden.",
                            "enum": omrades_id,
                        }
                    },
                    "required": ["omrade"],
                },
            },
            {
                "name": self.id,
                "description": (
                    "Exekverar Skatteverkets regler för ett område mot de svar du "
                    "anger, och returnerar utfallet med de lagrum utfallet vilar "
                    "på. Resonera ALDRIG själv fram utfallet ur regelfilen — det "
                    "här verktyget avgör, precis som beräkningsverktygen räknar. "
                    "Räcker inte svaren för att avgöra saken får du tillbaka vilka "
                    "frågor som återstår."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "omrade": {
                            "type": "string",
                            "description": "Områdes-id från lista_omraden.",
                            "enum": omrades_id,
                        },
                        "svar": {
                            "type": "object",
                            "description": (
                                "Svar per fråga: {\"frågans exakta text\": "
                                "\"svarsalternativet\"}. Både frågetext och svar "
                                "måste komma ordagrant från _fragor."
                            ),
                        },
                    },
                    "required": ["omrade", "svar"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Hämtning och normalisering av regelfiler
    # ------------------------------------------------------------------

    def _katalogpost(self, omrade_id: str) -> dict | None:
        return next((d for d in self._omraden if d.get("id") == omrade_id), None)

    def _fil_url(self, post: dict) -> str:
        return f"{self._kalla.bas_url}/{post['resurs']}"

    def _hamta_regelfil(self, post: dict) -> dict | None:
        """Hämtar och normaliserar en regelfil till {meta, fragor, regler}.

        De två schemana (se källregistret) skiljer sig i var frågorna, reglerna
        och källhänvisningarna sitter. Normaliseringen sker här så att
        exekveringen nedan bara ser en form.
        """
        url = self._fil_url(post)
        try:
            rad = hamta_json(self.id, "GET", url)
        except Exception:
            logger.warning(
                "%s: hämtning av regelfil %s (%s) misslyckades",
                self.id, post.get("id"), url, exc_info=True,
            )
            return None

        if not isinstance(rad, dict):
            logger.warning("%s: oväntat svarsformat för %s", self.id, post.get("id"))
            return None

        if "rulesets" in rad:
            return self._normalisera_gammalt(rad)
        return self._normalisera_nytt(rad)

    @staticmethod
    def _normalisera_nytt(rad: dict) -> dict:
        """Schema: meta[] / attributes[] / rules[] — källor som lista."""
        meta = {m.get("name"): m.get("value") for m in rad.get("meta") or []}
        regler = []
        for r in rad.get("rules") or []:
            handelse = r.get("event") or {}
            params = handelse.get("params") or {}
            kallor: list[str] = []
            extra: dict[str, str] = {}
            for res in params.get("results") or []:
                kallor.extend(res.get("sources") or [])
            for nyckel, varde in params.items():
                if nyckel != "results" and isinstance(varde, (str, int, float)):
                    extra[nyckel] = str(varde)
            regler.append({
                "villkor": (r.get("conditions") or {}),
                "utfall": handelse.get("type") or "",
                "kallor": kallor,
                "extra": extra,
            })
        return {
            "titel": meta.get("rulesArea") or meta.get("rulesetName") or "",
            "version": meta.get("rulesVersion") or "",
            "giltighet": meta.get("rulesVersionText") or "",
            "fragor": rad.get("attributes") or [],
            "regler": regler,
        }

    @staticmethod
    def _normalisera_gammalt(rad: dict) -> dict:
        """Schema: rulesArea / rulesVersion / rulesets[] — källor som sträng."""
        omrade = rad.get("rulesArea") or {}
        version = rad.get("rulesVersion") or {}
        fragor: list[dict] = []
        regler: list[dict] = []

        for uppsattning in rad.get("rulesets") or []:
            fragor.extend(uppsattning.get("attributes") or [])
            for beslut in uppsattning.get("decisions") or []:
                handelse = beslut.get("event") or {}
                params = handelse.get("params") or {}
                kallor: list[str] = []
                extra: dict[str, str] = {}
                for nyckel, varde in params.items():
                    if not isinstance(varde, (str, int, float)):
                        continue
                    text = str(varde)
                    # "Källor"/"Källa" bär en kommaseparerad lista med lagrum.
                    # Representation har dessutom per-utfall-nycklar som
                    # "…, tak" (belopp) och "…, källor" (lagrum för just det
                    # takbeloppet) — takbeloppen är fakta och ska med.
                    if nyckel in _KALLNYCKLAR_GAMMALT or nyckel.lower().endswith(
                        ("källor", "källa")
                    ):
                        kallor.extend(
                            del_.strip() for del_ in text.split(",") if del_.strip()
                        )
                    else:
                        extra[nyckel] = text
                regler.append({
                    "villkor": (beslut.get("conditions") or {}),
                    "utfall": handelse.get("type") or "",
                    "kallor": kallor,
                    "extra": extra,
                })

        return {
            "titel": omrade.get("name") or "",
            "version": version.get("name") or "",
            "giltighet": version.get("text") or "",
            "fragor": fragor,
            "regler": regler,
        }

    # ------------------------------------------------------------------
    # Regelexekvering
    # ------------------------------------------------------------------

    @staticmethod
    def _jamfor(a: Any, b: Any) -> bool:
        """Likhet mellan ett användarsvar och ett regelvärde.

        Skiftläge och blanktecken normaliseras — svaren är alternativ ur samma
        fil, och en versal ska inte avgöra ett skattebesked. Att även INRE
        blanktecken kollapsas är nödvändigt, inte kosmetiskt: några alternativ
        bär radbrytning mitt i sig ("Efter en längre tids anställning\n\n(minst
        20 år)"), och _lista_fragor presenterar dem på en rad. Utan den här
        normaliseringen matchade det svar modellen fick tillbaka aldrig sitt
        eget villkor. Ingen annan omtolkning sker.
        """
        return " ".join(str(a).split()).casefold() == " ".join(str(b).split()).casefold()

    @classmethod
    def _matchar(cls, villkor: dict, svar: dict) -> tuple[bool, list[str]]:
        """Prövar en regels villkor mot svaren.

        Returnerar (matchar, saknade_fragor). Matchar är False så snart ett
        villkor motsägs av ett givet svar. Saknas svar på ett villkor kan
        regeln varken bekräftas eller uteslutas — den räknas som obesvarad
        och frågan rapporteras tillbaka.

        Bara conditions.all och operatorn "equal" stöds. Skulle Skatteverket
        införa "any" eller en jämförelseoperator vägrar adaptern hellre än
        gissar — ett feltolkat villkor ger ett tyst felaktigt skattebesked.
        """
        okanda = set(villkor.keys()) - {"all"}
        if okanda:
            raise ValueError(
                f"Regelfilen använder villkorstypen {sorted(okanda)!r} som "
                "adaptern inte stöder. Utfallet vore en gissning."
            )

        saknade: list[str] = []
        for delvillkor in villkor.get("all") or []:
            operator = delvillkor.get("operator")
            if operator != "equal":
                raise ValueError(
                    f"Regelfilen använder operatorn {operator!r} som adaptern "
                    "inte stöder. Utfallet vore en gissning."
                )
            fraga = delvillkor.get("fact")
            if fraga not in svar:
                saknade.append(fraga)
                continue
            if not cls._jamfor(svar[fraga], delvillkor.get("value")):
                return False, []

        return (not saknade), saknade

    # ------------------------------------------------------------------
    # Verktygen
    # ------------------------------------------------------------------

    def _lista_omraden(self, sok: str | None) -> list[Faktautkast]:
        poster = self._omraden
        if sok:
            nal = sok.casefold()
            poster = [
                d for d in poster
                if nal in str(d.get("namn", "")).casefold()
                or nal in str(d.get("beskrivning", "")).casefold()
            ]
        if not poster:
            logger.info("%s: inget regelområde matchade sok=%r", self.id, sok)
            return []

        rader = [
            f"{d['id']} — {d.get('namn', '?')} (regelversion {d.get('regelversion', '?')}): "
            f"{' '.join(str(d.get('beskrivning', '')).split())}"
            for d in poster
        ]
        return [Faktautkast(
            etikett=(
                f"Skatteverkets regelområden som matchar {sok!r}" if sok
                else "Skatteverkets publicerade regelområden"
            ),
            varde=" | ".join(rader),
            kalla_id=self.id,
            myndighet=self._kalla.myndighet or "Skatteverket",
            licens=self._kalla.licens,
            attribution=self._kalla.attribution,
            lank_manniska=self._kalla.manniskolank_mall or self._kalla.bas_url,
            lank_maskin=self._kalla.bas_url,
            hamtad=datetime.now(UTC),
        )]

    def _lista_fragor(self, omrade_id: str) -> list[Faktautkast]:
        post = self._katalogpost(omrade_id)
        if not post:
            logger.info("%s: okänt regelområde %r", self.id, omrade_id)
            return []

        fil = self._hamta_regelfil(post)
        if not fil:
            return []

        # Svarsalternativen står inte i attributes — de framgår av vilka värden
        # reglerna faktiskt prövar. Utan dem gissar modellen formuleringen och
        # inget villkor matchar.
        alternativ: dict[str, list[str]] = {}
        for regel in fil["regler"]:
            for delvillkor in regel["villkor"].get("all") or []:
                fraga = delvillkor.get("fact")
                varde = delvillkor.get("value")
                if fraga is None or varde is None:
                    continue
                lista = alternativ.setdefault(fraga, [])
                if varde not in lista:
                    lista.append(varde)

        # Alternativen citeras ett och ett. Skatteverket har alternativ som
        # själva innehåller separatortecken — "Affärsförhandling / Personalfest"
        # är ETT alternativ i representationsfilen, och några bär radbrytning.
        # Utan citattecken går gränsen mellan alternativen inte att se, och ett
        # svar som modellen delar på egen hand matchar inget villkor.
        rader = [
            f"{f.get('name')} → "
            + " | ".join(
                '"' + " ".join(str(v).split()) + '"'
                for v in alternativ.get(f.get("name"), [])
            )
            for f in fil["fragor"]
        ]
        if not rader:
            logger.info("%s: regelfil %s saknar frågor", self.id, omrade_id)
            return []

        url = self._fil_url(post)
        return [Faktautkast(
            etikett=f"Frågor i Skatteverkets regler för {post.get('namn', omrade_id)}",
            # Radbrytning mellan frågorna — " | " skiljer alternativen inom en
            # fråga och kan inte också skilja frågorna åt.
            varde="\n".join(rader),
            kalla_id=self.id,
            myndighet=self._kalla.myndighet or "Skatteverket",
            licens=self._kalla.licens,
            attribution=self._kalla.attribution,
            dataset=post["resurs"],
            dimensioner={
                "regelomrade": omrade_id,
                "regelversion": fil["version"] or post.get("regelversion", ""),
            },
            lank_manniska=self._kalla.manniskolank_mall or url,
            lank_maskin=url,
            hamtad=datetime.now(UTC),
        )]

    def _exekvera(self, omrade_id: str, svar: dict) -> list[Faktautkast]:
        post = self._katalogpost(omrade_id)
        if not post:
            logger.info("%s: okänt regelområde %r", self.id, omrade_id)
            return []
        if not isinstance(svar, dict) or not svar:
            logger.info("%s: exekvering utan svar för %s", self.id, omrade_id)
            return []

        fil = self._hamta_regelfil(post)
        if not fil:
            return []

        url = self._fil_url(post)
        traffar: list[dict] = []
        saknade: list[str] = []
        try:
            for regel in fil["regler"]:
                matchar, regel_saknade = self._matchar(regel["villkor"], svar)
                if matchar:
                    traffar.append(regel)
                for fraga in regel_saknade:
                    if fraga not in saknade:
                        saknade.append(fraga)
        except ValueError:
            # Ett villkor adaptern inte kan tolka. Ett halvt exekverat
            # regelträd är farligare än inget svar alls.
            logger.warning(
                "%s: regelfil %s innehåller villkor adaptern inte stöder",
                self.id, omrade_id, exc_info=True,
            )
            return []

        gemensamt = {
            "kalla_id": self.id,
            "myndighet": self._kalla.myndighet or "Skatteverket",
            "licens": self._kalla.licens,
            "attribution": self._kalla.attribution,
            "dataset": post["resurs"],
            "lank_manniska": self._kalla.manniskolank_mall or url,
            "lank_maskin": url,
            "hamtad": datetime.now(UTC),
        }
        version = fil["version"] or post.get("regelversion", "")

        if not traffar:
            # Ingen regel matchade. Att svara "ingen skatteeffekt" här vore att
            # hitta på — skillnaden mellan "reglerna säger nej" och "reglerna
            # räckte inte till" måste synas i svaret.
            if saknade:
                varde = (
                    "Reglerna kan inte avgöra frågan med de svar som angetts. "
                    "Obesvarade frågor: " + "; ".join(saknade)
                )
            else:
                varde = (
                    "Inget utfall i regelfilen matchar de angivna svaren. "
                    "Kontrollera att svaren är ordagranna alternativ ur regelfilen."
                )
            return [Faktautkast(
                etikett=(
                    f"Skatteverkets regler för {post.get('namn', omrade_id)} — "
                    "inget avgörande utfall"
                ),
                varde=varde,
                dimensioner={"regelomrade": omrade_id, "regelversion": version},
                **gemensamt,
            )]

        utkast: list[Faktautkast] = []
        for regel in traffar:
            dimensioner: dict[str, str] = {
                "regelomrade": omrade_id,
                "regelversion": version,
            }
            # Svaren som ledde fram till utfallet. Utan dem går utfallet inte
            # att granska i källpanelen — "skattefri gåva" utan förutsättningar
            # är inte ett kontrollerbart påstående.
            for delvillkor in regel["villkor"].get("all") or []:
                fraga = delvillkor.get("fact")
                if fraga in svar:
                    dimensioner[str(fraga)] = str(svar[fraga])
            dimensioner.update(regel["extra"])
            if regel["kallor"]:
                dimensioner["lagrum"] = ", ".join(regel["kallor"])
            giltighet = _forsta_meningen(fil["giltighet"])
            if giltighet:
                dimensioner["giltighet"] = giltighet

            utkast.append(Faktautkast(
                etikett=(
                    f"Skatteverkets tolkning — {post.get('namn', omrade_id)}"
                ),
                varde=regel["utfall"],
                dimensioner=dimensioner,
                **gemensamt,
            ))

        return utkast

    # ------------------------------------------------------------------

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        verktyg = plan.extra.get("verktyg")

        if verktyg == f"{self.id}_lista_omraden":
            return self._lista_omraden(plan.extra.get("sok"))

        if verktyg == f"{self.id}_fragor":
            return self._lista_fragor(plan.extra.get("omrade") or "")

        return self._exekvera(
            plan.extra.get("omrade") or "",
            plan.extra.get("svar") or {},
        )

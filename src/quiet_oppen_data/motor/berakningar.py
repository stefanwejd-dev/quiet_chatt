"""Beräkningsmodul — deterministiska funktioner över redan hämtade Faktaposter.

Se ARKITEKTUR.md §5 regel 2: **modellen räknar aldrig**. Ett självsäkert
felaktigt tal är värre än inget tal, och en modell som multiplicerar två
hämtade värden själv har flera sätt att göra det fel (fel skala, fel
avrundning, fel tecken). Här ligger de fyra räknesätt boten behöver som
ren, testad kod. Modellen väljer VILKA Faktaposter som ska kombineras och
med VILKEN funktion — den utför aldrig själva räkningen.

Varje funktion:
  * tar den redan populerade Faktaregistret plus F-id på ingångarna,
  * kontrollerar att enheterna är jämförbara (kastar `ValueError` annars —
    att lägga ihop procent och kronor är inte ett fel att gissa sig förbi),
  * returnerar en ny, redan registrerad `Faktapost` med `harledd=True` och
    `harledd_av` satt till ingångarnas F-id.

Precis som adaptrar är `Faktaregister` den enda vägen in för en ny
Faktapost — även en härledd. En funktion här konstruerar aldrig `Faktapost`
direkt; den bygger ett `Faktautkast` och lämnar det till
`register.registrera_utkast()`, som upprätthåller samma länkkrav som gäller
för hämtade fakta (se modeller.py och ARKITEKTUR.md §3.4).

`lank_maskin` på en härledd post kan inte vara ett API-anrop — det finns
inget att curla. Den bär i stället beräkningens spårbara formel (vilka F-id,
vilka värden, vilken funktion), så att uppgiften även här går att kontrollera
mot något konkret. `lank_manniska` ärvs från den första ingångens källa,
enligt PLAN.md §12.
"""

from __future__ import annotations

import logging
from typing import Any

from quiet_oppen_data.modeller import Faktapost, Faktaregister, Faktautkast

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------

def _hamta_eller_kasta(register: Faktaregister, fid: str) -> Faktapost:
    post = register.hamta(fid)
    if post is None:
        raise ValueError(f"Faktapost '{fid}' finns inte i registret.")
    return post


def _tal(post: Faktapost) -> float:
    try:
        return float(post.varde)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Faktapost '{post.id}' har ett värde ('{post.varde}') som inte går att "
            "räkna med."
        ) from exc


def _kontrollera_samma_enhet(a: Faktapost, b: Faktapost) -> None:
    if a.enhet != b.enhet:
        raise ValueError(
            f"Kan inte kombinera '{a.id}' ({a.enhet or 'utan enhet'}) och "
            f"'{b.id}' ({b.enhet or 'utan enhet'}) — olika enheter."
        )


def _period_intervall(*poster: Faktapost) -> str | None:
    perioder = [p.period for p in poster if p.period]
    if not perioder:
        return None
    return " → ".join(perioder)


def _registrera(
    register: Faktaregister,
    *,
    etikett: str,
    varde: float,
    enhet: str | None,
    period: str | None,
    forsta: Faktapost,
    harledd_av: tuple[str, ...],
    formel: str,
) -> Faktapost:
    """Bygger utkastet gemensamt för alla fyra funktionerna och registrerar det.

    `lank_manniska` och `myndighet`/`licens`/`attribution` ärvs från den
    första ingången (PLAN.md §12: "vars lank_manniska pekar på den första
    ingångens källa"). `lank_maskin` bär beräkningens formel i stället för
    ett API-anrop — se moduldocstringen.
    """
    utkast = Faktautkast(
        etikett=etikett,
        varde=_formatera(varde),
        enhet=enhet,
        period=period,
        kalla_id=forsta.kalla_id,
        myndighet=forsta.myndighet,
        licens=forsta.licens,
        attribution=forsta.attribution,
        lank_manniska=forsta.lank_manniska,
        lank_maskin=f"beräkning: {formel}",
        harledd=True,
        harledd_av=harledd_av,
    )
    return register.registrera_utkast(utkast)


def _formatera(varde: float) -> str:
    # Trimma flyttalsbrus men behåll heltal utan onödiga decimaler.
    if varde == int(varde):
        return str(int(varde))
    return f"{varde:.6f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# De fyra räknesätten
# ---------------------------------------------------------------------------

def differens(register: Faktaregister, id_forsta: str, id_andra: str) -> Faktapost:
    """Differens: värde(forsta) − värde(andra). Kräver samma enhet."""
    forsta = _hamta_eller_kasta(register, id_forsta)
    andra = _hamta_eller_kasta(register, id_andra)
    _kontrollera_samma_enhet(forsta, andra)

    resultat = _tal(forsta) - _tal(andra)

    return _registrera(
        register,
        etikett=f"Differens: {forsta.etikett} − {andra.etikett}",
        varde=resultat,
        enhet=forsta.enhet,
        period=_period_intervall(forsta, andra),
        forsta=forsta,
        harledd_av=(id_forsta, id_andra),
        formel=f"{forsta.id}({forsta.varde}) − {andra.id}({andra.varde}) = {_formatera(resultat)}",
    )


def procentuell_forandring(register: Faktaregister, id_forsta: str, id_andra: str) -> Faktapost:
    """Procentuell förändring från forsta till andra: (andra − forsta) / forsta · 100.

    Kräver samma enhet på ingångarna (det är själva värdena, inte procenten,
    som måste vara jämförbara).
    """
    forsta = _hamta_eller_kasta(register, id_forsta)
    andra = _hamta_eller_kasta(register, id_andra)
    _kontrollera_samma_enhet(forsta, andra)

    v1 = _tal(forsta)
    v2 = _tal(andra)
    if v1 == 0:
        raise ZeroDivisionError(
            f"Kan inte beräkna procentuell förändring — '{forsta.id}' har värdet 0."
        )
    resultat = (v2 - v1) / v1 * 100

    return _registrera(
        register,
        etikett=f"Procentuell förändring: {forsta.etikett} → {andra.etikett}",
        varde=resultat,
        enhet="procent",
        period=_period_intervall(forsta, andra),
        forsta=forsta,
        harledd_av=(id_forsta, id_andra),
        formel=(
            f"({andra.id}({andra.varde}) − {forsta.id}({forsta.varde})) / "
            f"{forsta.id}({forsta.varde}) × 100 = {_formatera(resultat)}"
        ),
    )


def kvot(register: Faktaregister, id_taljare: str, id_namnare: str) -> Faktapost:
    """Kvot: värde(täljare) / värde(nämnare).

    Till skillnad från differens och procentuell förändring FÅR enheterna
    skilja sig — det är hela poängen med en kvot (t.ex. kronor per invånare).
    Enheten på resultatet blir "täljarenhet/nämnarenhet", eller ingen enhet
    om båda saknar enhet eller är identiska (kvoten är då dimensionslös).
    """
    taljare = _hamta_eller_kasta(register, id_taljare)
    namnare = _hamta_eller_kasta(register, id_namnare)

    n = _tal(namnare)
    if n == 0:
        raise ZeroDivisionError(f"Kan inte dela med '{namnare.id}' — värdet är 0.")
    resultat = _tal(taljare) / n

    if taljare.enhet == namnare.enhet:
        enhet = None
    elif taljare.enhet and namnare.enhet:
        enhet = f"{taljare.enhet} per {namnare.enhet}"
    else:
        enhet = None

    return _registrera(
        register,
        etikett=f"Kvot: {taljare.etikett} / {namnare.etikett}",
        varde=resultat,
        enhet=enhet,
        period=_period_intervall(taljare, namnare),
        forsta=taljare,
        harledd_av=(id_taljare, id_namnare),
        formel=f"{taljare.id}({taljare.varde}) / {namnare.id}({namnare.varde}) = {_formatera(resultat)}",
    )


def indexupprakning(
    register: Faktaregister, id_belopp: str, id_index_bas: str, id_index_ny: str
) -> Faktapost:
    """Räknar upp ett belopp med förändringen mellan två indexobservationer.

    nytt_belopp = belopp × (index_ny / index_bas)

    De två indexobservationerna måste ha samma enhet (de måste vara ur
    samma indexserie — annars är kvoten mellan dem meningslös). Beloppet
    som räknas upp behöver INTE ha samma enhet som indexet.
    """
    belopp = _hamta_eller_kasta(register, id_belopp)
    index_bas = _hamta_eller_kasta(register, id_index_bas)
    index_ny = _hamta_eller_kasta(register, id_index_ny)
    _kontrollera_samma_enhet(index_bas, index_ny)

    bas = _tal(index_bas)
    if bas == 0:
        raise ZeroDivisionError(
            f"Kan inte indexräkna upp — basindexet '{index_bas.id}' är 0."
        )
    resultat = _tal(belopp) * (_tal(index_ny) / bas)

    return _registrera(
        register,
        etikett=f"{belopp.etikett}, uppräknat med {index_ny.etikett}",
        varde=resultat,
        enhet=belopp.enhet,
        period=index_ny.period or belopp.period,
        forsta=belopp,
        harledd_av=(id_belopp, id_index_bas, id_index_ny),
        formel=(
            f"{belopp.id}({belopp.varde}) × ({index_ny.id}({index_ny.varde}) / "
            f"{index_bas.id}({index_bas.varde})) = {_formatera(resultat)}"
        ),
    )


# ---------------------------------------------------------------------------
# Verktygsexponering för fas A — se ARKITEKTUR.md §5 regel 2
# ---------------------------------------------------------------------------
#
# Samma mönster som adaptrarnas beskriv(), men verktygen körs direkt mot
# Faktaregistret (redan hämtade Faktaposter) i stället för mot en extern
# källa. Dispatchen i motor/hamtning.py känner igen namnen nedan och
# anropar kor_verktyg() i stället för en adapters hamta().

VERKTYGSSPECAR: list[dict[str, Any]] = [
    {
        "name": "berakna_differens",
        "description": (
            "Beräknar differensen (A − B) mellan två redan hämtade Faktaposter. "
            "Använd detta i stället för att räkna själv i svaret — modellen får "
            "aldrig subtrahera egenhändigt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_forsta": {"type": "string", "description": "F-id för A, t.ex. F1"},
                "id_andra": {"type": "string", "description": "F-id för B, t.ex. F2"},
            },
            "required": ["id_forsta", "id_andra"],
        },
    },
    {
        "name": "berakna_indexupprakning",
        "description": (
            "Räknar upp ett belopp med förändringen mellan två indexobservationer "
            "(nytt belopp = belopp × index_ny / index_bas). Använd detta i stället "
            "för att räkna själv."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_belopp": {"type": "string", "description": "F-id för beloppet som ska räknas upp"},
                "id_index_bas": {"type": "string", "description": "F-id för indexets basvärde"},
                "id_index_ny": {"type": "string", "description": "F-id för indexets nya värde"},
            },
            "required": ["id_belopp", "id_index_bas", "id_index_ny"],
        },
    },
    {
        "name": "berakna_kvot",
        "description": (
            "Beräknar kvoten (täljare / nämnare) mellan två redan hämtade Faktaposter, "
            "t.ex. kronor per invånare. Använd detta i stället för att räkna själv."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_taljare": {"type": "string", "description": "F-id för täljaren"},
                "id_namnare": {"type": "string", "description": "F-id för nämnaren"},
            },
            "required": ["id_taljare", "id_namnare"],
        },
    },
    {
        "name": "berakna_procentuell_forandring",
        "description": (
            "Beräknar procentuell förändring från en Faktapost till en annan "
            "((B − A) / A × 100). Använd detta i stället för att räkna själv."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id_forsta": {"type": "string", "description": "F-id för utgångsvärdet A"},
                "id_andra": {"type": "string", "description": "F-id för slutvärdet B"},
            },
            "required": ["id_forsta", "id_andra"],
        },
    },
]

VERKTYGSNAMN: frozenset[str] = frozenset(spec["name"] for spec in VERKTYGSSPECAR)

_DISPATCH = {
    "berakna_differens": lambda register, indata: differens(
        register, indata["id_forsta"], indata["id_andra"]
    ),
    "berakna_procentuell_forandring": lambda register, indata: procentuell_forandring(
        register, indata["id_forsta"], indata["id_andra"]
    ),
    "berakna_kvot": lambda register, indata: kvot(
        register, indata["id_taljare"], indata["id_namnare"]
    ),
    "berakna_indexupprakning": lambda register, indata: indexupprakning(
        register, indata["id_belopp"], indata["id_index_bas"], indata["id_index_ny"]
    ),
}


def kor_verktyg(verktygsnamn: str, register: Faktaregister, indata: dict[str, Any]) -> Faktapost:
    """Dispatchar ett beräkningsverktygsanrop från fas A till rätt funktion.

    Kastar KeyError om verktygsnamnet är okänt — anroparen (motor/hamtning.py)
    ansvarar för att bara skicka namn ur VERKTYGSNAMN hit.
    """
    fn = _DISPATCH.get(verktygsnamn)
    if fn is None:
        raise KeyError(f"Okänt beräkningsverktyg: {verktygsnamn}")
    return fn(register, indata)

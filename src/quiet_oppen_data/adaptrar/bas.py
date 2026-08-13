from typing import Any, Protocol

from quiet_oppen_data.modeller import Faktautkast, Fragplan

VerktygsSpec = dict[str, Any]


class Adapter(Protocol):
    """Protokoll för alla käll-adaptrar."""

    @property
    def id(self) -> str:
        """Adapterns unika id (ska matcha källans id i registret)."""
        ...

    def beskriv(self) -> list[VerktygsSpec]:
        """Returnerar verktygsdefinitioner för Anthropic API.

        En adapter kan exponera flera verktyg (t.ex. hamta_data och
        lista_dimensioner).
        """
        ...

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        """Utför hämtningen mot källan och returnerar utkast.

        Adaptrar returnerar Faktautkast, aldrig Faktapost. Bara
        Faktaregister får mynta F-id och bara registret kontrollerar att
        båda länkarna finns — se ARKITEKTUR.md §3.4 och docstringen på
        Faktautkast. Motorn anropar Faktaregister.registrera_alla() på
        resultatet.

        Vid tomt resultat returneras en tom lista. En adapter fabricerar
        aldrig ett utkast för att fylla ut, och rapporterar aldrig ett fel
        som om det vore ett faktum — fel loggas och ger tom lista.
        """
        ...

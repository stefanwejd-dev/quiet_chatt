from typing import Any, Protocol

from quiet_oppen_data.modeller import Faktapost, Fragplan

VerktygsSpec = dict[str, Any]

class Adapter(Protocol):
    """Protokoll för alla käll-adaptrar."""
    
    @property
    def id(self) -> str:
        """Adapterns unika id (ska matcha källans id i registret)."""
        ...
        
    def beskriv(self) -> list[VerktygsSpec]:
        """Returnerar verktygsdefinitioner för Anthropic API.
        
        En adapter kan exponera ett eller flera verktyg (t.ex. hamta_data och lista_dimensioner).
        """
        ...
        
    def hamta(self, plan: Fragplan) -> list[Faktapost]:
        """Utför sökningen mot källan utifrån planen och returnerar faktaposter.
        
        Eftersom Faktapost kräver ett 'id' (som normalt sätts av Faktaregister)
        bör adaptern sätta id='' eller liknande. Motor/Agent kommer att 
        packa upp och registrera posten i sessionens Faktaregister för att få rätt id.
        """
        ...

"""Simple Selector mit 3-Stufen-Fallback: data-* → Text/Role → CSS."""
from playwright.async_api import Page, Locator


async def find_element(page: Page, query: str, timeout: float = 5000) -> Locator:
    """
    3-Stufen-Fallback für robuste Element-Lokalisierung.
    
    Stufe 1: data-testid, data-qa, aria-label Attribute
    Stufe 2: Playwright Role + Text oder get_by_text
    Stufe 3: CSS/ID als letzter Ausweg
    
    Args:
        page: Playwright Page Objekt
        query: Suchbegriff (Text, ID, oder CSS Selector)
        timeout: Timeout in ms
        
    Returns:
        Locator des gefundenen Elements
        
    Raises:
        TimeoutError: Wenn kein Element gefunden wird
    """
    # Stufe 1: data-* Attribute und aria-label
    for attr in ["data-testid", "data-qa", "aria-label"]:
        loc = page.locator(f"[{attr}='{query}']")
        if await loc.count() > 0:
            return loc.first
    
    # Stufe 2: Playwright Role + Text (menschlich & robust)
    # Suche nach Buttons mit dem Text
    loc = page.get_by_role("button", name=query, exact=False)
    if await loc.count() > 0:
        return loc.first
    
    # Suche nach Links mit dem Text
    loc = page.get_by_role("link", name=query, exact=False)
    if await loc.count() > 0:
        return loc.first
    
    # Suche nach beliebigem Text
    loc = page.get_by_text(query, exact=False)
    if await loc.count() > 0:
        return loc.first
    
    # Suche nach Input Feldern mit Placeholder
    loc = page.get_by_placeholder(query)
    if await loc.count() > 0:
        return loc.first
    
    # Stufe 3: Fallback CSS/ID (wenn query wie ein Selector aussieht)
    if query.startswith("#") or query.startswith(".") or " " in query:
        loc = page.locator(query)
        if await loc.count() > 0:
            return loc.first
    
    # Letzter Versuch: exakte Textsuche
    loc = page.get_by_text(query, exact=True)
    if await loc.count() > 0:
        return loc.first
    
    raise TimeoutError(f"Element nicht gefunden: '{query}'")


async def safe_click(page: Page, query: str, timeout: float = 5000) -> bool:
    """
    Sicherer Klick mit automatischem Fallback.
    
    Returns:
        True wenn erfolgreich, False wenn fehlgeschlagen
    """
    try:
        el = await find_element(page, query, timeout)
        await el.click(timeout=timeout)
        return True
    except Exception as e:
        print(f"⚠️  Klick fehlgeschlagen für '{query}': {e}")
        return False


async def safe_fill(page: Page, query: str, value: str, timeout: float = 5000) -> bool:
    """
    Sicheres Ausfüllen eines Input-Felds.
    
    Returns:
        True wenn erfolgreich, False wenn fehlgeschlagen
    """
    try:
        el = await find_element(page, query, timeout)
        await el.fill(value, timeout=timeout)
        return True
    except Exception as e:
        print(f"⚠️  Fill fehlgeschlagen für '{query}': {e}")
        return False

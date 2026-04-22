"""HeyPiggy Dashboard Plugin."""
import re
from playwright.async_api import Page
from typing import Dict, List, Any
from .base_dashboard import BaseDashboardPlugin
from ..human_behavior import human_click, human_type
from ..smart_actions import SmartClickAction, SmartTypeAction
from ..telemetry import log_event

class HeyPiggyDashboard(BaseDashboardPlugin):
    """Dashboard plugin for HeyPiggy platform."""
    
    async def login(self, page: Page, email: str, password: str) -> bool:
        try:
            # Email field
            email_action = SmartTypeAction(target="input[name='email'], input[type='email'], #email", value=email)
            await email_action.execute(page, None)
            
            # Password field  
            pass_action = SmartTypeAction(target="input[name='password'], input[type='password'], #password", value=password)
            await pass_action.execute(page, None)
            
            # Submit button
            click_action = SmartClickAction(target="button[type='submit'], .login-btn, #login-submit, input[type='submit']")
            await click_action.execute(page, None)
            
            await page.wait_for_load_state("networkidle")
            
            # Check if dashboard loaded
            dashboard_indicators = [".dashboard", ".survey-list", ".balance", ".user-menu", ".overview"]
            for indicator in dashboard_indicators:
                if await page.is_visible(indicator):
                    log_event("auth", "login_success", platform="heypiggy_dashboard")
                    return True
            
            log_event("auth", "login_failed", platform="heypiggy_dashboard", error_code="dashboard_elements_missing")
            return False
        except Exception as e:
            log_event("auth", "login_failed", platform="heypiggy_dashboard", error_code=str(e))
            return False
    
    async def scan_surveys(self, page: Page) -> List[Dict[str, Any]]:
        js_scan = """
        () => {
            const items = [];
            const cards = document.querySelectorAll('.survey-card, .survey-item, [class*="survey"], .task-card, .offer-item, .study-card');
            cards.forEach(card => {
                const titleEl = card.querySelector('h3, h4, .title, [class*="title"], .name');
                const rewardEl = card.querySelector('.reward, .points, .price, .payout, [class*="reward"]');
                const timeEl = card.querySelector('.time, .duration, .minutes, .length, [class*="time"]');
                const btnEl = card.querySelector('button, a, .start-btn, .join-btn, [role="button"]');

                if (titleEl && btnEl) {
                    items.push({
                        id: card.id || btnEl.getAttribute('data-id') || btnEl.href || Math.random().toString(36).slice(2, 8),
                        title: titleEl.innerText.trim().slice(0, 120),
                        reward: rewardEl ? rewardEl.innerText.trim() : 'unknown',
                        duration: timeEl ? timeEl.innerText.trim() : 'unknown',
                        selector: btnEl.className.split(' ').slice(0, 3).join('.'),
                        href: btnEl.href || null
                    });
                }
            });
            return items;
        }
        """
        try:
            surveys = await page.evaluate(js_scan)
            log_event("scan", "surveys_found", platform="heypiggy_dashboard", metadata={"count": len(surveys)})
            return surveys
        except Exception:
            return []
    
    async def select_survey(self, page: Page, survey_id: str) -> bool:
        try:
            selector = f"a[href*='{survey_id}'], button[data-id*='{survey_id}'], .start-btn, .join-btn"
            click_action = SmartClickAction(target=selector)
            await click_action.execute(page, None)
            await page.wait_for_load_state("domcontentloaded")
            log_event("select", "survey_clicked", platform="heypiggy_dashboard", metadata={"id": survey_id})
            return True
        except Exception:
            return False
    
    async def handle_screening_gate(self, page: Page, max_steps: int = 3) -> Dict[str, Any]:
        disq_patterns = [
            r"(leider nicht|nicht qualifiziert|disqualified|screened out|you do not qualify|umfrage voll|no longer available)",
            r"(vielen dank.*interesse|thank you.*interest|zurück zum dashboard|back to dashboard|survey closed)"
        ]
        
        for step in range(1, max_steps + 1):
            content = await page.content()
            if any(re.search(p, content, re.I) for p in disq_patterns):
                log_event("screening", "disqualified", platform="heypiggy_dashboard")
                try:
                    click_action = SmartClickAction(target="zurück, back, dashboard, übersicht, home")
                    await click_action.execute(page, None)
                except Exception:
                    pass
                return {"status": "disqualified", "step": step}
            
            try:
                click_action = SmartClickAction(target="weiter, next, continue, starten, start survey")
                await click_action.execute(page, None)
            except Exception:
                break
            await page.wait_for_load_state("networkidle")
        
        return {"status": "passed", "step": max_steps}
    
    async def get_account_status(self, page: Page) -> Dict[str, Any]:
        try:
            js_status = """
            () => {
                const bal = document.querySelector('.balance, .points, .wallet, [class*="balance"], .guthaben')?.innerText || '0';
                const pend = document.querySelector('.pending, .ausstehend, [class*="pending"], .offen')?.innerText || '0';
                return { balance: bal.trim(), pending: pend.trim() };
            }
            """
            return await page.evaluate(js_status)
        except Exception:
            return {"balance": "unknown", "pending": "unknown"}

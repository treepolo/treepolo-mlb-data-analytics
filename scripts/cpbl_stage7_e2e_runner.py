from __future__ import annotations

import cpbl_stage7_e2e as base


async def activate(page, panel: str) -> None:
    """Activate a workspace panel using the current Playwright async API.

    Playwright 1.62 makes the argument to wait_for_function keyword-only. The
    original Stage 7 harness passed it positionally, so every browser case
    failed before exercising the UI. Keep the product-facing E2E logic intact
    and replace only this compatibility shim.
    """
    nav = page.locator(f'button.nav-item[data-panel="{panel}"]')
    if await nav.count():
        await nav.first.click()
    else:
        await page.evaluate(
            "panel => window.treepoloPanels.activate(panel,{updateUrl:false,source:'stage7'})",
            panel,
        )
    await page.wait_for_function(
        "panel => document.getElementById(panel)?.classList.contains('active-panel')",
        arg=panel,
    )


def main() -> None:
    base.activate = activate
    base.main()


if __name__ == "__main__":
    main()

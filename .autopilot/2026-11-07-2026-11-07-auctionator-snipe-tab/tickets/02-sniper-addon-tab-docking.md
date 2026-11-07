# 02 — Регистрация вкладки Snipe и Auctionator UI докинг

**Требования:** R01, R03, R07, R08, R13
**Зона:** `addons/ForeverTrade_Sniper/`
**Волна:** 2
**Зависит от:** 01
**Итог:** Внизу AuctionFrame добавлена вкладка Snipe, открывающая панель снайпера в стиле Auctionator.

## Что надо сделать
1. В `addons/ForeverTrade_Sniper/ForeverTrade_Sniper.lua`:
   - Назвать вкладку строго `Snipe` (вместо прежнего длинного названия).
   - Зарегистрировать вкладку на `AuctionFrame` рядом с вкладками Auctionator (`SetPoint("LEFT", prevTab, "RIGHT", -15, 0)`).
   - Реализовать точный докинг главной панели `FTSniperMainFrame` внутрь `AuctionFrame`:
     - Границы: `TOPLEFT: 18, -68`, `BOTTOMRIGHT: -18, 36` (соответствует рабочей области Auctionator).
     - Скрытие внешних рамок перетаскивания и кнопки закрытия при докинге.
   - Скрыть нативные фреймы аукциона (`AuctionFrameBrowse`, `AuctionFrameBid`, `AuctionFrameAuctions`) и фреймы Auctionator (`AuctionatorShoppingFrame`, `AuctionatorSellingFrame`, `AuctionatorCancellingFrame`) при выборе вкладки `Snipe`.
   - Гарантировать скрытие панели `Snipe` при клике на любые другие вкладки (`AuctionFrameTab_OnClick` хук).
2. Оформить фоновую подложку, разметку и заголовки таблицы в едином темном стиле интерфейса Auctionator.

## Критерии приёмки
- Вкладка называется `Snipe` и отображается на нижней панели `AuctionFrame`.
- При клике на `Snipe` отображается панель снайпера, а другие вкладки скрываются.
- При клике на другие вкладки панель снайпера скрывается.

"""i18n (Option B): RU/EN string dictionaries + access function.

The default language comes from .env (DEFAULT_LANGUAGE); a user's personal
choice is stored in users.language and set by the middleware
(UsersMiddleware) via a context variable. Background loops (digest,
alerts) read the recipient's language from the DB.

Usage in handlers:
    from src.i18n import t
    await message.answer(t("portfolio.empty"))
    await message.answer(t("fx.format", name=..., code=..., rate=...))
"""

from contextvars import ContextVar
from typing import Any

from src.config.settings import get_settings

SUPPORTED_LANGUAGES = ("ru", "en")
DEFAULT_LANGUAGE = "ru"  # fallback if DEFAULT_LANGUAGE is not set in .env

_lang: ContextVar[str] = ContextVar("lang", default="")

# ──────────────────────────────────────────────────────────────── словари

_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        # start / help
        "start.help_text": (
            "🤖 <b>Как пользоваться ботом</b>\n\n"
            "Нажимай кнопки меню или пиши команды:\n"
            "• /rate <b>USD</b> — курс валюты (USD, EUR, CNY...)\n"
            "• /convert <b>100 USD RUB</b> — конвертер валют\n"
            "• /stock <b>AAPL</b> — цена акции (SPX, DJI — индексы)\n"
            "• /crypto <b>BTC</b> — цена криптовалюты\n"
            "• /chart <b>BTC</b> — график цены за 30 дней\n"
            "• /trending — топ трендовых монет\n"
            "• /top — топ монет по капитализации\n"
            "• /news <b>AAPL</b> — последние новости по тикеру\n"
            "• /analyze <b>BTC</b> — AI-анализ актива\n"
            "• /portfolio — мой портфель (/add BTC)\n"
            "• /alert <b>BTC 70000</b> — уведомление при достижении цены\n"
            "• /alerts — мои активные алерты\n"
            "• /digest — дневной дайджест (подписка)\n"
            "• /myrole — моя роль в боте\n"
            "• /lang — язык бота\n"
            "• /help — эта справка\n\n"
            "Источники: ЦБ РФ (валюты), Finnhub (акции), CoinGecko (крипта).\n"
            "Данные кэшируются: валюты — 1 час, акции/крипта — 10 минут.\n"
            "Под котировками из меню (💱 Курсы, 📈 Акции, 🪙 Крипта) есть кнопки:\n"
            "• «🔄 Обновить» — снимает кэш и запрашивает свежие данные;\n"
            "• «↩️ Меню» — возвращает к списку активов.\n"
            "У ответов на команды (/rate, /stock, /crypto) кнопок нет.\n\n"
            "⚠️ Вся информация носит справочный характер и не является "
            "инвестиционной рекомендацией. Подробнее — по команде /start."
        ),
        "start.disclaimer": (
            "⚠️ <b>Важное предупреждение</b>\n\n"
            "Этот бот создан исключительно в образовательных и демонстрационных "
            "целях. Вся информация (включая анализ и прогнозы, сгенерированные AI) "
            "носит справочный характер и <b>не является финансовой консультацией, "
            "инвестиционной рекомендацией или призывом к действию</b>.\n\n"
            "🔹 <b>Риски</b>\n"
            "Инвестиции в финансовые инструменты сопряжены с высоким риском "
            "потери капитала. Историческая доходность не гарантирует будущих "
            "результатов. AI-модель может допускать ошибки, данные могут "
            "задерживаться или быть неточными. Решения о финансовых операциях вы "
            "принимаете самостоятельно и несёте полную ответственность за все "
            "риски.\n\n"
            "🔹 <b>Ответственность</b>\n"
            "Разработчик бота не несёт ответственности за любые убытки или "
            "упущенную выгоду, возникшие в результате использования этого бота.\n\n"
            "🔹 <b>Источники данных</b>\n"
            "Данные предоставляются через открытые API (ЦБ РФ, Finnhub, CoinGecko) "
            "и могут иметь задержки. Актуальность информации не гарантируется.\n\n"
            "Продолжая использовать бота, вы подтверждаете, что ознакомлены "
            "с данным предупреждением и принимаете все риски."
        ),
        # меню
        "menu.btn.fx": "💱 Курсы",
        "menu.btn.stock": "📈 Акции",
        "menu.btn.crypto": "🪙 Крипта",
        "menu.btn.analyse": "🤖 AI-анализ",
        "menu.btn.portfolio": "📁 Портфель",
        "menu.btn.help": "❓ Помощь",
        "menu.title.fx": "💱 Выбери валюту",
        "menu.title.stock": "📈 Выбери акцию или индекс",
        "menu.title.crypto": "🪙 Выбери монету",
        "menu.title.analyse": "🤖 Выбери категорию для AI-анализа",
        "menu.group.stock": "📈 Акции",
        "menu.group.index": "📊 Индексы",
        "menu.group.crypto": "🪙 Крипта",
        "menu.group.stock_world": "📈 Мир",
        "menu.group.stock_ru": "📈 РФ",
        "menu.analyse_choose": "{title}: выбери актив для AI-анализа",
        "menu.btn.convert": "💱 Перевод валют",
        "menu.btn.refresh": "🔄 Обновить",
        "menu.btn.news": "📰 Новости",
        "menu.btn.chart": "📊 График",
        "menu.btn.add_portfolio": "➕ В портфель",
        "menu.btn.back_menu": "↩️ Меню",
        "menu.btn.back_categories": "↩️ Категории",
        "menu.api_limit": "⚠️ Превышен лимит API. Попробуй через минуту.",
        "menu.fetch_failed": "😔 Не удалось получить данные. Попробуй позже.",
        "menu.unknown_refresh": "Не знаю, как обновить это. 🙈",
        # валюты / конвертер
        "fx.rub_name": "Российский рубль",
        "fx.format": (
            "💱 <b>{name}</b> ({code})\n" "Курс: <b>{rate} ₽</b>\n\nИсточник: ЦБ РФ"
        ),
        "fx.not_supported": "Валюта {code} не поддерживается. Доступны: {currencies}",
        "fx.fetch_failed": "😔 Не удалось получить курс от ЦБ РФ. Попробуй позже.",
        "fx.all_title": "💱 <b>Курсы ЦБ РФ</b>",
        "fx.all_failed": "😔 Не удалось получить курсы. Попробуй позже.",
        "fx.more": "\nПодробнее: /rate USD",
        "fx.pair_title": "💱 <b>{from_code} → {to_code}</b>",
        "fx.pair_line": "1 {base} = <b>{rate} {quote}</b>",
        "fx.pair_reverse": "1 {base} = <b>{rate} {quote}</b>",
        "fx.pair_choose": "💱 Выбери валюту для пары с <b>{code}</b>:",
        "fx.pair_back": "↩️ К курсу {code}",
        "menu.btn.pairs": "💱 Пары",
        "menu.btn.swap": "🔁 Поменять",
        "fx.pair_usage": (
            "Укажи две валюты, например: /rate USD EUR или /rate EUR JPY"
        ),
        "fx.pair_same": "Укажи две разные валюты.",
        "convert.start": "💱 <b>Конвертация</b>\n\nИз какого актива переводим?",
        "convert.start_hint": (
            "💱 <b>Конвертация</b>\n\nИз какого актива переводим?\n"
            "Или одной командой: /convert 100 USD RUB"
        ),
        "convert.ask_from": "Из какой валюты переводим?",
        "convert.from_to": "Из <b>{code}</b>. В какую валюту переводим?",
        "convert.ask_amount": ("<b>{f} → {t}</b>. Введи сумму или выбери готовую:"),
        "convert.result": (
            "💱 <b>{amount} {from_code}</b> = "
            "<b>{result} {to_code}</b>\n\nИсточник: ЦБ РФ, CoinGecko"
        ),
        "convert.cancelled": "❌ Перевод отменён.",
        "convert.cancelled_short": "❌ Отменено.",
        "convert.no_dialog": "Нет активного диалога.",
        "convert.stale": "Диалог устарел, начни заново.",
        "convert.stale_cmd": "Диалог устарел, начни заново: /convert",
        "convert.bad_number": "Это не похоже на число. Попробуй ещё раз.",
        "convert.bad_amount": "Сумма должна быть больше нуля и не слишком большой.",
        "convert.fetch_failed": "😔 Не удалось получить курсы. Попробуй позже.",
        "convert.unsupported": "Актив не поддерживается.",
        "convert.available": "Доступны: {assets}",
        "convert.btn.retry": "💱 Ещё раз",
        "convert.btn.cancel": "❌ Отмена",
        "convert.btn.swap": "🔁 Поменять",
        "convert.btn.back": "↩️ Меню",
        # акции / новости
        "stock.usage": (
            "Укажи тикер, например: /stock AAPL или /stock SPX\n"
            "Индексы: SPX, DJI, NASDAQ, VIX."
        ),
        "stock.bad_ticker": (
            "Тикер {raw} некорректный. Допустимы буквы, цифры, точки, "
            "дефис (до 15 символов)."
        ),
        "stock.rate_limit": (
            "⚠️ Превышен лимит запросов к API акций. Попробуй через минуту."
        ),
        "stock.fetch_failed": "😔 Не удалось получить котировку. Попробуй позже.",
        "stock.format": (
            "📈 <b>{label}</b>\n"
            "{name}\nЦена: <b>${price}</b>\n"
            "Изменение: {sign}{change}%"
        ),
        "stock.format_ru": (
            "📈 <b>{label}</b>\n"
            "{name}\nЦена: <b>{price} ₽</b>\n"
            "Изменение: {sign}{change}%"
        ),
        "stock.market.ru": "🇷🇺 РФ",
        "stock.market.world": "🌍 Мир",
        "stock.market.choose": "📈 <b>Акции</b> — выбери рынок:",
        "stock.page": "{page}/{total}",
        "stock.news_title": "📰 <b>Новости {symbol}</b> (за 10 дней)",
        "stock.news.read": "читать",
        "stock.news.empty": "Новостей за этот период нет.",
        "stock.news.usage": "Укажи тикер, например: /news AAPL или /news NVDA",
        "stock.bad_ticker_short": "Некорректный тикер.",
        "stock.news_rate_limit": (
            "⚠️ Превышен лимит запросов к API новостей. Попробуй через минуту."
        ),
        "stock.news_failed": "😔 Не удалось получить новости. Попробуй позже.",
        "stock.btn.back_stock": "↩️ К акции",
        "stock.chart.usage": "Укажи тикер, например: /chart SBER или /chart AAPL",
        "stock.chart.failed": "😔 Не удалось получить историю цен. Попробуй позже.",
        "stock.chart.insufficient": "😔 Недостаточно данных для графика.",
        "stock.chart.build_failed": "😔 Не удалось построить график.",
        "stock.chart.caption": "📊 <b>{symbol}</b> — цена за 30 дней",
        "stock.btn.back_menu": "↩️ Меню",
        # крипта / графики
        "crypto.usage": "Укажи монету, например: /crypto BTC или /crypto SOL",
        "crypto.bad_coin": "Некорректное название монеты.",
        "crypto.rate_limit": (
            "⚠️ Превышен лимит запросов к API крипты. Попробуй через минуту."
        ),
        "crypto.fetch_failed": "😔 Не удалось получить цену. Попробуй позже.",
        "crypto.format": "🪙 <b>{symbol}</b>\nЦена: <b>${price}</b>{change}",
        "crypto.change": "\nИзменение: {sign}{pct}%",
        "crypto.chart.usage": "Укажи монету, например: /chart BTC или /chart ETH",
        "crypto.chart.failed": "😔 Не удалось получить историю цен. Попробуй позже.",
        "crypto.chart.insufficient": "😔 Недостаточно данных для графика.",
        "crypto.chart.build_failed": "😔 Не удалось построить график.",
        "crypto.chart.caption": "📊 <b>{symbol}</b> — цена за 30 дней",
        "crypto.trending.title": "🔥 <b>Тренды CoinGecko</b>",
        "crypto.trending.rank": "ранг",
        "crypto.trending.hint": (
            "\nПроверь цену: /crypto SYMBOL или AI-анализ в меню."
        ),
        "crypto.trending.failed": "😔 Не удалось получить тренды. Попробуй позже.",
        "crypto.top.title": "🏆 <b>Топ криптовалют по капитализации</b>",
        "crypto.top.cap": "💰 Капитализация: {cap}",
        "crypto.top.hint": "\nПодробнее: /crypto SYMBOL или AI-анализ в меню.",
        "crypto.top.failed": "😔 Не удалось получить топ. Попробуй позже.",
        # AI-анализ
        "analyze.usage": (
            "🤖 Напиши запрос, например:\n"
            "/analyze BTC — анализ монеты\n"
            "/analyze стоит ли покупать BTC — вопрос про рынок\n"
            "или выбери актив в меню AI-анализ"
        ),
        "analyze.fetch_failed": "😔 Не удалось получить данные о активе.",
        "analyze.bad_query": "Некорректный запрос. Опиши вопрос проще.",
        "analyze.unknown_asset": "Неизвестный актив. 🙈",
        "analyze.not_configured": (
            "🤖 AI-агент ещё не настроен: добавь OPENROUTER_API_KEY в .env."
        ),
        "analyze.failed": "😔 AI не ответил. Попробуй позже или напиши проще.",
        "analyze.title": "🤖 <b>Анализ</b>",
        "analyze.disclaimer": "\n\n— <i>Это не инвестиционная рекомендация.</i>",
        "analyze.auto_query": "Проанализируй актив {symbol}.",
        "analyze.context_prompt": "Запрос пользователя: {query}",
        # контекст для LLM
        "analyze.ctx.type_stock": "Тип: акция/индекс",
        "analyze.ctx.type_crypto": "Тип: криптовалюта",
        "analyze.ctx.symbol": "Символ: {symbol}",
        "analyze.ctx.price": "Цена: {price}",
        "analyze.ctx.day_change": "Изменение за день: {sign}{pct}%",
        "analyze.ctx.change_24h": "Изменение за 24ч: {sign}{pct}%",
        "analyze.ctx.company": "Компания: {name} ({industry})",
        "analyze.ctx.desc": "Описание: {desc}",
        "analyze.ctx.news": "Последние новости:",
        "analyze.ctx.coin": "Монета: {name} (rank #{rank})",
        "analyze.ctx.fund": ("Капитализация: {cap}, объём за 24ч: {vol}, ATH: {ath}"),
        "analyze.ctx.trend": "Тренд: 7д {c7}%, 30д {c30}%",
        # LLM
        "llm.system_prompt": (
            "Ты — финансовый аналитик в Telegram-боте. Отвечай по-русски, "
            "кратко и по делу (до 200 слов).\n\n"
            "Правила:\n"
            "- Анализируй ТОЛЬКО данные, переданные в контексте сообщения. "
            "Ничего не выдумывай.\n"
            "- Не давай персональных инвестиционных рекомендаций "
            "(«покупай/продавай»), только факты и возможные сценарии.\n"
            "- Если данных недостаточно — честно скажи об этом.\n"
            "- Формат: короткий вывод о текущей ситуации, что влияет на цену, "
            "ключевые уровни (если есть данные)."
        ),
        "llm.user_prompt": (
            "Данные о активе:\n{context}\n\n"
            "Вопрос пользователя: {query}\n"
            "Дай анализ по этим данным."
        ),
        # портфель
        "portfolio.title": "📁 <b>Мой портфель</b>",
        "portfolio.empty": (
            "📁 <b>Мой портфель</b>\n\nПортфель пуст.\n"
            "Добавь актив: нажми «➕ Добавить» или в меню «📈 Акции» / "
            "«🪙 Крипта» / «💱 Курсы» у цены актива — «➕ В портфель»."
        ),
        "portfolio.choose": (
            "📁 <b>Мой портфель</b>\n\nВыбери категорию или действие."
        ),
        "portfolio.value": "\n💰 Стоимость: <b>${usd}</b> • <b>{rub} ₽</b>",
        "portfolio.value.usd_only": "\n💰 Стоимость: <b>${usd}</b>",
        "portfolio.value.rub_only": "\n💰 Стоимость: <b>{rub} ₽</b>",
        "portfolio.type.fx": "Валюты",
        "portfolio.type.stock": "Акции",
        "portfolio.type.crypto": "Крипта",
        "portfolio.btn.add": "➕ Добавить",
        "portfolio.btn.remove": "➖ Удалить",
        "portfolio.btn.alerts": "🔔 Алерты",
        "portfolio.btn.digest": "📰 Дайджест",
        "portfolio.btn.back": "↩️ Портфель",
        "portfolio.btn.refresh_all": "🔄 Обновить всё",
        "portfolio.btn.alert": "🔔 Алерт",
        "portfolio.btn.qty": "✏️ Кол-во",
        "portfolio.btn.remove_item": "➖ Убрать",
        "portfolio.btn.cancel": "↩️ Отмена",
        "portfolio.btn.skip": "⏭️ Пропустить",
        "portfolio.btn.skip_price": "⏭️ Пропустить (текущая цена)",
        "portfolio.btn.added": "✅ В портфеле",
        "portfolio.pnl": "P&L: <b>{diff} {currency}</b> ({pct}%)",
        "portfolio.add.price_prompt": (
            "По какой цене купил <b>{symbol}</b>? Напиши число "
            "(например 270.5) или пропусти — возьму текущую цену."
        ),
        "portfolio.add.bad_price": "Это не похоже на цену. Напиши число (например 270.5).",
        "portfolio.cmd.add.bad_price": ("Цена не распознана. Пример: /add SBER 270.5"),
        "portfolio.cat.empty": (
            "{icon} <b>{title}</b>: пусто.\n"
            "Добавить: нажми «➕ Добавить» или открой цену актива в меню "
            "и нажми «➕ В портфель»."
        ),
        "portfolio.cat.title": "{icon} <b>{title}</b> ({n})",
        "portfolio.unavailable": "{symbol} — недоступно",
        "portfolio.qty_line": (
            "\n\nКоличество: {qty} • Стоимость: <b>{value} {currency}</b>"
        ),
        "portfolio.cancelled": "Отменено.",
        "portfolio.stale": "Диалог устарел. Начни заново.",
        "portfolio.trend": "Тренд: {parts}",
        "portfolio.add.prompt": (
            "➕ Введи символ актива (например <b>BTC</b>, <b>AAPL</b>, "
            "<b>USD</b>, <b>SPX</b>). /cancel — выйти."
        ),
        "portfolio.add.bad_symbol": (
            "Не распознал актив. Введи тикер акции (AAPL), монету (BTC) "
            "или валюту (USD)."
        ),
        "portfolio.add.qty_prompt": (
            "Сколько у тебя <b>{symbol}</b>? Напиши число (например 5 или 0.5) "
            "или пропусти."
        ),
        "portfolio.add.bad_qty": "Это не число. Напиши количество цифрами (например 5).",
        "portfolio.add.done": (
            "✅ {icon} <b>{symbol}</b> добавлен в портфель " "({type}){qty}."
        ),
        "portfolio.add.qty_suffix": " ({qty} шт.)",
        "portfolio.add.exists": "{icon} <b>{symbol}</b> уже в портфеле.",
        "portfolio.add.unknown_type": "Не удалось определить тип актива. 😔",
        "portfolio.add.toast": "✅ {symbol} добавлен в портфель",
        "portfolio.add.already": "{symbol} уже в портфеле",
        "portfolio.qty.prompt": (
            "✏️ Сколько у тебя <b>{symbol}</b>? Введи число (например 5 или 0.5)."
        ),
        "portfolio.qty.saved": "✅ Для <b>{symbol}</b> задано количество {qty}.",
        "portfolio.remove.empty": "📁 Портфель пуст.",
        "portfolio.remove.title": "➖ <b>Выбери актив для удаления</b>",
        "portfolio.remove.confirm": "Удалить {icon} <b>{symbol}</b> из портфеля?",
        "portfolio.remove.yes": "✅ Да, убрать",
        "portfolio.remove.done": "✅ {icon} <b>{symbol}</b> убран из портфеля.",
        "portfolio.cmd.add.usage": (
            "Не понимаю, что добавить. Примеры: /add BTC, /add AAPL, /add USD"
        ),
        "portfolio.cmd.add.done": "📁 <b>{symbol}</b> добавлен в портфель ({icon}{type}).",
        "portfolio.cmd.add.exists": "📁 <b>{symbol}</b> уже в портфеле.",
        "portfolio.cmd.remove.usage": "Укажи актив: /remove BTC",
        "portfolio.cmd.remove.done": "📁 <b>{symbol}</b> убран из портфеля.",
        "portfolio.cmd.remove.missing": "📁 <b>{symbol}</b> не было в портфеле.",
        "portfolio.alert.above": "выше",
        "portfolio.alert.below": "ниже",
        "portfolio.alert.usage": (
            "Формат: /alert <символ> [выше|below] <цена>\n"
            "Примеры: /alert BTC 70000 (выше 70 000)\n"
            "         /alert ETH below 3500 (ниже 3 500)"
        ),
        "portfolio.alert.set": (
            "🔔 Алерт установлен: <b>{symbol}</b> {arrow} "
            "<b>{currency}{target}</b>\nПроверяется каждые 30 минут. /alerts — список"
        ),
        "portfolio.alert.empty": (
            "🔕 Активных алертов нет.\nСоздать: /alert BTC 70000"
        ),
        "portfolio.alert.title": "🔔 <b>Мои алерты</b>",
        "portfolio.alert.hint_remove": "\nУбрать: /portfolio → 🔔 Алерты",
        "portfolio.alert.empty2": (
            "🔕 Активных алертов нет.\n\nСоздать можно из карточки актива — "
            "кнопка «🔔 Алерт» — или командой: /alert BTC 70000."
        ),
        "portfolio.alert.not_found": "Алерт не найден.",
        "portfolio.alert.delete_confirm": "Удалить алерт?\n\n{line}",
        "portfolio.alert.yes_del": "✅ Да, удалить",
        "portfolio.alert.deleted": "✅ Алерт <code>{id}</code> удалён.",
        "portfolio.alert.btn_del": "🗑 Убрать #{num}",
        "portfolio.alert.type_prompt": "🔔 Алерт для <b>{symbol}</b>. Какой тип?",
        "portfolio.alert.btn_price": "💰 По цене",
        "portfolio.alert.btn_percent": "📈 Изменение в %",
        "portfolio.alert.price_prompt": "🔔 Цена алерта для <b>{symbol}</b>?\n{hint}Напиши число.",
        "portfolio.alert.percent_prompt": (
            "📈 На сколько процентов должен измениться <b>{symbol}</b>?\n"
            "{hint}Напиши число (например 5 или 3.5)."
        ),
        "portfolio.alert.hint_fx": "Текущая цена: <b>{price} ₽</b>\n",
        "portfolio.alert.hint": "Текущая цена: <b>${price}</b> ({sign}{pct}%)\n",
        "portfolio.alert.hint_ru": "Текущая цена: <b>{price} ₽</b> ({sign}{pct}%)\n",
        "portfolio.alert.bad_number": "Это не число. Напиши число цифрами.",
        "portfolio.alert.bad_value": "Значение должно быть больше нуля.",
        "portfolio.alert.direction": (
            "🔔 <b>{symbol}</b>: {value}{suffix}. Сработает, когда цена "
            "будет выше или ниже?"
        ),
        "portfolio.alert.btn_above": "⬆️ Выше",
        "portfolio.alert.btn_below": "⬇️ Ниже",
        "portfolio.alert.set2": "🔔 Алерт установлен: <b>{symbol}</b> {arrow} <b>{value}{unit}</b>",
        "portfolio.remove_alert.usage": (
            "Укажи id алерта: /remove_alert 3 (id видно в /alerts)"
        ),
        "portfolio.remove_alert.missing": (
            "⚠️ Алерт <code>{id}</code> не найден (он твой и активен?)."
        ),
        # алерты (фон)
        "alerts.fired": (
            "🔔 <b>Алерт сработал</b>\n"
            "{symbol}: цена <b>{price} {currency}</b> — изменилась на "
            "<b>{pct}%</b> ({arrow} порога {target}%)\n"
            "\nУправление: /alerts"
        ),
        "alerts.fired_abs": (
            "🔔 <b>Алерт сработал</b>\n"
            "{symbol}: <b>{price} {currency}</b> — {arrow} порога "
            "<b>{target} {currency}</b>\n\nУправление: /alerts"
        ),
        "alerts.above": "выше",
        "alerts.below": "ниже",
        # дайджест
        "digest.status": (
            "📰 <b>Дневной дайджест</b>\n\n"
            "Статус: <b>{state}</b>\n"
            "Время отправки: каждый день в {time}.\n\n"
            "Настрой свой набор активов или собери дайджест прямо сейчас."
        ),
        "digest.status.on": "🔔 включена",
        "digest.status.off": "🔕 выключена",
        "digest.btn.setup": "⚙️ Настроить набор",
        "digest.btn.time": "⏰ Время",
        "digest.btn.reset_time": "🔄 Сбросить",
        "digest.time_hour": "⏰ <b>Время дайджеста</b>\n\nВыбери час:",
        "digest.time_minute": "⏰ Час: <b>{hour}</b>. Теперь минуты:",
        "digest.time_saved": "✅ Время дайджеста: <b>{time}</b>",
        "digest.btn.send": "📤 Собрать сейчас",
        "digest.btn.subscribe": "🔔 Подписаться",
        "digest.btn.unsubscribe": "🔕 Отписаться",
        "digest.btn.back": "↩️ Назад",
        "digest.btn.categories": "↩️ Категории",
        "digest.subscribed": "✅ Вы подписаны на дневной дайджест.",
        "digest.unsubscribed": "🔕 Вы отписаны от дайджеста.",
        "digest.sending": "Собираю дайджест…",
        "digest.failed": "😔 Не удалось собрать дайджест. Попробуй позже.",
        "digest.setup.title": (
            "⚙️ <b>Настройка дайджеста</b>\n\n"
            "Выбери категорию, чтобы включить активы в свой набор. "
            "Если набор пуст — присылается дефолтный топ."
        ),
        "digest.setup_cat": (
            "{icon} <b>{title}</b> ({sel}/{total})\n\n"
            "Нажимай на актив, чтобы включить или выключить его в дайджесте."
        ),
        "digest.type.fx": "Валюты",
        "digest.type.stock": "Акции",
        "digest.type.stock_world": "Акции Мир 🌍",
        "digest.type.stock_ru": "Акции РФ 🇷🇺",
        "digest.type.index": "Индексы",
        "digest.type.crypto": "Крипта",
        "digest.build.title_morning": "🌅 <b>Доброе утро! Дневной дайджест</b>",
        "digest.build.title_day": "☀️ <b>Добрый день! Дневной дайджест</b>",
        "digest.build.title_evening": "🌆 <b>Добрый вечер! Дневной дайджест</b>",
        "digest.build.title_night": "🌙 <b>Доброй ночи! Дневной дайджест</b>",
        "digest.section.fx": "💱 <b>Валюты</b>",
        "digest.section.fx_default": "💱 <b>Курсы ЦБ</b>",
        "digest.section.stock": "📈 <b>Акции</b>",
        "digest.section.stock_world": "📈 <b>Акции 🌍 Мир</b>",
        "digest.section.stock_ru": "📈 <b>Акции 🇷🇺 РФ</b>",
        "digest.section.index": "📊 <b>Индексы</b>",
        "digest.section.crypto": "🪙 <b>Крипта</b>",
        "digest.portfolio_title": "📁 <b>Ваш портфель</b>",
        "digest.disclaimer": "\n\n— <i>Это не инвестиционная рекомендация.</i>",
        # админка
        "admin.title": (
            "🔐 <b>Панель администратора</b>\n\n"
            "👥 Пользователей: <b>{users}</b>\n"
            "🚫 В бане: <b>{banned}</b>\n"
            "🕐 Аптайм: <b>{uptime}</b>\n"
            "⚙️ Лимит сообщений: {rate}/мин\n\n"
            "🗂 Кэш: {entries} записей, hits {hits}, misses {misses}\n"
            "📊 Популярные команды:\n{top}\n\n"
            "Сообщений: {messages}, колбэков: {callbacks}"
        ),
        "admin.no_commands": "  пока нет",
        "admin.cache": (
            "🗂 <b>Кэш</b>\n"
            "Записей: {entries}\n"
            "Попаданий: {hits}\n"
            "Промахов: {misses}\n"
            "Эффективность: {rate}"
        ),
        "admin.recent.empty": "История запросов пока пуста.",
        "admin.recent.title": "🕐 <b>Последние запросы</b>",
        "admin.users.title": "👥 <b>Пользователи</b> — всего: {total}",
        "admin.users.empty": "пока нет",
        "admin.broadcast.usage": "Укажи текст рассылки: /broadcast Привет всем!",
        "admin.broadcast.no_users": "Нет зарегистрированных пользователей.",
        "admin.broadcast.confirm": (
            "⚠️ <b>Рассылка</b> {n} пользователям?\n\n"
            "<blockquote>{text}</blockquote>\n"
            "Подтверди или отмени:"
        ),
        "admin.broadcast.yes": "✅ Да, рассылать",
        "admin.broadcast.cancel": "❌ Отмена",
        "admin.broadcast.stale": "Рассылка уже отменена или завершена.",
        "admin.broadcast.inactive": "Рассылка не активна.",
        "admin.broadcast.cancelled": "❌ Рассылка отменена.",
        "admin.broadcast.sending": "📤 Отправляю рассылку…",
        "admin.broadcast.done": (
            "✅ Рассылка завершена: отправлено <b>{sent}</b>, ошибок <b>{failed}</b>."
        ),
        "admin.ban.usage": "Укажи Telegram ID: /ban 123456789",
        "admin.ban.done": "🚫 Пользователь <code>{id}</code> забанен.",
        "admin.ban.missing": (
            "⚠️ Пользователь <code>{id}</code> не найден (все равно "
            "блокируется при следующих запросах)."
        ),
        "admin.unban.usage": "Укажи Telegram ID: /unban 123456789",
        "admin.unban.done": (
            "✅ Пользователь <code>{id}</code> разбанен (если был в базе)."
        ),
        "admin.myrole": "Твоя роль: <b>{role}</b>",
        "admin.setrole.usage": (
            "Формат: /setRole <id> <роль>\nРоли: user, admin\n"
            "Пример: /setrole 123456789 admin"
        ),
        "admin.setrole.missing": "⚠️ Пользователь <code>{id}</code> не найден.",
        "admin.setrole.done": "✅ Роль пользователя <code>{id}</code> → <b>{role}</b>",
        # язык
        "lang.prompt": "🌐 <b>Выбери язык / Choose language</b>",
        "lang.set": "✅ Язык: {name}",
        "lang.menu_ready": "Главное меню обновлено. 🗂",
        "lang.name.ru": "🇷🇺 Русский",
        "lang.name.en": "🇬🇧 English",
        # троттлинг
        "throttle.message": "⏳ Слишком часто! Подожди немного и попробуй снова.",
        "throttle.callback": "⏳ Слишком часто! Подожди немного.",
        # команды в интерфейсе Telegram
        "cmd.start": "Главное меню",
        "cmd.rate": "Курс валюты: /rate USD",
        "cmd.convert": "Конвертер: /convert 100 USD RUB",
        "cmd.stock": "Цена акции: /stock AAPL",
        "cmd.crypto": "Цена крипты: /crypto BTC",
        "cmd.chart": "График цены: /chart BTC",
        "cmd.trending": "Топ трендовых монет",
        "cmd.top": "Топ по капитализации",
        "cmd.news": "Новости по тикеру: /news AAPL",
        "cmd.analyze": "AI-анализ: /analyze BTC",
        "cmd.portfolio": "Мой портфель",
        "cmd.alert": "Алерт: /alert BTC 70000",
        "cmd.alerts": "Мои алерты",
        "cmd.digest": "Дневной дайджест",
        "cmd.myrole": "Моя роль",
        "cmd.lang": "Язык / Language",
        "cmd.help": "Справка",
        "cmd.admin": "Панель администратора",
        "cmd.users": "Список пользователей",
        "cmd.broadcast": "Рассылка: /broadcast текст",
        "cmd.ban": "Бан: /ban id",
        "cmd.unban": "Разбан: /unban id",
        "cmd.cachestats": "Статистика кэша",
        "cmd.recent": "Последние запросы",
        "cmd.setrole": "Назначить роль: /setRole id role",
    },
    "en": {
        # start / help
        "start.help_text": (
            "🤖 <b>How to use the bot</b>\n\n"
            "Press menu buttons or type commands:\n"
            "• /rate <b>USD</b> — currency rate (USD, EUR, CNY...)\n"
            "• /convert <b>100 USD RUB</b> — currency converter\n"
            "• /stock <b>AAPL</b> — stock price (SPX, DJI — indexes)\n"
            "• /crypto <b>BTC</b> — cryptocurrency price\n"
            "• /chart <b>BTC</b> — 30-day price chart\n"
            "• /trending — top trending coins\n"
            "• /top — top coins by market cap\n"
            "• /news <b>AAPL</b> — latest ticker news\n"
            "• /analyze <b>BTC</b> — AI analysis of an asset\n"
            "• /portfolio — my portfolio (/add BTC)\n"
            "• /alert <b>BTC 70000</b> — notification when price reached\n"
            "• /alerts — my active alerts\n"
            "• /digest — daily digest (subscription)\n"
            "• /myrole — my role in the bot\n"
            "• /lang — bot language\n"
            "• /help — this help\n\n"
            "Sources: CBR (currencies), Finnhub (stocks), CoinGecko (crypto).\n"
            "Data is cached: currencies — 1 hour, stocks/crypto — 10 minutes.\n"
            "Quotes from the menu (💱 Rates, 📈 Stocks, 🪙 Crypto) have buttons:\n"
            "• «🔄 Refresh» — clears cache and fetches fresh data;\n"
            "• «↩️ Menu» — returns to the asset list.\n"
            "Answers to commands (/rate, /stock, /crypto) have no buttons.\n\n"
            "⚠️ All information is for reference only and is not an investment "
            "recommendation. Details: /start"
        ),
        "start.disclaimer": (
            "⚠️ <b>Important notice</b>\n\n"
            "This bot is created exclusively for educational and demonstration "
            "purposes. All information (including AI-generated analysis and "
            "forecasts) is for reference only and <b>is not financial advice, "
            "an investment recommendation or a call to action</b>.\n\n"
            "🔹 <b>Risks</b>\n"
            "Investing in financial instruments involves a high risk of losing "
            "capital. Past performance does not guarantee future results. The "
            "AI model may make mistakes, data may be delayed or inaccurate. You "
            "make financial decisions yourself and bear full responsibility for "
            "all risks.\n\n"
            "🔹 <b>Liability</b>\n"
            "The bot developer is not liable for any losses or lost profits "
            "resulting from the use of this bot.\n\n"
            "🔹 <b>Data sources</b>\n"
            "Data is provided through open APIs (CBR, Finnhub, CoinGecko) and "
            "may be delayed. Up-to-dateness is not guaranteed.\n\n"
            "By continuing to use the bot, you confirm that you have read this "
            "notice and accept all risks."
        ),
        # menu
        "menu.btn.fx": "💱 Rates",
        "menu.btn.stock": "📈 Stocks",
        "menu.btn.crypto": "🪙 Crypto",
        "menu.btn.analyse": "🤖 AI Analysis",
        "menu.btn.portfolio": "📁 Portfolio",
        "menu.btn.help": "❓ Help",
        "menu.title.fx": "💱 Choose currency",
        "menu.title.stock": "📈 Choose stock or index",
        "menu.title.crypto": "🪙 Choose coin",
        "menu.title.analyse": "🤖 Choose category for AI analysis",
        "menu.group.stock": "📈 Stocks",
        "menu.group.index": "📊 Indexes",
        "menu.group.crypto": "🪙 Crypto",
        "menu.group.stock_world": "📈 World",
        "menu.group.stock_ru": "📈 RU",
        "menu.analyse_choose": "{title}: choose asset for AI analysis",
        "menu.btn.convert": "💱 Currency convert",
        "menu.btn.refresh": "🔄 Refresh",
        "menu.btn.news": "📰 News",
        "menu.btn.chart": "📊 Chart",
        "menu.btn.add_portfolio": "➕ Add to portfolio",
        "menu.btn.back_menu": "↩️ Menu",
        "menu.btn.back_categories": "↩️ Categories",
        "menu.api_limit": "⚠️ API limit exceeded. Try again in a minute.",
        "menu.fetch_failed": "😔 Could not fetch data. Try again later.",
        "menu.unknown_refresh": "I don't know how to refresh this. 🙈",
        # currencies / converter
        "fx.rub_name": "Russian ruble",
        "fx.format": (
            "💱 <b>{name}</b> ({code})\n" "Rate: <b>{rate} ₽</b>\n\nSource: CBR"
        ),
        "fx.not_supported": "Currency {code} is not supported. Available: {currencies}",
        "fx.fetch_failed": "😔 Could not get rate from CBR. Try again later.",
        "fx.all_title": "💱 <b>CBR exchange rates</b>",
        "fx.all_failed": "😔 Could not fetch rates. Try again later.",
        "fx.more": "\nMore: /rate USD",
        "fx.pair_title": "💱 <b>{from_code} → {to_code}</b>",
        "fx.pair_line": "1 {base} = <b>{rate} {quote}</b>",
        "fx.pair_reverse": "1 {base} = <b>{rate} {quote}</b>",
        "fx.pair_choose": "💱 Выбери валюту для пары с <b>{code}</b>:",
        "fx.pair_back": "↩️ К курсу {code}",
        "menu.btn.pairs": "💱 Пары",
        "menu.btn.swap": "🔁 Поменять",
        "fx.pair_usage": (
            "Укажи две валюты, например: /rate USD EUR или /rate EUR JPY"
        ),
        "fx.pair_same": "Укажи две разные валюты.",
        "convert.start": "💱 <b>Convert</b>\n\nConvert from which asset?",
        "convert.start_hint": (
            "💱 <b>Convert</b>\n\nConvert from which asset?\n"
            "Or with one command: /convert 100 USD RUB"
        ),
        "convert.ask_from": "Convert from which currency?",
        "convert.from_to": "From <b>{code}</b>. Convert to which currency?",
        "convert.ask_amount": "<b>{f} → {t}</b>. Enter an amount or choose a preset:",
        "convert.result": (
            "💱 <b>{amount} {from_code}</b> = "
            "<b>{result} {to_code}</b>\n\nSource: CBR, CoinGecko"
        ),
        "convert.cancelled": "❌ Conversion cancelled.",
        "convert.cancelled_short": "❌ Cancelled.",
        "convert.no_dialog": "No active dialog.",
        "convert.stale": "Dialog expired, start over.",
        "convert.stale_cmd": "Dialog expired, start over: /convert",
        "convert.bad_number": "That doesn't look like a number. Try again.",
        "convert.bad_amount": "Amount must be positive and not too large.",
        "convert.fetch_failed": "😔 Could not fetch rates. Try again later.",
        "convert.unsupported": "Asset is not supported.",
        "convert.available": "Available: {assets}",
        "convert.btn.retry": "💱 Try again",
        "convert.btn.cancel": "❌ Cancel",
        "convert.btn.swap": "🔁 Swap",
        "convert.btn.back": "↩️ Menu",
        # stocks / news
        "stock.usage": (
            "Provide a ticker, e.g. /stock AAPL or /stock SPX\n"
            "Indexes: SPX, DJI, NASDAQ, VIX."
        ),
        "stock.bad_ticker": (
            "Ticker {raw} is invalid. Letters, digits, dots, "
            "dash allowed (up to 15 chars)."
        ),
        "stock.rate_limit": ("⚠️ Stock API limit exceeded. Try again in a minute."),
        "stock.fetch_failed": "😔 Could not fetch quote. Try again later.",
        "stock.format": (
            "📈 <b>{label}</b>\n"
            "{name}\nPrice: <b>${price}</b>\n"
            "Change: {sign}{change}%"
        ),
        "stock.format_ru": (
            "📈 <b>{label}</b>\n"
            "{name}\nPrice: <b>{price} ₽</b>\n"
            "Change: {sign}{change}%"
        ),
        "stock.market.ru": "🇷🇺 RU",
        "stock.market.world": "🌍 World",
        "stock.market.choose": "📈 <b>Stocks</b> — choose a market:",
        "stock.page": "{page}/{total}",
        "stock.news_title": "📰 <b>{symbol} news</b> (last 10 days)",
        "stock.news.read": "read",
        "stock.news.empty": "No news for this period.",
        "stock.news.usage": "Provide a ticker, e.g. /news AAPL or /news NVDA",
        "stock.bad_ticker_short": "Invalid ticker.",
        "stock.news_rate_limit": ("⚠️ News API limit exceeded. Try again in a minute."),
        "stock.news_failed": "😔 Could not fetch news. Try again later.",
        "stock.btn.back_stock": "↩️ To stock",
        "stock.chart.usage": "Provide a ticker, e.g. /chart SBER or /chart AAPL",
        "stock.chart.failed": "😔 Could not fetch price history. Try again later.",
        "stock.chart.insufficient": "😔 Not enough data for a chart.",
        "stock.chart.build_failed": "😔 Could not build a chart.",
        "stock.chart.caption": "📊 <b>{symbol}</b> — 30-day price",
        "stock.btn.back_menu": "↩️ Menu",
        # crypto / charts
        "crypto.usage": "Provide a coin, e.g. /crypto BTC or /crypto SOL",
        "crypto.bad_coin": "Invalid coin name.",
        "crypto.rate_limit": ("⚠️ Crypto API limit exceeded. Try again in a minute."),
        "crypto.fetch_failed": "😔 Could not fetch price. Try again later.",
        "crypto.format": "🪙 <b>{symbol}</b>\nPrice: <b>${price}</b>{change}",
        "crypto.change": "\nChange: {sign}{pct}%",
        "crypto.chart.usage": "Provide a coin, e.g. /chart BTC or /chart ETH",
        "crypto.chart.failed": "😔 Could not fetch price history. Try again later.",
        "crypto.chart.insufficient": "😔 Not enough data for a chart.",
        "crypto.chart.build_failed": "😔 Could not build a chart.",
        "crypto.chart.caption": "📊 <b>{symbol}</b> — 30-day price",
        "crypto.trending.title": "🔥 <b>CoinGecko trends</b>",
        "crypto.trending.rank": "rank",
        "crypto.trending.hint": "\nCheck price: /crypto SYMBOL or AI analysis in menu.",
        "crypto.trending.failed": "😔 Could not fetch trends. Try again later.",
        "crypto.top.title": "🏆 <b>Top cryptocurrencies by market cap</b>",
        "crypto.top.cap": "💰 Market cap: {cap}",
        "crypto.top.hint": "\nMore: /crypto SYMBOL or AI analysis in menu.",
        "crypto.top.failed": "😔 Could not fetch top. Try again later.",
        # AI analysis
        "analyze.usage": (
            "🤖 Write a request, e.g.:\n"
            "/analyze BTC — coin analysis\n"
            "/analyze should I buy BTC — a market question\n"
            "or choose an asset in the AI Analysis menu"
        ),
        "analyze.fetch_failed": "😔 Could not fetch asset data.",
        "analyze.bad_query": "Invalid query. Describe the question more simply.",
        "analyze.unknown_asset": "Unknown asset. 🙈",
        "analyze.not_configured": (
            "🤖 AI agent is not configured yet: add OPENROUTER_API_KEY to .env."
        ),
        "analyze.failed": "😔 AI did not answer. Try again later or write more simply.",
        "analyze.title": "🤖 <b>Analysis</b>",
        "analyze.disclaimer": "\n\n— <i>This is not investment advice.</i>",
        "analyze.auto_query": "Analyze the asset {symbol}.",
        "analyze.context_prompt": "User query: {query}",
        # LLM context
        "analyze.ctx.type_stock": "Type: stock/index",
        "analyze.ctx.type_crypto": "Type: cryptocurrency",
        "analyze.ctx.symbol": "Symbol: {symbol}",
        "analyze.ctx.price": "Price: {price}",
        "analyze.ctx.day_change": "Day change: {sign}{pct}%",
        "analyze.ctx.change_24h": "24h change: {sign}{pct}%",
        "analyze.ctx.company": "Company: {name} ({industry})",
        "analyze.ctx.desc": "Description: {desc}",
        "analyze.ctx.news": "Latest news:",
        "analyze.ctx.coin": "Coin: {name} (rank #{rank})",
        "analyze.ctx.fund": "Market cap: {cap}, 24h volume: {vol}, ATH: {ath}",
        "analyze.ctx.trend": "Trend: 7d {c7}%, 30d {c30}%",
        # LLM
        "llm.system_prompt": (
            "You are a financial analyst in a Telegram bot. Answer in English, "
            "concisely and to the point (up to 200 words).\n\n"
            "Rules:\n"
            "- Analyze ONLY the data provided in the message context. Do not "
            "make anything up.\n"
            "- Do not give personal investment recommendations "
            '("buy/sell"), only facts and possible scenarios.\n'
            "- If there is not enough data — honestly say so.\n"
            "- Format: a short conclusion about the current situation, what "
            "affects the price, key levels (if data is available)."
        ),
        "llm.user_prompt": (
            "Asset data:\n{context}\n\n"
            "User question: {query}\n"
            "Give an analysis based on this data."
        ),
        # portfolio
        "portfolio.title": "📁 <b>My portfolio</b>",
        "portfolio.empty": (
            "📁 <b>My portfolio</b>\n\nThe portfolio is empty.\n"
            "Add an asset: press «➕ Add» or in the menu «📈 Stocks» / "
            "«🪙 Crypto» / «💱 Rates» near the asset price — «➕ Add to portfolio»."
        ),
        "portfolio.choose": "📁 <b>My portfolio</b>\n\nChoose a category or action.",
        "portfolio.value": "\n💰 Value: <b>${usd}</b> • <b>{rub} ₽</b>",
        "portfolio.value.usd_only": "\n💰 Value: <b>${usd}</b>",
        "portfolio.value.rub_only": "\n💰 Value: <b>{rub} ₽</b>",
        "portfolio.type.fx": "Currencies",
        "portfolio.type.stock": "Stocks",
        "portfolio.type.crypto": "Crypto",
        "portfolio.btn.add": "➕ Add",
        "portfolio.btn.remove": "➖ Remove",
        "portfolio.btn.alerts": "🔔 Alerts",
        "portfolio.btn.digest": "📰 Digest",
        "portfolio.btn.back": "↩️ Portfolio",
        "portfolio.btn.refresh_all": "🔄 Refresh all",
        "portfolio.btn.alert": "🔔 Alert",
        "portfolio.btn.qty": "✏️ Qty",
        "portfolio.btn.remove_item": "➖ Remove",
        "portfolio.btn.cancel": "↩️ Cancel",
        "portfolio.btn.skip": "⏭️ Skip",
        "portfolio.btn.skip_price": "⏭️ Skip (current price)",
        "portfolio.btn.added": "✅ In portfolio",
        "portfolio.pnl": "P&L: <b>{diff} {currency}</b> ({pct}%)",
        "portfolio.add.price_prompt": (
            "At what price did you buy <b>{symbol}</b>? Enter a number "
            "(e.g. 270.5) or skip — I will use the current price."
        ),
        "portfolio.add.bad_price": "That does not look like a price. Enter a number (e.g. 270.5).",
        "portfolio.cmd.add.bad_price": "Price not recognized. Example: /add SBER 270.5",
        "portfolio.cat.empty": (
            "{icon} <b>{title}</b>: empty.\n"
            "Add: press «➕ Add» or open the asset price in the menu "
            "and press «➕ Add to portfolio»."
        ),
        "portfolio.cat.title": "{icon} <b>{title}</b> ({n})",
        "portfolio.unavailable": "{symbol} — unavailable",
        "portfolio.qty_line": (
            "\n\nQuantity: {qty} • Value: <b>{value} {currency}</b>"
        ),
        "portfolio.cancelled": "Cancelled.",
        "portfolio.stale": "Dialog expired. Start over.",
        "portfolio.trend": "Trend: {parts}",
        "portfolio.add.prompt": (
            "➕ Enter an asset symbol (e.g. <b>BTC</b>, <b>AAPL</b>, "
            "<b>USD</b>, <b>SPX</b>). /cancel — exit."
        ),
        "portfolio.add.bad_symbol": (
            "Could not recognize the asset. Enter a stock ticker (AAPL), "
            "a coin (BTC) or a currency (USD)."
        ),
        "portfolio.add.qty_prompt": (
            "How many <b>{symbol}</b> do you have? Enter a number (e.g. 5 or 0.5) "
            "or skip."
        ),
        "portfolio.add.bad_qty": "That's not a number. Enter the quantity in digits (e.g. 5).",
        "portfolio.add.done": (
            "✅ {icon} <b>{symbol}</b> added to portfolio " "({type}){qty}."
        ),
        "portfolio.add.qty_suffix": " ({qty} pcs.)",
        "portfolio.add.exists": "{icon} <b>{symbol}</b> is already in the portfolio.",
        "portfolio.add.unknown_type": "Could not determine asset type. 😔",
        "portfolio.add.toast": "✅ {symbol} added to portfolio",
        "portfolio.add.already": "{symbol} is already in the portfolio",
        "portfolio.qty.prompt": (
            "✏️ How many <b>{symbol}</b> do you have? Enter a number (e.g. 5 or 0.5)."
        ),
        "portfolio.qty.saved": "✅ Quantity for <b>{symbol}</b> set to {qty}.",
        "portfolio.remove.empty": "📁 Portfolio is empty.",
        "portfolio.remove.title": "➖ <b>Choose asset to remove</b>",
        "portfolio.remove.confirm": "Remove {icon} <b>{symbol}</b> from portfolio?",
        "portfolio.remove.yes": "✅ Yes, remove",
        "portfolio.remove.done": "✅ {icon} <b>{symbol}</b> removed from portfolio.",
        "portfolio.cmd.add.usage": (
            "I don't understand what to add. Examples: /add BTC, /add AAPL, /add USD"
        ),
        "portfolio.cmd.add.done": "📁 <b>{symbol}</b> added to portfolio ({icon}{type}).",
        "portfolio.cmd.add.exists": "📁 <b>{symbol}</b> is already in the portfolio.",
        "portfolio.cmd.remove.usage": "Specify an asset: /remove BTC",
        "portfolio.cmd.remove.done": "📁 <b>{symbol}</b> removed from portfolio.",
        "portfolio.cmd.remove.missing": "📁 <b>{symbol}</b> was not in the portfolio.",
        "portfolio.alert.above": "above",
        "portfolio.alert.below": "below",
        "portfolio.alert.usage": (
            "Format: /alert <symbol> [above|below] <price>\n"
            "Examples: /alert BTC 70000 (above 70 000)\n"
            "         /alert ETH below 3500 (below 3 500)"
        ),
        "portfolio.alert.set": (
            "🔔 Alert set: <b>{symbol}</b> {arrow} "
            "<b>{currency}{target}</b>\nChecked every 30 minutes. /alerts — list"
        ),
        "portfolio.alert.empty": "🔕 No active alerts.\nCreate: /alert BTC 70000",
        "portfolio.alert.title": "🔔 <b>My alerts</b>",
        "portfolio.alert.hint_remove": "\nRemove: /portfolio → 🔔 Alerts",
        "portfolio.alert.empty2": (
            "🔕 No active alerts.\n\nYou can create one from the asset card — "
            "«🔔 Alert» button — or with the command: /alert BTC 70000."
        ),
        "portfolio.alert.not_found": "Alert not found.",
        "portfolio.alert.delete_confirm": "Delete alert?\n\n{line}",
        "portfolio.alert.yes_del": "✅ Yes, delete",
        "portfolio.alert.deleted": "✅ Alert <code>{id}</code> deleted.",
        "portfolio.alert.btn_del": "🗑 Remove #{num}",
        "portfolio.alert.type_prompt": "🔔 Alert for <b>{symbol}</b>. Which type?",
        "portfolio.alert.btn_price": "💰 By price",
        "portfolio.alert.btn_percent": "📈 Change in %",
        "portfolio.alert.price_prompt": "🔔 Alert price for <b>{symbol}</b>?\n{hint}Enter a number.",
        "portfolio.alert.percent_prompt": (
            "📈 By how many percent should <b>{symbol}</b> change?\n"
            "{hint}Enter a number (e.g. 5 or 3.5)."
        ),
        "portfolio.alert.hint_fx": "Current price: <b>{price} ₽</b>\n",
        "portfolio.alert.hint": "Current price: <b>${price}</b> ({sign}{pct}%)\n",
        "portfolio.alert.hint_ru": "Current price: <b>{price} ₽</b> ({sign}{pct}%)\n",
        "portfolio.alert.bad_number": "That's not a number. Enter digits.",
        "portfolio.alert.bad_value": "Value must be greater than zero.",
        "portfolio.alert.direction": (
            "🔔 <b>{symbol}</b>: {value}{suffix}. Will trigger when the price "
            "is above or below?"
        ),
        "portfolio.alert.btn_above": "⬆️ Above",
        "portfolio.alert.btn_below": "⬇️ Below",
        "portfolio.alert.set2": "🔔 Alert set: <b>{symbol}</b> {arrow} <b>{value}{unit}</b>",
        "portfolio.remove_alert.usage": (
            "Specify the alert id: /remove_alert 3 (id is visible in /alerts)"
        ),
        "portfolio.remove_alert.missing": (
            "⚠️ Alert <code>{id}</code> not found (is it yours and active?)."
        ),
        # alerts (background)
        "alerts.fired": (
            "🔔 <b>Alert triggered</b>\n"
            "{symbol}: price <b>{price} {currency}</b> — changed by "
            "<b>{pct}%</b> ({arrow} threshold {target}%)\n"
            "\nManage: /alerts"
        ),
        "alerts.fired_abs": (
            "🔔 <b>Alert triggered</b>\n"
            "{symbol}: <b>{price} {currency}</b> — {arrow} threshold "
            "<b>{target} {currency}</b>\n\nManage: /alerts"
        ),
        "alerts.above": "above",
        "alerts.below": "below",
        # digest
        "digest.status": (
            "📰 <b>Daily digest</b>\n\n"
            "Status: <b>{state}</b>\n"
            "Send time: every day at {time}.\n\n"
            "Configure your asset set or collect the digest right now."
        ),
        "digest.status.on": "🔔 enabled",
        "digest.status.off": "🔕 disabled",
        "digest.btn.setup": "⚙️ Configure set",
        "digest.btn.time": "⏰ Time",
        "digest.btn.reset_time": "🔄 Reset",
        "digest.time_hour": "⏰ <b>Digest time</b>\n\nChoose the hour:",
        "digest.time_minute": "⏰ Hour: <b>{hour}</b>. Now the minutes:",
        "digest.time_saved": "✅ Digest time: <b>{time}</b>",
        "digest.btn.send": "📤 Send now",
        "digest.btn.subscribe": "🔔 Subscribe",
        "digest.btn.unsubscribe": "🔕 Unsubscribe",
        "digest.btn.back": "↩️ Back",
        "digest.btn.categories": "↩️ Categories",
        "digest.subscribed": "✅ You are subscribed to the daily digest.",
        "digest.unsubscribed": "🔕 You unsubscribed from the digest.",
        "digest.sending": "Collecting digest…",
        "digest.failed": "😔 Could not collect the digest. Try again later.",
        "digest.setup.title": (
            "⚙️ <b>Digest settings</b>\n\n"
            "Choose a category to add assets to your set. "
            "If the set is empty — the default top is sent."
        ),
        "digest.setup_cat": (
            "{icon} <b>{title}</b> ({sel}/{total})\n\n"
            "Press an asset to enable or disable it in the digest."
        ),
        "digest.type.fx": "Currencies",
        "digest.type.stock": "Stocks",
        "digest.type.stock_world": "World stocks 🌍",
        "digest.type.stock_ru": "RU stocks 🇷🇺",
        "digest.type.index": "Indexes",
        "digest.type.crypto": "Crypto",
        "digest.build.title_morning": "🌅 <b>Good morning! Daily digest</b>",
        "digest.build.title_day": "☀️ <b>Good afternoon! Daily digest</b>",
        "digest.build.title_evening": "🌆 <b>Good evening! Daily digest</b>",
        "digest.build.title_night": "🌙 <b>Good night! Daily digest</b>",
        "digest.section.fx": "💱 <b>Currencies</b>",
        "digest.section.fx_default": "💱 <b>CBR rates</b>",
        "digest.section.stock": "📈 <b>Stocks</b>",
        "digest.section.stock_world": "📈 <b>Stocks 🌍 World</b>",
        "digest.section.stock_ru": "📈 <b>Stocks 🇷🇺 RU</b>",
        "digest.section.index": "📊 <b>Indexes</b>",
        "digest.section.crypto": "🪙 <b>Crypto</b>",
        "digest.portfolio_title": "📁 <b>Your portfolio</b>",
        "digest.disclaimer": "\n\n— <i>This is not investment advice.</i>",
        # admin
        "admin.title": (
            "🔐 <b>Admin panel</b>\n\n"
            "👥 Users: <b>{users}</b>\n"
            "🚫 Banned: <b>{banned}</b>\n"
            "🕐 Uptime: <b>{uptime}</b>\n"
            "⚙️ Message limit: {rate}/min\n\n"
            "🗂 Cache: {entries} entries, hits {hits}, misses {misses}\n"
            "📊 Popular commands:\n{top}\n\n"
            "Messages: {messages}, callbacks: {callbacks}"
        ),
        "admin.no_commands": "  none yet",
        "admin.cache": (
            "🗂 <b>Cache</b>\n"
            "Entries: {entries}\n"
            "Hits: {hits}\n"
            "Misses: {misses}\n"
            "Efficiency: {rate}"
        ),
        "admin.recent.empty": "Query history is empty yet.",
        "admin.recent.title": "🕐 <b>Recent queries</b>",
        "admin.users.title": "👥 <b>Users</b> — total: {total}",
        "admin.users.empty": "none yet",
        "admin.broadcast.usage": "Specify the broadcast text: /broadcast Hello everyone!",
        "admin.broadcast.no_users": "No registered users.",
        "admin.broadcast.confirm": (
            "⚠️ <b>Broadcast</b> to {n} users?\n\n"
            "<blockquote>{text}</blockquote>\n"
            "Confirm or cancel:"
        ),
        "admin.broadcast.yes": "✅ Yes, send",
        "admin.broadcast.cancel": "❌ Cancel",
        "admin.broadcast.stale": "Broadcast already cancelled or finished.",
        "admin.broadcast.inactive": "Broadcast is not active.",
        "admin.broadcast.cancelled": "❌ Broadcast cancelled.",
        "admin.broadcast.sending": "📤 Sending broadcast…",
        "admin.broadcast.done": (
            "✅ Broadcast finished: sent <b>{sent}</b>, errors <b>{failed}</b>."
        ),
        "admin.ban.usage": "Specify Telegram ID: /ban 123456789",
        "admin.ban.done": "🚫 User <code>{id}</code> banned.",
        "admin.ban.missing": (
            "⚠️ User <code>{id}</code> not found (still blocked "
            "on their next requests)."
        ),
        "admin.unban.usage": "Specify Telegram ID: /unban 123456789",
        "admin.unban.done": "✅ User <code>{id}</code> unbanned (if was in the DB).",
        "admin.myrole": "Your role: <b>{role}</b>",
        "admin.setrole.usage": (
            "Format: /setRole <id> <role>\nRoles: user, admin\n"
            "Example: /setrole 123456789 admin"
        ),
        "admin.setrole.missing": "⚠️ User <code>{id}</code> not found.",
        "admin.setrole.done": "✅ Role of user <code>{id}</code> → <b>{role}</b>",
        # language
        "lang.prompt": "🌐 <b>Выбери язык / Choose language</b>",
        "lang.set": "✅ Language: {name}",
        "lang.menu_ready": "Main menu updated. 🗂",
        "lang.name.ru": "🇷🇺 Русский",
        "lang.name.en": "🇬🇧 English",
        # throttling
        "throttle.message": "⏳ Too often! Wait a bit and try again.",
        "throttle.callback": "⏳ Too often! Wait a bit.",
        # commands in the Telegram interface
        "cmd.start": "Main menu",
        "cmd.rate": "Currency rate: /rate USD",
        "cmd.convert": "Converter: /convert 100 USD RUB",
        "cmd.stock": "Stock price: /stock AAPL",
        "cmd.crypto": "Crypto price: /crypto BTC",
        "cmd.chart": "Price chart: /chart BTC",
        "cmd.trending": "Top trending coins",
        "cmd.top": "Top by market cap",
        "cmd.news": "Ticker news: /news AAPL",
        "cmd.analyze": "AI analysis: /analyze BTC",
        "cmd.portfolio": "My portfolio",
        "cmd.alert": "Alert: /alert BTC 70000",
        "cmd.alerts": "My alerts",
        "cmd.digest": "Daily digest",
        "cmd.myrole": "My role",
        "cmd.lang": "Language / Язык",
        "cmd.help": "Help",
        "cmd.admin": "Admin panel",
        "cmd.users": "Users list",
        "cmd.broadcast": "Broadcast: /broadcast text",
        "cmd.ban": "Ban: /ban id",
        "cmd.unban": "Unban: /unban id",
        "cmd.cachestats": "Cache stats",
        "cmd.recent": "Recent queries",
        "cmd.setrole": "Set role: /setRole id role",
    },
}


def get_lang() -> str:
    """Current language (context variable or the .env default)."""
    lang = _lang.get()
    if lang in SUPPORTED_LANGUAGES:
        return lang
    settings_lang = get_settings().default_language
    return settings_lang if settings_lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def set_lang(lang: str) -> None:
    """Sets the language in the current context (called by middleware)."""
    _lang.set(lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE)


def reset_lang() -> None:
    """Resets the language to the configured default (for tests)."""
    _lang.set("")


def t(key: str, **kwargs: Any) -> str:
    """Returns the string for the key in the current language; fills kwargs.

    An unknown key is returned as-is (does not crash the bot).
    """
    lang = get_lang()
    template = _STRINGS.get(lang, {}).get(key)
    if template is None:
        template = _STRINGS.get(DEFAULT_LANGUAGE, {}).get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template

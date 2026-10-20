"""PDF: отчёт клиентке и пакет доказательств для юриста (reportlab)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..config import settings
from ..models import FINDING_KINDS, FINDING_STATUSES, SERVICES, TAKEDOWN_STATUSES

FONT = "Helvetica"
for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
             "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
    if Path(cand).exists():
        pdfmetrics.registerFont(TTFont("Body", cand))
        FONT = "Body"
        break

ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Title"], fontName=FONT, fontSize=16, spaceAfter=6, alignment=TA_LEFT)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=FONT, fontSize=12, spaceBefore=10, spaceAfter=4)
P = ParagraphStyle("p", parent=ss["Normal"], fontName=FONT, fontSize=9.5, leading=13)
SMALL = ParagraphStyle("s", parent=P, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
MONO = ParagraphStyle("m", parent=P, fontName="Courier", fontSize=7.5, leading=9.5)

GRID = TableStyle([
    ("FONTNAME", (0, 0), (-1, -1), FONT), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#efe9dc")),
    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
])


def _esc(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(15 * mm, 10 * mm, "PRIVÉ · Digital Privacy Services · конфиденциально · не для чужих глаз")
    canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"стр. {doc.page}")
    canvas.restoreState()


def client_report(order) -> Path:
    """Отчёт клиентке: без внутренних источников (PimEyes и т.п. не упоминаются)."""
    out = settings.storage_dir / "reports" / f"order_{order.id}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"PRIVE_report_{order.id}_{datetime.utcnow():%Y%m%d}.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm)
    svc = SERVICES.get(order.service, {})
    el = [Paragraph("PRIVÉ", H1), Paragraph(f"{_esc(svc.get('name', order.service))} · заказ {order.id} · {datetime.utcnow():%d.%m.%Y}", P),
          Paragraph("Не для чужих глаз. Закрытый сервис, который возвращает тебе контроль над тем, что о тебе видно в интернете.", SMALL), Spacer(1, 8)]
    visible = [f for f in order.findings if f.status != "ignored"]
    if order.service == "check_free":
        counts: dict[str, int] = {}
        for f in visible:
            counts[f.kind] = counts.get(f.kind, 0) + 1
        el.append(Paragraph("Что нашли", H2))
        rows = [["Тип", "Сколько"]] + [[FINDING_KINDS.get(k, k), str(n)] for k, n in counts.items()] or [["—", "0"]]
        t = Table(rows, colWidths=[80 * mm, 30 * mm]); t.setStyle(GRID); el.append(t)
        el.append(Spacer(1, 6))
        el.append(Paragraph("Ссылки, оценка риска и план снятия с ценой — в «Проверке» за $190. Эти деньги засчитываются в следующий заказ.", P))
    else:
        confirmed = [f for f in visible if f.status in ("confirmed", "takedown", "removed", "returned")]
        pending = [f for f in visible if f.status == "new"]
        by = {}
        for f in confirmed:
            by[f.kind] = by.get(f.kind, 0) + 1
        hi = sum(1 for f in confirmed if f.risk == 3)
        el.append(Paragraph("Коротко", H2))
        summary = (f"Подтверждено {len(confirmed)} находок" + (f", из них {hi} высокого риска" if hi else "")
                   + (f"; ещё {len(pending)} требуют вашего ответа «моё / не моё»" if pending else "") + ".")
        el.append(Paragraph(summary, P))
        meaning = {
            "anketa": "Анкеты с вашими фото: если размещение не ваше — это основание для снятия по авторскому праву; если ваше — мы их не трогаем.",
            "fake": "Фейки: кто-то выдаёт себя за вас или использует ваши фото под другим ником. Снимаются как impersonation и нарушение авторских прав; при указании вашего реального номера — высокий риск.",
            "leak": "Сливы: интимные материалы или личные данные вне анкет. Снимаются в приоритетном порядке (NCII-жалобы), хеши регистрируются в StopNCII.",
            "mention": "Упоминания: текст без ваших фото. Снимаются реже; важны, если связывают рабочий ник с личным именем или номером.",
        }
        for k, n in by.items():
            if k in meaning:
                el.append(Paragraph(f"<b>{FINDING_KINDS.get(k, k)} — {n}.</b> {meaning[k]}", P))
        el.append(Paragraph("Находки", H2))
        rows = [["#", "Ссылка", "Тип", "Риск", "Статус", "Почему"]]
        for i, f in enumerate(sorted(visible, key=lambda x: (-(x.risk or 0), x.kind)), 1):
            rows.append([str(i), Paragraph(_esc(f.url), MONO), FINDING_KINDS.get(f.kind, f.kind), str(f.risk or "—"),
                         FINDING_STATUSES.get(f.status, f.status), Paragraph(_esc(f.auto_note or ""), SMALL)])
        t = Table(rows, colWidths=[7 * mm, 72 * mm, 26 * mm, 10 * mm, 24 * mm, 41 * mm], repeatRows=1); t.setStyle(GRID); el.append(t)
        if svc.get("kind") == "scan":
            el.append(Paragraph("Рекомендации", H2))
            recs = []
            if any(f.kind == "leak" and f.query_type == "email" for f in visible):
                recs.append("Ваша почта есть в утечках: смените пароль и включите двухфакторную защиту через приложение.")
            if any(f.kind == "leak" and f.query_type != "email" for f in confirmed):
                recs.append("Есть сливы: не удаляйте свои копии материалов — они нужны как доказательство авторства; зарегистрируйте хеши в StopNCII (инструкция в боте).")
            if any(f.kind == "fake" for f in confirmed):
                recs.append("Есть фейки: не вступайте в переписку с их авторами; собирайте скриншоты, мы снимаем профили по форме impersonation.")
            if any(f.risk == 3 for f in confirmed):
                recs.append("Есть связь рабочего ника с личными данными: рекомендуем «Невидимку» — разделение личного и рабочего, исключение из поисковиков по лицу.")
            if not recs:
                recs.append("Критичных находок нет. Рекомендуем подписку «Охрана Lite» — регулярная проверка, чтобы новое не копилось.")
            for r in recs:
                el.append(Paragraph("• " + r, P))
            el.append(Paragraph("План снятия", H2))
            fakes = [f for f in confirmed if f.kind in ("fake", "anketa")]  # подтверждённая анкета = не её размещение
            leaks = [f for f in confirmed if f.kind == "leak" and f.query_type != "email"]
            total = 0
            lines = []
            if fakes:
                extra = max(0, len(fakes) - 10)
                price = SERVICES["fakes"]["price"] + extra * 39
                total += price
                lines.append(f"«Фейки и чужие анкеты»: {len(fakes)} ссыл. — ${price}" + (f" (10 в пакете + {extra} × $39)" if extra else ""))
            if leaks:
                extra = max(0, len(leaks) - 30)
                price = SERVICES["leaks"]["price"] + extra * 15
                total += price
                lines.append(f"«Сливы»: {len(leaks)} ссыл., 60 дней контроля — ${price}" + (f" (30 в пакете + {extra} × $15)" if extra else ""))
            if len(fakes) + len(leaks) > 40:
                lines.append(f"Находок много — выгоднее «Выход Стандарт» ${SERVICES['exit_std']['price']} (до 300 ссылок, 6 месяцев контроля, рассрочка).")
            if lines:
                for l in lines:
                    el.append(Paragraph("• " + l, P))
                el.append(Paragraph(f"Итого по пакетам: ${total}, за вычетом $190 за эту проверку — <b>${max(total - 190, 0)}</b>. Оплата 50% вперёд, 50% после первой снятой ссылки.", P))
            else:
                el.append(Paragraph("Снимать нечего — подтверждённых чужих размещений нет.", P))
    el += [Spacer(1, 10), Paragraph("Мы не храним эталонные фото дольше, чем нужно для заказа. Любые вопросы — ответом в бот.", SMALL)]
    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return path


def lawyer_package(order) -> Path:
    """Пакет для юриста: каждая находка с доказательствами, хешами, архивными копиями и историей жалоб."""
    out = settings.storage_dir / "reports" / f"order_{order.id}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"PRIVE_evidence_{order.id}_{datetime.utcnow():%Y%m%d}.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm)
    el = [Paragraph("Пакет доказательств / Evidence package", H1),
          Paragraph(f"Заказ {order.id} · клиентка {_esc(order.client.code)} · сформирован {datetime.utcnow():%d.%m.%Y %H:%M} UTC", P),
          Paragraph("Все файлы хешированы SHA-256 в момент фиксации; метки времени OpenTimestamps прилагаются при наличии. Имя клиентки в пакете отсутствует намеренно и сообщается юристу отдельно.", SMALL)]
    for i, f in enumerate([x for x in order.findings if x.status != "ignored"], 1):
        el += [PageBreak(), Paragraph(f"Находка {i}: {FINDING_KINDS.get(f.kind, f.kind)}, риск {f.risk or '—'}", H2),
               Paragraph(f"URL: {_esc(f.url)}", MONO),
               Paragraph(f"Обнаружено {f.found_at:%d.%m.%Y %H:%M} UTC · запрос: {_esc(f.query_type)} «{_esc(f.query_value)}» · совпадение: {_esc(f.match_type)} {f.confidence or ''}", SMALL)]
        if f.evidence:
            rows = [["Зафиксировано (UTC)", "HTTP", "SHA-256 страницы", "Архивные копии", "OTS"]]
            for e in f.evidence:
                arch = " / ".join(x for x in (e.archive_url, e.archive_today_url) if x) or "—"
                rows.append([f"{e.captured_at:%d.%m.%Y %H:%M}", str(e.http_status or "—"), Paragraph(e.sha256 or "—", MONO), Paragraph(_esc(arch), MONO), "да" if e.ots_file else "нет"])
            t = Table(rows, colWidths=[30 * mm, 12 * mm, 55 * mm, 70 * mm, 12 * mm]); t.setStyle(GRID); el.append(t)
            shot = next((e.screenshot_file for e in f.evidence if e.screenshot_file), "") or f.screenshot_file
            if shot and (settings.storage_dir / shot).exists():
                try:
                    img = Image(str(settings.storage_dir / shot)); img._restrictSize(170 * mm, 120 * mm)
                    el += [Spacer(1, 4), img]
                except Exception:
                    pass
        else:
            el.append(Paragraph("Доказательства не зафиксированы — выполнить «Зафиксировать» в панели.", SMALL))
        if f.takedowns:
            el.append(Paragraph("История жалоб", H2))
            rows = [["Дата", "Площадка", "Канал", "Шаблон", "Статус"]]
            for t_ in f.takedowns:
                rows.append([f"{(t_.sent_at or t_.created_at):%d.%m.%Y}", t_.platform, t_.channel, t_.template, TAKEDOWN_STATUSES.get(t_.status, t_.status)])
            t = Table(rows, colWidths=[22 * mm, 60 * mm, 18 * mm, 30 * mm, 30 * mm]); t.setStyle(GRID); el.append(t)
            for t_ in f.takedowns:
                for l in t_.log:
                    el.append(Paragraph(f"{l['at'][:16]} — {_esc(l['text'])}", SMALL))
    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return path

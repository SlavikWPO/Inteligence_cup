"""Конвейер обработки каждого обращения: сервисы 1–5 из архитектуры.

1. Коннектор — читает обращения, отсеивает пустые, служебные и повторные.
2. Распознавание — звонок без описания -> текст (Qwen-ASR), вложение -> описание (Qwen-VL).
   В прототипе расшифровка и описание уже лежат в данных: это заглушка моделей.
3. Маскирование ПДн — до любой языковой модели, логов и выдачи.
4. Обогащение — суть, тип, критичность, скрытая потребность (Qwen 3.8; в прототипе — RuleBasedLLM).
5. Группировка — отнесение к теме.
"""
import json
import re

# ---------- 1. Коннектор ----------
def fetch_tickets(path):
    """Читает JSONL и отсеивает мусор до вызова моделей (приём снижения нагрузки)."""
    seen, kept, dropped = set(), [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            t = json.loads(line)
            if t["ticket_id"] in seen:
                dropped.append((t["ticket_id"], "повторный тикет"))
                continue
            seen.add(t["ticket_id"])
            if t.get("service"):
                dropped.append((t["ticket_id"], "служебный"))
            elif not (t.get("text", "").strip() or t.get("transcript", "").strip()):
                dropped.append((t["ticket_id"], "пустой"))
            else:
                kept.append(t)
    return kept, dropped

# ---------- 2. Распознавание ----------
def recognize(ticket, stats):
    """ASR вызывается только для звонка без текстового описания, VL — только при вложении."""
    parts = [ticket.get("text", "").strip()]
    if ticket["channel"] == "call" and not parts[0]:
        stats["asr_calls"] += 1
        parts[0] = ticket.get("transcript", "")      # заглушка Qwen-ASR
    if ticket.get("attachment"):
        stats["vl_calls"] += 1
        parts.append("Вложение: " + ticket["attachment"])  # заглушка Qwen-VL
    return " ".join(p for p in parts if p)

# ---------- 3. Маскирование ПДн ----------
PII_PATTERNS = [
    ("[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("[ТЕЛЕФОН]", re.compile(r"(?:\+7|\b8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")),
    ("[ИНН]", re.compile(r"(?i)\bИНН\s*:?\s*\d{10,12}\b")),
    ("[ФИО]", re.compile(r"\b[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+(?:вич|вна|ична|ич)\b")),
    ("[ФИО]", re.compile(r"\b[А-ЯЁ][а-яё]+\s[А-ЯЁ]\.\s?[А-ЯЁ]\.")),
]

def mask_pii(text):
    for token, rx in PII_PATTERNS:
        text = rx.sub(token, text)
    return text

def has_pii(text):
    return any(rx.search(text) for _, rx in PII_PATTERNS)

# ---------- 4–5. Обогащение и группировка ----------
def process(path, llm):
    stats = {"asr_calls": 0, "vl_calls": 0}
    tickets, dropped = fetch_tickets(path)
    enriched = []
    for t in tickets:
        text = mask_pii(recognize(t, stats))
        info = llm.enrich(text)                       # строгий JSON по одному обращению
        enriched.append({**{k: t[k] for k in ("ticket_id", "created", "client_id", "product")},
                         "text": text, **info})
    return enriched, dropped, stats

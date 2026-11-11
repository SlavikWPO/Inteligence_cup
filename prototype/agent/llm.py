"""Интерфейс языковой модели.

RuleBasedLLM — прозрачная заглушка для демонстрации логики без доступа к Qwen.
QwenClient — как подключается внутренняя Qwen 3.8 (OpenAI-совместимый API внутри контура);
адрес задаётся переменной окружения, в прототипе не вызывается.
"""
import json
import os
import re
import urllib.request

from .prompts import ENRICH_PROMPT

INJECTION = re.compile(r"(?i)(игнорир\w*\s+(все\s+)?(предыдущ|прошл)\w*\s+инструкц|ignore (all )?previous|ты теперь|системн\w+ промпт)")

RULES = [  # (условие, тема, потребность, тип, критичность)
    (lambda s: ("заявк" in s) and any(w in s for w in ("контакт", "телефон", "номер")),
     "edit_contacts", "Клиент сам редактирует контакты в созданной заявке", "feature_request", "hinders"),
    (lambda s: ("логин" in s or "пароль" in s) and "договор" in s,
     "access_old_contract", "Самостоятельное восстановление доступа по действующему договору", "process_pain", "hinders"),
    (lambda s: "очеред" in s and any(w in s for w in ("404", "не существует", "записат", "заброниро")),
     "queue_booking", "Бронирование электронной очереди без ошибок", "defect", "blocks"),
    (lambda s: "оферт" in s and any(w in s for w in (" ип", "опф", "организационно-правов")),
     "opf_offer", "Корректная организационно-правовая форма в договоре оферты", "defect", "blocks"),
    (lambda s: "машин" in s and "справочник" in s,
     "vehicle_catalog", "Управление справочником машин без ошибок", "defect", "blocks"),
]

class RuleBasedLLM:
    """Заглушка Qwen 3.8: возвращает тот же JSON, что и боевой промпт обогащения."""
    def enrich(self, text):
        s = " " + text.lower()
        if INJECTION.search(text):
            return {"topic_key": None, "type": "unknown", "criticality": "minor",
                    "need": None, "suspicious": True}
        for cond, key, need, typ, crit in RULES:
            if cond(s):
                return {"topic_key": key, "type": typ, "criticality": crit, "need": need, "suspicious": False}
        return {"topic_key": None, "type": "unknown", "criticality": "minor", "need": None, "suspicious": False}

class QwenClient:
    """Боевое подключение: POST {QWEN_URL}/v1/chat/completions внутри контура, без внешних API."""
    def __init__(self, url=None, model="qwen-3.8"):
        self.url = url or os.environ["QWEN_URL"]
        self.model = model

    def enrich(self, text):
        body = {"model": self.model, "temperature": 0,
                "messages": [{"role": "system", "content": ENRICH_PROMPT},
                             {"role": "user", "content": json.dumps({"ticket_text": text}, ensure_ascii=False)}]}
        req = urllib.request.Request(self.url.rstrip("/") + "/v1/chat/completions",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            content = json.load(r)["choices"][0]["message"]["content"]
        return json.loads(content)   # невалидный JSON -> исключение -> повтор оркестратором

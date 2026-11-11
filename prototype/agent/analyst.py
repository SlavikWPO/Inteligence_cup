"""Сервисы 6–7: агент-аналитик (решение о сигнале) и доставка в трекер.

В боевой версии решение принимает Qwen 3.8 с ANALYST_PROMPT через tools; здесь те же правила
порога и идемпотентности выполняются кодом, чтобы логику можно было проверить тестами.
"""
import json
import os
from collections import defaultdict
from datetime import date, timedelta

from .pipeline import has_pii

SIGNAL_KEYS = {"topic_id": str, "title": str, "type": str, "need": str, "clients": int, "trend": str,
               "evidence": list, "priority": str, "confidence": float}
PRIORITY = {"blocks": "high", "hinders": "medium", "minor": "low"}

def d(s):
    return date.fromisoformat(s)

def topic_stats(enriched, as_of, window_days):
    """Считает РАЗНЫХ клиентов по теме в окне T и по двум последним неделям."""
    start, wk1, wk0 = as_of - timedelta(days=window_days), as_of - timedelta(days=7), as_of - timedelta(days=14)
    topics = defaultdict(lambda: {"clients": set(), "last_week": set(), "prev_week": set(), "tickets": []})
    for e in enriched:
        if e["suspicious"] or not e["topic_key"] or not (start < d(e["created"]) <= as_of):
            continue
        t = topics[e["topic_key"]]
        t["clients"].add(e["client_id"]); t["tickets"].append(e)
        if d(e["created"]) > wk1: t["last_week"].add(e["client_id"])
        elif d(e["created"]) > wk0: t["prev_week"].add(e["client_id"])
    return topics

def decide(t, n_min):
    clients, last, prev = len(t["clients"]), len(t["last_week"]), len(t["prev_week"])
    growth = last >= 3 and last >= 2 * max(prev, 1)
    return clients >= n_min or growth, growth

def build_signal(key, t, growth):
    first = t["tickets"][0]
    clients = len(t["clients"])
    trend = f"за неделю {len(t['prev_week'])} -> {len(t['last_week'])} клиентов" + (" (рост)" if growth else "")
    sig = {"topic_id": key, "title": first["need"], "type": first["type"], "need": first["need"],
           "clients": clients, "trend": trend, "evidence": [e["ticket_id"] for e in t["tickets"]][:5],
           "priority": PRIORITY[first["criticality"]], "confidence": round(min(0.95, 0.5 + 0.08 * clients), 2),
           "examples": list(dict.fromkeys(e["text"] for e in t["tickets"]))[:3]}
    return sig

def validate_signal(sig):
    """Код проверяет схему и отсутствие ПДн до отправки — проверка из компонента 4."""
    for k, typ in SIGNAL_KEYS.items():
        if not isinstance(sig.get(k), typ):
            raise ValueError(f"поле {k}: ожидался {typ.__name__}")
    if not set(sig["evidence"]) or sig["clients"] < 1:
        raise ValueError("сигнал без доказательств")
    if any(has_pii(x) for x in [sig["title"], sig["need"], *sig.get("examples", [])]):
        raise ValueError("в сигнале остались персональные данные")
    return True

def analyze_and_deliver(enriched, as_of, state_path, n_min=5, window_days=30):
    """Создаёт сигнал или обновляет существующий; повторный запуск не плодит дубли."""
    state = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    report = {"created": [], "updated": [], "unchanged": [], "below_threshold": [],
              "suspicious": [e["ticket_id"] for e in enriched if e["suspicious"]]}
    for key, t in sorted(topic_stats(enriched, as_of, window_days).items()):
        ok, growth = decide(t, n_min)
        if not ok:
            report["below_threshold"].append((key, len(t["clients"]))); continue
        sig = build_signal(key, t, growth); validate_signal(sig)
        prev = state.get(key)
        if prev and (as_of - d(prev["created_on"])).days < window_days:
            if prev["signal"] == sig: report["unchanged"].append(key)          # идемпотентность
            else: prev["signal"] = sig; report["updated"].append(key)          # update_signal
        else:
            state[key] = {"created_on": as_of.isoformat(), "signal": sig}; report["created"].append(key)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return report, state

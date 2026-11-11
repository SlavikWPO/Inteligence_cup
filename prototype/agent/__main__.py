"""Запуск: python -m agent [--data data/tickets.jsonl] [--as-of 2026-11-10] [--state out/state.json]"""
import argparse
import os
from datetime import date

from .analyst import analyze_and_deliver
from .llm import RuleBasedLLM
from .pipeline import process

def main():
    p = argparse.ArgumentParser(description="Ночной прогон агента на обращениях")
    p.add_argument("--data", default="data/tickets.jsonl")
    p.add_argument("--as-of", default="2026-11-10")
    p.add_argument("--state", default="out/state.json")
    p.add_argument("--min-clients", type=int, default=5)
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.state) or ".", exist_ok=True)

    enriched, dropped, stats = process(a.data, RuleBasedLLM())
    report, state = analyze_and_deliver(enriched, date.fromisoformat(a.as_of), a.state, n_min=a.min_clients)

    print(f"Обращений принято в обработку: {len(enriched)}; отсеяно до LLM: {len(dropped)} {dropped}")
    print(f"Вызовов ASR: {stats['asr_calls']}, VL: {stats['vl_calls']}")
    print(f"Подозрительные (на ручную проверку, не в сигналы): {report['suspicious']}")
    print(f"Ниже порога: {report['below_threshold']}")
    for kind in ("created", "updated", "unchanged"):
        for key in report[kind]:
            s = state[key]["signal"]
            print(f"\n[{kind}] {s['title']} | {s['type']} | клиентов: {s['clients']} | {s['trend']} | "
                  f"приоритет: {s['priority']} | тикеты: {', '.join(s['evidence'])}")
            for ex in s["examples"]:
                print("   пример:", ex)

if __name__ == "__main__":
    main()

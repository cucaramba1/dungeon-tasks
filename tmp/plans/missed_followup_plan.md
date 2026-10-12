# Follow-up после «❌ Не сделал»

Текущее поведение: `ym:` в bot.py:331 сразу ставит status='missed' — финал, без диалога.
Задача выпадает из overdue_open() и больше никогда не всплывает.

## Новое поведение

1. Новый промежуточный статус `missed_pending` (текстовое поле status, без миграции схемы).
   - `ym:` → `mark_missed_pending(tid)` → status='missed_pending'.
   - Карточка: «❌ {title} — не сделано. Будешь делать?» + 3 кнопки.

2. Клавиатура `keyboards.missed_followup(t)`:
   - 📅 Сегодня → `rs:{id}:today`
   - 🗓 Другая дата → `rs:{id}:ask`
   - 🚫 Не нужно → `rs:{id}:drop`

3. Callback-хендлер `rs:(id):(today|ask|drop)`:
   - today → `svc.reopen_today(tid)`: due_at=сегодня, has_time=0, status='open'.
   - ask → карточка с просьбой ответить датой (+ #{id} в тексте для reply-flow).
   - drop → `svc.mark_missed(tid)` (финал).

4. Правка `edit_from_text`: если распарсена дата и текущий status missed/missed_pending — ставим status='open'.

5. Новый метод `services.missed_pending()` — список задач со статусом missed_pending.

6. В scheduler.py утренний блок: `send_missed_followup(app, svc)` — повторный вопрос на следующее утро для игнорированных missed_pending.

Не трогаем events/XP/стрики.

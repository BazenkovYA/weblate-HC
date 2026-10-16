<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Полный цикл качества визарда и канонизация повторов

> **Для исполнителей:** REQUIRED SUB-SKILL: использовать
> `subagent-driven-development` (рекомендуется) либо `executing-plans` и
> выполнять задачи по порядку. Шаги отмечаются checkbox (`- [ ]`).

**Статус:** предложен; код и production этим документом не изменены.

**Цель:** после машинного перевода FastAPI-визард должен проверить немецкий
перевод двумя местами штатного судьи Weblate, исправить безопасные замечания,
проверить обязательную терминологию, привести точные повторы к одному варианту,
повторно проверить только изменённые строки и вернуть свежий исправленный файл
в исходной структуре loc-kit.

**Архитектура:** Weblate остаётся единственным владельцем текста, glossary,
repeat-групп, judge evidence, кандидатов, проверок, аудита и применения.
Визард хранит идентификаторы, revisions, snapshot hashes и состояние workflow,
но не выбирает перевод по открытому тексту и не пишет `Unit.target` напрямую.
Индивидуальные исправления судей выполняются до унификации повторов; затем
repeat-группы строятся повторно, и уникальный вариант, только что исправленный
судьями, становится первым кандидатом канона после обязательных safety guards.

**Технологии:** Django REST Framework, Weblate checks/judge/repeat machinery,
Celery, PostgreSQL, FastAPI, SQLAlchemy/Alembic, httpx, React/TypeScript,
XLSX/CSV export.

**Спецификация:**
`docs/product/plans/2026-10-13-fastapi-dual-judge-automatic-quality-cycle.md`.

**Связанный выпуск:**
`docs/operations/plans/2026-10-13-producer-quality-api-server-release.md`.

## Подтверждённая исходная точка

- Production Weblate работает на `de468d90f63b`; новые quality endpoints там
  отсутствуют.
- На production включены `JUDGE_ENABLED=True`, `JUDGE_SEATS=(1, 2)`, оба места
  валидно настроены; существующий завершённый run содержит evidence seats 1 и
  2.
- Для проекта `wizard-2c0ef0fba7cf4cb9-ea1a3601` включён `repeat-drift`; в
  немецком переводе сохранено 19 текущих срабатываний.
- В проекте есть два glossary-компонента и 197 заполненных немецких
  glossary-unit, однако `check_glossary` для `kit-1` выключен. Поэтому текущая
  реализация, читающая только таблицу `Check`, не увидит glossary deviations.
- Немецкий judge repair маршрутизируется в `google/gemini-3.7-flash`;
  `JUDGE_MAX_REPAIR_ATTEMPTS=1`.
- Ветка Weblate `codex/fastapi-dual-judge-quality-plan` уже реализует decisions,
  quality issues/recheck и `judge_seats`, но ещё не развёрнута.
- Ветка визарда `codex/dual-judge-quality-cycle` уже реализует durable cycle,
  typed client, UI summary и свежую загрузку перед экспортом, но пока
  применяет только обычные judge candidates. Она не запускает штатный
  repeat recommendation/bulk apply и сужает следующий раунд только до
  непосредственно применённых unit.

## Выбранный алгоритм

```text
import / MT empty-only
  -> preflight + закрытый scope snapshot
  -> direct glossary + repeat baseline
  -> один dual-judge run по исходному scope
  -> безопасные индивидуальные judge/glossary repairs
  -> recheck изменённых unit и пересчёт затронутых repeat-групп
  -> новый repeat snapshot
  -> выбор канона группы с приоритетом judge-updated варианта
  -> безопасное repeat apply
  -> dual-judge только изменённых получателей
  -> повторный glossary/repeat scan затронутых групп
  -> не более одного дополнительного repair-раунда
  -> стабильный final snapshot
  -> свежая загрузка переводов
  -> повторная проверка snapshot hash
  -> export corrected file
  -> completed | completed_with_exceptions
```

Этот порядок предпочтительнее запуска repeat-унификации до судей. Иначе один
старый вариант сначала размножается по группе, а затем судьи исправляют его во
многих местах. После индивидуального judge repair канон выбирается из уже
исправленного состояния, а платный recheck получает только реально изменённые
unit.

## Политика «исправление судей как первое вхождение»

«Первое» означает приоритет проверенного происхождения, а не минимальный номер
строки или физический порядок в XLSX.

1. Нельзя автоматически менять human-approved строку. Два разных approved
   target в одной группе дают terminal exception `approved_conflict`.
2. Нельзя выбрать target, нарушающий `exact`, `read-only` или `forbidden`
   glossary rule либо детерминированные проверки markup/placeholders/numbers/
   game tokens/line separator.
3. После этих guards первым кандидатом считается target текущей ревизии,
   полученный успешным judge application в этом quality cycle.
4. Если все judge-updated anchors группы имеют один target hash, он становится
   preferred canonical target.
5. Если judge-updated anchors расходятся, порядок строк не разрешает конфликт:
   используется штатная repeat recommendation и коллегиальное evidence; без
   однозначного результата группа изолируется.
6. Если judge-updated anchor отсутствует, применяются существующие правила
   Weblate: единственный допустимый approved вариант, единственный passed
   вариант, затем проверенная recommendation.
7. `independent`, locked, readonly, stale и недоступные actor строки не
   переписываются и не мешают обработке остальных участников.

После любого judge или repeat apply группа строится заново из текущих данных.
Старый список участников и старый canonical hash повторно не используются.

## Глобальные ограничения

- Визард не получает source, target, candidate text, prompt или judge evidence
  из text-free Producer API.
- Любая запись выполняется штатным Weblate primitive с permission, revision,
  approval, glossary и stale guards; прямой PATCH target запрещён.
- Один judge run включает оба места; визард не запускает два параллельных run.
- Candidate считается пригодным только при parsed evidence обоих seats и
  совпавшей текущей revision.
- Glossary hard rule обязателен; morphology advisory сам по себе не разрешает
  автоматическую замену.
- Максимум два внешних semantic repair rounds. Внутренний Weblate repair-loop
  остаётся ограничен серверным `JUDGE_MAX_REPAIR_ATTEMPTS`.
- Mutating POST получает idempotency key или durable remote resource. После
  неизвестного результата повторный POST запрещён до read-after-write.
- Ошибка одной строки не останавливает проект; строка получает terminal
  outcome, а итог становится `completed_with_exceptions`.
- Экспорт строится только после стабильного финального snapshot и всегда из
  свежих данных Weblate, а не из MT-кэша до исправлений.
- Production deployment, рестарт, платный judge/recommendation run и запись в
  тестовый проект требуют отдельных явных разрешений.

## Файловая структура изменений

### Weblate

- `weblate/api/producer/quality.py` — direct glossary scan, scope revision и
  text-free quality snapshots.
- Новый `weblate/api/producer/repeat_quality.py` — server-side canonical
  selection и проекции repeat plan без текста.
- `weblate/api/producer/serializers.py`, `views.py`, `urls.py` — contracts
  repeat plan/apply, affected groups и final snapshot.
- `weblate/trans/repeat_judge.py`, `repeat_bulk.py`, `repeats.py` — выделение
  переиспользуемых helpers; UI и API используют одну политику.
- `weblate/trans/models/judge.py` — только если existing application lookup не
  позволяет доказать происхождение preferred anchor без N+1.
- `weblate/api/tests.py` и существующие repeat/judge tests — контракт и safety.
- `docs/api.rst`, `docs/changes.rst`, `docs/security/threat-model.rst`,
  `docs/specs/openapi.yaml` — опубликованный контракт.

### FastAPI-визард

- `backend/app/integrations/weblate.py` — typed repeat/final snapshot client.
- `backend/app/services/quality_cycle.py` — новый порядок фаз.
- `backend/app/services/quality_policy.py` — canonical policy и закрытые
  outcomes.
- `backend/app/services/quality_state.py`, `backend/app/models.py` и additive
  Alembic migration — durable group plans и snapshot lineage.
- `backend/app/worker.py` — ожидание цикла и stable export barrier.
- `backend/app/services/exports.py` — provenance итогового файла.
- `backend/app/services/quality_report.py` — полный census и причины исключений.
- `console/src/components/QualitySummary.tsx` и API contract — ход обработки и
  ссылка на исправленный файл.
- `tests/test_quality_cycle.py`, `test_quality_state.py`,
  `test_weblate_client.py`, `test_connected_export.py` — unit/contract/E2E.

## Фокус ревью

1. Два разных judge-updated target одной repeat-группы не должны разрешаться
   порядком строк; тест принадлежит задаче 4.
2. Glossary scan должен работать, даже когда project flag `check_glossary`
   выключен; тест принадлежит задаче 1.
3. Изменение любого участника между plan/apply должно сделать только эту
   группу stale без частичного скрытого overwrite; тест принадлежит задаче 4.
4. Export не должен смешивать unit из двух revisions при конкурентной правке;
   тест принадлежит задаче 8.
5. Timeout после repeat apply не должен создать второй `RepeatBulkRun` или
   второй `RepeatDecisionEvent`; тест принадлежит задаче 5.

---

### Задача 1. Сделать glossary scan независимым от UI-флага проверки

**Файлы:**

- Изменить: `weblate/api/producer/quality.py`
- Изменить: `weblate/api/producer/views.py`
- Тест: `weblate/api/tests.py`

**Интерфейсы:**

- Производит:
  `collect_quality_snapshot(*, project, language: str, kinds: tuple[str, ...], user, unit_ids: Collection[int] | None = None) -> dict[str, object]`.
- Snapshot содержит `issue_snapshot_hash`, `scope_revision`, issues и counts,
  но не переводные тексты.
- Glossary issues вычисляются через `evaluate_glossary_terms` по текущему
  закрытому scope, а не только по существующим строкам `Check`.

- [ ] Написать failing test: проект без `check_glossary`, unit нарушает exact
      term, `GET quality/issues` возвращает один `hard glossary` issue.
- [ ] Написать tests на `read-only`, `forbidden`, `not-applicable`, обычную
      немецкую словоформу и plural target; hard/advisory не смешиваются.
- [ ] Написать query-budget tests на 1 и 25 unit с заранее созданным glossary;
      рост запросов не должен быть линейным по числу терминов.
- [ ] Реализовать direct evaluation с bounded batching и существующими
      glossary caches/prefetch; не создавать и не удалять `Check` rows из GET.
- [ ] Добавить `scope_revision`: SHA-256 от упорядоченных `(unit_id,
      compute_decision_revision(unit))` полного запрошенного scope.
- [ ] Выполнить
      `./rundev.sh test weblate/api/tests.py -k "producer and glossary"`.
- [ ] Закоммитить: `fix(api): evaluate producer glossary independently`.

### Задача 2. Разрешить безопасный автоматический glossary repair

**Файлы:**

- Изменить: `weblate/api/producer/quality.py`
- Изменить: `weblate/api/producer/views.py`
- Изменить: `weblate/trans/judge_loop.py` только для переиспользуемой проверки
  candidate target без записи
- Тест: `weblate/api/tests.py`
- Тест: `weblate/trans/tests/test_judge_loop.py`

**Интерфейсы:**

- Производит:
  `candidate_quality(unit, target_plurals, *, evidence) -> CandidateQuality` с
  полями `two_seat_verified`, `deterministic_clean`, `glossary_hard_clean`,
  `glossary_advisory`.
- Decisions получают `candidate_glossary_status` со значениями
  `clean|advisory|hard`; исходная terminology category больше не блокирует
  candidate, если сам candidate `hard`-чист.

- [ ] Написать failing test: live target нарушает exact glossary, оба seats
      подтверждают candidate, candidate проходит glossary — applicability
      равно `ready`.
- [ ] Написать tests: candidate всё ещё нарушает hard term, unit approved,
      incomplete seat, новая deterministic failure и stale context — каждый
      случай остаётся неприменимым.
- [ ] Реализовать оценку candidate без сохранения target и без повторного
      provider call; использовать уже сохранённое candidate evidence.
- [ ] Проверить, что advisory без judge reject не создаёт repair сам по себе.
- [ ] Выполнить затронутые API и judge-loop tests.
- [ ] Закоммитить: `fix(judge): allow verified glossary repairs`.

### Задача 3. Зафиксировать group-aware recheck

**Файлы:**

- Изменить: `weblate/api/producer/quality.py`
- Изменить: `weblate/api/producer/serializers.py`
- Изменить: `weblate/api/producer/views.py`
- Тест: `weblate/api/tests.py`

**Интерфейсы:**

- `POST quality/recheck/` принимает не более 1000 уникальных
  `{unit_id, revision}`.
- Возвращает per-unit outcomes, `affected_repeat_group_ids`,
  `affected_unit_ids`, новый `issue_snapshot_hash` и `scope_revision`.

- [ ] Написать test: изменение одного участника exact repeat возвращает всю
      затронутую группу, а не только входной unit.
- [ ] Написать test: judge repair устраняет один вариант и создаёт новый;
      recheck удаляет старый repeat snapshot и отражает новый вариант.
- [ ] Написать duplicate, empty, over-1000, foreign-project, stale/ABA,
      forbidden и missing tests.
- [ ] Реализовать пересчёт через штатный `RepeatDriftCheck` и direct glossary
      evaluator; не доверять присланному клиентом списку group members.
- [ ] Выполнить
      `./rundev.sh test weblate/api/tests.py -k "producer and recheck"`.
- [ ] Закоммитить: `feat(api): return affected quality groups`.

### Задача 4. Построить server-side repeat resolution plan

**Файлы:**

- Создать: `weblate/api/producer/repeat_quality.py`
- Изменить: `weblate/trans/repeat_judge.py`
- Изменить: `weblate/trans/repeats.py`
- Изменить: `weblate/api/producer/serializers.py`, `views.py`, `urls.py`
- Тест: `weblate/api/tests.py`
- Тест: `weblate/trans/tests/test_repeat_judge.py`

**Интерфейсы:**

- Производит:
  `build_producer_repeat_plan(*, project, language, issue_snapshot_hash,
  application_ids, actor) -> ProducerRepeatPlan`.
- Добавляет:
  `POST /api/producer/projects/{slug}/quality/repeats/plan/` и
  `GET /api/producer/repeat-plans/{token}/`.
- План возвращает только token, group IDs, hashes, revisions, applicability,
  preferred origin и counts; target text не возвращается.

- [ ] Написать failing test: один актуальный `JudgeApplication` в группе
      становится `preferred_origin=judge_application` и первым canonical
      candidate.
- [ ] Написать test: два judge applications с одинаковым target hash дают
      один канон; с разными hashes дают `needs_recommendation`, а не выбор
      первого `unit_id`.
- [ ] Написать tests на single approved, conflicting approved, hard glossary,
      independent membership, locked component, overlapping policy и plural
      group.
- [ ] Выделить pure helper
      `select_canonical_candidate(group, *, preferred_applications,
      judgements, recommendation) -> CanonicalSelection`.
- [ ] Для `needs_recommendation` переиспользовать
      `RepeatRecommendationRun`/`RepeatRecommendationResult`; не создавать
      второй LLM-клиент в Producer API.
- [ ] Подписать frozen manifest actor/project/language/snapshot/revisions и
      ограничить срок существующим repeat manifest TTL.
- [ ] Выполнить API и repeat-judge tests.
- [ ] Закоммитить: `feat(api): plan canonical repeat repairs`.

### Задача 5. Открыть идемпотентное repeat apply и undo

**Файлы:**

- Изменить: `weblate/api/producer/repeat_quality.py`
- Изменить: `weblate/api/producer/serializers.py`, `views.py`, `urls.py`
- Изменить: `weblate/trans/repeat_bulk.py` только для producer-safe adapter
- Тест: `weblate/api/tests.py`
- Тест: `weblate/trans/tests/test_repeat_bulk.py`

**Интерфейсы:**

- Добавляет:
  `POST /api/producer/repeat-plans/{token}/apply/`,
  `GET /api/producer/repeat-runs/{token}/` и существующий audit/undo primitive.
- Apply возвращает per-group и per-unit outcomes, revisions, event/application
  IDs и `changed_unit_ids`.

- [ ] Написать tests на duplicate POST с одним manifest nonce: создаётся один
      `RepeatBulkRun` и не более одного `RepeatDecisionEvent` на группу.
- [ ] Написать tests на recipient revision drift, changed group revision,
      approved/locked/permission skip и успешность остальных групп.
- [ ] Применять существующие `plan_bulk`, `start_bulk`, `preview_group` и
      `apply_preview`; не копировать логику записи `Unit.translate` в API.
- [ ] Автоматически изменённые repeat recipients оставлять non-shippable до
      post-apply dual-judge recheck; итоговый judge projection возвращает их в
      нормальное состояние только после pass.
- [ ] Привязать preferred judge applications и repeat decision events к
      отчёту без сохранения текста в wizard.
- [ ] Выполнить API и repeat-bulk tests.
- [ ] Закоммитить: `feat(api): apply verified repeat plans`.

### Задача 6. Опубликовать capability, OpenAPI и security contract

**Файлы:**

- Изменить: `weblate/api/producer/serializers.py`, `views.py`
- Изменить: `docs/api.rst`, `docs/changes.rst`
- Изменить: `docs/security/threat-model.rst`
- Перегенерировать: `docs/specs/openapi.yaml`
- Тест: `weblate/api/tests.py`

**Интерфейсы:**

- `/producer/me/` дополнительно публикует
  `quality_repeat_plan=true` и `glossary_direct_scan=true` вместе с
  `judge_seats=2`.

- [ ] Написать capability tests для configured/unconfigured judge и
      отсутствующего repeat policy.
- [ ] Проверить object permissions и одинаковый `404` для чужих plan/run.
- [ ] Добавить recursive test, запрещающий source/target/candidate/prompt/
      evidence в новых JSON responses.
- [ ] Перегенерировать OpenAPI штатной командой из `docs/Makefile` и проверить
      только ожидаемый schema diff.
- [ ] Обновить threat model: paid recommendation, signed manifests,
      idempotency, retention, no-text boundary и private project isolation.
- [ ] Выполнить `uv run prek run --all-files`, API tests и `./rundev.sh check`.
- [ ] Закоммитить: `docs(api): document canonical repeat workflow`.

### Задача 7. Расширить durable-модель и typed client визарда

**Файлы:**

- Изменить в worktree визарда: `backend/app/models.py`
- Создать additive migration после `0002_quality_cycle.py`
- Изменить: `backend/app/services/quality_state.py`
- Изменить: `backend/app/integrations/weblate.py`
- Тест: `tests/test_quality_state.py`, `tests/test_weblate_client.py`

**Интерфейсы:**

- Durable phases:
  `initial-scan`, `dual-judge`, `individual-apply`, `affected-recheck`,
  `repeat-plan`, `repeat-apply`, `recipient-judge`, `final-scan`,
  `stable-export`, `completed`.
- Group state хранит remote plan/run IDs, snapshot lineage, preferred
  application IDs и terminal outcome без текста.

- [ ] Написать migration/state tests на восстановление lease в каждой новой
      фазе и уникальность operation keys.
- [ ] Добавить typed methods для direct snapshot, repeat plan/poll/apply и
      final scope revision; unknown enum вызывает schema error.
- [ ] GET повторять для timeout/429/5xx; POST повторять только при доказанном
      idempotent resource. Unknown write сначала наблюдать через GET.
- [ ] Сохранить fixed-host, no-redirect и same-origin правила клиента.
- [ ] Выполнить `uv run pytest tests/test_quality_state.py
      tests/test_weblate_client.py`.
- [ ] Закоммитить: `feat(wizard): track canonical repeat workflow`.

### Задача 8. Перестроить оркестратор и stable export barrier

**Файлы:**

- Изменить: `backend/app/services/quality_cycle.py`
- Создать: `backend/app/services/quality_policy.py`
- Изменить: `backend/app/worker.py`
- Изменить: `backend/app/services/exports.py`
- Тест: `tests/test_quality_cycle.py`, `tests/test_connected_export.py`

**Интерфейсы:**

- Производит:
  `advance_quality_cycle(...)` с group-aware scope и
  `build_stable_corrected_export(job_id, expected_scope_revision)`.

- [ ] Написать end-to-end fake-client test: judge меняет первый unit,
      affected recheck возвращает всю repeat-группу, plan выбирает новый hash,
      repeat apply меняет остальные unit, recipient judge проходит, final scan
      пуст и export содержит единый вариант.
- [ ] Написать test: два разных judge-updated hashes идут в exceptions, но
      незатронутые группы и экспорт завершаются.
- [ ] После individual apply передавать в recheck только `applied` IDs, но
      следующий repeat plan строить по server-derived `affected_unit_ids`.
- [ ] После repeat apply запускать dual judge только для `changed_unit_ids`;
      уже подтверждённые неизменные unit не оплачивать повторно.
- [ ] Если recipient judge снова изменил target, ещё раз пересчитать его
      repeat group и разрешить один дополнительный semantic round.
- [ ] Перед экспортом получить `scope_revision=A`, скачать все целевые файлы,
      получить `scope_revision=B`; публиковать export только при `A == B`.
      При различии повторить bounded read один раз, затем завершить
      `completed_with_exceptions`, не смешивая snapshots.
- [ ] Сохранить в export metadata remote project, language, final scope
      revision, quality status и counts; не добавлять служебные колонки в
      пользовательский лист без отдельного формата отчёта.
- [ ] Выполнить quality cycle и connected export tests.
- [ ] Закоммитить: `feat(wizard): export canonical post-judge translations`.

### Задача 9. Полный census, UI и доступ к исправленному файлу

**Файлы:**

- Изменить: `backend/app/services/quality_report.py`, `schemas.py`
- Изменить: `console/src/api/contract.ts`, generated client
- Изменить: `console/src/components/QualitySummary.tsx`
- Изменить: run/export routes при необходимости
- Тест: backend report tests и `QualitySummary.test.tsx`

**Интерфейсы:**

- Итоговые outcomes:
  `clean`, `judge_repaired`, `glossary_repaired`, `repeat_normalized`,
  `approved_conflict`, `glossary_advisory`, `stale`, `forbidden`, `unparsed`,
  `repair_exhausted`, `transport_exception`.

- [ ] Написать tests, что сумма terminal outcomes равна закрытому scope и unit
      не исчезает между раундами.
- [ ] Показывать фазы и counts без текстов переводов/evidence.
- [ ] Для `completed` и `completed_with_exceptions` показывать кнопку скачивания
      исправленного XLSX/CSV; второй статус дополнительно показывает отчёт
      исключений.
- [ ] Не обозначать advisory как автоматически исправленную ошибку.
- [ ] Выполнить backend tests, `pnpm test`, `pnpm typecheck`, `pnpm build`.
- [ ] Закоммитить: `feat(console): report canonical quality results`.

### Задача 10. Сквозная локальная проверка без production

**Файлы:**

- Создать/изменить: `analysis/probes/producer-quality-api-smoke.py`
- Создать: датированный отчёт в `docs/product/measurements/`

**Интерфейсы:**

- Smoke по умолчанию read-only; запись требует одновременно
  `--allow-writes`, явный project slug и scope до 10 unit.

- [ ] Поднять локальный Weblate и wizard из feature branches только после
      разрешения на shared dev stack.
- [ ] Подготовить немецкие случаи: clean, hard glossary, inflection advisory,
      один judge-updated repeat anchor, два расходящихся anchors, approved
      conflict, independent member, stale/ABA, one-seat unparsed.
- [ ] Прогнать happy path и доказать: один initial run с seats 1/2; repeat
      canonical выбран после judge apply; recipient recheck ограничен changed
      IDs; final snapshot пуст; файл содержит текущие значения Weblate.
- [ ] Прогнать exception path и доказать, что проект завершает экспорт, а
      защищённые строки не меняются.
- [ ] Проверить отсутствие второго mutating POST после simulated timeout.
- [ ] Выполнить полные затронутые наборы Weblate и wizard, lint/typecheck/build.
- [ ] Записать обезличенные run IDs/counts/query budgets без токенов и текстов.

### Задача 11. Pull requests, выпуск и ограниченная приёмка

**Файлы:**

- Обновить: существующие Weblate и wizard feature branches
- После разрешения создать:
  `docs/operations/reports/2026-10-16-wizard-quality-cycle-rollout.md`

- [ ] Актуализировать обе ветки относительно их текущих base branches без
      destructive reset; проверить весь diff и unrelated files.
- [ ] Открыть/обновить отдельные PR Weblate и визарда; Weblate PR идёт из
      `BazenkovYA/weblate-HC` в `v9833078908/weblate-HC:main`.
- [ ] Дождаться зелёного CI и обязательного code-owner approval; не выполнять
      merge в `main` от имени владельца.
- [ ] После merge получить отдельное разрешение на production preflight,
      зафиксировать rollback image/commit, очереди, health и отсутствие
      активных quality jobs.
- [ ] После отдельного разрешения развернуть сначала additive Weblate API с
      wizard feature flag выключенным; проверить capabilities/OpenAPI/health.
- [ ] После отдельного разрешения выполнить платную приёмку на
      `wizard-2c0ef0fba7cf4cb9-ea1a3601`, язык `de`, scope 5–10 строк и заранее
      снятый backup.
- [ ] Сверить Weblate audit, application/repeat event IDs, final scope revision
      и скачанный файл. Включать feature flag шире только отдельным решением.

## Что нельзя гарантировать автоматически

- Контекстно разные значения одинаковой исходной строки нельзя безопасно
  унифицировать только по совпадению source. Для этого существуют
  `independent`, context fingerprint и terminal exception.
- Обычная немецкая словоформа glossary может быть корректной, даже если
  morphology её не распознала. Advisory требует согласия обоих судей или
  человека и не исправляется механической заменой.
- Два конфликтующих human-approved варианта не разрешаются автоматикой.
- Отсутствующий repair route, оба недоступных judge seats, потерянные права или
  provider outage нельзя «исправить переводом»; цикл ждёт конфигурацию либо
  возвращает файл с явными исключениями после bounded retries.
- Невозможно честно обещать ноль любых смысловых ошибок. `completed` означает
  ноль обнаруженных hard glossary/repeat/deterministic/judge issues в
  стабильном snapshot. Необнаруженная моделью смысловая ошибка остаётся
  возможной.

## Критерии завершения

- Glossary scan работает независимо от текущего project flag и различает hard
  и advisory.
- Каждый initial judge run содержит оба seats; incomplete evidence не ведёт к
  apply.
- Judge-updated вариант рассматривается первым после safety guards, а не по
  номеру строки.
- После каждого изменения заново проверяются изменённые unit и все затронутые
  repeat groups.
- Approved, stale, locked, independent и glossary-hard конфликтующие строки не
  затираются.
- Цикл ограничен, восстанавливается после restart и не дублирует mutating
  операции.
- Исправленный файл строится из стабильного post-quality Weblate snapshot.
- Неразрешимые строки не останавливают экспорт и представлены в
  `completed_with_exceptions` с точной причиной.
- Production не изменяется до отдельных разрешений на deploy и платную
  приёмку.

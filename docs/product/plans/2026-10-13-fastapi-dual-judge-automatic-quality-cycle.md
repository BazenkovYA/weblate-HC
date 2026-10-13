<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Полный автоматический цикл качества FastAPI-визарда с двумя судьями

> **Для исполнителей:** перед реализацией использовать
> `subagent-driven-development` (рекомендуется) либо `executing-plans` и
> выполнять задачи по порядку с проверкой каждого шага.

**Дата:** 2026-10-13.
**Статус:** предложен, ожидает утверждения; код и работающие экземпляры не
изменялись.
**Цель:** после перевода loc-kit FastAPI-визард автоматически проводит
детерминированные проверки, коллегиальную проверку двумя судьями, поиск
терминологических и повторных расхождений, безопасный ремонт и повторную
проверку. Ошибка отдельной строки или внешнего вызова не останавливает весь
проект.
**Архитектура:** FastAPI-визард остаётся владельцем сквозного workflow, а
Weblate — единственным владельцем переводов, проверок, judge evidence,
repair-кандидатов и audited-записи. Визард не реализует второй judge engine и
не пишет `Unit.target` напрямую.
**Технологии:** FastAPI, SQLAlchemy/Alembic, httpx, React/TypeScript, Weblate
REST Producer API, Celery, PostgreSQL/SQLite в зависимости от среды.
**Связанные документы:**
`docs/product/plans/2026-09-15-judge-glossary-conflict-and-api-history.md`,
`docs/product/plans/2026-09-17-judge-full-scope-continuation.md`,
`docs/product/plans/2026-09-17-judge-redelivery-and-coverage.md`,
`docs/product/plans/2026-08-14-intra-component-consistency-check.md`.

## Исходное состояние и ключевое решение

1. Weblate уже использует коллегию из двух мест — `JUDGE_SEATS = (1, 2)`.
   Один `ProducerRun(kind=judge)` вызывает оба seat’а, сохраняет раздельные
   evidence и вычисляет общий verdict. Визард не должен стартовать два
   одинаковых run: это удвоит стоимость и создаст два конкурирующих набора
   результатов.
2. Producer API уже умеет оценить scope, запустить proposal-only judge-run,
   читать состояние, отменять/возобновлять технически неуспешный run и
   применять полностью проверенные repair-кандидаты с revision guard.
3. В API пока нет достаточного внешнего списка решений с `unit`, `revision`,
   `candidate_id`, evidence двух seat’ов и причиной неприменимости. Без него
   визард может запустить судей, но не может безопасно завершить автоматический
   repair/apply цикл.
4. Проверка точных повторов `repeat-drift` и глоссарная проверка
   `check_glossary` уже существуют. Первая применяется только к компонентам с
   `allow_translation_propagation`, учитывает явный режим `independent` и
   является advisory. Глоссарная проверка различает обязательные точные
   термины и допускающие грамматическое словоизменение рекомендации.
5. Текущий FastAPI worker завершает цикл после MT и экспорта. Его `Job.data`
   хранит intent/accepted/confirmed для удалённых операций, но отдельной
   модели качества, per-unit исходов и автоматического repair-loop нет.

**Решение:** использовать один штатный Weblate judge-run с двумя seat’ами на
каждом раунде. Обычный проверенный кандидат применяется существующим
`apply-candidate`/bulk primitive. Approved-строки, stale revisions, конфликты
терминологии, неполное evidence или неприменимые грамматические случаи не
перезаписываются обходным PATCH: они изолируются, а проект продолжает работу и
завершается как `completed_with_exceptions`.

## Что означает «цикл не останавливается»

Требование относится к проектному workflow, а не к бесконечным или опасным
повторам:

- ошибка одной строки, компонента, языка, seat’а или batch не переводит весь
  проект в `failed`;
- transient-сбой автоматически повторяется с backoff и checkpoint;
- неизвестный исход mutating POST сначала наблюдается через GET, а не
  повторяется вслепую;
- malformed/unparsed ответ повторяется для отсутствующего seat’а в рамках
  лимита, затем строка изолируется и остальные продолжаются;
- после исчерпания двух repair-раундов строка получает terminal outcome и не
  зацикливается;
- недействительный токен, отсутствие обоих judge seat’ов или небезопасная
  конфигурация получают `waiting_for_configuration`: worker периодически
  перепроверяет конфигурацию и автоматически продолжает после восстановления,
  но не пытается «исправить» секреты;
- итог всегда содержит полный census: clean, repaired, unchanged,
  quarantined, stale, forbidden, unparsed и exhausted. Никакая строка не
  исчезает из отчёта.

## Политика одинакового перевода

Нельзя требовать посимвольного совпадения каждого одинакового слова: немецкие
падежи, число, род, составные слова и контекст делают такое правило ошибочным.
Обязательность задаётся только двумя типами данных:

1. **Точный повтор строки:** одинаковый source и plural slot в пределах
   проекта/языка, компонент допускает propagation, а конкретный member не
   отмечен `independent`. Для группы выбирается один канонический target.
2. **Термин глоссария:** `exact`, `read-only` и `forbidden` — жёсткое правило;
   обычный inflectable term — рекомендация, которую подтверждают morphology и
   оба судьи. Advisory сам по себе не разрешает автоматическую замену.

Приоритет выбора канона для точного повтора:

1. единственный неизменённый human-approved вариант;
2. обязательный exact/read-only target из глоссария;
3. вариант, уже прошедший оба seat’а и детерминированные проверки;
4. строгий majority только среди равноценных проверенных вариантов;
5. новый repair-кандидат, который отдельно проверили оба seat’а.

Конфликт между пунктами 1–2, несколько approved-вариантов или ничья не
разрешаются эвристикой. Группа изолируется, но обработка проекта продолжается.
Глоссарий автоматически не переписывается.

## Машина состояний полного цикла

```text
queued
  -> preflight
  -> import
  -> translate-empty-only
  -> deterministic-checks
  -> dual-judge
  -> consistency-scan
  -> repair-plan
  -> safe-apply
  -> recheck
       -> dual-judge / consistency-scan (не более 2 repair-раундов)
  -> export
  -> completed | completed_with_exceptions
```

`cancel_requested` прекращает только новые внешние вызовы; уже принятые
Weblate операции наблюдаются до известного исхода. Каждая фаза идемпотентна и
может быть продолжена другим worker после истечения lease.

## Глобальные ограничения

- Existing target переводится только через проверенный Weblate candidate с
  совпавшей `revision`; первоначальный MT по-прежнему имеет scope
  `state:empty`.
- Никаких прямых SQL/PATCH-записей target из FastAPI-визарда.
- Оба seat’а должны дать разобранное evidence для применяемого кандидата.
- Детерминированные проверки markup, placeholders, numbers, game tokens и
  line separator имеют приоритет над LLM.
- Approved и terminology-conflict не проходят обычный bulk auto-apply.
- Максимум два семантических repair-раунда на unit; transport retries не
  создают новый семантический раунд.
- Все mutating вызовы имеют durable intent и read-after-write reconciliation.
- Секреты, prompts и полный текст ответов провайдера не попадают в wizard API,
  UI или логи.
- Развёртывание, production-вызовы и платный реальный прогон выполняются только
  после отдельного разрешения.

## Задача 0. Зафиксировать актуальную основу FastAPI-визарда

**Результат:** разработка начинается от того же исходника, который реально
обслуживает `192.168.0.180:18511`, а не от отставшей копии.

**Файлы:** репозиторий FastAPI-визарда `D:\bdhc_wizard\repo`; deployment
manifest и текущий checkout на сервере; без изменений Weblate.

- [ ] Сопоставить commit, миграции и checksum файлов локального checkout с
      текущим deployment checkout.
- [ ] Перенести отсутствующие уже развёрнутые изменения в Git-историю до
      новой разработки; не копировать runtime DB, токены и uploads.
- [ ] Зафиксировать baseline: `uv run pytest`, `uv run ruff check .`,
      `pnpm test`, `pnpm typecheck`, `pnpm build`.
- [ ] Создать отдельную feature-ветку FastAPI-визарда.

**Commit:** `chore(wizard): reconcile deployed source baseline` — только если
reconciliation действительно требует изменения tracked files.

## Задача 1. Закрепить контракт качества и durable-состояния визарда

**Результат:** один локализационный run хранит полный quality cycle и может
быть безопасно продолжен после падения процесса.

**FastAPI-файлы:**

- `backend/app/models.py`;
- `backend/app/migrations/versions/0002_quality_cycle.py`;
- `backend/app/schemas.py`;
- новые `backend/app/services/quality_policy.py` и
  `backend/app/services/quality_state.py`;
- `tests/test_quality_state.py`.

**Данные:** добавить нормализованные таблицы, не раздувать единственный JSON:

- `quality_cycles`: parent job, policy revision, current phase/round,
  terminal outcome, counts, heartbeat;
- `quality_operations`: idempotency key, phase, remote resource id, intent,
  accepted/confirmed/unknown, attempt, next retry, sanitized failure class;
- `quality_units`: remote unit id, immutable input revision/hash, current
  round, outcome, candidate/application ids, issue kinds, retry counters;
- `quality_events`: append-only переходы без source/target и секретов.

- [ ] Сначала написать failing tests на восстановление lease, уникальность
      operation key и невозможность перескочить незавершённую фазу.
- [ ] Ввести закрытые enum фазы и outcomes; unknown значение удалённого API
      должно давать schema error, а не считаться success.
- [ ] Включить `quality_policy_revision` и snapshot настроек в idempotency key.
- [ ] Миграция обязана быть additive; существующие Job продолжают читаться.

**Commit:** `feat(wizard): add durable quality cycle state`.

## Задача 2. Добавить наружное чтение judge-решений в Weblate

**Результат:** визард может получить все результаты конкретного run, но не
получает лишние проекты или внутренние provider payloads.

**Weblate-файлы:**

- новый `weblate/api/producer/quality.py`;
- `weblate/api/producer/serializers.py`;
- `weblate/api/producer/views.py` и `urls.py`;
- `weblate/api/tests.py`;
- `docs/specs/openapi.yaml`.

**Endpoint:**

`GET /api/producer/projects/{slug}/decisions/?kind=judge&run={uuid}&cursor=...`

Для каждой строки вернуть: `unit_id`, `revision`, `run_unit_outcome`,
`severity`, completeness seat 1/2, disagreement, issue categories,
`candidate_id`, `candidate_verified`, `approval_loss`,
`terminology_conflict`, `generation_outcome` и `applicability`. Target/source
текст возвращать только если он уже разрешён текущему actor и действительно
нужен UI; для автоматического apply достаточно идентификаторов и guards.

- [ ] Сначала тесты object permissions, pagination, чужого run/project,
      stale evidence, unparsed одного seat’а и кандидата без полной проверки.
- [ ] Решение вычислять существующими `active_verdict`,
      `active_judge_candidate` и `compute_decision_revision`; не дублировать
      коллегиальную логику в serializer.
- [ ] Один query-plan на страницу без N+1 по verdict/suggestion.
- [ ] Run detail дополнить итогами `pass/minor/major/critical/unparsed/skipped`
      и фактической полнотой, не только общим summary.

**Commit:** `feat(api): expose permission-scoped judge decisions`.

## Задача 3. Дать визарду внешний consistency API

**Результат:** exact repeats и glossary deviations доступны как устойчивые
структурированные группы, а не как парсинг HTML или строк описаний checks.

**Weblate-файлы:**

- `weblate/api/producer/quality.py`, `serializers.py`, `urls.py`;
- `weblate/checks/consistency.py`;
- `weblate/checks/glossary.py`;
- `weblate/trans/repeat_judge.py` и `weblate/trans/repeats.py` — только
  переиспользование/выделение query helpers, без второй политики;
- `weblate/api/tests.py`;
- `docs/specs/openapi.yaml`.

**Endpoints:**

- `GET projects/{slug}/quality/issues/?language=de&kind=repeat-drift,glossary`;
- `POST projects/{slug}/quality/recheck/` с закрытым списком unit IDs и
  ожидаемыми revisions.

Issue содержит group id, kind, hard/advisory, unit IDs/revisions, variants,
режим термина и `auto_repair_eligible`. Endpoint не выбирает target по простой
частоте и не меняет строки.

- [ ] Тестами закрепить exact source/plural grouping, `independent`,
      `allow_translation_propagation`, approved conflict, exact/read-only/
      forbidden и немецкую inflection advisory.
- [ ] Recheck идемпотентно пересчитывает штатные checks и возвращает новый
      snapshot hash.
- [ ] Не включать glossary components в repeat groups.

**Commit:** `feat(api): expose structured consistency issues`.

## Задача 4. Расширить Weblate-клиент визарда и preflight

**Результат:** до первого платного вызова визард знает, что оба seat’а,
права, MT engine, проверки и обязательные endpoints доступны.

**FastAPI-файлы:**

- `backend/app/integrations/weblate.py`;
- `backend/app/settings.py`;
- `backend/app/schemas.py`;
- `tests/test_weblate_client.py`;
- `tests/test_quality_preflight.py`.

- [ ] Добавить typed методы `producer_me`, `judge_estimate`, `start_judge`,
      `get_producer_run`, `resume_producer_run`, `list_decisions`,
      `list_quality_issues`, `recheck_quality`, `bulk_apply_candidates`.
- [ ] Проверять `capabilities.judge`, полноту двух seat’ов через estimate/
      capability contract, object permissions и выбранный engine.
- [ ] Сохранить fixed host, token file и запрет redirect; remote URLs из
      ответа принимать только после same-origin validation.
- [ ] GET автоматически повторять для timeout/429/5xx. POST повторять только
      по server idempotency resource; при неизвестном исходе сначала GET.
- [ ] Ошибки делить на `transient`, `unknown-write`, `configuration`,
      `permission`, `schema`, `stale` и `semantic-exhausted`.

**Commit:** `feat(wizard): add producer quality API client`.

## Задача 5. Реализовать долговечный оркестратор полного цикла

**Результат:** worker выполняет фазы по checkpoint, а рестарт продолжает с
последней подтверждённой операции.

**FastAPI-файлы:**

- новый `backend/app/services/quality_cycle.py`;
- новые `backend/app/services/retry_policy.py` и
  `backend/app/services/quality_report.py`;
- `backend/app/worker.py`;
- `backend/app/services/runs.py`;
- `tests/test_quality_cycle.py` и `tests/test_quality_recovery.py`.

- [ ] Отделить `advance_quality_cycle(cycle_id)` от бесконечного worker loop;
      один вызов делает ограниченное число действий и возвращает следующий
      wake-up.
- [ ] После `translate-empty-only` дождаться подтверждённых переводов и
      зафиксировать unit revisions/scope hash.
- [ ] Выполнить deterministic checks до judge, затем estimate → start → poll
      одного двухместного judge-run.
- [ ] Продолжения Weblate chunks наблюдать по одному run id; не запускать
      новый run при медленном переходе между chunks.
- [ ] После терминального Weblate run скачать все decisions с pagination и
      сверить `coverage.scope_complete`.
- [ ] В конце любого раунда записывать per-unit outcome до перехода к apply.

**Commit:** `feat(wizard): orchestrate automatic quality cycle`.

## Задача 6. Построить безопасный repair/apply/rejudge loop

**Результат:** обычные ошибки исправляются автоматически; неподтверждённые
изменения не попадают в target.

**FastAPI-файлы:**

- `backend/app/services/quality_policy.py`;
- `backend/app/services/quality_cycle.py`;
- `backend/app/integrations/weblate.py`;
- `tests/test_quality_repair.py`.

**Алгоритм одного раунда:**

1. `pass` + нет hard checks → `clean`.
2. Полностью проверенный candidate, target не approved, нет terminology
   conflict, revision совпадает → включить в bulk apply.
3. `repeat-drift` → выбрать канон по закреплённому приоритету; если готового
   проверенного канона нет, сформировать candidate в Weblate и проверить его
   обоими seat’ами до apply.
4. Hard glossary issue → repair candidate; сам glossary не менять.
5. Advisory morphology → отправить судьям как контекст, но не применять
   посимвольную замену автоматически.
6. После каждого apply выполнить recheck и новый judge scope только для
   изменённых unit’ов плюс затронутых repeat group members.
7. После второго неуспешного semantic round → `exhausted`; проект продолжает.

- [ ] Все элементы bulk обрабатывать независимо; `stale`, `forbidden`,
      `not_verified` и `needs_individual_confirmation` не откатывают соседей.
- [ ] На `stale` один раз перечитать issue/revision; никогда не применять
      старый candidate к новому тексту.
- [ ] Сохранить application id для возможного штатного undo.
- [ ] Экспорт формируется только после финального recheck snapshot.

**Commit:** `feat(wizard): automate verified repair and recheck`.

## Задача 7. Автоматическое восстановление ошибок без бесконечных циклов

**Результат:** transport/provider/worker ошибки обрабатываются автоматически,
а лимиты и диагностика остаются честными.

**FastAPI-файлы:**

- `backend/app/services/retry_policy.py`;
- `backend/app/services/quality_cycle.py`;
- `backend/app/services/maintenance.py`;
- `backend/app/worker.py`;
- `tests/test_quality_failure_matrix.py`.

| Сбой | Автоматическое действие | Terminal outcome |
| --- | --- | --- |
| GET timeout/429/5xx | exponential backoff + jitter | после лимита `quarantined_transport` |
| POST без ответа | read-after-write по idempotency resource | `unknown_write`, без blind retry |
| один seat unparsed | retry только недостающего evidence; при возможности provider fallback, закреплённый Weblate policy | `unparsed` |
| оба seat недоступны | circuit breaker, отложенный resume | `waiting_for_provider`; не терять scope |
| stale revision | перечитать один раз, пересчитать policy | `stale_changed` |
| нет прав | продолжить остальные units | `forbidden` |
| worker умер | lease expiry + claim другим worker | нет пользовательской ошибки |
| исчерпан repair budget | больше не платить и не менять | `exhausted` |

- [ ] Watchdog переводит просроченный running cycle обратно в runnable queue,
      сохраняя operation intents.
- [ ] Retry budget хранить в БД; перезапуск процесса его не обнуляет.
- [ ] Cost/request ceiling проверять перед новым semantic round. Достижение
      дневного бюджета планирует resume на следующее окно, а не теряет job.
- [ ] Sanitized error details доступны в отчёте; токены/HTTP bodies не
      сохраняются.

**Commit:** `feat(wizard): recover quality operations automatically`.

## Задача 8. API и UI визарда

**Результат:** пользователь видит один непрерывный процесс, не обязан вручную
нажимать «продолжить» на каждой найденной ошибке и получает честный итог.

**FastAPI-файлы:**

- `backend/app/routes/runs.py`;
- новый `backend/app/routes/quality.py`;
- `backend/app/main.py`, `schemas.py`;
- `docs/specs/openapi.yaml`.

**Console-файлы:**

- `console/src/api/client.ts`, `contract.ts`, generated types;
- `console/src/screens/RunCard.tsx` и тест;
- `console/src/screens/wizard/LocKitWizard.tsx` и тест;
- новый `console/src/components/QualitySummary.tsx`;
- `console/src/locales/ru.json`, `styles.css`.

- [ ] `POST localization/` принимает закрытую политику: quality enabled,
      max repair rounds = 2, consistency modes, language scope.
- [ ] `GET runs/{id}/` показывает стадии `checks`, `judge-1+2`,
      `consistency`, `repair`, `recheck`, counts и следующий automatic retry.
- [ ] Обычные найденные ошибки не показывать как красный crash; использовать
      состояние «автоматически исправляется».
- [ ] Финалы: `ready` и `ready_with_exceptions`. Для второго экспорт доступен,
      но рядом скачивается machine-readable report с unit IDs и причинами.
- [ ] Кнопка cancel остаётся; ручная resume нужна только после operator cancel
      или исправления конфигурации, не после обычного provider retry.
- [ ] Обновить accessibility: status text, `aria-live`, keyboard focus,
      не кодировать исход только цветом.

**Commit:** `feat(console): show automatic dual-judge quality cycle`.

## Задача 9. Документация и модель угроз

**Результат:** внешний контракт, права и ограничения качества не остаются
неявными.

**Файлы Weblate:** `docs/specs/openapi.yaml`,
`docs/security/threat-model.rst`, `docs/changes.rst`, соответствующий living
guide в `docs/product/guides/`.

**Файлы визарда:** `docs/specs/openapi.yaml`,
`docs/product/guides/deployment.md`, `.env.example`, `README.md`.

- [ ] Описать required permissions `translation.auto` + `unit.review`,
      token rotation, fixed Weblate host и rate/cost limits.
- [ ] Объяснить, что два AI-судьи не являются человеческой гарантией качества.
- [ ] Документировать `completed_with_exceptions` и все terminal per-unit
      outcomes.
- [ ] Обновить threat model из-за новых authenticated public endpoints,
      автоматического apply и фоновых повторов.

**Commit:** `docs(quality): document automatic dual-judge workflow`.

## Задача 10. Сквозная проверка

**Автоматические проверки Weblate:**

```sh
./rundev.sh test weblate/api/tests.py
./rundev.sh test weblate/trans/tests/test_judge_loop.py
./rundev.sh test weblate/trans/tests/test_repeat_judge.py
uv run prek run --all-files
```

**Автоматические проверки FastAPI-визарда:**

```sh
uv run pytest
uv run ruff check .
cd console
pnpm test
pnpm typecheck
pnpm build
pnpm e2e
```

**Обязательная failure matrix со stub provider:**

- [ ] оба seat pass;
- [ ] один seat major, второй pass;
- [ ] один unparsed, успешный targeted retry;
- [ ] оба unparsed до лимита;
- [ ] timeout до и после принятого POST;
- [ ] 429/5xx и circuit breaker;
- [ ] worker crash между chunks и между apply/recheck;
- [ ] stale edit/ABA edit во время apply;
- [ ] approved, terminology conflict, forbidden permission;
- [ ] exact repeat repair и допустимая немецкая inflection;
- [ ] два последовательных repair-раунда и exhaustion;
- [ ] отмена при активном удалённом run;
- [ ] существующий немецкий перевод не меняется без verified candidate.

**Приёмка на отдельном тестовом проекте:**

1. Снять before snapshot unit IDs, targets, states и revisions.
2. Запустить немецкий цикл только после отдельного разрешения платных вызовов.
3. Прервать worker в середине и подтвердить автоматическое продолжение без
   дублирования paid operation/apply.
4. Дождаться `completed` либо `completed_with_exceptions`; ни один unit не
   остаётся без outcome.
5. Для каждого изменения доказать цепочку: issue → candidate → evidence seat
   1/2 → revision guard → application → recheck.
6. Сравнить before/after: все не входившие в подтверждённый repair набор
   targets и states идентичны.
7. Скачать экспорт и quality report; parse-back должен пройти.

## Задача 11. Поэтапное включение

- [ ] Feature flag выключен по умолчанию.
- [ ] Этап A: dry-run — проверки и кандидаты без apply.
- [ ] Этап B: auto-apply только обычных verified candidates на тестовом
      проекте и одном языке.
- [ ] Этап C: включить repeat consistency, затем hard glossary repairs.
- [ ] Этап D: разрешить для новых проектов визарда после сравнения метрик.
- [ ] На каждом этапе измерять unparsed rate, disagreement rate, repair pass
      rate, stale/forbidden, среднее число calls на unit, стоимость и число
      `completed_with_exceptions`.
- [ ] Production deployment — отдельное явное согласование; PR сам по себе не
      разрешает deployment.

## Критерии готовности

- Один полный wizard run использует один Weblate judge-run на раунд, внутри
  которого присутствуют оба seat’а.
- Каждая строка scope получает terminal outcome; ошибка одной строки не
  останавливает соседние.
- Ни один существующий target не меняется без verified candidate, совпавшей
  revision и штатного audited apply.
- После рестарта worker продолжает тот же cycle и не дублирует paid запрос или
  application.
- Exact repeats становятся одинаковыми только в разрешённых группах;
  грамматически допустимые немецкие формы не выравниваются посимвольно.
- Hard glossary нарушения исправлены либо перечислены как исключения;
  глоссарий не изменён автоматически.
- После двух repair-раундов нет бесконечного цикла: проект экспортируется с
  явным списком исключений.
- OpenAPI, threat model, backend/frontend tests и сквозная failure matrix
  проходят.

## Вне объёма

- обещание стопроцентно правильного перевода без человеческого лингвиста;
- автоматическое изменение глоссария;
- обход approved/permission/revision guards;
- принуждение каждого вхождения слова к одной форме вне glossary/repeat policy;
- deployment или запуск платной production-обработки в рамках реализации PR.

## Фокус code review

1. Нет ли второго независимого judge engine или двойного запуска вместо
   существующих двух seat’ов.
2. Невозможно ли применить candidate с одним seat’ом, unparsed evidence,
   устаревшей revision или после ручного изменения.
3. Не скрывается ли частичный результат за общим `completed`.
4. Не повторяется ли mutating POST после неизвестного сетевого исхода.
5. Не превращается ли advisory morphology в жёсткую посимвольную замену.
6. Переживают ли intent, retry budget и scope worker restart.
7. Не попадают ли токены, prompts, source/target и provider bodies в лишние
   логи или ответы API.

<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Выпуск Producer API для полного цикла качества FastAPI-визарда

> **Для исполнителей:** обязательно использовать
> `subagent-driven-development` (рекомендуется) либо `executing-plans` и
> выполнять задачи по порядку. Шаги отмечаются checkbox (`- [ ]`).

**Цель:** выпустить на `l10n.herocraft.com` минимальный безопасный Producer
API, через который локальный FastAPI-визард сможет запускать одного
двухместного судью, получать решения по строкам, проверять повторы и глоссарий,
применять только дважды подтверждённые кандидаты и перепроверять результат.

**Архитектура:** Weblate остаётся владельцем переводов, judge evidence,
кандидатов, проверок, прав и audited-записи. Визард хранит только идентификаторы,
opaque revisions и классификации, управляет конечным автоматом и никогда не
пишет `Unit.target` через обычный unit PATCH. Выпуск разделён на PR, локальную
интеграционную приёмку, отдельное одобрение deployment и ограниченную
production-приёмку на тестовом проекте.

**Технологии:** Django REST Framework, drf-spectacular/OpenAPI, PostgreSQL,
Docker Compose, Celery, FastAPI/httpx, React/TypeScript.

**Спецификация:**
`docs/product/plans/2026-10-13-fastapi-dual-judge-automatic-quality-cycle.md`.

## Глобальные ограничения

- Production deployment, restart, management command, платный judge-run и
  запись в тестовый проект требуют отдельных явных разрешений владельца.
- `main` изменяется только через pull request с одобряющим code-owner review;
  агент не выполняет merge.
- Один `ProducerRun(kind=judge)` уже содержит оба места `JUDGE_SEATS=(1, 2)`;
  визард не запускает два конкурирующих run.
- API не возвращает source, target, candidate text, prompt или judge evidence;
  наружу выходят только ID, hashes, revisions и классификации.
- Автоматическая запись разрешена только для `applicability=ready` и
  `candidate_verified=true`; approved, stale, forbidden и terminology conflict
  не обходятся прямым PATCH.
- Изменяющий POST после неизвестного результата не повторяется вслепую.
- Feature flag `WIZARD_QUALITY_CYCLE_ENABLED` остаётся выключенным по умолчанию.
- Изменение API additive; миграций БД и новых production-секретов не требуется.
- Существующие несвязанные изменения и `.pnpm-store/` не включаются в коммиты.

## Фактический baseline 2026-10-13

Безопасная проверка `https://l10n.herocraft.com/api/` уже выполнена без записи:

- `/api/producer/me/` отвечает `200`, `judge=true`;
- доступны estimate/start/detail/cancel/resume, individual apply, bulk apply,
  clarification и undo;
- отсутствуют decisions list, quality issues и quality recheck;
- `ProducerCapabilities` содержит только `judge` и
  `glossary_profile_analysis`, без `judge_seats`;
- `ProducerBulkApplyResult` содержит только `unit` и `outcome`, без новой
  revision и `application_id`.

Локальная серверная реализация находится в ветке
`codex/fastapi-dual-judge-quality-plan`; локальная реализация визарда — в
`codex/dual-judge-quality-cycle`. Ни одна из них не развёрнута на production.

## Структура изменений

| Файл | Ответственность |
| --- | --- |
| `weblate/api/producer/quality.py` | Текстобезопасные проекции решений и consistency issues |
| `weblate/api/producer/serializers.py` | Закрытые входные/выходные схемы API |
| `weblate/api/producer/views.py` | Object permissions, pagination, revision guards и bounded recheck |
| `weblate/api/producer/urls.py` | Три новых project-scoped маршрута |
| `weblate/api/tests.py` | Контракт, безопасность, query budget и per-row outcomes |
| `docs/api.rst` | Публичный контракт Producer API |
| `docs/specs/openapi.yaml` | Сгенерированная OpenAPI 3.1.1 схема |
| `docs/security/threat-model.rst` | Граница доверия внешнего оркестратора |
| `backend/app/integrations/weblate.py` | Typed-клиент визарда к новому контракту |
| `backend/app/services/quality_cycle.py` | Долговечная оркестрация двух судей/apply/recheck |
| `analysis/probes/producer-quality-api-smoke.py` | Воспроизводимая read-only проверка опубликованного API |

## Фокус ревью

1. Чужой run, project или unit должен выглядеть как `404`, а не раскрывать
   существование объекта; это проверяется в задаче 1.
2. Изменение строки между list/apply/recheck, включая ABA-редактирование,
   должно вернуть `stale` и не перезаписать перевод; это проверяется в задаче 2.
3. Страница решений и полный consistency snapshot не должны создавать N+1 на
   verdict, suggestion, change и glossary lookups; query budget закрепляется в
   задаче 2.
4. Timeout/5xx после mutating POST не должен привести к повторному применению
   или второму платному run; это проверяется сквозным тестом в задаче 4.
5. Старый клиент должен продолжить работу после additive schema change, а новый
   клиент должен явно остановиться при отсутствии `judge_seats=2`; это
   проверяется в задачах 3 и 4.

---

### Задача 1. Зафиксировать закрытый серверный API-контракт

**Файлы:**

- Изменить: `weblate/api/producer/serializers.py`
- Изменить: `weblate/api/producer/views.py`
- Изменить: `weblate/api/producer/urls.py`
- Тест: `weblate/api/tests.py`

**Интерфейсы:**

- Производит:
  `GET /api/producer/projects/{slug}/decisions/?kind=judge&run={uuid}`;
  `GET /api/producer/projects/{slug}/quality/issues/?language=de&kind=repeat-drift,glossary`;
  `POST /api/producer/projects/{slug}/quality/recheck/`.
- Решение содержит `unit_id`, `revision`, `seat_results`, `candidate_id`,
  `candidate_verified`, guards и `applicability`, но не содержит переводные
  тексты.
- Recheck принимает не более 1000 уникальных `{unit_id, revision}` и возвращает
  независимый исход каждой строки.

- [ ] Добавить/проверить failing tests для anonymous, token без project access,
  чужого run, run другого проекта и недоступного unit; ожидать `401/404`, без
  различимых tenant-данных.
- [ ] Запустить каждый новый тест отдельно и подтвердить, что он падает на
  серверном baseline из-за отсутствующего маршрута или поля.
- [ ] Довести serializers/views/urls до контракта выше, используя
  `allowed_projects`, `filter_access` и существующий
  `user_can_view_producer_run`; не добавлять новый путь записи target.
- [ ] Добавить тест, который рекурсивно проверяет JSON decisions/issues на
  отсутствие ключей `source`, `target`, `candidate`, `prompt`, `evidence`.
- [ ] Добавить pagination test для decisions и duplicate/empty/>1000 tests для
  recheck.
- [ ] Выполнить:
  `./rundev.sh test weblate/api/tests.py -k "producer and (decision or quality)"`.
  Критерий: все выбранные тесты проходят.
- [ ] Закоммитить:
  `feat(api): expose producer quality decisions`.

### Задача 2. Сохранить server-side safety и ограничить стоимость запросов

**Файлы:**

- Изменить: `weblate/api/producer/quality.py`
- Изменить: `weblate/api/producer/views.py`
- Изменить: `weblate/trans/models/judge.py` только если нужен необязательный
  precomputed latest-change аргумент для revision
- Тест: `weblate/api/tests.py`

**Интерфейсы:**

- Использует: `compute_decision_revision`, `active_judge_candidate`,
  `candidate_round`, `evaluate_glossary_terms`.
- Производит: те же revisions и guards при фиксированном query budget; bulk
  apply возвращает `{unit, outcome, revision, application_id}`.

- [ ] Написать stale/ABA tests: после чтения revision изменить target/state и
  вернуть к прежнему тексту; bulk apply и recheck обязаны вернуть `stale`, не
  создавая вторую запись.
- [ ] Написать per-row bulk test: один `applied`, один `stale`, один
  `needs_individual_confirmation`, один `forbidden`; успешная строка не
  откатывается из-за остальных.
- [ ] Написать query-budget tests для 1 и 25 decision rows и для 1 и 25
  repeat/glossary issues. Разница не должна расти линейно по verdict,
  suggestions и `Change.aggregate`; конкретный потолок зафиксировать после
  измерения единичного запроса с запасом не более 20%.
- [ ] Устранить N+1 через `select_related`, ограниченные `Prefetch` и annotation
  максимального `Change.pk`; не кэшировать permission verdict между проектами.
- [ ] Проверить hard/advisory glossary, exact repeats, plural grouping,
  исключение glossary components и конфликт двух approved-вариантов.
- [ ] Проверить, что после `applied` возвращаются post-apply revision и
  `application_id`, а после остальных исходов — честная текущая revision или
  пустая строка для недоступного объекта.
- [ ] Выполнить `./rundev.sh test weblate/api/tests.py` и
  `./rundev.sh check`. Критерий: зелёные тесты, ноль новых Django checks.
- [ ] Закоммитить: `fix(api): harden producer quality guards`.

### Задача 3. Capability negotiation, OpenAPI и документация

**Файлы:**

- Изменить: `weblate/api/producer/serializers.py`
- Изменить: `weblate/api/producer/views.py`
- Изменить: `docs/api.rst`
- Изменить: `docs/changes.rst`
- Изменить: `docs/security/threat-model.rst`
- Перегенерировать: `docs/specs/openapi.yaml`
- Тест: `weblate/api/tests.py`

**Интерфейсы:**

- Производит: `capabilities.judge_seats=2` только при доступном judge, иначе
  `0`; старые поля не удаляются.
- OpenAPI описывает новые routes и post-apply fields без ручного расхождения с
  serializers.

- [ ] Написать tests для configured/unconfigured judge: ожидать соответственно
  `judge=true, judge_seats=2` и `judge=false, judge_seats=0`.
- [ ] Реализовать capability из фактической `JUDGE_SEATS`, не из клиентского
  параметра и не из постоянного optimistic `2`.
- [ ] Перегенерировать схему командой из `docs/Makefile`:
  `WEBLATE_DOCS_OPENAPI_ALL_VCS_CHOICES=1 DJANGO_SETTINGS=weblate.settings_test uv run --no-sync ./manage.py spectacular --skip-checks --fail-on-warn --validate > docs/specs/openapi.yaml`.
- [ ] Проверить schema diff: только новые routes/schemas и расширение двух
  существующих ответов; никаких случайных изменений несвязанных endpoints.
- [ ] Обновить API, changelog и threat model: token permissions,
  text-free boundary, revision guards, retention и rate-limit assumptions.
- [ ] Выполнить `uv run prek run --all-files`; затем повторить API tests задачи
  1. Критерий: зелёный lint и tests.
- [ ] Закоммитить: `docs(api): document producer quality workflow`.

### Задача 4. Сквозная локальная совместимость с FastAPI-визардом

**Файлы:**

- Изменить при несовпадении контракта:
  `backend/app/integrations/weblate.py`
- Изменить при несовпадении состояний:
  `backend/app/services/quality_cycle.py`
- Тест: `tests/test_weblate_client.py`
- Тест: `tests/test_quality_cycle.py`
- Создать: `analysis/probes/producer-quality-api-smoke.py`

**Интерфейсы:**

- Клиент использует фиксированный `WEBLATE_API_BASE_URL`, token file, запрет
  redirect, GET retry и однократный mutating POST.
- Smoke по умолчанию выполняет только `/me`, OpenAPI, GET/OPTIONS и не имеет
  режима записи без явного `--allow-writes --project <slug>`.

- [ ] Написать contract tests на unknown enum/field shape, 401/403, 429/5xx,
  timeout до POST и unknown outcome после POST.
- [ ] Поднять только локальные Weblate и wizard из feature-веток после
  отдельного разрешения на shared dev stack; production URL не использовать.
- [ ] Прогнать локальный цикл на изолированном проекте `de`: оба seat pass;
  disagreement; один unparsed; approved; stale/ABA; hard glossary; repeat
  drift; два repair rounds; исчерпание retries.
- [ ] Доказать, что неизвестный исход start/apply/recheck не создаёт второго
  mutating POST, а остальные строки завершаются с исключениями.
- [ ] Выполнить backend-набор визарда, `pnpm typecheck`, `pnpm test` и
  `pnpm build`. Критерий: все тесты проходят; экспорт доступен для
  `completed_with_exceptions`.
- [ ] Сохранить локальный отчёт без токенов и переводных текстов в
  `docs/product/measurements/` соответствующего репозитория.
- [ ] Закоммитить только необходимые compatibility-правки; если контракт уже
  совпал, зафиксировать результаты теста без пустого code commit.

### Задача 5. Pull request и release gate

**Файлы:**

- Проверить: весь diff ветки относительно актуального `main`
- Создать: pull request из `BazenkovYA/weblate-HC` в
  `v9833078908/weblate-HC:main`

**Интерфейсы:**

- Производит: reviewed merge candidate; production не меняется.

- [ ] Fetch актуального `main`, проверить merge-base и выполнить обычный
  merge/rebase только после просмотра конфликтов; не использовать destructive
  reset.
- [ ] Повторить API tests, полный затронутый judge/check набор, prek, schema
  validation и wizard contract tests на финальном SHA.
- [ ] Выполнить self-review по threat boundary, permissions, no-text response,
  idempotency и query budgets.
- [ ] Push feature branch в fork и открыть стандартный cross-repository PR.
- [ ] Приложить к PR: endpoints, права, отсутствие миграций, результаты тестов,
  rollout/rollback, доказательство отсутствия deployment.
- [ ] Дождаться обязательного code-owner approval и зелёного CI. Не выполнять
  merge от имени владельца.

### Задача 6. Production preflight после merge

**Файлы:**

- Не изменять код.
- После выполнения создать:
  `docs/operations/reports/2026-10-13-producer-quality-api-rollout.md`.

**Интерфейсы:**

- Использует: merged commit в `main` и `deploy/vps.sh`.
- Производит: однозначный go/no-go и проверяемый rollback target.

- [ ] Получить отдельное явное разрешение на production preflight. До него не
  выполнять `deploy/vps.sh`, SSH, `docker exec` или management commands.
- [ ] Read-only записать текущие checkout SHA, image revision/digest, health,
  свободное место, активные `auto_translate*` задачи и длины очередей.
- [ ] Проверить, что merged range не содержит миграций и environment changes;
  ожидаемое действие `deploy/vps.sh` — rebuild из-за `weblate/**.py`.
- [ ] Подготовить проверяемый rollback target: предыдущий image digest и commit;
  подтвердить, что API изменение additive и старая версия tolerates текущую БД.
- [ ] Повторить внешний read-only baseline: старые endpoints `200`, новые
  отсутствуют. Это последняя точка до изменения сервера.
- [ ] Остановиться и запросить отдельное разрешение на deployment, приложив
  preflight и rollback target.

### Задача 7. Развёртывание серверного API

**Файлы:**

- Не изменять код или `.env`.
- Дополнить rollout report фактическими SHA, временем и результатом.

**Интерфейсы:**

- Производит: Weblate с новым API; wizard flag всё ещё выключен.

- [ ] После явного разрешения выполнить из чистого checkout merged `main`:
  `./deploy/vps.sh deploy --build`.
- [ ] Требовать `DEPLOY-OK`, `health=healthy`, login HTTP 200, отсутствие stale
  processes и точное совпадение `image revision` с merged SHA.
- [ ] Проверить журналы старта на migrations/check errors; ожидается отсутствие
  новых миграций.
- [ ] Через существующий token выполнить только read-only acceptance:
  `/producer/me/` возвращает `judge_seats=2`; OpenAPI перечисляет три новых
  маршрута; `OPTIONS` возвращает разрешённые методы.
- [ ] Выполнить GET decisions с несуществующим UUID и GET issues на тестовом
  проекте; принять permission-scoped `404`/пустой результат, но не route-level
  `404`. Не запускать judge и не вызывать recheck/apply на этом шаге.
- [ ] При любой ошибке health/schema/auth откатить на записанный image/commit,
  подтвердить старые endpoints и не включать wizard flag.

### Задача 8. Ограниченная приёмка локального визарда на серверном тестовом проекте

**Файлы:**

- Локальная конфигурация визарда вне Git.
- Дополнить rollout report обезличенными counts и run IDs.

**Интерфейсы:**

- Локальный визард указывает на `https://l10n.herocraft.com/api/`.
- Только для этого этапа включаются локальные
  `WIZARD_EXTERNAL_WRITES=1` и `WIZARD_QUALITY_CYCLE_ENABLED=1`.

- [ ] Получить отдельное явное разрешение на платный judge-run и изменения в
  названном тестовом project slug; локальный slug визарда предварительно
  сопоставить с реальным remote slug.
- [ ] Снять экспорт/backup тестового проекта и baseline revisions выбранных
  немецких строк. Скоуп ограничить 5–10 строками.
- [ ] Подготовить случаи: clean, repeat drift, hard glossary mismatch,
  approved target и строка, изменяемая между decision/apply.
- [ ] Запустить один quality-enabled wizard job для `de`; убедиться, что создан
  ровно один judge run с двумя seats.
- [ ] Принять только verified ordinary candidates; approved/stale/glossary
  conflict обязаны остаться неизменными и появиться в исключениях.
- [ ] Сверить post-apply revisions, application IDs, recheck snapshot, Weblate
  audit history и свежий экспорт визарда.
- [ ] Выключить оба локальных флага после теста. Не удалять evidence или audit
  rows; тестовые переводы восстанавливать только штатным undo/backup путём.

### Задача 9. Наблюдение и закрытие выпуска

**Файлы:**

- Завершить:
  `docs/operations/reports/2026-10-13-producer-quality-api-rollout.md`

**Интерфейсы:**

- Производит: итоговое решение о доступности API для визарда.

- [ ] Зафиксировать за окно наблюдения: HTTP 4xx/5xx новых routes, latency и
  query count, Celery queue depth, один run ID, два seat outcomes, число
  applied/stale/approved/terminology/unparsed.
- [ ] Стоп-условия: tenant leakage; текст в text-free response; повторный
  mutating POST; запись approved/stale строки; N+1 сверх бюджета; неожиданный
  платный run; ухудшение health или очередей.
- [ ] При стоп-условии немедленно выключить локальный wizard flag; если проблема
  только в новом API — откатить production image к записанному digest/commit и
  подтвердить старый route inventory.
- [ ] Если критерии зелёные, отметить API как доступный для новых тестовых
  проектов визарда. Расширение на реальные проекты оформить отдельным решением,
  не считать автоматическим следствием этой приёмки.

## Критерии завершения

- PR одобрен и слит владельцем, CI зелёный.
- Production image соответствует merged SHA и healthy.
- Серверная OpenAPI и `producer/me` публикуют полный новый контракт.
- Один ограниченный немецкий тест завершён с двумя seats, безопасным per-row
  apply и post-apply recheck.
- Ни одна approved/stale/терминологически конфликтующая строка не затёрта.
- Локальный визард формирует экспорт и `completed_with_exceptions`, когда
  отдельная строка не исправлена.
- Rollout report содержит проверяемые SHA/digest/run IDs/counts и не содержит
  токены, source/target или judge evidence.

## Вне скоупа

- Перенос FastAPI-визарда на production-сервер.
- Создание второго judge engine в визарде.
- Прямой PATCH переводов в обход candidate API.
- Автоматическое исправление произвольного repeat drift без кандидата,
  подтверждённого обоими судьями.
- Включение функции для реальных игровых проектов без отдельной приёмки.

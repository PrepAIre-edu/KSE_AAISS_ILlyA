# План здачі MCP-завдання (100/100)

Рішення за результатами обговорення:
- **Домен:** Course-Planning agent на базі вже наявного `conceptgraph` (граф концептів + prerequisite-зв'язки з силабусів Calculus / Linear Algebra / ML / Probability).
- **Existing MCP (Part A):** Obsidian Local REST API MCP server.
- **Custom MCP (Part B):** новий сервер `curriculum-mcp`, що обгортає `output/dataset/concept_graph.json` доменною логікою планування.
- **Агент-фреймворк:** Claude Agent SDK (Python). Причина: `conceptgraph` вже побудований на Anthropic API + `tool_use`/pydantic-схемах — той самий стиль; нативна підтримка MCP (stdio) без додаткових залежностей; єдиний секрет — `ANTHROPIC_API_KEY`. Це мінімізує ризик "не завелось у перевіряючого", що було явною вимогою.

Це прямо співпадає з офіційним прикладом хорошого домену в завданні: *"Course-planning agent: validate a proposed study plan against prerequisites, detect timetable conflicts, and suggest feasible substitutions under credit constraints."* Ми не збігаємось із зарезервованою темою (research/experiment agent).

---

## 1. Архітектура

```
                         ┌─────────────────────────────┐
                         │   Claude Agent SDK  (агент)  │
                         │   study_planner_agent.py     │
                         └───────────┬─────────┬────────┘
                    MCP (stdio)       │         │      MCP (stdio→http)
                    ┌─────────────────┘         └──────────────────┐
                    ▼                                              ▼
        ┌───────────────────────┐                    ┌──────────────────────────────┐
        │  curriculum-mcp       │                    │  obsidian-local-rest-api MCP  │
        │  (наш, окремий процес)│                    │  (existing, окремий процес)   │
        │  4 substantive tools  │                    │  читає/пише ноутси у vault    │
        └──────────┬────────────┘                    └───────────────┬──────────────┘
                   │ читає локально                                   │ HTTP + API key
                   ▼                                                  ▼
        output/dataset/concept_graph.json                  Obsidian app + Local REST API plugin
        (згенеровано conceptgraph, локальний датасет,                 (демо-vault, без чутливих нотаток)
         мережа під час демо не потрібна)
```

Обидва MCP-сервери — окремі процеси, що стартують незалежно від агента (вимога процес-сепарації).

---

## 2. Custom MCP server — `curriculum-mcp` (Part B)

**Статус: реалізовано (пункт 2 плану).** `mcp_servers/curriculum_mcp/` — робочий FastMCP/`MCPServer`-сервер (пакет `mcp` 2.0.0) з 4 інструментами, перевірений реальним MCP-клієнтом по stdio (хендшейк, list_tools, успішний виклик + виклик з невідомим course_code → `is_error: true` з чітким повідомленням). Також додано `mcp_servers/curriculum_mcp/course_metadata.json` — вручну переписані ECTS та текст розділу "Prerequisites" кожного силабусу (цитовано в `provenance`) — виявилося, що жоден з 4 курсів формально не вимагає інший з цих 4-х як передумову (всі заявлені передумови зовнішні), а ECTS виявився реальним, корисним виміром для `credit constraints` (на відміну вигаданого "difficulty", яке в датасеті завжди = 3, безглузде).

**Джерело даних:** `output/dataset/concept_graph.json` — локальний, завантажується в пам'ять при старті сервера. Мережа не потрібна ⇒ за Part D фікстури API не обов'язкові, датасет сам є детермінованим demo input.

**Статус (виконано, пункт 1 плану): датасет перегенеровано.**
- Написано `conceptgraph/adapters/syllabus_flat.py` — адаптер під плоску структуру `sources/` (4 PDF без підпапок), дістає чистий `course_code`/`course_title` з імені файлу (`Syllabus_Calculus.pdf` → `CALC`/`Calculus`).
- Спроба отримати крос-курсові prerequisite-зв'язки через LLM (Ollama) провалилась двічі: 14B-модель впала по таймауту (CPU ~5 ток/сек), 3B-модель зависла на 22 години реального часу (ймовірно, schema-constrained decoding для великої JSON-схеми в Ollama гальмує непропорційно). **Рішення користувача:** відмовитись від LLM-збагачення й лишити нульового-вартісний детермінований шлях (`conceptgraph free`) як фінальний датасет.
- Виправлено 3 баги в `conceptgraph/syllabus.py`, які псували детерміновану екстракцію саме на цих силабусах: адміністративні фрагменти розкладу занять ("6 class hours (problem solving: 6h)"), злиті bullet-символи PUA-шрифту всередині рядка ("precision • F1" → мало розпастись на два концепти), протікання заголовка табличної колонки "Content" у текст концепту.
- **Фінальний датасет:** 110 концептів (CALC 20, LINALG 38, ML 18, PROB 34), 48 зв'язків (`part_of` + `prerequisite` в межах курсу), quality gate **PASSED**, quotes verbatim 100%. Повністю відтворювано без жодного ключа чи мережі:
  ```
  python -m conceptgraph free sources --out output --adapter syllabus --sequential-links
  ```

**Важливий наслідок для дизайну tools (пункт 2):** **0 крос-курсових prerequisite-зв'язків** — детермінований шлях дає їх лише в межах одного курсу (`prerequisite`, origin=`sequence`, слабкий strength 0.4) + `part_of` ієрархію тем. Concept-slugs також не перетинаються між курсами. Тому:
- `validate_study_plan` / `detect_plan_conflicts` валідують проти реальних внутрішньокурсових ланцюжків (є), а не проти вигаданих крос-курсових передумов.
- `compare_courses` не може покладатись на спільні slugs (їх нема) — порівняння робить нормалізоване зіставлення за назвами/ключовими словами концептів, а не lookup спільного slug.
- Це чесно задокументувати в Design Rationale як **known limitation**: "a syllabus states topics, not cross-course dependencies; a real cross-course prerequisite graph needs LLM judgement, which proved impractical on available hardware within the assignment timeframe" — рубрика explicitly хоче бачити усвідомлені trade-offs, тож це не мінус, а свідома інженерна відповідь.

### Інструменти (4, з них 3 обов'язкові + 1 бонусний; ≥2 виходять за межі retrieval)

| # | Tool name | Призначення | Чому "substantive" |
|---|---|---|---|
| 1 | `validate_study_plan` | Перевіряє набір цільових курсів проти prerequisite-графа: чи всі концепти-передумови цільових курсів покриті вже завершеними курсами/концептами. | Топологічна валідація графа, не просто lookup. Пряме влучання в приклад завдання ("validate ... against prerequisites"). |
| 2 | `detect_plan_conflicts` | На вхід — план із розподілом курсів по термінах (`{course_code, term}`), плюс ліміт ECTS/термін (реальний, з силабусів). Знаходить (a) перевантаження терміну понад ліміт ECTS; (b) дубль курсу; (c) конфлікт порядку, якщо колись з'являться internal_prerequisites. | Реальна доменна логіка над реальними даними (ECTS з силабусів), не фейковий timetable-датасет. Влучання в "detect timetable conflicts" через credit-load, а не вигадані часові слоти. |
| 3 | `suggest_substitution` | Оцінює, чи можна зарахувати курс "екстерном": звіряє список концептів, які студент вже знає (вільний текст), з реальними концептами курсу; якщо залишок (residual) малий — курс можна замінити на самостійне вивчення залишку. | Substitution = заміна повного курсу (і його ECTS) на менше самостійне навантаження; "credit constraint" — це поріг залишку, під який підпадає рішення. Реальна нормалізована текстова відповідність, не голий retrieval. |
| 4 (bonus) | `compare_courses` | Порівнює два курси: концепти зі співпадаючими/спорідненими назвами (нормалізоване порівняння тексту, а не lookup спільного slug — slugs між курсами не перетинаються), унікальні для кожного, розподіл за складністю. | Структуроване порівняння з текстовою нормалізацією, відмінна відповідальність від інших трьох. |

Усі чотири мають чіткі pydantic input/output схеми (не "довільний рядок/dict"), кожен торкається основного датасету, кожен впливає на подальший крок агента (валідація провалилась → викликається substitution; конфлікти знайдені → агент переносить курс у інший термін і повторює перевірку).

### Обробка помилок (як реалізовано)
Не окремий `{"ok"/"error"}` JSON-конверт (початковий план), а нативний MCP-механізм: невалідний вхід (напр. невідомий course_code) підіймає `mcp.server.mcpserver.exceptions.ToolError`, який MCP-шар повертає як `isError: true` з текстом помилки — клієнт відрізняє це від справжнього успіху. "Нічого не знайдено" (порожній `related_concepts`, 0 конфліктів) — завжди звичайний структурований успіх, ніколи помилка.

### Реалізація (як реалізовано)
- `mcp.server.mcpserver.MCPServer` (пакет `mcp` 2.0.0). **Важливо:** у цій версії SDK немає `mcp.server.fastmcp` — модуль перейменували/реструктурували між 1.x і 2.x; клас тепер `MCPServer`, імпорт з `mcp.server.mcpserver`. Це відкрилось лише під час реалізації (моя початкова згадка `FastMCP` вище була помилковим припущенням зі старих знань).
- stdio-транспорт, старт командою `python -m mcp_servers.curriculum_mcp.server` (повний шлях пакету, не просто `curriculum_mcp.server`) незалежно від агента.
- Схеми задаються через `Annotated[type, pydantic.Field(...)]` у сигнатурі функції-tool (SDK сама будує JSON Schema з анотацій) + окремі pydantic-моделі в `models.py` для output — та сама конвенція, що вже є в `conceptgraph/schemas.py`.

---

## 3. Existing MCP — Obsidian (Part A)

**Статус: наскрізний прогін підтверджено (пункти 3-5 плану).** `study_planner/study_planner_agent.py` (Claude Agent SDK) успішно виконав повний цикл: Obsidian MCP тепер працює через HTTP (порт 27123, не HTTPS — self-signed сертифікат не приймається Node-процесом Claude Code CLI, задокументовано як свідомий компроміс для локального демо). Прогін: прочитав StudyPlan.md → validate_study_plan (10/20 ECTS, valid) → detect_plan_conflicts (знайшов реальний credit_overload: 13 ECTS у term 1 проти ліміту 12) → suggest_substitution для PROB (31 непокритий концепт → not waivable) → записав 'StudyPlan Review.md' назад у vault. 7 turns, $0.125 (лише оцінка вартості для обліку — прогін пройшов через Claude Code login/Team-підписку, БЕЗ ANTHROPIC_API_KEY і без окремого платежу). Це підтверджує ключову вимогу рубрики: результат Obsidian-читання визначає, які саме curriculum-tools викликаються і з якими аргументами; результати curriculum-tools визначають вміст записаної назад нотатки.

**Fail-демо підтверджено на живому агенті.** `python -m study_planner.study_planner_agent NoSuchPlan.md` → `vault_read` повертає `is_error`, агент зупиняється, не вигадує план, пояснює користувачу. Script тепер приймає ім'я нотатки як CLI-аргумент — зручно варіювати вхід на захисті.

**Роль у проєкті (як реалізовано):** демо-vault містить нотатку `StudyPlan.md` (цільові курси, вже завершені курси, term_plan, known_concepts — у frontmatter). Агент:
1. читає `StudyPlan.md` через Obsidian MCP tool `vault_read` (реальна назва; мій ранній здогад `get_file_contents` виявився невірним — точні назви й схеми всіх 16 tools зафіксовано в `docs/TOOL_CONTRACTS.md`);
2. модель сама парсить frontmatter (без окремого коду парсингу — LLM читає JSON, який повертає `vault_read`);
3. викликає `validate_study_plan` / `detect_plan_conflicts` з curriculum-mcp;
4. якщо нотатка ставить питання про надлишковість курсу — викликає `suggest_substitution`;
5. записує нотатку `<ім'я> Review.md` назад у vault через `vault_write` із висновком, знайденими конфліктами і рекомендаціями.

Це задовольняє вимогу "tool result affects a later step or final output" для **обох** серверів одночасно: вміст нотатки (Obsidian) визначає, які саме curriculum-tools викликаються і з якими аргументами; результат curriculum-tools визначає вміст нотатки, яку агент пише назад.

**Документовано** — див. `docs/TOOL_CONTRACTS.md`: exact tool name і model-facing description `vault_read`/`vault_write`, аргументи/обмеження, формат відповіді, помилки (файл не існує — перевірено живим викликом), side effects (`vault_write` перезаписує файл без попередження — явно зазначено).

**Демонстрація відмови — виконано на живому агенті.** `python -m study_planner.study_planner_agent NoSuchPlan.md` → `vault_read` повертає `isError: true`, агент зупиняється й пояснює, не вигадує план (повний вивід — вище, "Fail-демо підтверджено"). Додатково перевірено на рівні raw MCP-клієнта: невірний шлях файлу → `"File not found: ..."`.

Ризик відтворюваності (Obsidian — desktop-застосунок з ручним кроком встановлення) — знятий: README має точний покроковий інструктаж, і користувач реально пройшов його один раз під час розробки (з першої спроби не спрацювало через HTTPS-сертифікат — виправлено переходом на HTTP-порт 27123, задокументовано в README та Design Rationale).

**Знайдено під час імплементації:** таблиця завдання посилається на `coddingtonbear/obsidian-local-rest-api`, який історично був REST-плагіном без власного MCP. Перевірка (WebSearch/WebFetch) підтвердила: плагін тепер має вбудований MCP-сервер (Streamable HTTP, `/mcp/`, bearer-токен) — окремий сторонній MCP-пакет не знадобився, ризику "unofficial substitute" немає.

---

## 4. Документація (Part C) — виконано

`docs/TOOL_CONTRACTS.md` — таблиця для кожного з 4 custom tools + `vault_read`/`vault_write` (Name, Purpose, Model-facing description, Input schema, Output schema, Error conditions, Side effects, Example) — усі приклади реальні, зняті з живих прогонів.

## 5. Операційні вимоги (Part D) — виконано

- `.env.example`: `OBSIDIAN_API_KEY`/`OBSIDIAN_BASE_URL` (обов'язкові), `ANTHROPIC_API_KEY` (опційно — за замовчуванням ambient Claude Code login, без окремого рахунку), `CURRICULUM_DATA_DIR`/`STUDY_PLANNER_MODEL` (опційно). Реальні значення лише в `.env` (`.gitignore`).
- Секретів у коді/репо нема. Окремо перевірено й виправлено: `obsidian_demo_vault/.obsidian/` (містив реальний API-ключ і приватний RSA-ключ сертифіката плагіна) — доданий у `.gitignore` до першого коміту.
- Мережевих запитів під час роботи custom-сервера немає (локальний датасет) ⇒ фікстури API не потрібні для Part B.
- README (корінь репо) з окремими командами старту: `python -m mcp_servers.curriculum_mcp.server` (custom, незалежно) і `python -m study_planner.study_planner_agent [note.md]` (агент, сам піднімає/конектиться до обох MCP).

## 6. Порядок робіт — усі пункти виконано

1. ✅ Перегенеровано `output/dataset/concept_graph.json` (110 концептів, gate PASSED).
2. ✅ `mcp_servers/curriculum_mcp/` — `MCPServer` з 4 tools + `graph_store.py` (топо-сортування, нормалізоване текстове зіставлення, term-конфлікти).
3. ✅ Перевірено реальним MCP-клієнтом по stdio напряму (без агента) — усі 4 tools + помилка на невідомому курсі.
4. ✅ Демо-vault Obsidian + `.env.example`; плагін виявився вже з вбудованим MCP.
5. ✅ `study_planner_agent.py` на Claude Agent SDK — повний flow підтверджено живим прогоном.
6. ✅ `docs/TOOL_CONTRACTS.md`, `docs/DESIGN_RATIONALE.md`, `docs/DEFENSE_SCRIPT.md`.
7. ✅ Повний сценарій + fail-демо (missing note) прогнано на живому агенті; вивід зафіксовано вище й у `docs/DEFENSE_SCRIPT.md`.

## 7. Захист від "мінімальної умови" (rubric minimum-condition rule) — виконано
- 4 custom tools (≥3 вимога) — buffer +1.
- `validate_study_plan`/інші напряму читають `concept_graph.json` — primary data-source tool є.
- Обидва MCP виклики реально показані успішними в живому прогоні (не лише сконфігуровані) — стенограма вище.
- Обидва сервери явно впливають на agent flow — результат Obsidian-читання визначає, які curriculum-tools викликаються; результат curriculum-tools визначає вміст записаної нотатки.

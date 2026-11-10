# Инструкции для агента

## Границы системы

- MCP-вход — `adapters/claude/server.py`; инструменты — `register(mcp, tool)` в `tools_*.py`, новые модули — в `registry.MODULES`. Исполнение: `bridge.py` → `session.call` (operation ID, state version, один resync/retry). Прямой HTTP из инструментов обходит этот протокол.
- Только `execution/solidworks` работает с COM: `ToolController.cs` → единый partial-класс `Services/SolidWorksService.*.cs`. Все COM-вызовы, включая подключение, выполняются на `StaExecutor`, не на HTTP/MCP worker-потоках.
- `cad-planner/drawing` не зависит от COM/MCP; адаптер добавляет `cad-planner` в `sys.path` и импортирует `drawing`. Экспорты `drawing/__init__.py` проверяет `test_schema_contract.py`; формат результата — `cad-planner/contracts/draw-dialect.schema.json`.

## Команды из корня репозитория

- Полная установка: `powershell -ExecutionPolicy Bypass -File install.ps1 -Dev -NoRegister` (Windows, Python 3.11+, SOLIDWORKS, .NET Framework 4.8). `-NoRegister` не отключает установку Claude skills. Только офлайн-разработка: создать `.venv`, установить `adapters/claude/requirements-dev.txt`; SOLIDWORKS не нужен.
- C#: `powershell -ExecutionPolicy Bypass -File execution\solidworks\build.ps1`, не `dotnet build`. Сначала остановить `SolidworksExecution`: exe занят. Roslyn скачивается при первом запуске; сборка использует установленный interop. Параметры: `-SwApiDir "D:\SW\api\redist"`, `-OutDir "D:\build"`. Новые `.cs` автоматически подхватывают скрипт и `.csproj`.
- Офлайн-проверки: lint → tests, как в `.github/workflows/tests.yml`; formatter/typecheck отдельно не настроены:

```powershell
.venv\Scripts\python.exe -m ruff check --select F adapters/claude
.venv\Scripts\python.exe -m ruff check --select F821,F811,F823 adapters tools cad-planner
.venv\Scripts\python.exe -m pytest -q adapters/claude/tests cad-planner/drawing/tests
```

- MCP-контракт/регистрация: `.venv\Scripts\python.exe -m pytest -q adapters/claude/tests/test_schema_contract.py adapters/claude/tests/test_tool_surface.py`. Только drawing: `.venv\Scripts\python.exe -m pytest -q cad-planner/drawing/tests`.
- Использовать pytest: `adapters/claude/tests/conftest.py` отключает `.env`, включает все группы инструментов и изолирует логи. Самописный runner обходит изоляцию. CI — Windows/Python 3.11 и 3.13; C# и COM не проверяет.

## Изменение инструментов и контрактов

- C#-обработчик: маршрут в `ToolController.cs`, тело через `Run(request, mutating, p => ...)` из `SolidWorksService.Envelope.cs`; параметры — `PStr/PNum/PInt/PBool/PArr/PStrList`, документы — `Active*OrFail`, ошибки — `ToolFail`. Envelope восстанавливает активный документ для read-only вызовов и повышает версию при частичном изменении модели перед ошибкой.
- MCP: мм/градусы (Simulation: мм, Н, МПа). Старые SI-инструменты — `bridge.call` + `units.py`; новые — `_mm` + `call_mm`/`call_mm_json`, перевод в SI внутри C#. Не конвертировать дважды.
- Ошибка адаптера — `raise response_mapper.fail(code, text)`: строка `FAILED | ...` считается успешным вызовом. Ответ — один JSON-объект, кроме справочного `tool_help`; массивы — `list[...]`, не JSON-строки.
- После изменения схем: `.venv\Scripts\python.exe tools\gen_contract.py`, затем `.venv\Scripts\python.exe tools\gen_tools_doc.py`. Генератор контракта обновляет только `[mm/deg/kg]` и добавляет новые записи; handwritten-записи и удаление инструментов — вручную. Внутренние маршруты `model_snapshot`, `sim_export_mesh`, `flat_geometry` в MCP-контракт не входят.
- Не редактировать вручную `docs/TOOLS.md` и блоки `tools:start`/`tools:end` во всех трёх README. Подсказка модели — docstring инструмента: строковый литерал; общий хвост — `bridge.doc_append`, ограничения — `Literal`/Pydantic `Field`.
- COM-сигнатуру сверять с установленной interop-сборкой до изменения вызова; проверенные особенности — `docs/SOLIDWORKS_API_NOTES.md`. Python-тесты не подтверждают работоспособность API.

## Запуск и live-проверки

- `config.py` загружает окружение и `adapters/claude/.env` независимо от cwd. Не менять `http://localhost:8778` на `127.0.0.1`: HTTP.sys проверяет Host и возвращает 400. Python не hot-reload: переподключить MCP; C# — пересобрать/перезапустить exe. Работающий exe не перечитывает окружение адаптера.
- Адаптер запускает exe, `ensure_ready` — SOLIDWORKS. Скрытый экземпляр включается `own_instance.flag`: поиск в `%USERPROFILE%\SolidWorksMCP`, затем возле exe и выше. `install.ps1 -SharedInstance` удаляет только корневой флаг, не пользовательский.
- После `{"running": true, "job_id": ...}` получать `job_result`, не повторять CAD-операцию. Порог — `SWMCP_SYNC_WAIT` (45 с). Пути инструментов — абсолютные; `session.py` отклоняет относительные.
- MCP/live: `.venv\Scripts\python.exe tools\run_mcp.py tools\mcp\units.json` (мм). Прямой C#/live: `.venv\Scripts\python.exe tools\run_steps.py tools\smoke_test.json` (старые инструменты — метры). Оба запускают SOLIDWORKS и меняют документы; Simulation требует add-in/лицензии.
- До C#-рефакторинга: `.venv\Scripts\python.exe tools\golden.py record tools\golden_handlers.json` старым рабочим exe; после сборки — с `check`. Baseline в `tools/out/golden/` не в Git: на свежем клоне сначала `record`. Подстановки сценариев: `{REPO}`, `{OUT}`, `{SW_SAMPLES}` (переопределяется `SWMCP_SW_SAMPLES`).
- Диагностика: `.venv\Scripts\python.exe tools\doctor.py`; логи — `adapters/claude/adapter.log`, `execution/solidworks/SolidworksExecution/bin/Debug/execution.log`. Чертежи проверять экспортированным PDF, не только счётчиками interop.
- Подробности и sign-off коммитов — `CONTRIBUTING.md`; настройки — `docs/DEVELOPMENT.md`. `docs/UPSTREAM_README.md` и `docs/master-architecture.md` — исторический SolidPilot, не текущая архитектура.

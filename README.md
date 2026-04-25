# android-apps-similarity

## LIBLOOM dependency policy

`NOISE-21-DEPENDENCY-POLICY` вводит единое правило для рабочих сценариев шума:

- `available`: `LIBLOOM_HOME` задан, `LIBLOOM.jar` существует, `libs_profile/` непустой, `java` доступен.
- `unavailable`: `LIBLOOM_HOME` не задан. Пайплайн не делает молчаливый fallback и пишет `libloom_unavailable`.
- `misconfigured`: `LIBLOOM_HOME` задан, но установка сломана: нет `LIBLOOM.jar`, нет/пустой `libs_profile/`, либо нет `java`. Пайплайн пишет `libloom_misconfigured`.

Выбран режим: мягко-обязательный. Сервис и smoke-проверки обязаны явно логировать один из этих статусов; режим "тихо продолжаем без LIBLOOM" запрещён.

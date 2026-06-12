# android-apps-similarity

## Datasets

Публичные датасеты лежат в [`datasets/`](datasets/).

Текущий release:

- [`datasets/android-similarity-cube-v1`](datasets/android-similarity-cube-v1/) —
  пары APK для сценариев `repack`, `library_injection` и `code_injection`,
  SHA-256, split, метки и ограничения использования.

Сырые APK из внешних источников в git не публикуются. Для воспроизведения C01
и C05 нужно материализовать APK по SHA-256 из `manifest.csv` через доступный
пользователю источник. Synthetic APK для C02 опубликованы прямо в dataset
package.

## LIBLOOM dependency policy

`NOISE-21-DEPENDENCY-POLICY` вводит единое правило для рабочих сценариев шума:

- `available`: `LIBLOOM_HOME` задан, `LIBLOOM.jar` существует, `libs_profile/` непустой, `java` доступен.
- `unavailable`: `LIBLOOM_HOME` не задан. Пайплайн не делает молчаливый fallback и пишет `libloom_unavailable`.
- `misconfigured`: `LIBLOOM_HOME` задан, но установка сломана: нет `LIBLOOM.jar`, нет/пустой `libs_profile/`, либо нет `java`. Пайплайн пишет `libloom_misconfigured`.

Выбран режим: мягко-обязательный. Сервис и smoke-проверки обязаны явно логировать один из этих статусов; режим "тихо продолжаем без LIBLOOM" запрещён.

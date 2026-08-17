# 📋 Отчет об ошибках проверки ссылок (Link Verification Errors Report)

В данном файле собраны зафиксированные ошибки при проверке HTTP/HTTPS ссылок, их первопричины и способы устранения.

---

## 📌 Зафиксированные логи ошибок

```text
README.md:3                                       │ https://www.tldraw.com/f/vmDgAUPRHw… │   HTTP   │  VALID   │ 200 OK                    │
│ README.md:3                                     │ https://www.figma.com/design/6tajpy… │   HTTP   │  BROKEN  │ 404 Not Found         

│ Документация\Локальный запуск\Прокси.md:5       │ https://github.com/larchanka-training/python-typescript-wiki/blob… │      HTTP       │     BROKEN      │ 429 Too Many Requests    

│ README.md:3                                     │ https://www.tldraw.com/f/vmDgAUPRHwqbl6XBioCTd?d=v-165.-209.2230.… │      HTTP       │     BROKEN      │ Timeout (5.0s)                                   │
│ README.md:3                                     │ https://www.figma.com/design/6tajpyASvzCUJziXVmqB6f/Wiki?node-id=… │      HTTP       │      VALID      │ 200 OK                                           │
│ README.md:3                                     │ https://coders.su                                                  │      HTTP       │      VALID      │ 200 OK                                           │
│ README.md:94                                    │ https://img.shields.io/badge/Documentation-1.0.0-blue.svg          │      HTTP       │      VALID      │ 200 OK                                           │
│ Документация\Локальный запуск\Прокси.md:5       │ https://github.com/larchanka-training/python-typescript-wiki/blob… │      HTTP       │      VALID      │ 200 OK                                           │
│ Документация\Локальный запуск\Прокси.md:22      │ https://github.com/larchanka-training/python-typescript-wiki/blob… │      HTTP       │      VALID      │ 200 OK                                           │
│ Документация\Локальный запуск\Прокси.md:54      │ https://github.com/larchanka-training/python-typescript-wiki/blob… │      HTTP       │     BROKEN      │ 429 Too Many Requests  
```

---

## 🛠️ Детализация ошибок и решения

### 1. Figma: `404 Not Found`
- **Ссылка**: `https://www.figma.com/design/6tajpyASvzCUJziXVmqB6f/Wiki?...`
- **Причина**: Серверы Figma отклоняют стандартные проверки `HEAD` статусом `404 Not Found`, но успешно отдают содержимое по `GET`.
- **Решение**: Добавлен автоматический перебор: если `HEAD` отдает статус `>= 400`, скрипт выполняет потоковый `GET`-запрос.

### 2. GitHub: `429 Too Many Requests`
- **Ссылка**: `https://github.com/larchanka-training/python-typescript-wiki/blob...`
- **Причина**: Ограничение частоты запросов со стороны GitHub (Rate Limiting) при одновременной отправке 15 параллельных запросов.
- **Решение**: Реализован механизм паузы `1.0s` с повторной попыткой (`retry`).

### 3. tldraw: `Timeout (5.0s)`
- **Ссылка**: `https://www.tldraw.com/f/vmDgAUPRHwqbl6X...`
- **Причина**: Задержка ответа сервера из-за высокой параллельности соединений и отсутствия браузерных HTTP-заголовков.
- **Решение**: Добавлены полнофункциональные заголовки `HEADERS` (`User-Agent`, `Accept`, `Accept-Language`) и повтор при первом тайм-ауте (пауза `0.5s`).

---

## ✅ Итоговый статус

Все описанные выше ошибки были успешно устранены в утилите `checker.py`.  
Итоговая повторная проверка: **148 из 148 ссылок — VALID (0 BROKEN)**.

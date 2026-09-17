"""
core.py — Логика создания проектов Wallpaper Engine из видеофайлов.

Содержит:
  * ClipItem      — одно видео + найденный к нему превью (gif/png/jpg)
  * find_preview  — поиск превью по имени (повторяет логику shell-скрипта)
  * scan_source   — сканирование папки-источника, валидация
  * BuildEngine   — фоновый движок сборки проектов (отдельный поток)

Что делает движок для каждого валидного клипа:
  1. Создаёт папку  target/<имя>-<случайный_суффикс>
  2. Перемещает (или копирует) видео внутрь
  3. Копирует превью как preview.<ext>
  4. Пишет project.json в формате Wallpaper Engine
Клипы без найденного превью ПРОПУСКАЮТСЯ.
"""

import json
import os
import random
import shutil
import threading
import time

# Расширения превью в порядке приоритета (как в исходном скрипте — сначала gif).
PREVIEW_EXTS = (".gif", ".png", ".jpg", ".jpeg")

# Счётчик для гарантии уникальности суффикса в пределах одной секунды.
_uuid_counter = 0


def preview_base_name(filename):
    """
    Повторяет preview_base_name из shell-скрипта.

    Если имя заканчивается на '-<цифры>' (например 'freya-12'), отрезает суффикс
    и возвращает базовое имя ('freya'). Иначе возвращает имя без изменений.
    """
    if "-" not in filename:
        return filename
    head, _, suffix = filename.rpartition("-")
    if suffix and suffix.isdigit():
        return head
    return filename


def find_preview(previews_dir, filename):
    """
    Ищет файл превью для данного имени видео.

    Порядок поиска: точное имя, затем базовое имя (без числового суффикса);
    для каждого — расширения из PREVIEW_EXTS.
    Возвращает полный путь к найденному файлу или None.
    """
    candidates = [filename]
    base = preview_base_name(filename)
    if base != filename:
        candidates.append(base)

    for name in candidates:
        for ext in PREVIEW_EXTS:
            path = os.path.join(previews_dir, name + ext)
            if os.path.isfile(path):
                return path
    return None


def generate_suffix():
    """
    Случайный суффикс для имени папки (как в скрипте: epoch + pid + rand6).
    Добавлен счётчик, чтобы суффиксы не совпадали в пределах одной секунды.
    """
    global _uuid_counter
    _uuid_counter += 1
    return f"{int(time.time())}{os.getpid()}{random.randint(0, 999999):06d}{_uuid_counter}"


class ClipItem:
    """Одно видео из папки-источника и привязанное к нему превью."""

    def __init__(self, video_path, previews_dir):
        self.video_path = os.path.normpath(video_path)
        self.basename = os.path.basename(self.video_path)        # freya.mp4
        self.filename = os.path.splitext(self.basename)[0]        # freya
        self.preview_path = find_preview(previews_dir, self.filename)

    @property
    def valid(self):
        """Клип готов к сборке, если превью найдено."""
        return self.preview_path is not None

    @property
    def preview_ext(self):
        if self.preview_path:
            return os.path.splitext(self.preview_path)[1].lower()
        return None


def scan_source(source_dir, previews_dir):
    """
    Сканирует папку-источник и возвращает список ClipItem (отсортирован по имени).
    """
    items = []
    if not os.path.isdir(source_dir):
        return items
    for entry in sorted(os.listdir(source_dir)):
        if entry.lower().endswith(".mp4"):
            full = os.path.join(source_dir, entry)
            if os.path.isfile(full):
                items.append(ClipItem(full, previews_dir))
    return items


def build_project_json(basename, filename, preview_name):
    """Формирует словарь project.json в формате Wallpaper Engine."""
    return {
        "file": basename,
        "general": {
            "properties": {
                "schemecolor": {
                    "order": 0,
                    "text": "ui_browse_properties_scheme_color",
                    "type": "color",
                    "value": "0.20392 0.14902 0.10196",
                }
            }
        },
        "preview": preview_name,
        "snapshotformat": -1,
        "snapshotoverlay": "",
        "tags": ["Girls"],
        "title": filename,
        "type": "video",
        "version": 0,
    }


class BuildEngine:
    """
    Фоновый движок сборки проектов Wallpaper Engine.

    Колбэки (опциональны, вызываются из рабочего потока):
        log(text)               — строка в лог
        progress(done, total)   — общий прогресс по клипам
        item_done(name, status) — итог по одному клипу ('ok' | 'failed')
        finished(report)        — завершение (report = список dict)
    """

    def __init__(self, log=None, progress=None, item_done=None, finished=None):
        self._log = log or (lambda *_: None)
        self._progress = progress or (lambda *_: None)
        self._item_done = item_done or (lambda *_: None)
        self._finished = finished or (lambda *_: None)

        self._cancel = threading.Event()
        self._thread = None

    # ----- управление потоком -------------------------------------------------

    def start(self, items, target_dir, move=True):
        if self.is_running():
            return False
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, args=(items, target_dir, move), daemon=True
        )
        self._thread.start()
        return True

    def cancel(self):
        self._cancel.set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ----- основная логика ----------------------------------------------------

    def _run(self, items, target_dir, move):
        report = []
        valid = [it for it in items if it.valid]
        skipped = [it for it in items if not it.valid]

        total = len(valid)
        self._progress(0, total)
        self._log(
            f"[START] К сборке: {total} клип(ов), "
            f"пропущено без превью: {len(skipped)}. Режим: "
            f"{'перемещение' if move else 'копирование'}."
        )

        for it in skipped:
            self._log(f"[SKIP]  {it.basename} — превью не найдено.")

        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            self._log(f"[ERROR] Не удалось создать целевую папку: {exc}")
            self._finished(report)
            return

        done = 0
        for it in valid:
            if self._cancel.is_set():
                self._log("[CANCEL] Операция отменена пользователем.")
                break
            entry = self._process_item(it, target_dir, move)
            report.append(entry)
            self._item_done(it.basename, entry["status"])
            done += 1
            self._progress(done, total)

        self._print_summary(report, len(skipped))
        self._finished(report)

    def _process_item(self, item, target_dir, move):
        entry = {"name": item.basename, "status": "failed", "folder": None}
        try:
            folder_name = f"{item.filename}-{generate_suffix()}"
            folder = os.path.join(target_dir, folder_name)
            os.makedirs(folder, exist_ok=True)
            entry["folder"] = folder_name

            # --- видео ---
            dst_video = os.path.join(folder, item.basename)
            if move:
                shutil.move(item.video_path, dst_video)
            else:
                shutil.copy2(item.video_path, dst_video)

            # --- превью ---
            preview_name = "preview" + item.preview_ext
            shutil.copy2(item.preview_path, os.path.join(folder, preview_name))

            # --- project.json ---
            data = build_project_json(item.basename, item.filename, preview_name)
            with open(os.path.join(folder, "project.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent="\t")

            entry["status"] = "ok"
            self._log(f"[OK]    {folder_name}")
        except Exception as exc:  # noqa: BLE001 — один сбой не валит весь процесс
            self._log(f"[ERROR] {item.basename}: {exc}")
        return entry

    def _print_summary(self, report, skipped):
        ok = sum(1 for e in report if e["status"] == "ok")
        failed = sum(1 for e in report if e["status"] == "failed")
        self._log("")
        self._log("=" * 50)
        self._log(
            f"ИТОГ: создано {ok}, ошибок {failed}, пропущено {skipped}."
        )
        self._log("=" * 50)

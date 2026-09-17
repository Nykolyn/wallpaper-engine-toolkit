"""
copier.py — Логика копирования папок Wallpaper Engine.

Содержит:
  * CopyJob          — описание одной задачи (источник + кол-во копий)
  * CopyEngine       — фоновый движок копирования (запускается в отдельном потоке)

Алгоритм отработан на практике:
  1. Анализ источника: разделяем файлы на большие (>50MB) и маленькие.
  2. Для каждой копии: создаём папку -> копируем маленькие -> копируем большие.
  3. Верификация: сравниваем кол-во файлов, докопируем недостающие.
  4. Итоговый отчёт.
"""

import os
import shutil
import threading
import time

# Файл считается "большим" если он больше этого порога (байт).
LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50 MB

# Размер буфера для копирования больших файлов (для подсчёта скорости).
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB


class CopyJob:
    """Одна задача копирования: папка-источник и желаемое кол-во копий."""

    def __init__(self, source, count=3):
        self.source = os.path.normpath(source)
        self.count = int(count)

    @property
    def name(self):
        return os.path.basename(self.source.rstrip("\\/"))


def list_files_recursive(folder):
    """Возвращает список относительных путей всех файлов в папке (рекурсивно)."""
    result = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, folder)
            result.append(rel)
    return result


def human_size(num_bytes):
    """Человекочитаемый размер."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


class CopyEngine:
    """
    Фоновый движок копирования.

    Колбэки (все опциональны, вызываются из рабочего потока):
        log(text)                    — добавить строку в лог
        progress(done, total)        — обновить общий прогресс (по копиям)
        speed(mbps)                  — текущая скорость копирования больших файлов
        finished(report)             — завершение (report = список dict)
    """

    def __init__(self, log=None, progress=None, speed=None, finished=None):
        self._log = log or (lambda *_: None)
        self._progress = progress or (lambda *_: None)
        self._speed = speed or (lambda *_: None)
        self._finished = finished or (lambda *_: None)

        self._cancel = threading.Event()
        self._thread = None

    # ----- управление потоком -------------------------------------------------

    def start(self, jobs, dest_root):
        """Запустить копирование в фоновом потоке."""
        if self.is_running():
            return False
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, args=(jobs, dest_root), daemon=True
        )
        self._thread.start()
        return True

    def cancel(self):
        """Запросить отмену (прервётся после текущего файла)."""
        self._cancel.set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ----- основная логика ----------------------------------------------------

    def _run(self, jobs, dest_root):
        report = []

        # Считаем общее кол-во копий для прогресс-бара.
        total_copies = sum(j.count for j in jobs)
        done_copies = 0
        self._progress(0, total_copies)

        for job in jobs:
            if self._cancel.is_set():
                self._log("[CANCEL] Операция отменена пользователем.")
                break

            entry = self._process_job(job, dest_root, report)

            # Обновляем прогресс по факту обработанных копий этой задачи.
            done_copies += job.count
            self._progress(min(done_copies, total_copies), total_copies)
            report.append(entry)

        self._print_summary(report)
        self._finished(report)

    def _process_job(self, job, dest_root, report):
        """Обработать одну задачу (создать N копий одной папки)."""
        entry = {"name": job.name, "requested": job.count, "ok": 0, "failed": 0}

        if not os.path.isdir(job.source):
            self._log(f"[ERROR] Источник не найден: {job.source}")
            entry["failed"] = job.count
            return entry

        # --- Шаг 1: Анализ источника ---
        source_files = list_files_recursive(job.source)
        if not source_files:
            self._log(f"[WARN]  Источник пуст: {job.source}")

        large, small = [], []
        for rel in source_files:
            try:
                size = os.path.getsize(os.path.join(job.source, rel))
            except OSError:
                size = 0
            (large if size > LARGE_FILE_THRESHOLD else small).append(rel)

        preview = ", ".join(os.path.basename(f) for f in source_files[:3])
        if len(source_files) > 3:
            preview += ", ..."
        self._log(
            f"[START] Обработка: {job.name} → "
            f"{os.path.basename(dest_root.rstrip(chr(92)+'/'))} ({job.count} копий)"
        )
        self._log(
            f"[INFO]  Источник: {len(source_files)} файлов "
            f"({len(small)} мал. + {len(large)} больших) [{preview}]"
        )

        # --- Определяем стартовый номер копии (продолжаем нумерацию) ---
        start_index = self._next_copy_index(dest_root, job.name)

        made = 0
        index = start_index
        while made < job.count:
            if self._cancel.is_set():
                self._log("[CANCEL] Прерывание текущей задачи.")
                break

            copy_name = f"{job.name}_copy{index}"
            dest = os.path.join(dest_root, copy_name)
            index += 1

            ok = self._make_one_copy(job.source, dest, copy_name, source_files, small, large)
            if ok:
                entry["ok"] += 1
            else:
                entry["failed"] += 1
            made += 1

        status = "✅" if entry["failed"] == 0 and not self._cancel.is_set() else "❌"
        self._log(
            f"[DONE]  {job.name}: {entry['ok']}/{job.count} копий успешно {status}"
        )
        return entry

    def _next_copy_index(self, dest_root, base_name):
        """
        Найти первый свободный номер копии.
        Если _copy1.._copy5 существуют — вернёт 6.
        """
        n = 1
        while os.path.exists(os.path.join(dest_root, f"{base_name}_copy{n}")):
            n += 1
        return n

    def _make_one_copy(self, source, dest, copy_name, source_files, small, large):
        """Создать одну копию + верифицировать + докопировать недостающее."""
        try:
            os.makedirs(dest, exist_ok=True)

            # Шаг 2.1: сначала маленькие файлы (быстро).
            for rel in small:
                if self._cancel.is_set():
                    return False
                self._copy_small(source, dest, rel)

            # Шаг 2.2: затем большие файлы по одному (со скоростью).
            for rel in large:
                if self._cancel.is_set():
                    return False
                self._copy_large(source, dest, rel)

            # Шаг 3: верификация.
            expected = len(source_files)
            dest_files = list_files_recursive(dest)
            actual = len(dest_files)

            if actual != expected:
                missing = set(source_files) - set(dest_files)
                self._log(
                    f"[WARN]  {copy_name} неполная ({actual}/{expected}), "
                    f"докопирование {len(missing)} файлов..."
                )
                for rel in missing:
                    if self._cancel.is_set():
                        return False
                    size = self._safe_size(os.path.join(source, rel))
                    if size > LARGE_FILE_THRESHOLD:
                        self._copy_large(source, dest, rel)
                    else:
                        self._copy_small(source, dest, rel)

                actual = len(list_files_recursive(dest))
                if actual == expected:
                    self._log(f"[FIX]   {copy_name} ... ✅ {actual}/{expected}")
                else:
                    self._log(
                        f"[ERROR] {copy_name} остаётся неполной ({actual}/{expected})"
                    )
                    return False
            else:
                self._log(f"[COPY]  {copy_name} ... ✅ {actual}/{expected}")

            return True

        except Exception as exc:  # noqa: BLE001 — не прерываем весь процесс
            self._log(f"[ERROR] {copy_name}: {exc}")
            return False

    # ----- низкоуровневое копирование ----------------------------------------

    def _copy_small(self, source, dest, rel):
        src = os.path.join(source, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    def _copy_large(self, source, dest, rel):
        """Копирование большого файла кусками с подсчётом скорости (MB/s)."""
        src = os.path.join(source, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)

        total = self._safe_size(src)
        copied = 0
        start = time.time()
        last_report = start

        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                if self._cancel.is_set():
                    break
                chunk = fsrc.read(CHUNK_SIZE)
                if not chunk:
                    break
                fdst.write(chunk)
                copied += len(chunk)

                now = time.time()
                if now - last_report >= 0.5:
                    elapsed = now - start
                    mbps = (copied / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                    self._speed(mbps)
                    last_report = now

        # Сохраняем метаданные (время изменения и т.д.).
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass

        elapsed = time.time() - start
        if elapsed > 0 and total > 0:
            mbps = (total / (1024 * 1024)) / elapsed
            self._log(
                f"        ⤷ {os.path.basename(rel)} "
                f"({human_size(total)}) @ {mbps:.1f} MB/s"
            )
        self._speed(0)

    @staticmethod
    def _safe_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    # ----- отчёт --------------------------------------------------------------

    def _print_summary(self, report):
        self._log("")
        self._log("=" * 50)
        self._log("ИТОГОВЫЙ ОТЧЁТ")
        self._log("=" * 50)
        self._log(f"{'Папка':<28}{'Копий':<10}{'Статус'}")
        self._log("-" * 50)
        for e in report:
            status = "✅" if e["failed"] == 0 else "❌"
            self._log(
                f"{e['name'][:27]:<28}"
                f"{str(e['ok']) + '/' + str(e['requested']):<10}"
                f"{status}"
            )
        self._log("=" * 50)

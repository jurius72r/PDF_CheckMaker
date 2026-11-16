from __future__ import annotations

import csv
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
TEMPLATES_DIR = ROOT_DIR / "templates"
OUTPUT_DIR = ROOT_DIR / "output"
PLACEHOLDER_PATTERN = re.compile(r"{{\s*(.*?)\s*}}")
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class CheckmakerError(Exception):
    """An expected, user-facing application error."""


def list_files(directory: Path, extensions: set[str]) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in extensions
        ),
        key=lambda path: path.name.casefold(),
    )


def read_invoices(data_file: Path) -> list[dict[str, Any]]:
    try:
        if data_file.suffix.lower() == ".csv":
            with data_file.open("r", encoding="utf-8-sig", newline="") as file:
                invoices = list(csv.DictReader(file))
            if not invoices:
                raise CheckmakerError(f"В файле {data_file.name} нет строк со счетами.")
        elif data_file.suffix.lower() == ".json":
            with data_file.open("r", encoding="utf-8-sig") as file:
                data = json.load(file)
            if isinstance(data, dict):
                data = data.get("invoices")
            if not isinstance(data, list):
                raise CheckmakerError(
                    "JSON должен содержать массив счетов или объект с массивом в поле 'invoices'."
                )
            invoices = data
        else:
            raise CheckmakerError(f"Неподдерживаемый формат: {data_file.suffix}")
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as error:
        raise CheckmakerError(f"Не удалось прочитать {data_file.name}: {error}") from error

    if not invoices:
        raise CheckmakerError(f"В файле {data_file.name} нет счетов.")

    for row_number, invoice in enumerate(invoices, start=1):
        if not isinstance(invoice, dict):
            raise CheckmakerError(
                f"Запись №{row_number} в {data_file.name} должна быть объектом с полями."
            )
        invoice_id = invoice.get("invoice_id")
        if invoice_id is None or not str(invoice_id).strip():
            raise CheckmakerError(
                f"В записи №{row_number} файла {data_file.name} отсутствует invoice_id."
            )
    return invoices


def choose_item(items: list[Path], title: str) -> Path:
    while True:
        choice = input(f"\n{title} (номер): ").strip()
        try:
            selected = int(choice)
        except ValueError:
            print("Введите целое число из списка.")
            continue
        if 1 <= selected <= len(items):
            return items[selected - 1]
        print(f"Выберите номер от 1 до {len(items)}.")


def choose_invoice(invoices: list[dict[str, Any]]) -> dict[str, Any]:
    print("\nДоступные счета:")
    for index, invoice in enumerate(invoices, start=1):
        print(f"  {index}. {invoice['invoice_id']}")
    while True:
        choice = input("Выберите номер счета: ").strip()
        try:
            selected = int(choice)
        except ValueError:
            print("Введите целое число из списка.")
            continue
        if 1 <= selected <= len(invoices):
            return invoices[selected - 1]
        print(f"Выберите номер от 1 до {len(invoices)}.")


def display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_template(template: str, invoice: dict[str, Any]) -> str:
    def replace_placeholder(match: re.Match[str]) -> str:
        field_name = match.group(1).strip()
        if not field_name:
            raise CheckmakerError("В HTML-шаблоне найден пустой плейсхолдер {{ }}.")
        if field_name not in invoice:
            raise CheckmakerError(
                f"В выбранном счете нет поля '{field_name}', указанного в шаблоне."
            )
        return html.escape(display_value(invoice[field_name]), quote=True)

    return PLACEHOLDER_PATTERN.sub(replace_placeholder, template)


def make_output_path(invoice_id: Any) -> Path:
    safe_id = INVALID_FILENAME_CHARS.sub("_", str(invoice_id)).strip(" .")
    if not safe_id:
        safe_id = "invoice"
    base_path = OUTPUT_DIR / f"invoice_{safe_id}"
    output_path = OUTPUT_DIR / f"{base_path.name}.pdf"
    suffix = 2
    while output_path.exists():
        output_path = OUTPUT_DIR / f"{base_path.name}_{suffix}.pdf"
        suffix += 1
    return output_path


def create_pdf(template_file: Path, invoice: dict[str, Any]) -> Path:
    try:
        from weasyprint import HTML
    except ImportError as error:
        raise CheckmakerError(
            "WeasyPrint не установлен. Установите зависимости командой "
            "'python -m pip install -r requirements.txt'."
        ) from error

    try:
        template = template_file.read_text(encoding="utf-8-sig")
        rendered_html = render_template(template, invoice)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = make_output_path(invoice["invoice_id"])
        HTML(string=rendered_html, base_url=str(template_file.parent)).write_pdf(
            str(output_path)
        )
    except CheckmakerError:
        raise
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        raise CheckmakerError(f"Не удалось создать PDF: {error}") from error

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise CheckmakerError("WeasyPrint не создал PDF-файл.")
    return output_path


def open_pdf(pdf_path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(pdf_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(pdf_path)])
        else:
            raise CheckmakerError("Автоматическое открытие поддерживается только в Windows и macOS.")
    except OSError as error:
        raise CheckmakerError(f"PDF создан, но не удалось открыть его: {error}") from error


def configure_console() -> None:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> int:
    configure_console()
    data_files = list_files(DATA_DIR, {".csv", ".json"})
    template_files = list_files(TEMPLATES_DIR, {".html", ".htm"})

    print("=== Генератор PDF-чеков ===")
    if not data_files:
        print(f"Файлы CSV/JSON не найдены в каталоге: {DATA_DIR}")
        return 1
    if not template_files:
        print(f"HTML-шаблоны не найдены в каталоге: {TEMPLATES_DIR}")
        return 1

    print("\nФайлы данных:")
    for index, path in enumerate(data_files, start=1):
        print(f"  {index}. {path.name}")
    print("\nHTML-шаблоны:")
    for index, path in enumerate(template_files, start=1):
        print(f"  {index}. {path.name}")

    try:
        data_file = choose_item(data_files, "Выберите файл данных")
        template_file = choose_item(template_files, "Выберите HTML-шаблон")
        invoices = read_invoices(data_file)
        invoice = choose_invoice(invoices)
        pdf_path = create_pdf(template_file, invoice)
        open_pdf(pdf_path)
        print(f"\nPDF создан и открыт в системной программе: {pdf_path}")
    except (CheckmakerError, EOFError, KeyboardInterrupt) as error:
        if isinstance(error, (EOFError, KeyboardInterrupt)):
            print("\nРабота отменена.")
            return 1
        print(f"\nОшибка: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

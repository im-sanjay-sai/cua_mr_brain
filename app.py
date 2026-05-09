from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from .model_client import DEFAULT_BASE_URL, DEFAULT_MODEL, LocalizationResult, localize_region
except ImportError:
    from model_client import DEFAULT_BASE_URL, DEFAULT_MODEL, LocalizationResult, localize_region


APP_TITLE = "X-ray / MRI Region Locator"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".dcm"}


@dataclass
class ImageRecord:
    path: Path
    image: Image.Image
    annotated_image: Image.Image | None = None
    result: LocalizationResult | None = None
    error: str = ""
    status: str = "Loaded"

    @property
    def display_image(self) -> Image.Image:
        return self.annotated_image or self.image


class MedicalImageLocatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1320x820")
        self.root.minsize(1040, 680)

        self.records: list[ImageRecord] = []
        self.selected_index = -1
        self.tk_image: ImageTk.PhotoImage | None = None
        self.busy = False

        self.question_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Load images to begin.")
        self.summary_var = tk.StringVar(value="No images loaded")
        self.model_var = tk.StringVar(value=os.getenv("TZAFON_MODEL", DEFAULT_MODEL))
        self.base_url_var = tk.StringVar(value=os.getenv("TZAFON_BASE_URL", DEFAULT_BASE_URL))
        self.api_key_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="box_tool")

        self._build_ui()
        self.root.bind("<Configure>", self._on_resize)
        self.root.bind("<Left>", lambda _event: self.previous_image())
        self.root.bind("<Right>", lambda _event: self.next_image())

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        panes.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(panes, width=280)
        center = ttk.Frame(panes)
        right = ttk.Frame(panes, width=360)
        panes.add(left, weight=0)
        panes.add(center, weight=1)
        panes.add(right, weight=0)

        self._build_left_panel(left)
        self._build_center_panel(center)
        self._build_right_panel(right)
        self._render_canvas()

    def _build_left_panel(self, panel: ttk.Frame) -> None:
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        ttk.Label(panel, text="Images").grid(row=0, column=0, sticky="w")
        self.load_button = ttk.Button(panel, text="Load Images", command=self.load_images)
        self.load_button.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.folder_button = ttk.Button(panel, text="Load Folder", command=self.load_folder)
        self.folder_button.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.clear_study_button = ttk.Button(panel, text="Clear Study", command=self.clear_study)
        self.clear_study_button.grid(row=3, column=0, sticky="ew", pady=(6, 10))

        list_frame = ttk.Frame(panel)
        list_frame.grid(row=4, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.image_tree = ttk.Treeview(
            list_frame,
            columns=("status",),
            show="tree headings",
            selectmode="browse",
            height=18,
        )
        self.image_tree.heading("#0", text="File")
        self.image_tree.heading("status", text="Status")
        self.image_tree.column("#0", width=185, minwidth=140, stretch=True)
        self.image_tree.column("status", width=80, minwidth=70, stretch=False)
        self.image_tree.grid(row=0, column=0, sticky="nsew")
        self.image_tree.bind("<<TreeviewSelect>>", self._on_image_select)

        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.image_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.image_tree.configure(yscrollcommand=scroll.set)

        nav = ttk.Frame(panel)
        nav.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        nav.columnconfigure((0, 1), weight=1)
        ttk.Button(nav, text="Previous", command=self.previous_image).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(nav, text="Next", command=self.next_image).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ttk.Label(panel, textvariable=self.summary_var, wraplength=260).grid(row=6, column=0, sticky="ew", pady=(10, 0))

    def _build_center_panel(self, panel: ttk.Frame) -> None:
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(panel, bg="#111111", highlightthickness=1, highlightbackground="#444444")
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=10)

        self.image_meta = ttk.Label(panel, text="", anchor="center")
        self.image_meta.grid(row=1, column=0, sticky="ew", padx=10, pady=(8, 0))

    def _build_right_panel(self, panel: ttk.Frame) -> None:
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(20, weight=1)

        ttk.Label(panel, text="Question").grid(row=0, column=0, sticky="w")
        question_entry = ttk.Entry(panel, textvariable=self.question_var)
        question_entry.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        question_entry.bind("<Return>", lambda _event: self.ask_current())

        ttk.Label(panel, text="Mode").grid(row=2, column=0, sticky="w", pady=(12, 2))
        mode = ttk.Combobox(
            panel,
            textvariable=self.mode_var,
            values=("box_tool", "computer_action"),
            state="readonly",
        )
        mode.grid(row=3, column=0, sticky="ew")

        action_row = ttk.Frame(panel)
        action_row.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        action_row.columnconfigure((0, 1), weight=1)
        self.ask_button = ttk.Button(action_row, text="Ask Current", command=self.ask_current)
        self.ask_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.ask_all_button = ttk.Button(action_row, text="Ask All", command=self.ask_all)
        self.ask_all_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        output_row = ttk.Frame(panel)
        output_row.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        output_row.columnconfigure((0, 1), weight=1)
        self.save_button = ttk.Button(output_row, text="Save Current", command=self.save_current, state="disabled")
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.save_all_button = ttk.Button(output_row, text="Save All", command=self.save_all, state="disabled")
        self.save_all_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        clear_row = ttk.Frame(panel)
        clear_row.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        clear_row.columnconfigure((0, 1), weight=1)
        self.clear_button = ttk.Button(clear_row, text="Clear Current", command=self.clear_current_overlay, state="disabled")
        self.clear_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.clear_all_button = ttk.Button(clear_row, text="Clear All", command=self.clear_all_overlays, state="disabled")
        self.clear_all_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ttk.Separator(panel).grid(row=7, column=0, sticky="ew", pady=14)

        ttk.Label(panel, text="Model").grid(row=8, column=0, sticky="w")
        ttk.Entry(panel, textvariable=self.model_var).grid(row=9, column=0, sticky="ew", pady=(3, 0))

        ttk.Label(panel, text="Base URL").grid(row=10, column=0, sticky="w", pady=(10, 2))
        ttk.Entry(panel, textvariable=self.base_url_var).grid(row=11, column=0, sticky="ew")

        ttk.Label(panel, text="API key override").grid(row=12, column=0, sticky="w", pady=(10, 2))
        ttk.Entry(panel, textvariable=self.api_key_var, show="*").grid(row=13, column=0, sticky="ew")

        ttk.Separator(panel).grid(row=14, column=0, sticky="ew", pady=14)

        ttk.Label(panel, text="Answer").grid(row=15, column=0, sticky="w")
        self.answer = tk.Text(panel, height=15, wrap="word", state="disabled")
        self.answer.grid(row=16, column=0, sticky="nsew")
        panel.rowconfigure(16, weight=1)

        ttk.Label(panel, textvariable=self.status_var, wraplength=340).grid(row=17, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            panel,
            text="Visual localization only. Do not use this for clinical diagnosis.",
            wraplength=340,
            foreground="#8a5a00",
        ).grid(row=18, column=0, sticky="ew", pady=(8, 0))
        self._update_action_states()

    def load_images(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose X-ray or MRI images",
            filetypes=[
                ("Medical/common images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.dcm"),
                ("All files", "*.*"),
            ],
        )
        if paths:
            self._add_paths([Path(path) for path in paths])

    def load_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose image folder")
        if not folder:
            return
        paths = sorted(
            path
            for path in Path(folder).rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        if not paths:
            messagebox.showinfo("No images", "No supported images found in that folder.")
            return
        self._add_paths(paths)

    def _add_paths(self, paths: list[Path]) -> None:
        existing = {record.path.resolve() for record in self.records}
        loaded = 0
        failures: list[str] = []

        for path in paths:
            resolved = path.resolve()
            if resolved in existing:
                continue
            try:
                image = load_medical_image(path)
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")
                continue
            self.records.append(ImageRecord(path=path, image=image))
            existing.add(resolved)
            loaded += 1

        if loaded and self.selected_index == -1:
            self.selected_index = 0
        elif loaded:
            self.selected_index = min(self.selected_index, len(self.records) - 1)

        self._refresh_image_list()
        self._select_tree_index(self.selected_index)
        self._update_current_view()

        message = f"Loaded {loaded} image{'s' if loaded != 1 else ''}."
        if failures:
            message += f" {len(failures)} file{'s' if len(failures) != 1 else ''} failed."
            self._set_answer("\n".join(failures[:12]))
        self.status_var.set(message)

    def clear_study(self) -> None:
        if self.busy:
            return
        self.records.clear()
        self.selected_index = -1
        self._refresh_image_list()
        self._update_current_view()
        self.status_var.set("Study cleared.")

    def ask_current(self) -> None:
        index = self.selected_index
        if index < 0 or index >= len(self.records):
            messagebox.showinfo("No image", "Load and select an image first.")
            return
        question = self._validated_question()
        if question is None:
            return
        self._set_busy(True)
        self.records[index].status = "Running"
        self.records[index].error = ""
        self._refresh_image_list()
        self.status_var.set(f"Asking model for {self.records[index].path.name}...")
        self._start_worker(index, question)

    def ask_all(self) -> None:
        if not self.records:
            messagebox.showinfo("No images", "Load images first.")
            return
        question = self._validated_question()
        if question is None:
            return

        self._set_busy(True)
        for record in self.records:
            record.status = "Queued"
            record.error = ""
        self._refresh_image_list()
        self.status_var.set(f"Queued {len(self.records)} images.")
        api_key = self.api_key_var.get().strip() or None
        base_url = self.base_url_var.get().strip()
        model = self.model_var.get().strip()
        mode = self.mode_var.get()

        thread = threading.Thread(target=self._ask_all_worker, args=(question, mode, api_key, base_url, model), daemon=True)
        thread.start()

    def _validated_question(self) -> str | None:
        question = self.question_var.get().strip()
        if not question:
            messagebox.showinfo("No question", "Type a question first.")
            return None
        return question

    def _start_worker(self, index: int, question: str) -> None:
        record = self.records[index]
        image = record.image.copy()
        api_key = self.api_key_var.get().strip() or None
        base_url = self.base_url_var.get().strip()
        model = self.model_var.get().strip()
        mode = self.mode_var.get()

        thread = threading.Thread(
            target=self._ask_worker,
            args=(index, image, question, mode, api_key, base_url, model),
            daemon=True,
        )
        thread.start()

    def _ask_all_worker(
        self,
        question: str,
        mode: str,
        api_key: str | None,
        base_url: str,
        model: str,
    ) -> None:
        for index, record in enumerate(list(self.records)):
            self.root.after(0, self._mark_running, index)
            try:
                result = localize_region(
                    record.image.copy(),
                    question,
                    mode=mode,  # type: ignore[arg-type]
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                )
            except Exception as exc:
                self.root.after(0, self._handle_error, index, exc)
                continue
            self.root.after(0, self._handle_result, index, result)

        self.root.after(0, self._finish_batch)

    def _ask_worker(
        self,
        index: int,
        image: Image.Image,
        question: str,
        mode: str,
        api_key: str | None,
        base_url: str,
        model: str,
    ) -> None:
        try:
            result = localize_region(
                image,
                question,
                mode=mode,  # type: ignore[arg-type]
                api_key=api_key,
                base_url=base_url,
                model=model,
            )
        except Exception as exc:
            self.root.after(0, self._handle_error, index, exc)
            self.root.after(0, self._set_busy, False)
            return
        self.root.after(0, self._handle_result, index, result)
        self.root.after(0, self._set_busy, False)

    def _mark_running(self, index: int) -> None:
        if 0 <= index < len(self.records):
            self.records[index].status = "Running"
            self._refresh_image_list()
            self.status_var.set(f"Running {index + 1}/{len(self.records)}: {self.records[index].path.name}")

    def _handle_result(self, index: int, result: LocalizationResult) -> None:
        if not 0 <= index < len(self.records):
            return

        record = self.records[index]
        record.result = result
        record.error = ""
        if result.pixel_box is not None:
            record.annotated_image = draw_annotation(record.image, result)
            record.status = "Marked"
        else:
            record.annotated_image = None
            record.status = "No region"

        self._refresh_image_list()
        if index == self.selected_index:
            self._update_current_view()
            self.status_var.set(f"Updated {record.path.name}.")
        self._update_action_states()

    def _handle_error(self, index: int, exc: Exception) -> None:
        if not 0 <= index < len(self.records):
            self._set_busy(False)
            return

        record = self.records[index]
        record.status = "Error"
        record.error = str(exc)
        record.result = None
        record.annotated_image = None
        self._refresh_image_list()
        if index == self.selected_index:
            self._update_current_view()
            self.status_var.set(f"Request failed for {record.path.name}.")
        self._update_action_states()
        if not self.busy:
            self._set_busy(False)

    def _finish_batch(self) -> None:
        marked = sum(1 for record in self.records if record.status == "Marked")
        errors = sum(1 for record in self.records if record.status == "Error")
        self.status_var.set(f"Finished batch. Marked {marked}; errors {errors}.")
        self._set_busy(False)

    def save_current(self) -> None:
        record = self._current_record()
        if record is None or record.annotated_image is None:
            return
        default_name = f"{record.path.stem}_annotated.png"
        path = filedialog.asksaveasfilename(
            title="Save annotated image",
            initialfile=default_name,
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")],
        )
        if not path:
            return
        record.annotated_image.save(path)
        self.status_var.set(f"Saved {Path(path).name}.")

    def save_all(self) -> None:
        annotated = [record for record in self.records if record.annotated_image is not None]
        if not annotated:
            return
        folder = filedialog.askdirectory(title="Choose output folder")
        if not folder:
            return
        out_dir = Path(folder)
        for record in annotated:
            output_path = out_dir / f"{record.path.stem}_annotated.png"
            record.annotated_image.save(output_path)
        self.status_var.set(f"Saved {len(annotated)} annotated image{'s' if len(annotated) != 1 else ''}.")

    def clear_current_overlay(self) -> None:
        record = self._current_record()
        if record is None:
            return
        record.annotated_image = None
        record.result = None
        record.error = ""
        record.status = "Loaded"
        self._refresh_image_list()
        self._update_current_view()
        self.status_var.set(f"Cleared overlay for {record.path.name}.")

    def clear_all_overlays(self) -> None:
        for record in self.records:
            record.annotated_image = None
            record.result = None
            record.error = ""
            record.status = "Loaded"
        self._refresh_image_list()
        self._update_current_view()
        self.status_var.set("Cleared all overlays.")

    def previous_image(self) -> None:
        if not self.records:
            return
        self.selected_index = max(0, self.selected_index - 1)
        self._select_tree_index(self.selected_index)
        self._update_current_view()

    def next_image(self) -> None:
        if not self.records:
            return
        self.selected_index = min(len(self.records) - 1, self.selected_index + 1)
        self._select_tree_index(self.selected_index)
        self._update_current_view()

    def _on_image_select(self, _event: tk.Event) -> None:
        selection = self.image_tree.selection()
        if not selection:
            return
        try:
            self.selected_index = int(selection[0])
        except ValueError:
            return
        self._update_current_view()

    def _refresh_image_list(self) -> None:
        selected = self.selected_index
        self.image_tree.delete(*self.image_tree.get_children())
        for index, record in enumerate(self.records):
            self.image_tree.insert("", "end", iid=str(index), text=record.path.name, values=(record.status,))
        self._select_tree_index(selected)
        self._update_summary()

    def _select_tree_index(self, index: int) -> None:
        if 0 <= index < len(self.records):
            iid = str(index)
            self.image_tree.selection_set(iid)
            self.image_tree.see(iid)

    def _update_current_view(self) -> None:
        record = self._current_record()
        if record is None:
            self.image_meta.configure(text="")
            self._set_answer("")
        else:
            self.image_meta.configure(text=f"{record.path.name} | {record.image.width}x{record.image.height} | {record.status}")
            self._set_answer(format_record(record))
        self._render_canvas()
        self._update_action_states()

    def _update_summary(self) -> None:
        total = len(self.records)
        marked = sum(1 for record in self.records if record.status == "Marked")
        errors = sum(1 for record in self.records if record.status == "Error")
        if not total:
            self.summary_var.set("No images loaded")
            return
        self.summary_var.set(f"{total} images | {marked} marked | {errors} errors")

    def _update_action_states(self) -> None:
        current = self._current_record()
        has_images = bool(self.records)
        has_annotated = any(record.annotated_image is not None for record in self.records)
        current_annotated = current is not None and current.annotated_image is not None

        normal_if_ready = "disabled" if self.busy else "normal"
        self.load_button.configure(state=normal_if_ready)
        self.folder_button.configure(state=normal_if_ready)
        self.clear_study_button.configure(state=normal_if_ready)
        self.ask_button.configure(state=normal_if_ready if current is not None else "disabled")
        self.ask_all_button.configure(state=normal_if_ready if has_images else "disabled")
        self.save_button.configure(state="normal" if current_annotated and not self.busy else "disabled")
        self.save_all_button.configure(state="normal" if has_annotated and not self.busy else "disabled")
        self.clear_button.configure(state="normal" if current_annotated and not self.busy else "disabled")
        self.clear_all_button.configure(state="normal" if has_annotated and not self.busy else "disabled")

    def _current_record(self) -> ImageRecord | None:
        if 0 <= self.selected_index < len(self.records):
            return self.records[self.selected_index]
        return None

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._update_action_states()

    def _set_answer(self, text: str) -> None:
        self.answer.configure(state="normal")
        self.answer.delete("1.0", tk.END)
        if text:
            self.answer.insert(tk.END, text)
        self.answer.configure(state="disabled")

    def _on_resize(self, _event: tk.Event) -> None:
        self.root.after_idle(self._render_canvas)

    def _render_canvas(self) -> None:
        self.canvas.delete("all")
        record = self._current_record()
        canvas_width = max(240, self.canvas.winfo_width())
        canvas_height = max(240, self.canvas.winfo_height())

        if record is None:
            self.canvas.create_text(
                canvas_width // 2,
                canvas_height // 2,
                text="Load multiple X-ray or MRI images",
                fill="#d8d8d8",
                font=("Helvetica", 18),
            )
            return

        display = record.display_image.copy()
        display.thumbnail((canvas_width - 28, canvas_height - 28), Image.Resampling.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(display)
        x = (canvas_width - display.width) // 2
        y = (canvas_height - display.height) // 2
        self.canvas.create_image(x, y, anchor="nw", image=self.tk_image)


def load_medical_image(path: Path) -> Image.Image:
    if path.suffix.lower() == ".dcm":
        return load_dicom_image(path)
    return Image.open(path).convert("RGB")


def load_dicom_image(path: Path) -> Image.Image:
    try:
        import numpy as np
        import pydicom
    except ImportError as exc:
        raise RuntimeError("DICOM files need pydicom and numpy. Install with: python -m pip install -r requirements.txt") from exc

    ds = pydicom.dcmread(str(path))
    pixels = ds.pixel_array.astype("float32")
    if pixels.ndim > 2:
        pixels = pixels[0]

    slope = float(getattr(ds, "RescaleSlope", 1.0))
    intercept = float(getattr(ds, "RescaleIntercept", 0.0))
    pixels = pixels * slope + intercept

    center = _first_number(getattr(ds, "WindowCenter", None))
    width = _first_number(getattr(ds, "WindowWidth", None))
    if center is not None and width is not None and width > 0:
        low = center - width / 2
        high = center + width / 2
    else:
        low, high = np.percentile(pixels, [1, 99])
        if high <= low:
            low, high = float(pixels.min()), float(pixels.max() or 1)

    normalized = np.clip((pixels - low) / max(high - low, 1e-6), 0, 1)
    array = (normalized * 255).astype("uint8")

    if getattr(ds, "PhotometricInterpretation", "") == "MONOCHROME1":
        array = 255 - array

    return Image.fromarray(array).convert("RGB")


def _first_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        value = value[0] if value else None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def draw_annotation(image: Image.Image, result: LocalizationResult) -> Image.Image:
    output = image.convert("RGB").copy()
    width, height = output.size
    stroke = max(3, min(width, height) // 180)
    color = _annotation_color(result)

    if result.pixel_box is not None:
        x1, y1, x2, y2 = result.pixel_box.as_tuple()
        _draw_region_overlay(output, (x1, y1, x2, y2), color, stroke)

    draw = ImageDraw.Draw(output)

    if result.pixel_point is not None:
        x, y = result.pixel_point
    elif result.pixel_box is not None:
        x = (result.pixel_box.x1 + result.pixel_box.x2) // 2
        y = (result.pixel_box.y1 + result.pixel_box.y2) // 2
    else:
        return output

    radius = max(8, min(width, height) // 80)
    _draw_crosshair(draw, x, y, radius, color, stroke, output.size)

    label = result.label or "Region"
    if result.confidence is not None:
        label += f" {result.confidence:.2f}"
    _draw_label(draw, label, x + radius + 8, max(0, y - radius - 8), output.size, color)
    return output


def _annotation_color(result: LocalizationResult) -> tuple[int, int, int]:
    if result.mode == "computer_action" and (result.label or "").lower() == "pointer":
        return (0, 168, 255)
    if result.mode == "computer_action":
        return (255, 176, 0)
    return (255, 48, 48)


def _draw_region_overlay(
    image: Image.Image,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
    stroke: int,
) -> None:
    x1, y1, x2, y2 = box
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((x1, y1, x2, y2), fill=(*color, 34))
    composed = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    image.paste(composed)

    draw = ImageDraw.Draw(image)
    draw.rectangle((x1, y1, x2, y2), outline=color, width=stroke)
    _draw_corner_handles(draw, x1, y1, x2, y2, color, stroke)


def _draw_corner_handles(
    draw: ImageDraw.ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: tuple[int, int, int],
    stroke: int,
) -> None:
    box_width = max(1, x2 - x1)
    box_height = max(1, y2 - y1)
    length = max(12, min(box_width, box_height) // 5)
    length = min(length, max(1, box_width // 2), max(1, box_height // 2))
    width = stroke + 1
    draw.line((x1, y1, x1 + length, y1), fill=color, width=width)
    draw.line((x1, y1, x1, y1 + length), fill=color, width=width)
    draw.line((x2, y1, x2 - length, y1), fill=color, width=width)
    draw.line((x2, y1, x2, y1 + length), fill=color, width=width)
    draw.line((x1, y2, x1 + length, y2), fill=color, width=width)
    draw.line((x1, y2, x1, y2 - length), fill=color, width=width)
    draw.line((x2, y2, x2 - length, y2), fill=color, width=width)
    draw.line((x2, y2, x2, y2 - length), fill=color, width=width)


def _draw_crosshair(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    radius: int,
    color: tuple[int, int, int],
    stroke: int,
    image_size: tuple[int, int],
) -> None:
    image_width, image_height = image_size
    line_width = max(2, stroke - 1)
    x0 = max(0, x - radius * 2)
    x1 = min(image_width - 1, x + radius * 2)
    y0 = max(0, y - radius * 2)
    y1 = min(image_height - 1, y + radius * 2)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=stroke)
    draw.line((x0, y, x1, y), fill=color, width=line_width)
    draw.line((x, y0, x, y1), fill=color, width=line_width)


def _draw_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    image_size: tuple[int, int],
    color: tuple[int, int, int],
) -> None:
    font = ImageFont.load_default()
    bbox = draw.textbbox((x, y), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    image_width, image_height = image_size
    x = min(max(0, x), max(0, image_width - text_width - 10))
    y = min(max(0, y), max(0, image_height - text_height - 8))
    box = (x - 4, y - 3, x + text_width + 6, y + text_height + 5)
    draw.rectangle(box, fill=color)
    draw.text((x, y), text, fill=(255, 255, 255), font=font)


def format_record(record: ImageRecord) -> str:
    if record.error:
        return record.error
    if record.result is None:
        return "No result yet."
    return format_result(record.result)


def format_result(result: LocalizationResult) -> str:
    lines = [result.answer.strip() or "No answer returned."]
    if result.model_box is not None and result.pixel_box is not None:
        lines.append("")
        lines.append(f"Model box 0..999: {result.model_box.as_tuple()}")
        lines.append(f"Pixel box: {result.pixel_box.as_tuple()}")
    if result.model_point is not None and result.pixel_point is not None:
        lines.append(f"Model point 0..999: {result.model_point}")
        lines.append(f"Pixel point: {result.pixel_point}")
    if result.mode:
        lines.append(f"Mode: {result.mode}")
    return "\n".join(lines)


def main() -> None:
    if load_dotenv is not None:
        load_dotenv()
        load_dotenv(Path(__file__).with_name(".env"), override=False)

    root = tk.Tk()
    MedicalImageLocatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

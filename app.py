from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
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
    from .report_extractor import DEFAULT_OPENAI_MODEL, ReportTerm, build_localization_prompt, extract_terms_from_report
except ImportError:
    from model_client import DEFAULT_BASE_URL, DEFAULT_MODEL, LocalizationResult, localize_region
    from report_extractor import DEFAULT_OPENAI_MODEL, ReportTerm, build_localization_prompt, extract_terms_from_report


APP_TITLE = "Medical Report Image Locator"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".dcm"}


@dataclass
class ImageRecord:
    path: Path
    image: Image.Image
    annotated_image: Image.Image | None = None
    results: dict[str, LocalizationResult] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    error: str = ""
    status: str = "Loaded"

    @property
    def display_image(self) -> Image.Image:
        return self.annotated_image or self.image


class MedicalImageLocatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1500x960")
        self.root.minsize(1180, 800)

        self.records: list[ImageRecord] = []
        self.terms: list[ReportTerm] = []
        self.selected_index = -1
        self.tk_image: ImageTk.PhotoImage | None = None
        self.busy = False

        self.status_var = tk.StringVar(value="Load images to begin.")
        self.summary_var = tk.StringVar(value="No images loaded")
        self.term_summary_var = tk.StringVar(value="No terms extracted")
        self.report_provider_var = tk.StringVar(value="OpenAI GPT-5.5")
        self.openai_model_var = tk.StringVar(value=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
        self.openai_api_key_var = tk.StringVar()
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
        panel.rowconfigure(1, weight=1)
        panel.rowconfigure(4, weight=1)

        ttk.Label(panel, text="Report / Diagnosis").grid(row=0, column=0, sticky="w")
        report_frame = ttk.Frame(panel)
        report_frame.grid(row=1, column=0, sticky="nsew", pady=(3, 0))
        report_frame.columnconfigure(0, weight=1)
        report_frame.rowconfigure(0, weight=1)
        self.report_text = tk.Text(report_frame, height=8, wrap="word")
        self.report_text.grid(row=0, column=0, sticky="nsew")
        report_scroll = ttk.Scrollbar(report_frame, orient=tk.VERTICAL, command=self.report_text.yview)
        report_scroll.grid(row=0, column=1, sticky="ns")
        self.report_text.configure(yscrollcommand=report_scroll.set)

        provider_row = ttk.Frame(panel)
        provider_row.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        provider_row.columnconfigure(2, weight=1)
        ttk.Radiobutton(
            provider_row,
            text="OpenAI GPT-5.5",
            variable=self.report_provider_var,
            value="OpenAI GPT-5.5",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Radiobutton(
            provider_row,
            text="Lightcone",
            variable=self.report_provider_var,
            value="Lightcone",
        ).grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.extract_button = ttk.Button(provider_row, text="Extract Terms", command=self.extract_terms)
        self.extract_button.grid(row=0, column=2, sticky="e")

        ttk.Label(panel, text="Terms to Locate").grid(row=3, column=0, sticky="w", pady=(12, 2))
        term_frame = ttk.Frame(panel)
        term_frame.grid(row=4, column=0, sticky="nsew")
        term_frame.columnconfigure(0, weight=1)
        term_frame.rowconfigure(0, weight=1)
        self.term_tree = ttk.Treeview(
            term_frame,
            columns=("use", "found", "status"),
            show="tree headings",
            selectmode="browse",
            height=8,
        )
        self.term_tree.heading("#0", text="Term")
        self.term_tree.heading("use", text="Use")
        self.term_tree.heading("found", text="Found")
        self.term_tree.heading("status", text="Status")
        self.term_tree.column("#0", width=170, minwidth=120, stretch=True)
        self.term_tree.column("use", width=46, minwidth=42, stretch=False, anchor="center")
        self.term_tree.column("found", width=56, minwidth=50, stretch=False, anchor="center")
        self.term_tree.column("status", width=92, minwidth=76, stretch=False)
        self.term_tree.grid(row=0, column=0, sticky="nsew")
        self.term_tree.bind("<Double-1>", self._on_term_double_click)
        term_scroll = ttk.Scrollbar(term_frame, orient=tk.VERTICAL, command=self.term_tree.yview)
        term_scroll.grid(row=0, column=1, sticky="ns")
        self.term_tree.configure(yscrollcommand=term_scroll.set)

        term_actions = ttk.Frame(panel)
        term_actions.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        term_actions.columnconfigure((0, 1, 2), weight=1)
        self.locate_terms_button = ttk.Button(term_actions, text="Locate Selected", command=self.locate_selected_terms)
        self.locate_terms_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.select_terms_button = ttk.Button(term_actions, text="Select All", command=self.select_all_terms)
        self.select_terms_button.grid(row=0, column=1, sticky="ew", padx=(4, 4))
        self.clear_terms_button = ttk.Button(term_actions, text="Clear Terms", command=self.clear_terms)
        self.clear_terms_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        ttk.Label(panel, textvariable=self.term_summary_var, wraplength=390).grid(row=6, column=0, sticky="ew", pady=(8, 0))

        output_row = ttk.Frame(panel)
        output_row.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        output_row.columnconfigure((0, 1), weight=1)
        self.save_button = ttk.Button(output_row, text="Save Current", command=self.save_current, state="disabled")
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.save_all_button = ttk.Button(output_row, text="Save All", command=self.save_all, state="disabled")
        self.save_all_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        clear_row = ttk.Frame(panel)
        clear_row.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        clear_row.columnconfigure((0, 1), weight=1)
        self.clear_button = ttk.Button(clear_row, text="Clear Current", command=self.clear_current_overlay, state="disabled")
        self.clear_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.clear_all_button = ttk.Button(clear_row, text="Clear All", command=self.clear_all_overlays, state="disabled")
        self.clear_all_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ttk.Separator(panel).grid(row=9, column=0, sticky="ew", pady=12)

        ttk.Label(panel, text="Lightcone Localization Mode").grid(row=10, column=0, sticky="w")
        mode = ttk.Combobox(
            panel,
            textvariable=self.mode_var,
            values=("box_tool", "computer_action"),
            state="readonly",
        )
        mode.grid(row=11, column=0, sticky="ew", pady=(3, 0))

        ttk.Label(panel, text="Lightcone Model").grid(row=12, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(panel, textvariable=self.model_var).grid(row=13, column=0, sticky="ew")

        ttk.Label(panel, text="Lightcone Base URL").grid(row=14, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(panel, textvariable=self.base_url_var).grid(row=15, column=0, sticky="ew")

        ttk.Label(panel, text="Lightcone API key override").grid(row=16, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(panel, textvariable=self.api_key_var, show="*").grid(row=17, column=0, sticky="ew")

        ttk.Separator(panel).grid(row=18, column=0, sticky="ew", pady=12)

        ttk.Label(panel, text="OpenAI Extraction Model").grid(row=19, column=0, sticky="w")
        ttk.Entry(panel, textvariable=self.openai_model_var).grid(row=20, column=0, sticky="ew", pady=(3, 0))

        ttk.Label(panel, text="OpenAI API key override").grid(row=21, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(panel, textvariable=self.openai_api_key_var, show="*").grid(row=22, column=0, sticky="ew")

        ttk.Separator(panel).grid(row=23, column=0, sticky="ew", pady=12)

        ttk.Label(panel, text="Results").grid(row=24, column=0, sticky="w")
        self.answer = tk.Text(panel, height=11, wrap="word", state="disabled")
        self.answer.grid(row=25, column=0, sticky="nsew")
        panel.rowconfigure(25, weight=1)

        ttk.Label(panel, textvariable=self.status_var, wraplength=390).grid(row=26, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            panel,
            text="Visual localization only. Do not use this for clinical diagnosis.",
            wraplength=390,
            foreground="#8a5a00",
        ).grid(row=27, column=0, sticky="ew", pady=(8, 0))
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
        self._refresh_terms_list()
        self._update_current_view()
        self.status_var.set("Study cleared.")

    def extract_terms(self) -> None:
        report = self._report_text()
        if not report:
            messagebox.showinfo("No report", "Paste a report or diagnosis first.")
            return

        provider = self._selected_report_provider()
        self._set_busy(True)
        self.status_var.set(f"Extracting report terms with {provider}...")
        self._set_answer("")

        thread = threading.Thread(
            target=self._extract_terms_worker,
            args=(
                report,
                provider,
                self.openai_api_key_var.get().strip() or None,
                self.openai_model_var.get().strip(),
                self.api_key_var.get().strip() or None,
                self.base_url_var.get().strip(),
                self.model_var.get().strip(),
            ),
            daemon=True,
        )
        thread.start()

    def _extract_terms_worker(
        self,
        report: str,
        provider: str,
        openai_api_key: str | None,
        openai_model: str,
        tzafon_api_key: str | None,
        tzafon_base_url: str,
        tzafon_model: str,
    ) -> None:
        try:
            result = extract_terms_from_report(
                report,
                provider=provider,  # type: ignore[arg-type]
                openai_api_key=openai_api_key,
                openai_model=openai_model,
                tzafon_api_key=tzafon_api_key,
                tzafon_base_url=tzafon_base_url,
                tzafon_model=tzafon_model,
            )
        except Exception as exc:
            self.root.after(0, self._handle_extract_error, exc)
            return
        self.root.after(0, self._handle_terms_extracted, result.terms, result.provider)

    def _handle_extract_error(self, exc: Exception) -> None:
        self.status_var.set("Term extraction failed.")
        self._set_answer(str(exc))
        self._set_busy(False)

    def _handle_terms_extracted(self, terms: list[ReportTerm], provider: str) -> None:
        self.terms = terms
        for record in self.records:
            record.results.clear()
            record.errors.clear()
            record.annotated_image = None
            record.error = ""
            record.status = "Loaded"
        self._refresh_terms_list()
        self._refresh_image_list()
        self._update_current_view()
        if terms:
            self.status_var.set(f"Extracted {len(terms)} term{'s' if len(terms) != 1 else ''} with {provider}.")
            self._set_answer(format_terms(terms))
        else:
            self.status_var.set("No localizable report terms were extracted.")
            self._set_answer("No localizable terms found.")
        self._set_busy(False)

    def locate_selected_terms(self) -> None:
        if not self.records:
            messagebox.showinfo("No images", "Load images first.")
            return

        terms = [term for term in self.terms if term.enabled]
        if not terms:
            messagebox.showinfo("No terms", "Extract or select at least one term first.")
            return

        self._set_busy(True)
        for record in self.records:
            record.results.clear()
            record.errors.clear()
            record.annotated_image = None
            record.error = ""
            record.status = "Queued"
        self._refresh_image_list()
        self._refresh_terms_list()
        self.status_var.set(f"Queued {len(terms)} term{'s' if len(terms) != 1 else ''} across {len(self.records)} images.")

        thread = threading.Thread(
            target=self._locate_terms_worker,
            args=(
                [ReportTerm(**term.__dict__) for term in terms],
                self.mode_var.get(),
                self.api_key_var.get().strip() or None,
                self.base_url_var.get().strip(),
                self.model_var.get().strip(),
            ),
            daemon=True,
        )
        thread.start()

    def _locate_terms_worker(
        self,
        terms: list[ReportTerm],
        mode: str,
        api_key: str | None,
        base_url: str,
        model: str,
    ) -> None:
        total = len(terms) * len(self.records)
        completed = 0
        for term in terms:
            prompt = build_localization_prompt(term)
            for index, record in enumerate(list(self.records)):
                completed += 1
                self.root.after(0, self._mark_term_running, index, term.name, completed, total)
                try:
                    result = localize_region(
                        record.image.copy(),
                        prompt,
                        mode=mode,  # type: ignore[arg-type]
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                    )
                except Exception as exc:
                    self.root.after(0, self._handle_term_error, index, term.name, exc)
                    continue

                if result.pixel_box is not None:
                    result.label = term.name
                self.root.after(0, self._handle_term_result, index, term.name, result)

        self.root.after(0, self._finish_term_batch)

    def _mark_term_running(self, index: int, term_name: str, completed: int, total: int) -> None:
        if 0 <= index < len(self.records):
            self.records[index].status = "Running"
            self._refresh_image_list()
            self._refresh_terms_list()
            self.status_var.set(f"Running {completed}/{total}: {term_name} on {self.records[index].path.name}")

    def _handle_term_result(self, index: int, term_name: str, result: LocalizationResult) -> None:
        if not 0 <= index < len(self.records):
            return

        record = self.records[index]
        record.results[term_name] = result
        record.errors.pop(term_name, None)
        record.error = ""
        self._refresh_record_annotation(record)
        self._set_record_status(record)
        self._refresh_image_list()
        self._refresh_terms_list()
        if index == self.selected_index:
            self._update_current_view()
        self._update_action_states()

    def _handle_term_error(self, index: int, term_name: str, exc: Exception) -> None:
        if not 0 <= index < len(self.records):
            return

        record = self.records[index]
        record.errors[term_name] = str(exc)
        record.error = ""
        self._set_record_status(record)
        self._refresh_image_list()
        self._refresh_terms_list()
        if index == self.selected_index:
            self._update_current_view()
        self._update_action_states()

    def _finish_term_batch(self) -> None:
        marked = sum(1 for record in self.records for result in record.results.values() if result.pixel_box is not None)
        missing = sum(1 for record in self.records for result in record.results.values() if result.pixel_box is None)
        errors = sum(len(record.errors) for record in self.records)
        self.status_var.set(f"Finished. Marked {marked}; no region {missing}; errors {errors}.")
        self._set_answer(format_study_results(self.records, self.terms))
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
        record.results.clear()
        record.errors.clear()
        record.error = ""
        record.status = "Loaded"
        self._refresh_image_list()
        self._refresh_terms_list()
        self._update_current_view()
        self.status_var.set(f"Cleared overlay for {record.path.name}.")

    def clear_all_overlays(self) -> None:
        for record in self.records:
            record.annotated_image = None
            record.results.clear()
            record.errors.clear()
            record.error = ""
            record.status = "Loaded"
        self._refresh_image_list()
        self._refresh_terms_list()
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

    def _on_term_double_click(self, _event: tk.Event) -> None:
        selection = self.term_tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if 0 <= index < len(self.terms):
            self.terms[index].enabled = not self.terms[index].enabled
            self._refresh_terms_list()

    def select_all_terms(self) -> None:
        for term in self.terms:
            term.enabled = True
        self._refresh_terms_list()

    def clear_terms(self) -> None:
        if self.busy:
            return
        self.terms.clear()
        for record in self.records:
            record.results.clear()
            record.errors.clear()
            record.annotated_image = None
            record.error = ""
            record.status = "Loaded"
        self._refresh_terms_list()
        self._refresh_image_list()
        self._update_current_view()
        self.status_var.set("Cleared terms and overlays.")

    def _report_text(self) -> str:
        return self.report_text.get("1.0", tk.END).strip()

    def _selected_report_provider(self) -> str:
        provider = self.report_provider_var.get().strip().lower()
        return "lightcone" if provider.startswith("lightcone") else "openai"

    def _refresh_terms_list(self) -> None:
        selected = self.term_tree.selection()
        self.term_tree.delete(*self.term_tree.get_children())
        for index, term in enumerate(self.terms):
            found = self._term_found_count(term.name)
            status = self._term_status(term.name)
            self.term_tree.insert(
                "",
                "end",
                iid=str(index),
                text=term.name,
                values=("yes" if term.enabled else "no", str(found), status),
            )
        if selected:
            iid = selected[0]
            if self.term_tree.exists(iid):
                self.term_tree.selection_set(iid)
        self._update_term_summary()

    def _term_found_count(self, term_name: str) -> int:
        return sum(
            1
            for record in self.records
            if term_name in record.results and record.results[term_name].pixel_box is not None
        )

    def _term_status(self, term_name: str) -> str:
        errors = sum(1 for record in self.records if term_name in record.errors)
        attempted = sum(1 for record in self.records if term_name in record.results or term_name in record.errors)
        found = self._term_found_count(term_name)
        if errors:
            return f"{errors} error"
        if found:
            return "Marked"
        if attempted:
            return "No region"
        return "Ready"

    def _update_term_summary(self) -> None:
        total = len(self.terms)
        selected = sum(1 for term in self.terms if term.enabled)
        marked = sum(1 for record in self.records for result in record.results.values() if result.pixel_box is not None)
        if not total:
            self.term_summary_var.set("No terms extracted")
            return
        self.term_summary_var.set(f"{selected}/{total} terms selected | {marked} marked regions")

    def _refresh_record_annotation(self, record: ImageRecord) -> None:
        marked_results = [result for result in record.results.values() if result.pixel_box is not None]
        record.annotated_image = draw_annotations(record.image, marked_results) if marked_results else None

    def _set_record_status(self, record: ImageRecord) -> None:
        marked = sum(1 for result in record.results.values() if result.pixel_box is not None)
        attempted = len(record.results) + len(record.errors)
        if record.errors and not marked:
            record.status = "Error"
        elif record.errors:
            record.status = f"Marked {marked} + errors"
        elif marked:
            record.status = f"Marked {marked}"
        elif attempted:
            record.status = "No region"
        else:
            record.status = "Loaded"

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
        marked = sum(1 for record in self.records for result in record.results.values() if result.pixel_box is not None)
        errors = sum(len(record.errors) for record in self.records)
        if not total:
            self.summary_var.set("No images loaded")
            return
        self.summary_var.set(f"{total} images | {marked} marked regions | {errors} errors")

    def _update_action_states(self) -> None:
        current = self._current_record()
        has_images = bool(self.records)
        has_annotated = any(record.annotated_image is not None for record in self.records)
        current_annotated = current is not None and current.annotated_image is not None

        normal_if_ready = "disabled" if self.busy else "normal"
        self.load_button.configure(state=normal_if_ready)
        self.folder_button.configure(state=normal_if_ready)
        self.clear_study_button.configure(state=normal_if_ready)
        self.extract_button.configure(state=normal_if_ready)
        self.locate_terms_button.configure(state=normal_if_ready if has_images and any(term.enabled for term in self.terms) else "disabled")
        self.select_terms_button.configure(state=normal_if_ready if self.terms else "disabled")
        self.clear_terms_button.configure(state=normal_if_ready if self.terms else "disabled")
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
        self._refresh_terms_list()
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


ANNOTATION_COLORS = (
    (255, 48, 48),
    (0, 168, 255),
    (255, 176, 0),
    (33, 186, 115),
    (180, 95, 255),
    (255, 95, 160),
    (0, 190, 190),
)


def draw_annotation(image: Image.Image, result: LocalizationResult) -> Image.Image:
    return draw_annotations(image, [result])


def draw_annotations(image: Image.Image, results: list[LocalizationResult]) -> Image.Image:
    output = image.convert("RGB").copy()
    width, height = output.size
    stroke = max(3, min(width, height) // 180)
    for index, result in enumerate(results):
        color = _annotation_color(result, index)

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
            continue

        radius = max(8, min(width, height) // 80)
        _draw_crosshair(draw, x, y, radius, color, stroke, output.size)

        label = result.label or "Region"
        if result.confidence is not None:
            label += f" {result.confidence:.2f}"
        label_y = max(0, y - radius - 8 + index * (radius + 8))
        _draw_label(draw, label, x + radius + 8, label_y, output.size, color)
    return output


def _annotation_color(result: LocalizationResult, index: int = 0) -> tuple[int, int, int]:
    if result.mode == "computer_action" and (result.label or "").lower() == "pointer":
        return (0, 168, 255)
    if result.label:
        return ANNOTATION_COLORS[index % len(ANNOTATION_COLORS)]
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
    if not record.results and not record.errors:
        return "No localization results yet."

    lines: list[str] = []
    for term_name, result in record.results.items():
        status = "Marked" if result.pixel_box is not None else "No region"
        lines.append(f"{term_name}: {status}")
        answer = result.answer.strip()
        if answer:
            lines.append(answer)
        if result.model_box is not None and result.pixel_box is not None:
            lines.append(f"Model box 0..999: {result.model_box.as_tuple()}")
            lines.append(f"Pixel box: {result.pixel_box.as_tuple()}")
        if result.model_point is not None and result.pixel_point is not None:
            lines.append(f"Model point 0..999: {result.model_point}")
            lines.append(f"Pixel point: {result.pixel_point}")
        lines.append("")

    for term_name, error in record.errors.items():
        lines.append(f"{term_name}: Error")
        lines.append(error)
        lines.append("")

    return "\n".join(lines).strip()


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


def format_terms(terms: list[ReportTerm]) -> str:
    if not terms:
        return "No localizable terms found."
    lines = ["Extracted terms:"]
    for index, term in enumerate(terms, start=1):
        lines.append(f"{index}. {term.name}")
        if term.aliases:
            lines.append("   Aliases: " + ", ".join(term.aliases[:8]))
        if term.context:
            lines.append("   Context: " + term.context)
    return "\n".join(lines)


def format_study_results(records: list[ImageRecord], terms: list[ReportTerm]) -> str:
    if not terms:
        return "No terms extracted."

    lines = ["Found by term:"]
    for term in terms:
        found = [record.path.name for record in records if record.results.get(term.name) and record.results[term.name].pixel_box is not None]
        errors = [record.path.name for record in records if term.name in record.errors]
        if found:
            lines.append(f"- {term.name}: " + ", ".join(found))
        elif errors:
            lines.append(f"- {term.name}: errors on " + ", ".join(errors))
        else:
            lines.append(f"- {term.name}: no region found")
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

# Medical Report Image Locator

Python desktop app for loading a folder of medical images, extracting localizable terms from a pasted report, and drawing Lightcone-returned boxes or pointers on every matching image.

The app has two separate model stages:

1. Report term extraction: OpenAI `gpt-5.5` or the current Lightcone/Tzafon model.
2. Image localization and drawing: Lightcone/Tzafon only.

If a term is not visible in an image, the image is left unmarked and the result is recorded as `No region`.

## Setup

```bash
cd /Users/sai/Documents/hackthon/cua_tzafon/docs/medical_image_locator_report_identify
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set:

```bash
OPENAI_API_KEY=...
TZAFON_API_KEY=...
```

`OPENAI_API_KEY` can also be read from `~/.bashrc`, `~/.bash_profile`, `~/.profile`, or `~/.zshrc` when it is exported there.

## Run

```bash
python app.py
```

Or from `/Users/sai/Documents/hackthon/cua_tzafon/docs`:

```bash
python -m medical_image_locator_report_identify
```

## Workflow

1. Click **Load Folder** and select the image folder.
2. Paste the report or diagnosis text into **Report / Diagnosis**.
3. Choose the extraction provider:
   - `openai` uses `OPENAI_MODEL`, default `gpt-5.5`.
   - `lightcone` uses `TZAFON_MODEL`, default `tzafon.northstar-cua-fast`.
4. Click **Extract Terms**.
5. Double-click any term to toggle whether it should be used.
6. Click **Locate Selected**.
7. Save the current annotated image or all annotated images.

## Coordinates

Lightcone/Northstar returns coordinates in a fixed `0..999` grid. This app follows that rule:

```python
pixel_x = int(model_x / 1000 * image_width)
pixel_y = int(model_y / 1000 * image_height)
```

For a rectangle, the model returns two corners: `(x1, y1)` and `(x2, y2)`. The app scales both corners to pixels and draws the overlay locally.

## Notes

This is a visual localization prototype, not a diagnostic medical device. It should not be used for clinical diagnosis or treatment decisions.

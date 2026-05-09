# X-ray / MRI Region Locator

Basic Python app for loading one or many medical images, asking a question, and drawing model-returned boxes or pointers on each image.

The Lightcone coordinate docs say Northstar returns coordinates in a fixed `0..999` grid. This app follows that rule:

```python
pixel_x = int(model_x / 1000 * image_width)
pixel_y = int(model_y / 1000 * image_height)
```

For a rectangle, the model returns two corners: `(x1, y1)` and `(x2, y2)`. The app scales both corners to pixels and draws the overlay locally.

## Setup

```bash
cd /Users/sai/Documents/hackthon/cua_tzafon/docs/medical_image_locator
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `TZAFON_API_KEY`.

## Run

```bash
python app.py
```

Or from `/Users/sai/Documents/hackthon/cua_tzafon/docs`:

```bash
python -m medical_image_locator
```

Load PNG/JPG/TIFF/DICOM images with **Load Images**, or load a whole directory with **Load Folder**. Select an image from the left-side study list, type a question such as:

```text
Where is the left lung?
```

Then click:

- **Ask Current** to localize the selected image.
- **Ask All** to run the same question across every loaded image.
- **Save Current** or **Save All** to export annotated PNGs.

Each image keeps its own result, answer text, overlay, and status.

## Modes

- `box_tool`: Uses an OpenAI-compatible chat tool named `mark_region` to ask for a two-corner box in the `0..999` coordinate grid. This is the default and best mode for drawing boxes.
- `computer_action`: Uses the documented `computer_use` action space. The prompt asks for a `drag` action for a box, or a `click` action for a pointer, then the app draws the returned action locally.

## Notes

This is a visual localization prototype, not a diagnostic medical device. It should not be used for clinical diagnosis or treatment decisions.

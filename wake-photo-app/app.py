import hashlib
import hmac
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
import reportlab
import streamlit as st
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Pt
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from supabase import create_client

register_heif_opener()
st.set_page_config(page_title="In Loving Memory Of John Francis Lismore", layout="wide")

MAX_FILE_MB = 25
DRAFT_VERSION = 7
CURRENT_YEAR = datetime.now().year
DB_BATCH_SIZE = 100
READ_PAGE_SIZE = 1000
IMAGE_TYPES = ["jpg", "jpeg", "png", "webp", "heic", "heif"]
LOGGER = logging.getLogger(__name__)
SLIDE_W, SLIDE_H = 960, 540
BACKGROUND = "FAF7F2"
TEXT_COLOUR = "33302B"
CAPTION_SIZE = 18
CAPTION_LINES = 5
CAPTION_WIDTH = 860
EXPORT_VERSION = 1


@st.cache_resource
def get_public_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"])


def get_service_client():
    # Never return this client or its credentials to a browser.
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_ROLE_KEY"])


def new_draft(name=""):
    return {
        "version": DRAFT_VERSION, "id": str(uuid.uuid4()), "name": name,
        "photos": [], "complete": False, "upload_started": False,
        "uploaded": {}, "parent_saved": False, "photos_saved": False,
        "saved_photo_ids": [],
    }


def get_draft():
    draft = st.session_state.get("photo_draft")
    if not draft or draft.get("version") != DRAFT_VERSION:
        st.session_state["photo_draft"] = new_draft()
    st.session_state.setdefault("upload_generation", 0)
    return st.session_state["photo_draft"]


def save_name():
    get_draft()["name"] = st.session_state.get("draft_name", "")


def save_photo_field(photo_id, field, widget_key):
    for photo in get_draft()["photos"]:
        if photo["id"] == photo_id:
            photo[field] = st.session_state.get(widget_key, "")
            return


def delete_photo(photo_id):
    draft = get_draft()
    draft["photos"] = [photo for photo in draft["photos"] if photo["id"] != photo_id]


def start_again():
    name = get_draft()["name"]
    st.session_state["photo_draft"] = new_draft(name)
    st.session_state["upload_generation"] += 1
    st.session_state.pop("upload_messages", None)


def safe_filename(filename):
    return re.sub(r"[^a-zA-Z0-9._-]", "_", os.path.basename(filename)).lower() or "photo.jpg"


def rgb_image(image):
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def preview_photo(photo):
    with Image.open(io.BytesIO(photo["bytes"])) as source:
        image = rgb_image(source)
        image.thumbnail((1000, 1000))
        return image


def prepare_photo(photo):
    with Image.open(io.BytesIO(photo["bytes"])) as source:
        image = rgb_image(source)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=93, optimize=True)
    name = os.path.splitext(safe_filename(photo["filename"]))[0] + ".jpg"
    return output.getvalue(), name


def valid_year(value):
    text = str(value).strip()
    return bool(re.fullmatch(r"[0-9]{4}", text)) and 1800 <= int(text) <= CURRENT_YEAR


def metadata_photo_year(photo):
    """Only DateTimeOriginal; never substitute digitisation or file dates."""
    try:
        with Image.open(io.BytesIO(photo["bytes"])) as image:
            exif = image.getexif()
            if not exif:
                return None
            try:
                details = exif.get_ifd(0x8769)
            except Exception:
                details = {}
            value = details.get(0x9003) or exif.get(0x9003)
        if not value:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        date = datetime.strptime(str(value).replace("\x00", "").strip(), "%Y:%m:%d %H:%M:%S")
        return date.year if valid_year(date.year) else None
    except Exception:
        return None


def detected_photo_year(photo):
    if "metadata_year" not in photo:
        photo["metadata_year"] = metadata_photo_year(photo)
    return photo["metadata_year"]


def resolved_photo_year(photo):
    entered = str(photo.get("year", "")).strip()
    if entered:
        return int(entered) if valid_year(entered) else None
    return detected_photo_year(photo)


def ingest_uploads(files):
    draft = get_draft()
    existing = {photo["digest"] for photo in draft["photos"]}
    messages = []
    for uploaded in files:
        data = uploaded.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if digest in existing:
            messages.append(f"{uploaded.name}: this photograph is already added.")
            continue
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            messages.append(f"{uploaded.name}: please choose a file under {MAX_FILE_MB} MB.")
            continue
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        except Exception:
            messages.append(f"{uploaded.name}: this photograph could not be opened.")
            continue
        photo = {
            "id": str(uuid.uuid4()), "digest": digest, "filename": uploaded.name,
            "bytes": data, "caption": "", "year": "",
        }
        photo["metadata_year"] = metadata_photo_year(photo)
        draft["photos"].append(photo)
        existing.add(digest)
    st.session_state["upload_messages"] = messages
    st.session_state["upload_generation"] += 1


def validation_errors():
    draft = get_draft()
    errors = []
    if not draft["name"].strip():
        errors.append("Please enter your name under Submitted By.")
    if not draft["photos"]:
        errors.append("Please add at least one photograph.")
    for index, photo in enumerate(draft["photos"], start=1):
        year = str(photo.get("year", "")).strip()
        if year and not valid_year(year):
            errors.append(
                f"Photograph {index}: enter a four-digit year between 1800 and "
                f"{CURRENT_YEAR}, or leave the year blank."
            )
    return errors


def render_header():
    st.markdown(
        """<div style="text-align:center;padding:1.6rem 1rem;background:#F1ECE4;
        color:#33302B;border-radius:16px;margin-bottom:1.5rem;">
        <h1 style="font-size:2rem;margin:0 0 .5rem;">In Loving Memory Of John Francis Lismore</h1>
        <p style="margin:0;">Sharing photographs and memories</p></div>""",
        unsafe_allow_html=True,
    )


def render_photo_card(photo, position, total):
    with st.container(border=True):
        image_column, details_column = st.columns([1, 1.35])
        with image_column:
            st.image(preview_photo(photo), width="stretch")
        with details_column:
            st.markdown(f"### Photograph {position + 1} of {total}")
            caption_key = f"edit_caption_{photo['id']}"
            year_key = f"edit_year_{photo['id']}"
            st.session_state[caption_key] = photo["caption"]
            st.session_state[year_key] = photo.get("year", "")
            st.text_area(
                "Caption", key=caption_key, on_change=save_photo_field,
                args=(photo["id"], "caption", caption_key), height=110,
                placeholder="For example: John with family at Emma’s wedding",
            )
            st.text_input(
                "Year (optional)", key=year_key, on_change=save_photo_field,
                args=(photo["id"], "year", year_key), max_chars=4,
                placeholder="For example: 1978",
                help="An entered year takes priority over the original metadata year.",
            )
            entered = str(photo.get("year", "")).strip()
            if entered and not valid_year(entered):
                st.warning(f"Enter a four-digit year between 1800 and {CURRENT_YEAR}, or leave it blank.")
            elif not entered:
                year = detected_photo_year(photo)
                if year:
                    st.caption(
                        f"Metadata year: {year}. This will be used unless you enter a year. "
                        "For scans or photographs of old prints, please check the original year."
                    )
                else:
                    st.caption("No original capture year found. You can enter a year or leave it unknown.")
            st.button("Delete", key=f"delete_{photo['id']}", on_click=delete_photo, args=(photo["id"],))


def record_for_photo(photo, position, draft):
    return {
        "id": photo["id"], "submission_id": draft["id"],
        "storage_path": draft["uploaded"][photo["id"]],
        "original_filename": photo["filename"], "photo_order": position,
        "caption": photo["caption"].strip() or None,
        "photo_year": resolved_photo_year(photo),
    }


def submit_photos():
    draft = get_draft()
    errors = validation_errors()
    if errors:
        for message in errors:
            st.error(message)
        return
    if "submission_snapshot" not in draft:
        draft["submission_snapshot"] = {
            "name": draft["name"].strip(), "photos": [dict(photo) for photo in draft["photos"]],
        }
    snapshot = draft["submission_snapshot"]
    photos = snapshot["photos"]
    progress = st.progress(0, text="Preparing your photographs…")
    try:
        client = get_public_client()
        bucket = client.storage.from_(st.secrets["SUPABASE_BUCKET"])
        draft["upload_started"] = True
        for index, photo in enumerate(photos):
            if photo["id"] not in draft["uploaded"]:
                data, filename = prepare_photo(photo)
                path = f"pending/{draft['id']}/{photo['id']}-{filename}"
                progress.progress(
                    int(index / len(photos) * 80),
                    text=f"Uploading photograph {index + 1} of {len(photos)}…",
                )
                bucket.upload(path=path, file=data, file_options={
                    "content-type": "image/jpeg", "cache-control": "3600", "upsert": "false",
                })
                draft["uploaded"][photo["id"]] = path
        progress.progress(80, text="Saving photograph details…")
        if not draft["parent_saved"]:
            client.table("submissions").insert({
                "id": draft["id"], "submitter_name": snapshot["name"], "status": "pending",
            }, returning="minimal").execute()
            draft["parent_saved"] = True
        if not draft["photos_saved"]:
            saved_ids = set(draft["saved_photo_ids"])
            pending = [record_for_photo(photo, index, draft)
                       for index, photo in enumerate(photos, start=1) if photo["id"] not in saved_ids]
            for start in range(0, len(pending), DB_BATCH_SIZE):
                batch = pending[start:start + DB_BATCH_SIZE]
                client.table("submission_photos").insert(batch, returning="minimal").execute()
                draft["saved_photo_ids"].extend(row["id"] for row in batch)
                progress.progress(
                    min(99, 80 + int(len(draft["saved_photo_ids"]) / len(photos) * 19)),
                    text=f"Saving photograph details: {len(draft['saved_photo_ids'])} of {len(photos)}…",
                )
            draft["photos_saved"] = True
        progress.progress(100, text="Your photographs have been received.")
        draft["complete"] = True
    except Exception:
        LOGGER.exception("Photo submission failed: %s", draft["id"])
        progress.empty()
        st.error("We couldn’t finish saving your photographs. Please try again.")
        st.caption(f"Reference: {draft['id']}")
        if st.button("Try again", key="retry_after_failure", type="primary"):
            st.rerun()
        return
    st.rerun()


def render_submission_form():
    draft = get_draft()
    st.subheader("Your photographs")
    st.write(
        "Choose the photographs you would like to share. You can select multiple "
        "photographs at once or add more after your first selection."
    )
    st.write(
        "Add a caption and, if you know it, the year beside each photograph. If you leave "
        "the year blank, we’ll use its original metadata year when available."
    )
    uploaded = st.file_uploader(
        "Choose photographs", type=IMAGE_TYPES, accept_multiple_files=True,
        key=f"uploader_{st.session_state['upload_generation']}",
        help=(f"No set photograph-count limit. Each photograph must be under {MAX_FILE_MB} MB. "
              "Very large collections may need to be shared in smaller groups."),
    )
    if uploaded:
        ingest_uploads(uploaded)
        st.rerun()
    for message in st.session_state.pop("upload_messages", []):
        st.warning(message)
    photos = draft["photos"]
    if photos:
        st.caption(f"{len(photos)} photograph{'s' if len(photos) != 1 else ''} added.")
        for position, photo in enumerate(photos):
            render_photo_card(photo, position, len(photos))
    st.divider()
    st.subheader("Submitted By")
    st.session_state["draft_name"] = draft["name"]
    st.text_input("Your name", key="draft_name", on_change=save_name, placeholder="Enter your name")
    if st.button("Submit photographs", type="primary", disabled=not photos, width="stretch"):
        submit_photos()


def render_public_upload():
    render_header()
    draft = get_draft()
    if draft["complete"]:
        st.success("Thank you — your photographs have been received.")
        st.write("Thank you for helping us create this collection of photographs and memories of John.")
        st.button("Share more photographs", type="primary", on_click=start_again, width="stretch")
    elif draft["upload_started"]:
        st.subheader("Finish sharing your photographs")
        st.write("Select ‘Try again’ to finish saving your photographs.")
        if st.button("Try again", type="primary", width="stretch"):
            submit_photos()
    else:
        render_submission_form()


def curator_login():
    if st.session_state.get("curator_authenticated"):
        if st.sidebar.button("Sign out"):
            st.session_state["curator_authenticated"] = False
            st.session_state.pop("prepared_export", None)
            st.rerun()
        return True
    st.title("Curator dashboard")
    with st.form("curator_login"):
        password = st.text_input("Curator password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        expected = str(st.secrets["CURATOR_PASSWORD"])
        if hmac.compare_digest(password.encode(), expected.encode()):
            st.session_state["curator_authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


def read_all_rows(client, table, order_columns):
    rows = []
    offset = 0
    while True:
        query = client.table(table).select("*")
        for column, descending in order_columns:
            query = query.order(column, desc=descending)
        page = query.range(offset, offset + READ_PAGE_SIZE - 1).execute().data or []
        if not page:
            return rows
        rows.extend(page)
        offset += len(page)


def load_curator_data(client):
    submissions = read_all_rows(client, "submissions", [("created_at", True), ("id", False)])
    photos = read_all_rows(client, "submission_photos", [("photo_order", False), ("id", False)])
    grouped = {}
    for photo in photos:
        grouped.setdefault(photo["submission_id"], []).append(photo)
    for submission in submissions:
        submission["photos"] = grouped.get(submission["id"], [])
    return submissions


def photo_year(photo, submission):
    # A present NULL is deliberately unknown; only fall back for legacy rows without this column.
    value = photo.get("photo_year") if "photo_year" in photo else submission.get("photo_year")
    return int(value) if value not in (None, "") else None


def submission_years(submission):
    return sorted({photo_year(photo, submission) for photo in submission.get("photos", [])
                   if photo_year(photo, submission) is not None})


def signed_image_url(client, photo):
    response = client.storage.from_(st.secrets["SUPABASE_BUCKET"]).create_signed_url(photo["storage_path"], 3600)
    return response.get("signedURL") or response.get("signedUrl")


def show_stored_image(client, photo):
    try:
        url = signed_image_url(client, photo)
        if url:
            st.image(url, width="stretch")
        else:
            st.warning("Preview unavailable.")
    except Exception:
        st.warning("Preview unavailable.")


def cycle_gallery_photo(submission_id, direction, photo_count):
    if photo_count < 1:
        return
    positions = dict(st.session_state.get("gallery_photo_positions", {}))
    positions[submission_id] = (positions.get(submission_id, 0) + direction) % photo_count
    st.session_state["gallery_photo_positions"] = positions


def render_square_preview(client, photo):
    try:
        url = signed_image_url(client, photo)
    except Exception:
        url = None
    content = (f'<img src="{escape(url, quote=True)}" alt="Photograph from a shared memory" '
               'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;">') if url else "Preview unavailable"
    st.markdown(
        '<div style="width:100%;aspect-ratio:1 / 1;overflow:hidden;border-radius:12px;'
        'background:#F1ECE4;position:relative;display:flex;align-items:center;justify-content:center;">'
        + content + '</div>', unsafe_allow_html=True,
    )


def render_gallery(client, submissions):
    approved = [item for item in submissions if item.get("status") == "approved" and item.get("photos")]
    approved.sort(key=lambda item: min(submission_years(item), default=9999))
    selected = st.session_state.get("selected_gallery_submission")
    detail = next((item for item in approved if item["id"] == selected), None)
    if detail:
        if st.button("Back to gallery"):
            st.session_state["selected_gallery_submission"] = None
            st.rerun()
        st.subheader(f"Photographs shared by {detail['submitter_name']}")
        for photo in detail["photos"]:
            with st.container(border=True):
                show_stored_image(client, photo)
                year = photo_year(photo, detail)
                if year is not None:
                    st.caption(f"Year: {year}")
                st.write(photo.get("caption") or "No caption added.")
        return
    st.subheader("Photo gallery")
    if not approved:
        st.info("No approved photographs are available yet.")
        return
    st.caption(f"{len(approved)} memories. Use the arrows to browse photographs in each memory.")
    positions = st.session_state.setdefault("gallery_photo_positions", {})
    for start in range(0, len(approved), 3):
        columns = st.columns(3, gap="small")
        for column, submission in zip(columns, approved[start:start + 3]):
            submission_id = submission["id"]
            photos = submission["photos"]
            count = len(photos)
            index = positions.get(submission_id, 0) % count
            current = photos[index]
            with column:
                with st.container(border=True):
                    render_square_preview(client, current)
                    previous, counter, following = st.columns([1, 2, 1], vertical_alignment="center")
                    previous.button(
                        "←", key=f"gallery_previous_{submission_id}", help="Previous photograph",
                        on_click=cycle_gallery_photo, args=(submission_id, -1, count),
                        disabled=count == 1, width="stretch",
                    )
                    with counter:
                        st.markdown(f"<div style='text-align:center;'>{index + 1} of {count}</div>", unsafe_allow_html=True)
                    following.button(
                        "→", key=f"gallery_next_{submission_id}", help="Next photograph",
                        on_click=cycle_gallery_photo, args=(submission_id, 1, count),
                        disabled=count == 1, width="stretch",
                    )
                    st.write(current.get("caption") or "No caption added.")
                    year = photo_year(current, submission)
                    if year is not None:
                        st.caption(f"Year: {year}")
                    st.caption(f"Shared by {submission['submitter_name']}")
                    if st.button("View photographs", key=f"gallery_open_{submission_id}", width="stretch"):
                        st.session_state["selected_gallery_submission"] = submission_id
                        st.rerun()


def filter_review_submissions(submissions, chosen):
    if chosen == "All":
        return [item for item in submissions if item.get("status") != "rejected"]
    return [item for item in submissions if item.get("status") == chosen]


def render_review(client, submissions):
    statuses = ["pending", "approved", "private", "rejected"]
    st.subheader("Review and edit")
    notice = st.session_state.pop("curator_notice", None)
    if notice:
        st.success(notice)
    chosen = st.selectbox(
        "Filter by status", ["All"] + statuses, key="review_filter",
        format_func=lambda value: "All (excluding rejected)" if value == "All" else value.title(),
    )
    filtered = filter_review_submissions(submissions, chosen)
    st.caption(f"{len(filtered)} submission(s)")
    if not filtered:
        st.info("No submissions match this filter.")
    for submission in filtered:
        with st.expander(f"{submission['submitter_name']} — {len(submission.get('photos', []))} photographs — {submission['status']}"):
            st.caption(f"Received: {submission['created_at']}")
            with st.form(f"review_{submission['id']}"):
                updates = []
                for photo in submission.get("photos", []):
                    image_column, details_column = st.columns([1, 2])
                    with image_column:
                        show_stored_image(client, photo)
                    with details_column:
                        caption = st.text_area("Caption", value=photo.get("caption") or "", key=f"review_caption_{photo['id']}")
                        year = st.text_input(
                            "Year (optional)", value=str(photo_year(photo, submission) or ""),
                            key=f"review_year_{photo['id']}", max_chars=4,
                        )
                    updates.append((photo["id"], caption, year))
                current = submission.get("status", "pending")
                status = st.selectbox(
                    "Submission status", statuses,
                    index=statuses.index(current) if current in statuses else 0,
                    key=f"review_status_{submission['id']}", format_func=str.title,
                )
                saved = st.form_submit_button("Save changes")
            if saved:
                if any(year.strip() and not valid_year(year) for _, _, year in updates):
                    st.error("Please enter valid four-digit years or leave them blank.")
                    continue
                try:
                    for photo_id, caption, year in updates:
                        client.table("submission_photos").update({
                            "caption": caption.strip() or None,
                            "photo_year": int(year) if year.strip() else None,
                        }).eq("id", photo_id).execute()
                    years = {int(year) for _, _, year in updates if year.strip()}
                    client.table("submissions").update({
                        "status": status, "photo_year": next(iter(years)) if len(years) == 1 else None,
                    }).eq("id", submission["id"]).execute()
                except Exception:
                    LOGGER.exception("Curator update failed")
                    st.error("Could not save all changes. Please try again.")
                else:
                    st.session_state.pop("prepared_export", None)
                    st.session_state["curator_notice"] = (
                        "Submission rejected and removed from this view. Select Rejected in the filter to view or restore it."
                        if status == "rejected" and chosen != "rejected" else "Changes saved."
                    )
                    st.rerun()


# ---------- Presentation and PDF exports ----------

def clean_text(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", text).strip()


def export_rows(submissions, statuses=None):
    rows = []
    for submission in submissions:
        if statuses is not None and submission.get("status") not in statuses:
            continue
        for photo in submission.get("photos", []):
            rows.append({
                "submission_created_at": submission.get("created_at", ""),
                "submission_id": submission["id"],
                "submitter_name": submission.get("submitter_name", ""),
                "photo_id": photo["id"], "photo_year": photo_year(photo, submission),
                "caption": photo.get("caption") or "",
                "photo_filename": photo.get("original_filename") or "photograph.jpg",
                "photo_storage_path": photo["storage_path"],
                "photo_order": photo.get("photo_order") or 0,
                "submission_status": submission.get("status", ""),
            })
    return rows


def sort_export_rows(rows, order):
    def tie(row):
        return (str(row["submission_created_at"] or ""), str(row["submission_id"]),
                int(row["photo_order"]), str(row["photo_id"]))
    if order == "Year (oldest first)":
        return sorted(rows, key=lambda row: (row["photo_year"] is None, row["photo_year"] or 0, *tie(row)))
    return sorted(rows, key=tie)


def register_export_fonts():
    # Bundled ReportLab fonts avoid OS font dependencies and support common accented names.
    font_dir = Path(reportlab.__file__).parent / "fonts"
    if "MemorialSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("MemorialSans", str(font_dir / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("MemorialSansBold", str(font_dir / "VeraBd.ttf")))


def wrap_caption(text):
    register_export_fonts()
    lines = []
    for paragraph in clean_text(text).split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if pdfmetrics.stringWidth(candidate, "MemorialSans", CAPTION_SIZE) <= CAPTION_WIDTH:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            for char in word:
                if line and pdfmetrics.stringWidth(line + char, "MemorialSans", CAPTION_SIZE) > CAPTION_WIDTH:
                    lines.append(line)
                    line = ""
                line += char
        if line:
            lines.append(line)
    return lines or [""]


def caption_chunks(caption):
    lines = wrap_caption(caption)
    return [lines[start:start + CAPTION_LINES] for start in range(0, len(lines), CAPTION_LINES)]


def export_image(data):
    with Image.open(io.BytesIO(data)) as source:
        image = rgb_image(source)
        # Export copies only; the stored photographs are never changed.
        image.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
        dimensions = image.size
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=92, optimize=True)
    return buffer.getvalue(), dimensions


def fitted_image_box(dimensions):
    width, height = dimensions
    scale = min(900 / width, 340 / height)
    draw_w, draw_h = width * scale, height * scale
    return (30 + (900 - draw_w) / 2, 20 + (340 - draw_h) / 2, draw_w, draw_h)


def pptx_text(slide, text, top, height, size=18, bold=False):
    box = slide.shapes.add_textbox(Pt(30), Pt(top), Pt(900), Pt(height))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = False
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    for index, line in enumerate(clean_text(text).split("\n")):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.text = line
        paragraph.alignment = PP_ALIGN.CENTER
        paragraph.font.name = "Arial"
        paragraph.font.size = Pt(size)
        paragraph.font.bold = bold
        paragraph.font.color.rgb = RGBColor.from_string(TEXT_COLOUR)
        paragraph.space_before = Pt(0)
        paragraph.space_after = Pt(0)
        paragraph.line_spacing = Pt(size * 1.25)
    return box


def add_pptx_photo(prs, image_data, dimensions, row, lines, continuation):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string(BACKGROUND)
    left, top, width, height = fitted_image_box(dimensions)
    slide.shapes.add_picture(io.BytesIO(image_data), Pt(left), Pt(top), width=Pt(width), height=Pt(height))
    year = str(row["photo_year"]) if row["photo_year"] is not None else ""
    if year:
        pptx_text(slide, year, 382, 30, size=22, bold=True)
    pptx_text(slide, "\n".join(lines), 418, 116, size=CAPTION_SIZE)
    if continuation:
        pptx_text(slide, "Caption continued", 366, 15, size=10)


def pdf_centred(pdf, text, top, font="MemorialSans", size=18):
    pdf.setFont(font, size)
    pdf.setFillColor(HexColor("#" + TEXT_COLOUR))
    pdf.drawCentredString(SLIDE_W / 2, SLIDE_H - top - size, clean_text(text))


def add_pdf_photo(pdf, image_data, dimensions, row, lines, continuation):
    from reportlab.lib.utils import ImageReader
    pdf.setFillColor(HexColor("#" + BACKGROUND))
    pdf.rect(0, 0, SLIDE_W, SLIDE_H, fill=1, stroke=0)
    left, top, width, height = fitted_image_box(dimensions)
    pdf.drawImage(ImageReader(io.BytesIO(image_data)), left, SLIDE_H - top - height,
                  width=width, height=height, preserveAspectRatio=True)
    if row["photo_year"] is not None:
        pdf_centred(pdf, str(row["photo_year"]), 382, "MemorialSansBold", 22)
    for index, line in enumerate(lines):
        pdf_centred(pdf, line, 418 + index * CAPTION_SIZE * 1.25, size=CAPTION_SIZE)
    if continuation:
        pdf_centred(pdf, "Caption continued", 366, size=10)
    pdf.showPage()


def build_presentations(client, rows, formats, progress_callback=None):
    if not rows:
        raise ValueError("No photographs selected.")
    register_export_fonts()
    prs = Presentation() if "PowerPoint (.pptx)" in formats else None
    if prs is not None:
        prs.slide_width, prs.slide_height = Pt(SLIDE_W), Pt(SLIDE_H)
        prs.core_properties.title = "In Loving Memory Of John Francis Lismore"
        prs.core_properties.subject = "Photographs and memories"
    pdf_buffer = io.BytesIO() if "PDF (.pdf)" in formats else None
    pdf = canvas.Canvas(pdf_buffer, pagesize=(SLIDE_W, SLIDE_H), pageCompression=1) if pdf_buffer is not None else None
    if pdf is not None:
        pdf.setTitle("In Loving Memory Of John Francis Lismore")
    bucket = client.storage.from_(st.secrets["SUPABASE_BUCKET"])
    page_count = 0
    for index, row in enumerate(rows, start=1):
        try:
            # Use authenticated storage downloads rather than expiring preview URLs.
            data = bucket.download(row["photo_storage_path"])
            image_data, dimensions = export_image(data)
            for part, lines in enumerate(caption_chunks(row["caption"])):
                if prs is not None:
                    add_pptx_photo(prs, image_data, dimensions, row, lines, part > 0)
                if pdf is not None:
                    add_pdf_photo(pdf, image_data, dimensions, row, lines, part > 0)
                page_count += 1
        except Exception as exc:
            raise RuntimeError(f"Could not export photograph {index}: {row['photo_filename']}") from exc
        if progress_callback:
            progress_callback(index, len(rows))
    files = {}
    if prs is not None:
        output = io.BytesIO()
        prs.save(output)
        files["pptx"] = output.getvalue()
    if pdf is not None:
        pdf.save()
        files["pdf"] = pdf_buffer.getvalue()
    return files, page_count


def render_export(client, submissions):
    st.subheader("Export photographs")
    st.write(
        "Create a PowerPoint presentation for Google Slides, a PDF, or both. "
        "Each photograph is shown without cropping, with its year and caption underneath."
    )
    statuses = st.multiselect(
        "Include submissions with these statuses", ["approved", "pending", "private", "rejected"],
        default=["approved"], format_func=str.title, key="export_statuses",
    )
    if any(status != "approved" for status in statuses):
        st.warning("This export includes submissions that are not approved for the gallery. Check before sharing.")
    order = st.selectbox("Photograph order", ["Year (oldest first)", "Submission date (oldest first)"])
    formats = st.multiselect("File formats", ["PowerPoint (.pptx)", "PDF (.pdf)"], default=["PowerPoint (.pptx)", "PDF (.pdf)"])
    rows = sort_export_rows(export_rows(submissions, statuses), order)
    st.caption(
        f"{len(rows)} photograph(s) selected. Unknown years are placed last when sorting by year. "
        "Missing years and captions are left blank. Long captions continue on additional slides/pages."
    )
    st.info(
        "For Google Slides: download the PowerPoint file, upload it to Google Drive, "
        "open it with Google Slides, then choose File → Save as Google Slides. "
        "The PDF is ready for viewing or printing."
    )
    signature = hashlib.sha256(json.dumps({
        "version": EXPORT_VERSION, "rows": rows, "formats": sorted(formats),
        "bucket": str(st.secrets["SUPABASE_BUCKET"]),
    }, sort_keys=True, default=str).encode()).hexdigest()
    prepared = st.session_state.get("prepared_export")
    if prepared and prepared["signature"] != signature:
        st.session_state.pop("prepared_export", None)
        prepared = None
    st.caption(
        "Large exports can take time and use substantial server memory. "
        "If needed, prepare one file format at a time."
    )
    if st.button("Prepare export files", type="primary", disabled=not rows or not formats, width="stretch"):
        st.session_state.pop("prepared_export", None)
        prepared = None
        progress = st.progress(0, text="Preparing export…")
        def update_progress(done, total):
            progress.progress(done / total, text=f"Preparing photograph {done} of {total}…")
        try:
            files, page_count = build_presentations(client, rows, formats, update_progress)
        except Exception as exc:
            LOGGER.exception("Presentation export failed")
            progress.empty()
            st.error(f"{exc}. No partial presentation was offered. Check the image and try again.")
        else:
            progress.empty()
            prepared = {"signature": signature, "files": files, "pages": page_count, "photos": len(rows)}
            st.session_state["prepared_export"] = prepared
    if prepared:
        st.success(f"Ready: {prepared['photos']} photograph(s), {prepared['pages']} slide(s)/page(s).")
        for extension, data in prepared["files"].items():
            is_pptx = extension == "pptx"
            st.download_button(
                "Download PowerPoint for Google Slides" if is_pptx else "Download PDF",
                data=data, file_name=f"john_francis_lismore_memories.{extension}",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation" if is_pptx else "application/pdf",
                key=f"download_{extension}", width="stretch", on_click="ignore",
            )
    st.divider()
    st.subheader("CSV export")
    st.caption("Includes all statuses, including private and rejected submissions, as in the existing export.")
    all_rows = export_rows(submissions)
    if not all_rows:
        st.info("No photographs to export yet.")
        return
    frame = pd.DataFrame(all_rows)
    st.download_button(
        "Download CSV for Google Sheets", data=frame.to_csv(index=False).encode("utf-8-sig"),
        file_name="wake_photo_submissions.csv", mime="text/csv", width="stretch", on_click="ignore",
    )
    st.dataframe(frame, width="stretch")


def render_curator_dashboard():
    st.title("Photo submissions")
    try:
        client = get_service_client()
        submissions = load_curator_data(client)
    except Exception:
        LOGGER.exception("Could not load curator data")
        st.error("Could not load photographs. Please try again.")
        return
    area = st.radio("Dashboard view", ["Timeline", "Gallery", "Review and edit", "Export"], horizontal=True, key="curator_view")
    if area == "Timeline":
        if not submissions:
            st.info("No photographs have been received yet.")
        for submission in submissions:
            with st.expander(f"{submission['submitter_name']} — {len(submission.get('photos', []))} photographs — {submission['status']}"):
                st.write(f"Received: {submission['created_at']}")
                years = submission_years(submission)
                if years:
                    label = str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}"
                    st.write(f"Years: {label}")
    elif area == "Gallery":
        render_gallery(client, submissions)
    elif area == "Review and edit":
        render_review(client, submissions)
    else:
        render_export(client, submissions)


def main():
    area = st.sidebar.radio("Choose an area", ["Share a photograph", "Curator dashboard"])
    if area == "Share a photograph":
        render_public_upload()
    elif curator_login():
        render_curator_dashboard()


if __name__ == "__main__":
    main()

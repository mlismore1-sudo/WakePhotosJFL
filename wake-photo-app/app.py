import hashlib
import hmac
import io
import logging
import os
import re
import uuid
from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from supabase import create_client


register_heif_opener()
st.set_page_config(
    page_title="In Loving Memory Of John Francis Lismore",
    layout="wide",
)

MAX_PHOTOS = 5
MAX_FILE_MB = 25
DRAFT_VERSION = 6
CURRENT_YEAR = datetime.now().year
IMAGE_TYPES = ["jpg", "jpeg", "png", "webp", "heic", "heif"]
LOGGER = logging.getLogger(__name__)


@st.cache_resource
def get_public_client():
    return create_client(
        st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"]
    )


def get_service_client():
    return create_client(
        st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
    )


def new_draft(name=""):
    return {
        "version": DRAFT_VERSION,
        "id": str(uuid.uuid4()),
        "name": name,
        "photos": [],
        "complete": False,
        "upload_started": False,
        "uploaded": {},
        "parent_saved": False,
        "photos_saved": False,
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
            break


def delete_photo(photo_id):
    draft = get_draft()
    draft["photos"] = [p for p in draft["photos"] if p["id"] != photo_id]


def start_again():
    name = get_draft()["name"]
    st.session_state["photo_draft"] = new_draft(name)
    st.session_state["upload_generation"] += 1
    st.session_state.pop("upload_messages", None)


def safe_filename(filename):
    name = os.path.basename(filename)
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name).lower() or "photo.jpg"


def open_photo(photo):
    with Image.open(io.BytesIO(photo["bytes"])) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def prepare_photo(photo):
    image = open_photo(photo)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=93, optimize=True)
    filename = os.path.splitext(safe_filename(photo["filename"]))[0] + ".jpg"
    return buffer.getvalue(), filename


def valid_year(value):
    text = str(value).strip()
    return (
        re.fullmatch(r"[0-9]{4}", text) is not None
        and 1800 <= int(text) <= CURRENT_YEAR
    )


def metadata_photo_year(photo):
    """Read EXIF DateTimeOriginal, never upload or modification time."""
    try:
        with Image.open(io.BytesIO(photo["bytes"])) as image:
            exif = image.getexif()
            if not exif:
                return None
            try:
                details = exif.get_ifd(0x8769)
            except Exception:
                details = {}
            raw_date = details.get(0x9003) or exif.get(0x9003)
        if not raw_date:
            return None
        if isinstance(raw_date, bytes):
            raw_date = raw_date.decode("utf-8", errors="ignore")
        date_text = str(raw_date).replace("\x00", "").strip()
        captured_at = datetime.strptime(date_text, "%Y:%m:%d %H:%M:%S")
        year = str(captured_at.year)
        return int(year) if valid_year(year) else None
    except Exception:
        return None


def detected_photo_year(photo):
    # Older in-session drafts may not yet have the cached metadata value.
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
    existing = {p["digest"] for p in draft["photos"]}
    messages = []
    for uploaded in files:
        data = uploaded.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if digest in existing:
            messages.append(f"{uploaded.name}: this photograph is already added.")
            continue
        if len(draft["photos"]) >= MAX_PHOTOS:
            messages.append(f"{uploaded.name}: you can add up to {MAX_PHOTOS} photographs.")
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
            "id": str(uuid.uuid4()),
            "digest": digest,
            "filename": uploaded.name,
            "bytes": data,
            "caption": "",
            "year": "",
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
    if len(draft["photos"]) > MAX_PHOTOS:
        errors.append(f"Please add no more than {MAX_PHOTOS} photographs.")
    for position, photo in enumerate(draft["photos"], start=1):
        year = str(photo.get("year", "")).strip()
        if year and not valid_year(year):
            errors.append(
                f"Photograph {position}: enter a four-digit year between "
                f"1800 and {CURRENT_YEAR}, or leave the year blank."
            )
    return errors


def render_header():
    st.markdown(
        """
        <div style="text-align:center;padding:1.6rem 1rem;background:#F1ECE4;
                    color:#33302B;border-radius:16px;margin-bottom:1.5rem;">
            <h1 style="font-size:2rem;margin:0 0 .5rem;">
                In Loving Memory Of John Francis Lismore
            </h1>
            <p style="margin:0;">Sharing photographs and memories</p>
        </div>
        """, unsafe_allow_html=True,
    )


def render_photo_card(photo, position, total):
    with st.container(border=True):
        image_column, details_column = st.columns([1, 1.35])
        with image_column:
            st.image(open_photo(photo), width="stretch")
        with details_column:
            st.markdown(f"### Photograph {position + 1} of {total}")
            caption_key = f"edit_caption_{photo['id']}"
            year_key = f"edit_year_{photo['id']}"
            st.session_state[caption_key] = photo["caption"]
            st.session_state[year_key] = photo.get("year", "")
            st.text_area(
                "Caption", key=caption_key,
                on_change=save_photo_field,
                args=(photo["id"], "caption", caption_key),
                placeholder="For example: John with family at Emma’s wedding",
                height=110,
            )
            st.text_input(
                "Year (optional)", key=year_key,
                on_change=save_photo_field,
                args=(photo["id"], "year", year_key),
                placeholder="For example: 1978", max_chars=4,
                help=(
                    "An entered year takes priority. If left blank, the original "
                    "capture year in the photograph’s metadata is used when available."
                ),
            )
            year = str(photo.get("year", "")).strip()
            if year and not valid_year(year):
                st.warning(
                    f"Enter a four-digit year between 1800 and {CURRENT_YEAR}, "
                    "or leave it blank."
                )
            elif not year:
                detected = detected_photo_year(photo)
                if detected:
                    st.caption(
                        f"Metadata year: {detected}. This will be used unless "
                        "you enter a year above. For scans or photos of old prints, "
                        "please check the original year."
                    )
                else:
                    st.caption(
                        "No original capture year found. You can enter a year "
                        "above or leave it unknown."
                    )
            st.button(
                "Delete", key=f"delete_{photo['id']}",
                on_click=delete_photo, args=(photo["id"],),
            )


def submit_photos():
    draft = get_draft()
    errors = validation_errors()
    if errors:
        for message in errors:
            st.error(message)
        return
    if "submission_snapshot" not in draft:
        draft["submission_snapshot"] = {
            "name": draft["name"].strip(),
            "photos": [dict(photo) for photo in draft["photos"]],
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
                bucket.upload(
                    path=path, file=data,
                    file_options={
                        "content-type": "image/jpeg",
                        "cache-control": "3600", "upsert": "false",
                    },
                )
                draft["uploaded"][photo["id"]] = path
        progress.progress(85, text="Saving your photographs…")
        if not draft["parent_saved"]:
            client.table("submissions").insert(
                {
                    "id": draft["id"],
                    "submitter_name": snapshot["name"],
                    "status": "pending",
                }, returning="minimal",
            ).execute()
            draft["parent_saved"] = True
        if not draft["photos_saved"]:
            records = []
            for position, photo in enumerate(photos, start=1):
                records.append({
                    "id": photo["id"],
                    "submission_id": draft["id"],
                    "storage_path": draft["uploaded"][photo["id"]],
                    "original_filename": photo["filename"],
                    "photo_order": position,
                    "caption": photo["caption"].strip() or None,
                    "photo_year": resolved_photo_year(photo),
                })
            client.table("submission_photos").insert(
                records, returning="minimal"
            ).execute()
            draft["photos_saved"] = True
        progress.progress(100, text="Your photographs have been received.")
        draft["complete"] = True
    except Exception:
        LOGGER.exception("Photo submission failed: %s", draft["id"])
        progress.empty()
        st.error(
            "We couldn’t finish saving your photographs. "
            "Please select ‘Try again’ to retry."
        )
        st.caption(f"Reference: {draft['id']}")
        if st.button("Try again", key="retry_after_failure", type="primary"):
            st.rerun()
        return
    st.rerun()


def render_submission_form():
    draft = get_draft()
    st.subheader("Your photographs")
    st.write(
        "Choose up to five photographs. Add a caption and, if you know it, "
        "the year beside each photograph. If you leave the year blank, "
        "we’ll use its original metadata year when available."
    )
    uploaded = st.file_uploader(
        "Choose photographs", type=IMAGE_TYPES, accept_multiple_files=True,
        key=f"uploader_{st.session_state['upload_generation']}",
        help=f"Add up to {MAX_PHOTOS} photographs, each under {MAX_FILE_MB} MB.",
    )
    if uploaded:
        ingest_uploads(uploaded)
        st.rerun()
    for message in st.session_state.pop("upload_messages", []):
        st.warning(message)
    photos = draft["photos"]
    if photos:
        st.caption(f"{len(photos)} of {MAX_PHOTOS} photographs added.")
        for position, photo in enumerate(photos):
            render_photo_card(photo, position, len(photos))
    st.divider()
    st.subheader("Submitted By")
    st.session_state["draft_name"] = draft["name"]
    st.text_input(
        "Your name", key="draft_name", on_change=save_name,
        placeholder="Enter your name",
    )
    if st.button(
        "Submit photographs", type="primary", disabled=not photos, width="stretch",
    ):
        submit_photos()


def render_public_upload():
    render_header()
    draft = get_draft()
    if draft["complete"]:
        st.success("Thank you — your photographs have been received.")
        st.write(
            "Thank you for helping us create this collection "
            "of photographs and memories of John."
        )
        st.button(
            "Share more photographs", type="primary",
            on_click=start_again, width="stretch",
        )
        return
    if draft["upload_started"]:
        st.subheader("Finish sharing your photographs")
        st.write("Select ‘Try again’ to finish saving your photographs.")
        if st.button("Try again", type="primary", width="stretch"):
            submit_photos()
        return
    render_submission_form()


def curator_login():
    if st.session_state.get("curator_authenticated"):
        if st.sidebar.button("Sign out"):
            st.session_state["curator_authenticated"] = False
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


def load_curator_data(client):
    submissions = (
        client.table("submissions").select("*")
        .order("created_at", desc=True).execute().data or []
    )
    photos = (
        client.table("submission_photos").select("*")
        .order("photo_order").execute().data or []
    )
    grouped = {}
    for photo in photos:
        grouped.setdefault(photo["submission_id"], []).append(photo)
    for submission in submissions:
        submission["photos"] = grouped.get(submission["id"], [])
    return submissions


def photo_year(photo, submission):
    return photo.get("photo_year") or submission.get("photo_year")


def submission_years(submission):
    return sorted({
        int(photo_year(photo, submission))
        for photo in submission.get("photos", [])
        if photo_year(photo, submission)
    })


def submission_year_label(submission):
    years = submission_years(submission)
    if not years:
        return str(submission.get("photo_year") or "Year not recorded")
    return str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}"


def signed_image_url(client, photo):
    response = client.storage.from_(st.secrets["SUPABASE_BUCKET"]).create_signed_url(
        photo["storage_path"], 3600
    )
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
    positions[submission_id] = (
        positions.get(submission_id, 0) + direction
    ) % photo_count
    st.session_state["gallery_photo_positions"] = positions


def render_square_preview(client, photo):
    try:
        url = signed_image_url(client, photo)
    except Exception:
        url = None
    if url:
        safe_url = escape(url, quote=True)
        st.markdown(
            f"""
            <div style="width:100%;aspect-ratio:1 / 1;overflow:hidden;
                        border-radius:12px;background:#F1ECE4;position:relative;">
                <img src="{safe_url}" alt="Photograph from a shared memory"
                     style="position:absolute;inset:0;width:100%;height:100%;
                            object-fit:cover;display:block;">
            </div>
            """, unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div style="width:100%;aspect-ratio:1 / 1;border-radius:12px;
                        background:#F1ECE4;display:flex;align-items:center;
                        justify-content:center;">Preview unavailable</div>
            """, unsafe_allow_html=True,
        )


def render_gallery(client, submissions):
    approved = [
        item for item in submissions
        if item.get("status") == "approved" and item.get("photos")
    ]
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
                if year:
                    st.caption(f"Year: {year}")
                st.write(photo.get("caption") or "No caption added.")
        return
    st.subheader("Photo gallery")
    if not approved:
        st.info("No approved photographs are available yet.")
        return
    st.caption(
        f"{len(approved)} memories. "
        "Use the arrows to browse photographs in each memory."
    )
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
                    previous, counter, following = st.columns(
                        [1, 2, 1], vertical_alignment="center"
                    )
                    previous.button(
                        "←", key=f"gallery_previous_{submission_id}",
                        help="Previous photograph", on_click=cycle_gallery_photo,
                        args=(submission_id, -1, count), disabled=count == 1,
                        width="stretch",
                    )
                    with counter:
                        st.markdown(
                            "<div style='text-align:center;'>"
                            f"{index + 1} of {count}</div>", unsafe_allow_html=True,
                        )
                    following.button(
                        "→", key=f"gallery_next_{submission_id}",
                        help="Next photograph", on_click=cycle_gallery_photo,
                        args=(submission_id, 1, count), disabled=count == 1,
                        width="stretch",
                    )
                    st.write(current.get("caption") or "No caption added.")
                    year = photo_year(current, submission)
                    if year:
                        st.caption(f"Year: {year}")
                    st.caption(f"Shared by {submission['submitter_name']}")
                    if st.button(
                        "View photographs", key=f"gallery_open_{submission_id}",
                        width="stretch",
                    ):
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
        format_func=lambda value: (
            "All (excluding rejected)" if value == "All" else value.title()
        ),
    )
    filtered = filter_review_submissions(submissions, chosen)
    st.caption(f"{len(filtered)} submission(s)")
    if not filtered:
        st.info("No submissions match this filter.")
    for submission in filtered:
        with st.expander(
            f"{submission['submitter_name']} — "
            f"{len(submission.get('photos', []))} photographs — "
            f"{submission['status']}"
        ):
            st.caption(f"Received: {submission['created_at']}")
            with st.form(f"review_{submission['id']}"):
                updates = []
                for photo in submission.get("photos", []):
                    image_column, details_column = st.columns([1, 2])
                    with image_column:
                        show_stored_image(client, photo)
                    with details_column:
                        caption = st.text_area(
                            "Caption", value=photo.get("caption") or "",
                            key=f"review_caption_{photo['id']}",
                        )
                        year = st.text_input(
                            "Year (optional)",
                            value=str(photo_year(photo, submission) or ""),
                            key=f"review_year_{photo['id']}", max_chars=4,
                        )
                    updates.append((photo["id"], caption, year))
                current_status = submission.get("status", "pending")
                status = st.selectbox(
                    "Submission status", statuses,
                    index=statuses.index(current_status) if current_status in statuses else 0,
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
                        "status": status,
                        "photo_year": next(iter(years)) if len(years) == 1 else None,
                    }).eq("id", submission["id"]).execute()
                except Exception:
                    LOGGER.exception("Curator update failed")
                    st.error("Could not save all changes. Please try again.")
                else:
                    if status == "rejected" and chosen != "rejected":
                        st.session_state["curator_notice"] = (
                            "Submission rejected and removed from this view. "
                            "Select Rejected in the filter to view or restore it."
                        )
                    else:
                        st.session_state["curator_notice"] = "Changes saved."
                    st.rerun()


def render_export(submissions):
    rows = []
    for submission in submissions:
        for photo in submission.get("photos", []):
            rows.append({
                "submission_created_at": submission["created_at"],
                "submitter_name": submission["submitter_name"],
                "photo_year": photo_year(photo, submission),
                "caption": photo.get("caption") or "",
                "photo_filename": photo["original_filename"],
                "photo_storage_path": photo["storage_path"],
                "photo_order": photo["photo_order"],
                "submission_status": submission["status"],
            })
    if not rows:
        st.info("No photographs to export yet.")
        return
    frame = pd.DataFrame(rows)
    st.download_button(
        "Download CSV for Google Sheets",
        data=frame.to_csv(index=False).encode("utf-8-sig"),
        file_name="wake_photo_submissions.csv", mime="text/csv", width="stretch",
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
    area = st.radio(
        "Dashboard view", ["Timeline", "Gallery", "Review and edit", "Export"],
        horizontal=True, key="curator_view",
    )
    if area == "Timeline":
        if not submissions:
            st.info("No photographs have been received yet.")
        for submission in submissions:
            with st.expander(
                f"{submission['submitter_name']} — "
                f"{len(submission.get('photos', []))} photographs — "
                f"{submission['status']}"
            ):
                st.write(f"Received: {submission['created_at']}")
                if submission_years(submission):
                    st.write(f"Years: {submission_year_label(submission)}")
    elif area == "Gallery":
        render_gallery(client, submissions)
    elif area == "Review and edit":
        render_review(client, submissions)
    else:
        render_export(submissions)


def main():
    area = st.sidebar.radio(
        "Choose an area", ["Share a photograph", "Curator dashboard"]
    )
    if area == "Share a photograph":
        render_public_upload()
    elif curator_login():
        render_curator_dashboard()


if __name__ == "__main__":
    main()

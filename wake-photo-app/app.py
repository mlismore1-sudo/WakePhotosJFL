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
DRAFT_VERSION = 4
IMAGE_TYPES = ["jpg", "jpeg", "png", "webp", "heic", "heif"]
CURRENT_YEAR = datetime.now().year
LOGGER = logging.getLogger(__name__)


@st.cache_resource
def get_public_client():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_ANON_KEY"],
    )


def get_service_client():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SERVICE_ROLE_KEY"],
    )


def new_draft(name=""):
    return {
        "version": DRAFT_VERSION,
        "id": str(uuid.uuid4()),
        "step": 1,
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


def save_caption(photo_id, widget_key):
    for photo in get_draft()["photos"]:
        if photo["id"] == photo_id:
            photo["caption"] = st.session_state.get(widget_key, "")
            break


def delete_photo(photo_id):
    draft = get_draft()
    draft["photos"] = [p for p in draft["photos"] if p["id"] != photo_id]


def go_back():
    get_draft()["step"] = 1


def start_again():
    name = get_draft()["name"]
    st.session_state["photo_draft"] = new_draft(name)
    st.session_state["upload_generation"] += 1
    st.session_state.pop("upload_messages", None)


def safe_filename(filename):
    name = os.path.basename(filename)
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name).lower() or "photo.jpg"


def open_photo(photo):
    with Image.open(io.BytesIO(photo["bytes"])) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


def prepare_photo(photo):
    image = open_photo(photo)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=93, optimize=True)
    name = os.path.splitext(safe_filename(photo["filename"]))[0] + ".jpg"
    return buffer.getvalue(), name


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
        draft["photos"].append({
            "id": str(uuid.uuid4()),
            "digest": digest,
            "filename": uploaded.name,
            "bytes": data,
            "caption": "",
        })
        existing.add(digest)
    st.session_state["upload_messages"] = messages
    st.session_state["upload_generation"] += 1


def validation_errors():
    draft = get_draft()
    errors = []
    if not draft["name"].strip():
        errors.append("Please enter your name.")
    if not draft["photos"]:
        errors.append("Please add at least one photograph.")
    if len(draft["photos"]) > MAX_PHOTOS:
        errors.append(f"Please add no more than {MAX_PHOTOS} photographs.")
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
        """,
        unsafe_allow_html=True,
    )


def render_step_one():
    draft = get_draft()
    st.subheader("Your name")
    st.write("Let us know who is sharing these photographs.")
    st.session_state["draft_name"] = draft["name"]
    st.text_input(
        "Your name",
        key="draft_name",
        on_change=save_name,
        placeholder="For example: Sarah Smith",
    )
    if st.button("Continue to photographs", type="primary", use_container_width=True):
        if not draft["name"].strip():
            st.error("Please enter your name.")
        else:
            draft["step"] = 2
            st.rerun()


def render_photo_card(photo, position, total):
    with st.container(border=True):
        image_column, details_column = st.columns([1, 1.35])
        with image_column:
            st.image(open_photo(photo), use_container_width=True)
        with details_column:
            st.markdown(f"### Photograph {position + 1} of {total}")
            key = f"edit_caption_{photo['id']}"
            st.session_state[key] = photo["caption"]
            st.text_area(
                "Caption",
                key=key,
                on_change=save_caption,
                args=(photo["id"], key),
                placeholder="For example: John with family at Emma’s wedding",
                height=110,
            )
            st.button(
                "Delete",
                key=f"delete_{photo['id']}",
                on_click=delete_photo,
                args=(photo["id"],),
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
                    path=path,
                    file=data,
                    file_options={
                        "content-type": "image/jpeg",
                        "cache-control": "3600",
                        "upsert": "false",
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
                },
                returning="minimal",
            ).execute()
            draft["parent_saved"] = True

        if not draft["photos_saved"]:
            records = [
                {
                    "id": photo["id"],
                    "submission_id": draft["id"],
                    "storage_path": draft["uploaded"][photo["id"]],
                    "original_filename": photo["filename"],
                    "photo_order": position,
                    "caption": photo["caption"].strip() or None,
                }
                for position, photo in enumerate(photos, start=1)
            ]
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


def render_step_two():
    draft = get_draft()
    st.subheader("Your photographs")
    st.write("Choose up to five photographs and add a caption to each.")
    uploaded = st.file_uploader(
        "Choose photographs",
        type=IMAGE_TYPES,
        accept_multiple_files=True,
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
    else:
        st.info("Choose at least one photograph to get started.")

    st.divider()
    back, share = st.columns(2)
    back.button("Back", on_click=go_back, use_container_width=True)
    if share.button(
        "Share photographs",
        type="primary",
        disabled=not photos,
        use_container_width=True,
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
            "Share more photographs",
            type="primary",
            on_click=start_again,
            use_container_width=True,
        )
        return
    if draft["upload_started"]:
        st.subheader("Finish sharing your photographs")
        st.write("Select ‘Try again’ to finish saving your photographs.")
        if st.button("Try again", type="primary", use_container_width=True):
            submit_photos()
        return
    if draft["step"] not in (1, 2):
        draft["step"] = 2
    step = draft["step"]
    label = "Your name" if step == 1 else "Your photographs"
    st.caption(f"Step {step} of 2 — {label}")
    if step == 1:
        render_step_one()
    else:
        render_step_two()


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
    if len(years) == 1:
        return str(years[0])
    return f"{years[0]}–{years[-1]}"


def signed_image_url(client, photo):
    response = client.storage.from_(
        st.secrets["SUPABASE_BUCKET"]
    ).create_signed_url(photo["storage_path"], 3600)
    return response.get("signedURL") or response.get("signedUrl")


def show_stored_image(client, photo):
    try:
        url = signed_image_url(client, photo)
        if url:
            st.image(url, use_container_width=True)
        else:
            st.warning("Preview unavailable.")
    except Exception:
        st.warning("Preview unavailable.")


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
    st.caption(f"{len(approved)} memories")
    for start in range(0, len(approved), 3):
        columns = st.columns(3, gap="small")
        for column, submission in zip(columns, approved[start:start + 3]):
            with column:
                try:
                    url = signed_image_url(client, submission["photos"][0])
                except Exception:
                    url = None
                if url:
                    safe_url = escape(url, quote=True)
                    st.markdown(
                        f"""
                        <div style="width:100%;aspect-ratio:1 / 1;overflow:hidden;
                                    border-radius:12px;background:#F1ECE4;
                                    position:relative;">
                            <img src="{safe_url}" alt="Memory cover photograph"
                                 style="position:absolute;inset:0;width:100%;
                                        height:100%;object-fit:cover;display:block;">
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.warning("Preview unavailable.")
                st.caption(f"Shared by {submission['submitter_name']}")
                years = submission_years(submission)
                if years:
                    st.caption(submission_year_label(submission))
                if st.button(
                    "View photographs",
                    key=f"gallery_{submission['id']}",
                    use_container_width=True,
                ):
                    st.session_state["selected_gallery_submission"] = submission["id"]
                    st.rerun()


def valid_year(value):
    text = str(value).strip()
    return (
        re.fullmatch(r"[0-9]{4}", text) is not None
        and 1800 <= int(text) <= CURRENT_YEAR
    )


def render_review(client, submissions):
    statuses = ["pending", "approved", "private", "rejected"]
    chosen = st.selectbox("Filter by status", ["All"] + statuses)
    filtered = [
        item for item in submissions
        if chosen == "All" or item["status"] == chosen
    ]
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
                            "Caption",
                            value=photo.get("caption") or "",
                            key=f"review_caption_{photo['id']}",
                        )
                        year = st.text_input(
                            "Year (optional, curator only)",
                            value=str(photo_year(photo, submission) or ""),
                            key=f"review_year_{photo['id']}",
                            max_chars=4,
                        )
                    updates.append((photo["id"], caption, year))
                current_status = submission.get("status", "pending")
                status = st.selectbox(
                    "Submission status",
                    statuses,
                    index=statuses.index(current_status) if current_status in statuses else 0,
                    key=f"review_status_{submission['id']}",
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
        file_name="wake_photo_submissions.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.dataframe(frame, use_container_width=True)


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
        "Dashboard view",
        ["Timeline", "Gallery", "Review and edit", "Export"],
        horizontal=True,
        key="curator_view",
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

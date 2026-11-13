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
CURRENT_YEAR = datetime.now().year
DRAFT_VERSION = 3

IMAGE_TYPES = ["jpg", "jpeg", "png", "webp", "heic", "heif"]


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


def safe_filename(filename):
    name = os.path.basename(filename)
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", name).lower()
    return cleaned or "photo.jpg"


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


def find_photo(photo_id):
    return next(
        photo
        for photo in get_draft()["photos"]
        if photo["id"] == photo_id
    )


def save_name():
    get_draft()["name"] = st.session_state.get("draft_name", "")


def save_photo_field(photo_id, field, widget_key):
    find_photo(photo_id)[field] = st.session_state.get(widget_key, "")


def go_to_step(step):
    get_draft()["step"] = step


def rotate_photo(photo_id, angle):
    photo = find_photo(photo_id)
    photo["rotation"] = (photo["rotation"] + angle) % 360


def reset_rotation(photo_id):
    find_photo(photo_id)["rotation"] = 0


def move_photo(photo_id, direction):
    photos = get_draft()["photos"]
    position = next(
        index
        for index, photo in enumerate(photos)
        if photo["id"] == photo_id
    )
    target = position + direction

    if 0 <= target < len(photos):
        photos[position], photos[target] = photos[target], photos[position]


def remove_photo(photo_id):
    draft = get_draft()
    draft["photos"] = [
        photo for photo in draft["photos"]
        if photo["id"] != photo_id
    ]


def start_again():
    name = get_draft()["name"]
    st.session_state["photo_draft"] = new_draft(name=name)
    st.session_state["upload_generation"] += 1


def valid_year(value):
    text = str(value).strip()

    return (
        re.fullmatch(r"[0-9]{4}", text) is not None
        and 1800 <= int(text) <= CURRENT_YEAR
    )


def open_photo(photo):
    with Image.open(io.BytesIO(photo["bytes"])) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")

    if photo["rotation"]:
        image = image.rotate(photo["rotation"], expand=True)

    return image


def prepare_photo(photo):
    image = open_photo(photo)
    buffer = io.BytesIO()

    image.save(
        buffer,
        format="JPEG",
        quality=93,
        optimize=True,
    )

    filename = (
        os.path.splitext(safe_filename(photo["filename"]))[0] + ".jpg"
    )

    return buffer.getvalue(), filename


def ingest_uploads(files):
    draft = get_draft()
    existing = {photo["digest"] for photo in draft["photos"]}
    errors = []

    for uploaded in files:
        data = uploaded.getvalue()
        digest = hashlib.sha256(data).hexdigest()

        if digest in existing:
            continue

        if len(draft["photos"]) >= MAX_PHOTOS:
            errors.append(
                f"{uploaded.name}: you can add up to {MAX_PHOTOS} photographs."
            )
            continue

        if len(data) > MAX_FILE_MB * 1024 * 1024:
            errors.append(
                f"{uploaded.name}: please choose a file under {MAX_FILE_MB} MB."
            )
            continue

        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        except Exception:
            errors.append(
                f"{uploaded.name}: this photograph could not be opened."
            )
            continue

        draft["photos"].append(
            {
                "id": str(uuid.uuid4()),
                "digest": digest,
                "filename": uploaded.name,
                "bytes": data,
                "caption": "",
                "year": "",
                "rotation": 0,
            }
        )
        existing.add(digest)

    st.session_state["upload_messages"] = errors
    st.session_state["upload_generation"] += 1


def apply_shared_details():
    caption = st.session_state.get("shared_caption", "").strip()
    year = st.session_state.get("shared_year", "").strip()

    if not caption and not year:
        st.session_state["bulk_message"] = (
            "Enter a shared caption, a year, or both first."
        )
        return

    if year and not valid_year(year):
        st.session_state["bulk_message"] = (
            f"Please enter a four-digit year between 1800 and {CURRENT_YEAR}."
        )
        return

    for photo in get_draft()["photos"]:
        if caption:
            photo["caption"] = caption
            st.session_state[f"edit_caption_{photo['id']}"] = caption

        if year:
            photo["year"] = year
            st.session_state[f"edit_year_{photo['id']}"] = year

    st.session_state["bulk_message"] = (
        "Shared details applied. You can still edit each photograph below."
    )


def validation_errors():
    draft = get_draft()
    errors = []

    if not draft["name"].strip():
        errors.append("Please add your name in Step 1.")

    if not draft["photos"]:
        errors.append("Please add at least one photograph.")

    if len(draft["photos"]) > MAX_PHOTOS:
        errors.append(f"Please keep no more than {MAX_PHOTOS} photographs.")

    for index, photo in enumerate(draft["photos"], start=1):
        if not valid_year(photo["year"]):
            errors.append(
                f"Photograph {index}: enter a four-digit year "
                f"between 1800 and {CURRENT_YEAR}."
            )

    return errors


def render_header():
    st.markdown(
        """
        <div style="
            text-align:center;
            padding:1.6rem 1rem;
            background:#F1ECE4;
            color:#33302B;
            border-radius:16px;
            margin-bottom:1.5rem;
        ">
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
    st.write("Let the family know who is sharing these photographs.")

    st.session_state["draft_name"] = draft["name"]

    st.text_input(
        "Your name",
        key="draft_name",
        on_change=save_name,
        placeholder="For example: Sarah Smith",
    )

    st.info(
        "The family will review your photographs before adding them "
        "to the shared gallery."
    )

    if st.button(
        "Continue to photographs",
        type="primary",
        use_container_width=True,
    ):
        if not draft["name"].strip():
            st.error("Please enter your name.")
        else:
            draft["step"] = 2
            st.rerun()


def render_photo_card(photo, position, total):
    with st.container(border=True):
        st.markdown(f"### Photograph {position + 1} of {total}")

        if position == 0:
            st.caption("Cover photograph — this will appear in the gallery.")

        image_column, details_column = st.columns([1, 1.35])

        with image_column:
            # Controls stay with the photograph, above its preview.
            left, right, reset = st.columns(3)

            left.button(
                "90° left",
                key=f"left_{photo['id']}",
                on_click=rotate_photo,
                args=(photo["id"], 90),
                use_container_width=True,
            )

            right.button(
                "90° right",
                key=f"right_{photo['id']}",
                on_click=rotate_photo,
                args=(photo["id"], -90),
                use_container_width=True,
            )

            reset.button(
                "Reset",
                key=f"reset_{photo['id']}",
                on_click=reset_rotation,
                args=(photo["id"],),
                use_container_width=True,
            )

            st.image(
                open_photo(photo),
                use_container_width=True,
            )

        with details_column:
            caption_key = f"edit_caption_{photo['id']}"
            year_key = f"edit_year_{photo['id']}"

            st.session_state[caption_key] = photo["caption"]
            st.session_state[year_key] = photo["year"]

            st.text_area(
                "Caption",
                key=caption_key,
                on_change=save_photo_field,
                args=(photo["id"], "caption", caption_key),
                placeholder="For example: John with family at Emma’s wedding",
                height=110,
            )

            st.text_input(
                "Year of this photograph (YYYY)",
                key=year_key,
                on_change=save_photo_field,
                args=(photo["id"], "year", year_key),
                placeholder="For example: 2008",
                max_chars=4,
            )

            if photo["year"] and not valid_year(photo["year"]):
                st.warning(
                    f"Use a four-digit year between 1800 and {CURRENT_YEAR}."
                )

            st.caption(photo["filename"])

            up, down, remove = st.columns(3)

            up.button(
                "Move up",
                key=f"up_{photo['id']}",
                on_click=move_photo,
                args=(photo["id"], -1),
                disabled=position == 0,
                use_container_width=True,
            )

            down.button(
                "Move down",
                key=f"down_{photo['id']}",
                on_click=move_photo,
                args=(photo["id"], 1),
                disabled=position == total - 1,
                use_container_width=True,
            )

            remove.button(
                "Remove",
                key=f"remove_{photo['id']}",
                on_click=remove_photo,
                args=(photo["id"],),
                use_container_width=True,
            )


def render_step_two():
    draft = get_draft()

    st.subheader("Add your photographs")
    st.write(
        "Add up to five photographs. Each can have its own caption and year."
    )

    uploaded = st.file_uploader(
        "Choose photographs",
        type=IMAGE_TYPES,
        accept_multiple_files=True,
        key=f"uploader_{st.session_state['upload_generation']}",
        help="You can add more photographs after your first selection.",
    )

    if uploaded:
        ingest_uploads(uploaded)
        st.rerun()

    for message in st.session_state.pop("upload_messages", []):
        st.warning(message)

    if draft["photos"]:
        st.caption(
            f"{len(draft['photos'])} of {MAX_PHOTOS} photographs added."
        )

        with st.container(border=True):
            st.markdown("### Use the same details for every photograph")
            st.caption(
                "Fill in a caption, a year, or both, then select Apply to all. "
                "Blank fields leave existing details unchanged."
            )

            caption_column, year_column = st.columns([3, 1])

            caption_column.text_input(
                "Shared caption",
                key="shared_caption",
                placeholder="For example: Emma’s wedding",
            )

            year_column.text_input(
                "Shared year (YYYY)",
                key="shared_year",
                placeholder="2008",
                max_chars=4,
            )

            st.button(
                "Apply to all",
                on_click=apply_shared_details,
                use_container_width=True,
            )

            message = st.session_state.pop("bulk_message", None)
            if message:
                st.info(message)

        st.caption(
            "Applying shared details replaces the corresponding values "
            "on all photographs. You can edit individual photographs afterwards."
        )

        for position, photo in enumerate(draft["photos"]):
            render_photo_card(photo, position, len(draft["photos"]))

    else:
        st.info("Choose at least one photograph to get started.")

    st.caption(
        "Your draft stays available while this browser session remains active. "
        "Refreshing or closing the page may clear it."
    )

    back, forward = st.columns(2)

    back.button(
        "Back",
        on_click=go_to_step,
        args=(1,),
        use_container_width=True,
    )

    if forward.button(
        "Check and share",
        type="primary",
        use_container_width=True,
    ):
        errors = validation_errors()

        if errors:
            for message in errors:
                st.error(message)
        else:
            draft["step"] = 3
            st.rerun()


def submit_photos():
    draft = get_draft()
    errors = validation_errors()

    if errors:
        for message in errors:
            st.error(message)
        return

    progress = st.progress(0, text="Preparing your photographs…")

    try:
        client = get_public_client()
        bucket = client.storage.from_(st.secrets["SUPABASE_BUCKET"])
        photos = draft["photos"]
        total = len(photos)

        draft["upload_started"] = True

        for index, photo in enumerate(photos):
            if photo["id"] not in draft["uploaded"]:
                data, filename = prepare_photo(photo)

                path = (
                    f"pending/{draft['id']}/"
                    f"{photo['id']}-{filename}"
                )

                progress.progress(
                    int(index / total * 80),
                    text=f"Uploading photograph {index + 1} of {total}…",
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

                draft["uploaded"][photo["id"]] = {
                    "path": path,
                    "filename": filename,
                }

        progress.progress(85, text="Saving your photograph details…")

        years = {int(photo["year"]) for photo in photos}

        if not draft["parent_saved"]:
            client.table("submissions").insert(
                {
                    "id": draft["id"],
                    "submitter_name": draft["name"].strip(),
                    # Retained for older views; mixed-year sets use NULL.
                    "photo_year": next(iter(years)) if len(years) == 1 else None,
                    "status": "pending",
                },
                returning="minimal",
            ).execute()

            draft["parent_saved"] = True

        if not draft["photos_saved"]:
            records = []

            for position, photo in enumerate(photos, start=1):
                stored = draft["uploaded"][photo["id"]]

                records.append(
                    {
                        "id": photo["id"],
                        "submission_id": draft["id"],
                        "storage_path": stored["path"],
                        "original_filename": photo["filename"],
                        "photo_order": position,
                        "caption": photo["caption"].strip() or None,
                        "photo_year": int(photo["year"]),
                    }
                )

            client.table("submission_photos").insert(
                records,
                returning="minimal",
            ).execute()

            draft["photos_saved"] = True

        progress.progress(100, text="Your photographs have been received.")
        draft["complete"] = True

    except Exception:
        logging.exception("Photo submission failed: %s", draft["id"])
        st.error(
            "We couldn’t finish saving your photographs. "
            "Your draft is still available in this session. "
            "Please try again."
        )
        st.caption(f"Reference: {draft['id']}")
        return

    st.rerun()


def render_step_three():
    draft = get_draft()

    st.subheader("Check and share")
    st.write(f"Shared by: {draft['name']}")
    st.write(f"Photographs: {len(draft['photos'])}")

    for position, photo in enumerate(draft["photos"], start=1):
        with st.container(border=True):
            image_column, details_column = st.columns([1, 2])

            with image_column:
                st.image(open_photo(photo), use_container_width=True)

            with details_column:
                st.markdown(f"### Photograph {position}")
                st.write(f"Year: {photo['year']}")
                st.write(photo["caption"] or "No caption added.")

    st.info(
        "The family will review your photographs before adding them "
        "to the shared gallery."
    )

    back, share = st.columns(2)

    back.button(
        "Back to edit",
        on_click=go_to_step,
        args=(2,),
        disabled=draft["upload_started"],
        use_container_width=True,
        help=(
            "After an upload attempt, retry from this screen so the "
            "same draft can finish saving."
        ),
    )

    if share.button(
        "Share these photographs",
        type="primary",
        use_container_width=True,
    ):
        submit_photos()


def render_public_upload():
    render_header()
    draft = get_draft()

    if draft["complete"]:
        st.success("Thank you — your photographs have been received.")
        st.write(
            "Thank you for helping the family create this collection "
            "of photographs and memories of John."
        )

        st.button(
            "Share more photographs",
            type="primary",
            on_click=start_again,
            use_container_width=True,
        )
        return

    labels = {
        1: "Your name",
        2: "Add photographs",
        3: "Check and share",
    }

    step = draft["step"]

    st.progress(
        (step - 1) / 3,
        text=f"Step {step} of 3 — {labels[step]}",
    )

    if step == 1:
        render_step_one()
    elif step == 2:
        render_step_two()
    else:
        render_step_three()


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

        if hmac.compare_digest(password, expected):
            st.session_state["curator_authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    return False


def load_curator_data(client):
    submissions = (
        client.table("submissions")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )

    photos = (
        client.table("submission_photos")
        .select("*")
        .order("photo_order")
        .execute()
        .data
        or []
    )

    grouped = {}

    for photo in photos:
        grouped.setdefault(photo["submission_id"], []).append(photo)

    for submission in submissions:
        submission["photos"] = grouped.get(submission["id"], [])

    return submissions


def photo_year(photo, submission):
    return photo.get("photo_year") or submission.get("photo_year")


def submission_year_label(submission):
    years = sorted(
        {
            int(photo_year(photo, submission))
            for photo in submission.get("photos", [])
            if photo_year(photo, submission)
        }
    )

    if not years:
        return str(submission.get("photo_year") or "Year unknown")

    if len(years) == 1:
        return str(years[0])

    return f"{years[0]}–{years[-1]}"


def signed_image_url(client, photo):
    response = client.storage.from_(
        st.secrets["SUPABASE_BUCKET"]
    ).create_signed_url(
        photo["storage_path"],
        3600,
    )

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
        submission for submission in submissions
        if submission.get("status") == "approved"
        and submission.get("photos")
    ]

    approved.sort(
        key=lambda submission: min(
            (
                int(photo_year(photo, submission) or 9999)
                for photo in submission["photos"]
            ),
            default=9999,
        )
    )

    selected = st.session_state.get("selected_gallery_submission")

    detail = next(
        (submission for submission in approved if submission["id"] == selected),
        None,
    )

    if detail:
        if st.button("Back to gallery"):
            st.session_state["selected_gallery_submission"] = None
            st.rerun()

        st.subheader(submission_year_label(detail))
        st.write(f"Shared by: {detail['submitter_name']}")

        for photo in detail["photos"]:
            with st.container(border=True):
                show_stored_image(client, photo)
                st.write(f"Year: {photo_year(photo, detail) or 'Unknown'}")
                st.write(photo.get("caption") or "No caption added.")

        return

    st.subheader("Photo gallery")
    st.caption("Approved memories, ordered by their earliest photograph year.")

    if not approved:
        st.info("No approved photographs are available yet.")
        return

    # A new set of columns per row keeps the grid aligned.
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
                        <div style="
                            width:100%;
                            aspect-ratio:1 / 1;
                            overflow:hidden;
                            border-radius:12px;
                            background:#F1ECE4;
                            position:relative;
                        ">
                            <img src="{safe_url}"
                                 alt="Memory cover photograph"
                                 style="
                                     position:absolute;
                                     inset:0;
                                     width:100%;
                                     height:100%;
                                     object-fit:cover;
                                     display:block;
                                 ">
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.warning("Preview unavailable.")

                st.caption(submission_year_label(submission))
                st.caption(f"Shared by {submission['submitter_name']}")

                if st.button(
                    "View photographs",
                    key=f"gallery_{submission['id']}",
                    use_container_width=True,
                ):
                    st.session_state["selected_gallery_submission"] = (
                        submission["id"]
                    )
                    st.rerun()


def render_review(client, submissions):
    statuses = ["pending", "approved", "private", "rejected"]

    chosen = st.selectbox(
        "Filter by status",
        ["All"] + statuses,
    )

    for submission in submissions:
        if chosen != "All" and submission["status"] != chosen:
            continue

        with st.expander(
            f"{submission['submitter_name']} — "
            f"{submission_year_label(submission)} — "
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
                            "Year (YYYY)",
                            value=str(photo_year(photo, submission) or ""),
                            key=f"review_year_{photo['id']}",
                            max_chars=4,
                        )

                    updates.append((photo["id"], caption, year))

                current_status = submission.get("status", "pending")

                status = st.selectbox(
                    "Submission status",
                    statuses,
                    index=(
                        statuses.index(current_status)
                        if current_status in statuses
                        else 0
                    ),
                    key=f"review_status_{submission['id']}",
                )

                saved = st.form_submit_button("Save changes")

            if saved:
                invalid = [
                    year for _, _, year in updates
                    if year.strip() and not valid_year(year)
                ]

                if invalid:
                    st.error("Please enter valid four-digit years.")
                    continue

                try:
                    for photo_id, caption, year in updates:
                        client.table("submission_photos").update(
                            {
                                "caption": caption.strip() or None,
                                "photo_year": int(year) if year.strip() else None,
                            }
                        ).eq("id", photo_id).execute()

                    years = {
                        int(year)
                        for _, _, year in updates
                        if year.strip()
                    }

                    client.table("submissions").update(
                        {
                            "status": status,
                            "photo_year": (
                                next(iter(years)) if len(years) == 1 else None
                            ),
                        }
                    ).eq("id", submission["id"]).execute()

                except Exception:
                    logging.exception("Curator update failed")
                    st.error("Could not save all changes. Please try again.")
                else:
                    st.rerun()


def render_export(submissions):
    rows = []

    for submission in submissions:
        for photo in submission.get("photos", []):
            rows.append(
                {
                    "submission_created_at": submission["created_at"],
                    "submitter_name": submission["submitter_name"],
                    "photo_year": photo_year(photo, submission),
                    "caption": photo.get("caption") or "",
                    "photo_filename": photo["original_filename"],
                    "photo_storage_path": photo["storage_path"],
                    "photo_order": photo["photo_order"],
                    "submission_status": submission["status"],
                }
            )

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
        logging.exception("Could not load curator data")
        st.error("Could not load photographs. Please try again.")
        return

    # Radio navigation preserves the selected view across reruns.
    area = st.radio(
        "Dashboard view",
        ["Timeline", "Gallery", "Review and edit", "Export"],
        horizontal=True,
        key="curator_view",
    )

    if area == "Timeline":
        for submission in submissions:
            with st.expander(
                f"{submission['submitter_name']} — "
                f"{len(submission.get('photos', []))} photographs — "
                f"{submission['status']}"
            ):
                st.write(f"Received: {submission['created_at']}")
                st.write(f"Years: {submission_year_label(submission)}")

    elif area == "Gallery":
        render_gallery(client, submissions)

    elif area == "Review and edit":
        render_review(client, submissions)

    else:
        render_export(submissions)


def main():
    area = st.sidebar.radio(
        "Choose an area",
        ["Share a photograph", "Curator dashboard"],
    )

    if area == "Share a photograph":
        render_public_upload()
    elif curator_login():
        render_curator_dashboard()


if __name__ == "__main__":
    main()
